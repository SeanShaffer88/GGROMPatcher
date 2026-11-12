"""High-level ROM inspection, patch planning, patching, backup, and IPS export orchestration."""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ggrompatcher.cheatfiles import CheatEntry, parse_code_tokens
from ggrompatcher.models import (
    BatchRomItemResult,
    ChecksumEntry,
    CodeFormatError,
    ConsoleSystem,
    DecodedCode,
    DiffHunk,
    DiffPreview,
    GGPatcherError,
    MatchStatus,
    PatchResult,
    PlannedPatch,
    RomFormatError,
    RomInfo,
)
from ggrompatcher.patch_formats import apply_binary_patch, write_binary_patch_file
from ggrompatcher.systems import detect_system, get_handler

KNOWN_ROM_EXTENSIONS = frozenset(
    {
        ".gb",
        ".gbc",
        ".sgb",
        ".gg",
        ".sms",
        ".nes",
        ".unf",
        ".sfc",
        ".smc",
        ".swc",
        ".fig",
        ".bin",
        ".md",
        ".gen",
        ".smd",
        ".68k",
    }
)


def decode_code(
    code: str,
    system: ConsoleSystem | str | None = None,
    rom: bytes | None = None,
    filename: str | None = None,
    description: str = "",
) -> DecodedCode:
    """Decode a single Game Genie or raw code for the specified or auto-detected system."""
    if system is None:
        if rom is None:
            raise CodeFormatError("Please specify a console system when decoding without a ROM.")
        detected, _ = detect_system(rom, filename=filename)
        handler = get_handler(detected)
    else:
        handler = get_handler(system)
    return handler.decode_code(code, description=description)


def encode_code(
    system: ConsoleSystem | str,
    address: int,
    value: int,
    compare: int | None = None,
    *,
    code_format: str = "gg",
) -> str:
    """Encode an address, replacement value, and optional compare byte for a console."""
    handler = get_handler(system)
    return handler.encode_code(
        address=address,
        value=value,
        compare=compare,
        code_format=code_format,
    )


def inspect_rom(
    rom: bytes,
    system: ConsoleSystem | str | None = None,
    filename: str | None = None,
    **options: Any,
) -> RomInfo:
    """Inspect ROM metadata, header format, and current checksums."""
    resolved_sys = (
        (system if isinstance(system, ConsoleSystem) else ConsoleSystem.from_str(system))
        if system is not None
        else detect_system(rom, filename=filename)[0]
    )
    handler = get_handler(resolved_sys)
    norm_rom, ctx = handler.normalize_input_rom(rom, filename=filename)
    merged_opts = {**ctx, **options}
    return handler.inspect_rom(norm_rom, **merged_opts)


def _normalize_codes_input(
    codes: Sequence[str | CheatEntry | DecodedCode] | str,
    handler_decode: Any,
) -> list[DecodedCode]:
    if isinstance(codes, str):
        items: list[str | CheatEntry | DecodedCode] = list(parse_code_tokens(codes))
    else:
        items = list(codes)

    decoded_list: list[DecodedCode] = []
    for item in items:
        if isinstance(item, DecodedCode):
            decoded_list.append(item)
        elif isinstance(item, CheatEntry):
            for tok in parse_code_tokens(item.code):
                decoded_list.append(handler_decode(tok, description=item.description))
        else:
            for tok in parse_code_tokens(item):
                decoded_list.append(handler_decode(tok))
    return decoded_list


def plan_patches(
    rom: bytes,
    codes: Sequence[str | CheatEntry | DecodedCode] | str,
    *,
    system: ConsoleSystem | str | None = None,
    filename: str | None = None,
    bank: int | None = None,
    bank_overrides: dict[int, int] | None = None,
    offset_overrides: dict[int, int] | None = None,
    all_matches: bool = False,
    force: bool = False,
    allow_raw_fallback: bool = True,
    **options: Any,
) -> tuple[RomInfo, bytes, dict[str, Any], list[PlannedPatch]]:
    """Resolve codes against `rom` without modifying bytes, returning a `PlannedPatch` list."""
    resolved_sys = (
        (system if isinstance(system, ConsoleSystem) else ConsoleSystem.from_str(system))
        if system is not None
        else detect_system(rom, filename=filename)[0]
    )
    handler = get_handler(resolved_sys)
    norm_rom, ctx = handler.normalize_input_rom(rom, filename=filename)
    merged_opts = {**ctx, **options}
    rom_info = handler.inspect_rom(norm_rom, **merged_opts)

    raw_handler = get_handler(ConsoleSystem.RAW)

    def _decode_item(tok: str, description: str = "") -> DecodedCode:
        upper = tok.strip().upper()
        is_prefixed = upper.startswith(
            ("PAR:", "ROCKY:", "GS:", "AR:", "PAR ", "ROCKY ", "GS ", "AR ")
        )
        if (
            allow_raw_fallback
            and not is_prefixed
            and (":" in tok or "=" in tok)
            and resolved_sys != ConsoleSystem.RAW
        ):
            return raw_handler.decode_code(tok, description=description)
        return handler.decode_code(tok, description=description)

    decoded_codes = _normalize_codes_input(codes, _decode_item)
    plans: list[PlannedPatch] = []

    for idx, decoded in enumerate(decoded_codes):
        active_handler = raw_handler if decoded.system == ConsoleSystem.RAW else handler
        candidates = active_handler.resolve_candidates(norm_rom, decoded, **merged_opts)

        if not candidates:
            plans.append(
                PlannedPatch(
                    code=decoded,
                    candidates=[],
                    selected_offsets=[],
                    status=MatchStatus.OUT_OF_RANGE,
                    message=f"Address 0x{decoded.address:X} does not map inside this ROM.",
                    can_apply=False,
                )
            )
            continue

        chosen_offset = offset_overrides.get(idx) if offset_overrides else None
        chosen_bank = bank_overrides.get(idx) if bank_overrides and idx in bank_overrides else bank

        if chosen_offset is not None or chosen_bank is not None:
            if chosen_offset is not None:
                matched_cands = [c for c in candidates if c.file_offset == chosen_offset]
                missing_msg = f"Selected offset 0x{chosen_offset:06X} is not valid for this code."
            else:
                assert chosen_bank is not None
                matched_cands = [c for c in candidates if c.bank == chosen_bank]
                missing_msg = f"Bank 0x{chosen_bank:02X} is not a candidate for this code."

            if not matched_cands:
                plans.append(
                    PlannedPatch(
                        code=decoded,
                        candidates=candidates,
                        selected_offsets=[],
                        status=MatchStatus.OUT_OF_RANGE,
                        message=missing_msg,
                        can_apply=False,
                    )
                )
                continue

            cand = matched_cands[0]
            if cand.current_bytes == decoded.value:
                plans.append(
                    PlannedPatch(
                        code=decoded,
                        candidates=candidates,
                        selected_offsets=[cand.file_offset],
                        status=MatchStatus.ALREADY_PATCHED,
                        message=(
                            f"Already patched at {cand.label} (0x{decoded.value.hex().upper()})."
                        ),
                        can_apply=True,
                    )
                )
            elif decoded.compare is not None and not cand.matches_compare and not force:
                plans.append(
                    PlannedPatch(
                        code=decoded,
                        candidates=candidates,
                        selected_offsets=[],
                        status=MatchStatus.COMPARE_MISMATCH,
                        message=(
                            f"Compare mismatch at {cand.label}: expected "
                            f"0x{decoded.compare.hex().upper()}, found "
                            f"0x{cand.current_bytes.hex().upper()}."
                        ),
                        can_apply=False,
                    )
                )
            else:
                note = (
                    " (forced)" if decoded.compare is not None and not cand.matches_compare else ""
                )
                plans.append(
                    PlannedPatch(
                        code=decoded,
                        candidates=candidates,
                        selected_offsets=[cand.file_offset],
                        status=MatchStatus.READY,
                        message=f"Ready at {cand.label}{note}.",
                        can_apply=True,
                    )
                )
            continue

        # Automatic candidate resolution when no explicit bank/offset override is provided.
        if decoded.compare is not None:
            matching = [c for c in candidates if c.matches_compare]
            if len(matching) == 1:
                cand = matching[0]
                if cand.current_bytes == decoded.value:
                    plans.append(
                        PlannedPatch(
                            code=decoded,
                            candidates=candidates,
                            selected_offsets=[cand.file_offset],
                            status=MatchStatus.ALREADY_PATCHED,
                            message=f"Already patched at {cand.label}.",
                            can_apply=True,
                        )
                    )
                else:
                    plans.append(
                        PlannedPatch(
                            code=decoded,
                            candidates=candidates,
                            selected_offsets=[cand.file_offset],
                            status=MatchStatus.READY,
                            message=(
                                f"Compare matched (0x{decoded.compare.hex().upper()}) "
                                f"at {cand.label}."
                            ),
                            can_apply=True,
                        )
                    )
            elif len(matching) > 1:
                if all_matches:
                    plans.append(
                        PlannedPatch(
                            code=decoded,
                            candidates=candidates,
                            selected_offsets=[c.file_offset for c in matching],
                            status=MatchStatus.READY,
                            message=f"Matched {len(matching)} banks (patching all matches).",
                            can_apply=True,
                        )
                    )
                else:
                    locs = ", ".join(c.label for c in matching[:6])
                    suffix = "..." if len(matching) > 6 else ""
                    plans.append(
                        PlannedPatch(
                            code=decoded,
                            candidates=candidates,
                            selected_offsets=[],
                            status=MatchStatus.AMBIGUOUS,
                            message=(
                                f"Compare byte 0x{decoded.compare.hex().upper()} matched "
                                f"{len(matching)} banks ({locs}{suffix}); "
                                "specify --bank or --all-matches."
                            ),
                            can_apply=False,
                        )
                    )
            else:
                # 0 compare matches.
                already = [c for c in candidates if c.current_bytes == decoded.value]
                if len(already) == 1:
                    plans.append(
                        PlannedPatch(
                            code=decoded,
                            candidates=candidates,
                            selected_offsets=[already[0].file_offset],
                            status=MatchStatus.ALREADY_PATCHED,
                            message=f"Already patched at {already[0].label}.",
                            can_apply=True,
                        )
                    )
                elif force and (len(candidates) == 1 or all_matches):
                    plans.append(
                        PlannedPatch(
                            code=decoded,
                            candidates=candidates,
                            selected_offsets=[c.file_offset for c in candidates],
                            status=MatchStatus.READY,
                            message=(
                                f"Compare mismatch overridden (--force) across "
                                f"{len(candidates)} candidate(s)."
                            ),
                            can_apply=True,
                        )
                    )
                else:
                    found_desc = (
                        f"0x{candidates[0].current_bytes.hex().upper()}"
                        if len(candidates) == 1
                        else f"0 of {len(candidates)} banks"
                    )
                    plans.append(
                        PlannedPatch(
                            code=decoded,
                            candidates=candidates,
                            selected_offsets=[],
                            status=MatchStatus.COMPARE_MISMATCH,
                            message=(
                                f"Compare byte 0x{decoded.compare.hex().upper()} not matched "
                                f"(found {found_desc})."
                            ),
                            can_apply=False,
                        )
                    )
        else:
            # No compare byte (6-digit GB/GG/NES, SNES, Genesis, Raw without compare).
            if len(candidates) == 1:
                cand = candidates[0]
                if cand.current_bytes == decoded.value:
                    plans.append(
                        PlannedPatch(
                            code=decoded,
                            candidates=candidates,
                            selected_offsets=[cand.file_offset],
                            status=MatchStatus.ALREADY_PATCHED,
                            message=f"Already patched at {cand.label}.",
                            can_apply=True,
                        )
                    )
                else:
                    plans.append(
                        PlannedPatch(
                            code=decoded,
                            candidates=candidates,
                            selected_offsets=[cand.file_offset],
                            status=MatchStatus.READY,
                            message=f"Ready at {cand.label}.",
                            can_apply=True,
                        )
                    )
            elif all_matches:
                plans.append(
                    PlannedPatch(
                        code=decoded,
                        candidates=candidates,
                        selected_offsets=[c.file_offset for c in candidates],
                        status=MatchStatus.READY,
                        message=f"Patching all {len(candidates)} candidate banks.",
                        can_apply=True,
                    )
                )
            else:
                plans.append(
                    PlannedPatch(
                        code=decoded,
                        candidates=candidates,
                        selected_offsets=[],
                        status=MatchStatus.AMBIGUOUS,
                        message=(
                            f"Code has no compare byte and ROM has {len(candidates)} candidate "
                            "banks; specify --bank or --all-matches."
                        ),
                        can_apply=False,
                    )
                )

    return rom_info, norm_rom, merged_opts, plans


def apply_patches(
    rom: bytes,
    codes: Sequence[str | CheatEntry | DecodedCode] | str,
    *,
    system: ConsoleSystem | str | None = None,
    filename: str | None = None,
    output_filename: str | None = None,
    bank: int | None = None,
    bank_overrides: dict[int, int] | None = None,
    offset_overrides: dict[int, int] | None = None,
    all_matches: bool = False,
    force: bool = False,
    fix_checksum: bool = True,
    strict: bool = False,
    **options: Any,
) -> PatchResult:
    """Apply Game Genie / raw codes to `rom` bytes and optionally recalculate checksums."""
    rom_info_before, norm_rom, merged_opts, plans = plan_patches(
        rom,
        codes,
        system=system,
        filename=filename,
        bank=bank,
        bank_overrides=bank_overrides,
        offset_overrides=offset_overrides,
        all_matches=all_matches,
        force=force,
        **options,
    )

    if strict:
        blocked = [p for p in plans if not p.can_apply]
        if blocked:
            details = "; ".join(f"{p.code.normalized_code}: {p.message}" for p in blocked)
            raise GGPatcherError(f"Patch validation failed: {details}")

    buf = bytearray(norm_rom)
    applied: list[PlannedPatch] = []
    skipped: list[PlannedPatch] = []
    warnings: list[str] = []
    written_offsets: dict[int, tuple[str, int]] = {}
    bytes_modified = 0

    for plan in plans:
        for warn in plan.code.warnings:
            warnings.append(f"{plan.code.normalized_code}: {warn}")

        if not plan.can_apply or not plan.selected_offsets:
            skipped.append(plan)
            continue

        val = plan.code.value
        for off in plan.selected_offsets:
            for i, b in enumerate(val):
                target = off + i
                if target in written_offsets and written_offsets[target][1] != b:
                    prev_code = written_offsets[target][0]
                    warnings.append(
                        f"Offset 0x{target:06X} overwritten by {plan.code.normalized_code} "
                        f"(previously modified by {prev_code})."
                    )
                if buf[target] != b:
                    bytes_modified += 1
                buf[target] = b
                written_offsets[target] = (plan.code.normalized_code, b)
        applied.append(plan)

    handler = get_handler(rom_info_before.system)
    checksums_updated: list[ChecksumEntry] = []
    if fix_checksum and (applied or bytes_modified > 0):
        checksums_updated = handler.fix_checksums(buf, **merged_opts)

    rom_info_after = handler.inspect_rom(bytes(buf), **merged_opts)
    final_bytes = handler.finalize_output_rom(
        bytes(buf),
        merged_opts,
        output_filename=output_filename or filename,
    )

    return PatchResult(
        rom_info_before=rom_info_before,
        rom_info_after=rom_info_after,
        planned_patches=plans,
        applied_patches=applied,
        skipped_patches=skipped,
        checksums_updated=checksums_updated,
        bytes_modified=bytes_modified,
        output_bytes=final_bytes,
        warnings=warnings,
    )


def fix_rom_checksums(
    rom: bytes,
    *,
    system: ConsoleSystem | str | None = None,
    filename: str | None = None,
    output_filename: str | None = None,
    **options: Any,
) -> tuple[RomInfo, RomInfo, bytes]:
    """Recalculate and repair checksums on a ROM without applying any code patches."""
    resolved_sys = (
        (system if isinstance(system, ConsoleSystem) else ConsoleSystem.from_str(system))
        if system is not None
        else detect_system(rom, filename=filename)[0]
    )
    handler = get_handler(resolved_sys)
    norm_rom, ctx = handler.normalize_input_rom(rom, filename=filename)
    merged_opts = {**ctx, **options}
    before = handler.inspect_rom(norm_rom, **merged_opts)
    buf = bytearray(norm_rom)
    handler.fix_checksums(buf, **merged_opts)
    after = handler.inspect_rom(bytes(buf), **merged_opts)
    final_bytes = handler.finalize_output_rom(
        bytes(buf), merged_opts, output_filename=output_filename or filename
    )
    return before, after, final_bytes


def default_patched_path(input_path: str | Path) -> Path:
    """Return `<stem>.patched<suffix>` alongside `input_path`."""
    p = Path(input_path)
    return p.with_name(f"{p.stem}.patched{p.suffix}")


def patch_rom_file(
    input_path: str | Path,
    codes: Sequence[str | CheatEntry | DecodedCode] | str,
    *,
    output_path: str | Path | None = None,
    in_place: bool = False,
    backup: bool = True,
    ips_output_path: str | Path | None = None,
    bps_output_path: str | Path | None = None,
    ups_output_path: str | Path | None = None,
    system: ConsoleSystem | str | None = None,
    bank: int | None = None,
    bank_overrides: dict[int, int] | None = None,
    offset_overrides: dict[int, int] | None = None,
    all_matches: bool = False,
    force: bool = False,
    fix_checksum: bool = True,
    strict: bool = False,
    dry_run: bool = False,
    **options: Any,
) -> tuple[PatchResult, Path | None]:
    """Read `input_path`, apply patches, and write the patched ROM (and optional IPS/BPS/UPS)."""
    src = Path(input_path)
    if not src.is_file():
        raise RomFormatError(f"ROM file not found: {src}")

    original_bytes = src.read_bytes()
    dest = src if in_place else (Path(output_path) if output_path else default_patched_path(src))

    result = apply_patches(
        original_bytes,
        codes,
        system=system,
        filename=src.name,
        output_filename=dest.name,
        bank=bank,
        bank_overrides=bank_overrides,
        offset_overrides=offset_overrides,
        all_matches=all_matches,
        force=force,
        fix_checksum=fix_checksum,
        strict=strict,
        **options,
    )

    if dry_run:
        return result, None

    if in_place and backup:
        bak = src.with_name(f"{src.name}.bak")
        shutil.copy2(src, bak)

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(result.output_bytes)

    patch_exports: list[tuple[str, str | Path]] = []
    if ips_output_path is not None:
        patch_exports.append(("ips", ips_output_path))
    if bps_output_path is not None:
        patch_exports.append(("bps", bps_output_path))
    if ups_output_path is not None:
        patch_exports.append(("ups", ups_output_path))

    if patch_exports:
        base_for_patch = original_bytes
        if result.rom_info_before.extra.get("smd_interleaved") and dest.suffix.lower() != ".smd":
            _, norm_rom, _, _ = plan_patches(
                original_bytes,
                codes,
                system=system,
                filename=src.name,
                **options,
            )
            base_for_patch = norm_rom
            result.warnings.append(
                "Input was .smd interleaved while output is plain binary; "
                "binary patch was generated relative to the de-interleaved binary ROM."
            )
        for fmt, out_patch in patch_exports:
            write_binary_patch_file(out_patch, base_for_patch, result.output_bytes, fmt=fmt)

    return result, dest


def build_hex_diff_preview(
    original: bytes,
    patched: bytes,
    *,
    max_hunks: int = 256,
    merge_gap: int = 4,
    checksum_notes: Sequence[str] = (),
) -> DiffPreview:
    """Compute contiguous hex diff hunks between `original` and `patched` bytes."""
    orig_len = len(original)
    pat_len = len(patched)
    scan_len = max(orig_len, pat_len)

    diff_indices: list[int] = []
    min_len = min(orig_len, pat_len)
    for i in range(min_len):
        if original[i] != patched[i]:
            diff_indices.append(i)
    if scan_len > min_len:
        diff_indices.extend(range(min_len, scan_len))

    if not diff_indices:
        return DiffPreview(
            original_size=orig_len,
            patched_size=pat_len,
            bytes_changed=0,
            total_hunks=0,
            truncated=False,
            hunks=(),
            checksum_notes=tuple(checksum_notes),
        )

    ranges: list[tuple[int, int]] = []
    start = diff_indices[0]
    end = start + 1
    for idx in diff_indices[1:]:
        if idx - end <= merge_gap and (idx + 1 - start) <= 64:
            end = idx + 1
        else:
            ranges.append((start, end))
            start = idx
            end = idx + 1
    ranges.append((start, end))

    truncated = len(ranges) > max_hunks
    hunks = tuple(
        DiffHunk(
            offset=s,
            original=original[s : min(e, orig_len)],
            patched=patched[s : min(e, pat_len)],
        )
        for s, e in ranges[:max_hunks]
    )
    return DiffPreview(
        original_size=orig_len,
        patched_size=pat_len,
        bytes_changed=len(diff_indices),
        total_hunks=len(ranges),
        truncated=truncated,
        hunks=hunks,
        checksum_notes=tuple(checksum_notes),
    )


def _format_ascii_preview(data: bytes) -> str:
    return "".join(chr(b) if 0x20 <= b <= 0x7E else "." for b in data)


def format_hex_diff_text(preview: DiffPreview) -> str:
    """Render a `DiffPreview` into a readable hex + ASCII comparison report."""
    lines = [
        f"Original size : {preview.original_size:,} bytes",
        f"Patched size  : {preview.patched_size:,} bytes",
        f"Bytes changed : {preview.bytes_changed:,} byte(s) across {preview.total_hunks} hunk(s)",
    ]
    for note in preview.checksum_notes:
        lines.append(f"Checksum      : {note}")

    if not preview.hunks:
        lines.append("")
        lines.append("No byte differences.")
        return "\n".join(lines)

    lines.append("")
    for hunk in preview.hunks:
        span = max(len(hunk.original), len(hunk.patched))
        lines.append(f"Offset 0x{hunk.offset:06X} ({span} byte{'s' if span != 1 else ''}):")
        for row_off in range(0, span, 16):
            orig_chunk = hunk.original[row_off : row_off + 16]
            pat_chunk = hunk.patched[row_off : row_off + 16]
            orig_hex = " ".join(f"{b:02X}" for b in orig_chunk) if orig_chunk else "<EOF>"
            pat_hex = " ".join(f"{b:02X}" for b in pat_chunk) if pat_chunk else "<EOF>"
            lines.append(
                f"  0x{hunk.offset + row_off:06X} - {orig_hex:<47} | "
                f"{_format_ascii_preview(orig_chunk)}"
            )
            lines.append(
                f"  0x{hunk.offset + row_off:06X} + {pat_hex:<47} | "
                f"{_format_ascii_preview(pat_chunk)}"
            )
    if preview.truncated:
        lines.append(
            f"\n... truncated (showing first {len(preview.hunks)} of {preview.total_hunks} hunks)"
        )
    return "\n".join(lines)


def preview_patch_diff(
    rom: bytes,
    codes: Sequence[str | CheatEntry | DecodedCode] | str,
    *,
    system: ConsoleSystem | str | None = None,
    filename: str | None = None,
    bank: int | None = None,
    bank_overrides: dict[int, int] | None = None,
    offset_overrides: dict[int, int] | None = None,
    all_matches: bool = False,
    force: bool = False,
    fix_checksum: bool = True,
    strict: bool = False,
    max_hunks: int = 256,
    **options: Any,
) -> tuple[PatchResult, DiffPreview]:
    """Run patch application in memory and return `(PatchResult, DiffPreview)`."""
    result = apply_patches(
        rom,
        codes,
        system=system,
        filename=filename,
        bank=bank,
        bank_overrides=bank_overrides,
        offset_overrides=offset_overrides,
        all_matches=all_matches,
        force=force,
        fix_checksum=fix_checksum,
        strict=strict,
        **options,
    )
    chk_notes: list[str] = []
    before_map = {c.name: c for c in result.rom_info_before.checksums}
    for after_c in result.rom_info_after.checksums:
        before_c = before_map.get(after_c.name)
        w = after_c.width * 2
        if before_c is not None:
            chk_notes.append(
                f"{after_c.name}: 0x{before_c.stored:0{w}X} -> 0x{after_c.stored:0{w}X} "
                f"({'OK' if after_c.valid else 'INVALID'})"
            )
    preview = build_hex_diff_preview(
        rom,
        result.output_bytes,
        max_hunks=max_hunks,
        checksum_notes=chk_notes,
    )
    return result, preview


def collect_rom_files(paths: Sequence[str | Path], *, recursive: bool = False) -> list[Path]:
    """Expand files and directories into an ordered deduplicated list of ROM paths."""
    collected: list[Path] = []
    seen: set[Path] = set()
    for item in paths:
        p = Path(item)
        if p.is_file():
            resolved = p.resolve()
            if resolved not in seen:
                seen.add(resolved)
                collected.append(p)
        elif p.is_dir():
            iterator = p.rglob("*") if recursive else p.iterdir()
            for child in sorted(iterator):
                if child.is_file() and child.suffix.lower() in KNOWN_ROM_EXTENSIONS:
                    resolved = child.resolve()
                    if resolved not in seen:
                        seen.add(resolved)
                        collected.append(child)
        else:
            raise RomFormatError(f"Input path does not exist: {p}")
    return collected


def batch_patch_roms(
    rom_paths: Sequence[str | Path],
    codes: Sequence[str | CheatEntry | DecodedCode] | str | None = None,
    *,
    patch_file: str | Path | None = None,
    output_dir: str | Path | None = None,
    in_place: bool = False,
    backup: bool = True,
    verify_patch_checksums: bool = True,
    system: ConsoleSystem | str | None = None,
    bank: int | None = None,
    all_matches: bool = False,
    force: bool = False,
    fix_checksum: bool = True,
    strict: bool = False,
    dry_run: bool = False,
    **options: Any,
) -> list[BatchRomItemResult]:
    """Apply codes or a binary patch (.ips/.bps/.ups) across multiple ROM files."""
    if not rom_paths:
        raise RomFormatError("No ROM files provided for batch patching.")
    if patch_file is None and not codes:
        raise GGPatcherError("Provide Game Genie/PAR codes or a .ips/.bps/.ups patch file.")
    if in_place and output_dir is not None:
        raise GGPatcherError("Cannot specify both in-place mode and an output directory.")

    patch_bytes: bytes | None = None
    patch_name: str | None = None
    if patch_file is not None:
        pf = Path(patch_file)
        if not pf.is_file():
            raise GGPatcherError(f"Patch file not found: {pf}")
        patch_bytes = pf.read_bytes()
        patch_name = pf.name

    out_dir_path = Path(output_dir) if output_dir is not None else None
    results: list[BatchRomItemResult] = []

    for rom_item in rom_paths:
        src = Path(rom_item)
        if in_place:
            dest = src
        elif out_dir_path is not None:
            dest = out_dir_path / src.name
        else:
            dest = default_patched_path(src)

        try:
            if not src.is_file():
                raise RomFormatError(f"ROM file not found: {src}")

            if patch_bytes is not None:
                orig = src.read_bytes()
                patched = apply_binary_patch(
                    orig,
                    patch_bytes,
                    filename=patch_name,
                    verify_checksums=verify_patch_checksums,
                )
                if fix_checksum:
                    _, after, patched = fix_rom_checksums(
                        patched,
                        system=system,
                        filename=src.name,
                        output_filename=dest.name,
                        **options,
                    )
                    _ = after
                diff = build_hex_diff_preview(orig, patched, max_hunks=1)
                if not dry_run:
                    if in_place and backup:
                        shutil.copy2(src, src.with_name(f"{src.name}.bak"))
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(patched)
                results.append(
                    BatchRomItemResult(
                        rom_path=src,
                        output_path=None if dry_run else dest,
                        success=True,
                        bytes_modified=diff.bytes_changed,
                        applied_count=1 if diff.bytes_changed > 0 else 0,
                    )
                )
            else:
                assert codes is not None
                res, written_dest = patch_rom_file(
                    src,
                    codes,
                    output_path=None if in_place else dest,
                    in_place=in_place,
                    backup=backup,
                    system=system,
                    bank=bank,
                    all_matches=all_matches,
                    force=force,
                    fix_checksum=fix_checksum,
                    strict=strict,
                    dry_run=dry_run,
                    **options,
                )
                results.append(
                    BatchRomItemResult(
                        rom_path=src,
                        output_path=written_dest,
                        success=True,
                        bytes_modified=res.bytes_modified,
                        applied_count=len(res.applied_patches),
                        warnings=tuple(res.warnings),
                    )
                )
        except Exception as exc:
            results.append(
                BatchRomItemResult(
                    rom_path=src,
                    output_path=None,
                    success=False,
                    error=str(exc),
                )
            )
    return results
