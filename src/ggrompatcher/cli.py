"""Command-line interface for GGROMPatcher."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ggrompatcher.aps import create_aps
from ggrompatcher.cheatfiles import (
    CheatEntry,
    export_cheat_file,
    load_cheat_file,
    parse_code_tokens,
)
from ggrompatcher.models import (
    ConsoleSystem,
    DiffPreview,
    GGPatcherError,
    PatchResult,
    RomInfo,
)
from ggrompatcher.patch_formats import apply_binary_patch, write_binary_patch_file
from ggrompatcher.patcher import (
    batch_patch_roms,
    build_hex_diff_preview,
    collect_rom_files,
    decode_code,
    default_patched_path,
    encode_code,
    fix_rom_checksums,
    format_hex_diff_text,
    inspect_rom,
    patch_rom_file,
    preview_patch_diff,
)
from ggrompatcher.ppf import create_ppf
from ggrompatcher.romdb import RomDatabase, compute_rom_hashes
from ggrompatcher.systems import detect_system


def parse_int_auto(text: str) -> int:
    """Parse an integer written as decimal, `0x...`, or `$...` hex."""
    s = text.strip()
    if s.startswith("$"):
        return int(s[1:], 16)
    if s.lower().startswith("0x"):
        return int(s, 16)
    # Default to hex if A-F characters appear, else allow 0x/hex in addr/value helpers.
    if any(ch in "abcdefABCDEF" for ch in s):
        return int(s, 16)
    return int(s, 0)


def parse_hex_int(text: str) -> int:
    """Parse a hexadecimal value (accepting optional `0x` or `$` prefix)."""
    s = text.strip()
    if s.startswith("$"):
        s = s[1:]
    elif s.lower().startswith("0x"):
        s = s[2:]
    return int(s, 16)


def _rom_info_to_dict(info: RomInfo) -> dict[str, Any]:
    return {
        "system": info.system.value,
        "system_name": info.system.display_name,
        "file_size": info.file_size,
        "title": info.title,
        "header_format": info.header_format,
        "checksums": [
            {
                "name": c.name,
                "offset": f"0x{c.offset:06X}",
                "stored": f"0x{c.stored:0{c.width * 2}X}",
                "computed": f"0x{c.computed:0{c.width * 2}X}",
                "valid": c.valid,
            }
            for c in info.checksums
        ],
        "extra": info.extra,
    }


def _patch_result_to_dict(result: PatchResult, output_path: Path | None) -> dict[str, Any]:
    return {
        "output_path": str(output_path) if output_path else None,
        "bytes_modified": result.bytes_modified,
        "rom_before": _rom_info_to_dict(result.rom_info_before),
        "rom_after": _rom_info_to_dict(result.rom_info_after),
        "applied_count": len(result.applied_patches),
        "skipped_count": len(result.skipped_patches),
        "warnings": result.warnings,
        "patches": [
            {
                "code": p.code.normalized_code,
                "description": p.code.description,
                "address": f"0x{p.code.address:06X}",
                "value": p.code.value.hex().upper(),
                "compare": p.code.compare.hex().upper() if p.code.compare else None,
                "status": p.status.value,
                "can_apply": p.can_apply,
                "selected_offsets": [f"0x{off:06X}" for off in p.selected_offsets],
                "message": p.message,
            }
            for p in result.planned_patches
        ],
    }


def _print_rom_info(info: RomInfo) -> None:
    print(f"System       : {info.system.display_name} ({info.system.value})")
    print(f"Title        : {info.title}")
    print(f"Format       : {info.header_format}")
    print(f"Size         : {info.file_size:,} bytes (0x{info.file_size:X})")
    for k, v in info.extra.items():
        if v is not None and v != "":
            print(f"  {k:<14}: {v}")
    if info.checksums:
        print("Checksums    :")
        for c in info.checksums:
            w = c.width * 2
            status = "OK" if c.valid else "MISMATCH"
            print(
                f"  - {c.name} @ 0x{c.offset:06X}: "
                f"stored=0x{c.stored:0{w}X}, computed=0x{c.computed:0{w}X} [{status}]"
            )
    else:
        print("Checksums    : N/A (not used by this system)")


def _is_gba_target(
    system_arg: str | None, rom_bytes: bytes | None = None, filename: str | None = None
) -> bool:
    if system_arg:
        return ConsoleSystem.from_str(system_arg) == ConsoleSystem.GBA
    if rom_bytes is not None:
        return detect_system(rom_bytes, filename=filename)[0] == ConsoleSystem.GBA
    if filename:
        return Path(filename).suffix.lower() in {".gba", ".agb"}
    return False


def _cmd_decode(args: argparse.Namespace) -> int:
    rom_bytes: bytes | None = None
    filename: str | None = None
    if args.rom:
        rom_path = Path(args.rom)
        if not rom_path.is_file():
            raise GGPatcherError(f"ROM file not found: {rom_path}")
        rom_bytes = rom_path.read_bytes()
        filename = rom_path.name

    pair_hex8 = _is_gba_target(args.system, rom_bytes, filename)
    tokens: list[str] = []
    for raw in args.codes:
        tokens.extend(parse_code_tokens(raw, pair_hex8=pair_hex8))

    decoded_items = [
        decode_code(tok, system=args.system, rom=rom_bytes, filename=filename) for tok in tokens
    ]

    if args.json:
        payload = [
            {
                "code": d.normalized_code,
                "system": d.system.value,
                "address": f"0x{d.address:06X}",
                "value": f"0x{d.value.hex().upper()}",
                "compare": f"0x{d.compare.hex().upper()}" if d.compare else None,
                "warnings": list(d.warnings),
            }
            for d in decoded_items
        ]
        print(json.dumps(payload, indent=2))
        return 0

    for d in decoded_items:
        cmp_part = f" | compare=0x{d.compare.hex().upper()}" if d.compare else ""
        print(
            f"{d.normalized_code:<14} [{d.system.value}] "
            f"addr=0x{d.address:06X} | value=0x{d.value.hex().upper()}{cmp_part}"
        )
        for w in d.warnings:
            print(f"  WARNING: {w}")
    return 0


def _diff_preview_to_dict(preview: DiffPreview) -> dict[str, Any]:
    return {
        "original_size": preview.original_size,
        "patched_size": preview.patched_size,
        "bytes_changed": preview.bytes_changed,
        "total_hunks": preview.total_hunks,
        "truncated": preview.truncated,
        "checksum_notes": list(preview.checksum_notes),
        "hunks": [
            {
                "offset": f"0x{h.offset:06X}",
                "original": h.original.hex().upper(),
                "patched": h.patched.hex().upper(),
            }
            for h in preview.hunks
        ],
    }


def _cmd_encode(args: argparse.Namespace) -> int:
    addr = parse_hex_int(args.addr)
    val = parse_hex_int(args.value)
    cmp_val = parse_hex_int(args.compare) if args.compare is not None else None
    sys_type = ConsoleSystem.from_str(args.system)
    code = encode_code(sys_type, addr, val, cmp_val, code_format=args.format)

    if args.json:
        print(
            json.dumps(
                {
                    "system": sys_type.value,
                    "format": args.format,
                    "address": f"0x{addr:06X}",
                    "value": f"0x{val:02X}",
                    "compare": f"0x{cmp_val:02X}" if cmp_val is not None else None,
                    "code": code,
                },
                indent=2,
            )
        )
    else:
        print(code)
    return 0


def _cmd_inspect(args: argparse.Namespace) -> int:
    rom_path = Path(args.rom)
    if not rom_path.is_file():
        raise GGPatcherError(f"ROM file not found: {rom_path}")
    raw_bytes = rom_path.read_bytes()
    info = inspect_rom(
        raw_bytes,
        system=args.system,
        filename=rom_path.name,
        snes_map=args.snes_map,
    )

    hashes = None
    dat_matches = []
    if getattr(args, "hashes", False) or getattr(args, "dat", None):
        hashes = compute_rom_hashes(raw_bytes, system=info.system)
    if getattr(args, "dat", None):
        db = RomDatabase.from_file(args.dat)
        assert hashes is not None
        dat_matches = db.lookup_hashes(hashes)

    if args.json:
        payload = _rom_info_to_dict(info)
        if hashes is not None:
            payload["hashes"] = {
                "size": hashes.size,
                "crc32": hashes.crc32,
                "md5": hashes.md5,
                "sha1": hashes.sha1,
                "sha256": hashes.sha256,
                "header_bytes": hashes.header_bytes,
                "headerless_size": hashes.headerless_size,
                "headerless_crc32": hashes.headerless_crc32,
                "headerless_md5": hashes.headerless_md5,
                "headerless_sha1": hashes.headerless_sha1,
            }
        if getattr(args, "dat", None):
            payload["dat_matches"] = [
                {
                    "name": m.entry.name,
                    "game_title": m.entry.game_title,
                    "system": m.entry.system,
                    "region": m.entry.region,
                    "matched_by": m.matched_by,
                    "via_headerless": m.via_headerless,
                }
                for m in dat_matches
            ]
        print(json.dumps(payload, indent=2))
    else:
        _print_rom_info(info)
        if hashes is not None:
            print("Hashes       :")
            print(f"  - CRC32   : {hashes.crc32}")
            print(f"  - MD5     : {hashes.md5}")
            print(f"  - SHA-1   : {hashes.sha1}")
            print(f"  - SHA-256 : {hashes.sha256}")
            if hashes.header_bytes > 0 and hashes.headerless_crc32:
                print(
                    f"  - Headerless ({hashes.header_bytes}B stripped, "
                    f"{hashes.headerless_size:,} bytes): "
                    f"CRC32={hashes.headerless_crc32}, SHA-1={hashes.headerless_sha1}"
                )
        if getattr(args, "dat", None):
            if dat_matches:
                print("DAT Matches  :")
                for m in dat_matches:
                    hl = " [headerless]" if m.via_headerless else ""
                    reg = f" [{m.entry.region}]" if m.entry.region else ""
                    print(f"  - {m.entry.game_title}{reg} ({m.matched_by}{hl})")
            else:
                print("DAT Matches  : None found")
    return 0


def _cmd_patch(args: argparse.Namespace) -> int:
    rom_path = Path(args.rom)
    if not rom_path.is_file():
        raise GGPatcherError(f"ROM file not found: {rom_path}")

    raw_rom = rom_path.read_bytes()
    pair_hex8 = _is_gba_target(args.system, raw_rom, rom_path.name)

    entries: list[str | CheatEntry] = []
    for raw in args.code or []:
        for tok in parse_code_tokens(raw, pair_hex8=pair_hex8):
            entries.append(tok)
    for cheat_path in args.cheat_file or []:
        p = Path(cheat_path)
        if not p.is_file():
            raise GGPatcherError(f"Cheat file not found: {p}")
        entries.extend(load_cheat_file(p, enabled_only=args.enabled_only))

    if not entries:
        raise GGPatcherError("No Game Genie codes supplied. Provide --code and/or --cheat-file.")

    if args.in_place and args.output:
        raise GGPatcherError("Cannot specify both --in-place and --output.")

    bank = parse_int_auto(args.bank) if args.bank is not None else None

    strict = not args.skip_invalid and not args.dry_run
    result, out_path = patch_rom_file(
        rom_path,
        entries,
        output_path=args.output,
        in_place=args.in_place,
        backup=not args.no_backup,
        ips_output_path=args.ips_out,
        bps_output_path=args.bps_out,
        ups_output_path=args.ups_out,
        ppf_output_path=getattr(args, "ppf_out", None),
        aps_output_path=getattr(args, "aps_out", None),
        system=args.system,
        bank=bank,
        all_matches=args.all_matches,
        force=args.force,
        fix_checksum=not args.no_checksum,
        strict=strict,
        dry_run=args.dry_run,
        snes_map=args.snes_map,
        nes_bank_size=args.nes_bank_size,
    )

    if args.export_cheats:
        cheat_entries = [
            CheatEntry(code=tok, description="") if isinstance(tok, str) else tok for tok in entries
        ]
        export_cheat_file(args.export_cheats, cheat_entries)

    diff_preview: DiffPreview | None = None
    if args.preview_diff:
        _, diff_preview = preview_patch_diff(
            raw_rom,
            entries,
            system=args.system,
            filename=rom_path.name,
            bank=bank,
            all_matches=args.all_matches,
            force=args.force,
            fix_checksum=not args.no_checksum,
            strict=False,
            snes_map=args.snes_map,
            nes_bank_size=args.nes_bank_size,
        )

    if args.json:
        payload = _patch_result_to_dict(result, out_path)
        if diff_preview is not None:
            payload["diff"] = _diff_preview_to_dict(diff_preview)
        print(json.dumps(payload, indent=2))
    else:
        print(
            f"ROM: {rom_path.name} ({result.rom_info_before.system.display_name}, "
            f"{result.rom_info_before.header_format})"
        )
        print("-" * 72)
        for plan in result.planned_patches:
            status_tag = plan.status.value.upper()
            desc = f" ({plan.code.description})" if plan.code.description else ""
            print(f"[{status_tag:<16}] {plan.code.normalized_code:<14}{desc} -> {plan.message}")
        print("-" * 72)
        if result.warnings:
            for w in result.warnings:
                print(f"WARNING: {w}")
        if args.dry_run:
            print(
                f"Dry run complete: {len(result.applied_patches)} ready, "
                f"{len(result.skipped_patches)} blocked/skipped."
            )
        else:
            print(
                f"Applied {len(result.applied_patches)} patch(es), "
                f"{result.bytes_modified} byte(s) changed -> {out_path}"
            )
            if result.checksums_updated:
                chk_summary = ", ".join(
                    f"{c.name}=0x{c.computed:0{c.width * 2}X}" for c in result.checksums_updated
                )
                print(f"Checksums updated: {chk_summary}")
            if args.ips_out:
                print(f"IPS patch exported -> {args.ips_out}")
            if args.bps_out:
                print(f"BPS patch exported -> {args.bps_out}")
            if args.ups_out:
                print(f"UPS patch exported -> {args.ups_out}")
            if getattr(args, "ppf_out", None):
                print(f"PPF patch exported -> {args.ppf_out}")
            if getattr(args, "aps_out", None):
                print(f"APS patch exported -> {args.aps_out}")
            if args.export_cheats:
                print(f"Cheat sheet exported -> {args.export_cheats}")
        if diff_preview is not None:
            print()
            print(format_hex_diff_text(diff_preview))

    has_failures = any(not p.can_apply for p in result.planned_patches)
    if args.dry_run and has_failures:
        return 2
    if args.skip_invalid and len(result.applied_patches) == 0:
        return 2
    return 0


def _cmd_fix_checksum(args: argparse.Namespace) -> int:
    rom_path = Path(args.rom)
    if not rom_path.is_file():
        raise GGPatcherError(f"ROM file not found: {rom_path}")
    if args.in_place and args.output:
        raise GGPatcherError("Cannot specify both --in-place and --output.")

    dest = rom_path if args.in_place else Path(args.output or default_patched_path(rom_path))
    raw = rom_path.read_bytes()
    before, after, fixed = fix_rom_checksums(
        raw,
        system=args.system,
        filename=rom_path.name,
        output_filename=dest.name,
        snes_map=args.snes_map,
    )

    if not after.checksums:
        raise GGPatcherError(
            f"{before.system.display_name} does not use an embedded header checksum "
            "(or header missing)."
        )

    if args.in_place and not args.no_backup:
        shutil.copy2(rom_path, rom_path.with_name(f"{rom_path.name}.bak"))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(fixed)

    if args.json:
        print(
            json.dumps(
                {
                    "output_path": str(dest),
                    "before": _rom_info_to_dict(before),
                    "after": _rom_info_to_dict(after),
                },
                indent=2,
            )
        )
    else:
        print(f"Fixed checksums for {rom_path.name} -> {dest}")
        _print_rom_info(after)
    return 0


def _cmd_binary_patch_create(args: argparse.Namespace, fmt: str) -> int:
    orig_path = Path(args.original)
    mod_path = Path(args.modified)
    if not orig_path.is_file():
        raise GGPatcherError(f"Original ROM not found: {orig_path}")
    if not mod_path.is_file():
        raise GGPatcherError(f"Modified ROM not found: {mod_path}")

    orig_bytes = orig_path.read_bytes()
    mod_bytes = mod_path.read_bytes()

    if fmt == "ppf":
        payload = create_ppf(
            orig_bytes,
            mod_bytes,
            description=getattr(args, "description", "Created with GGROMPatcher"),
            version=getattr(args, "version", 3),
            blockcheck=getattr(args, "blockcheck", False),
            undo=getattr(args, "undo", False),
            file_id_diz=getattr(args, "diz", ""),
        )
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(payload)
    elif fmt == "aps":
        payload = create_aps(
            orig_bytes,
            mod_bytes,
            description=getattr(args, "description", "Created with GGROMPatcher"),
            variant=getattr(args, "variant", "auto"),
            n64_v64=getattr(args, "v64", False),
        )
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(payload)
    else:
        metadata = getattr(args, "metadata", "") or ""
        out_path = write_binary_patch_file(
            args.output,
            orig_bytes,
            mod_bytes,
            fmt=fmt,
            metadata=metadata,
        )
    print(f"{fmt.upper()} patch written -> {out_path} ({out_path.stat().st_size:,} bytes)")
    return 0


def _cmd_binary_patch_apply(args: argparse.Namespace, fmt: str) -> int:
    rom_path = Path(args.rom)
    patch_path = Path(args.patch)
    if not rom_path.is_file():
        raise GGPatcherError(f"ROM file not found: {rom_path}")
    if not patch_path.is_file():
        raise GGPatcherError(f"{fmt.upper()} patch not found: {patch_path}")
    if args.in_place and args.output:
        raise GGPatcherError("Cannot specify both --in-place and --output.")

    dest = rom_path if args.in_place else Path(args.output or default_patched_path(rom_path))
    verify = not getattr(args, "ignore_checksums", False)
    undo = bool(getattr(args, "undo", False))
    patched = apply_binary_patch(
        rom_path.read_bytes(),
        patch_path.read_bytes(),
        fmt=fmt,
        filename=patch_path.name,
        verify_checksums=verify,
        undo=undo,
    )

    if args.fix_checksum:
        _, _, patched = fix_rom_checksums(
            patched,
            system=args.system,
            filename=rom_path.name,
            output_filename=dest.name,
        )

    if args.in_place and not args.no_backup:
        shutil.copy2(rom_path, rom_path.with_name(f"{rom_path.name}.bak"))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(patched)
    print(f"Applied {patch_path.name} -> {dest} ({len(patched):,} bytes)")
    return 0


def _cmd_diff(args: argparse.Namespace) -> int:
    orig_path = Path(args.original)
    if not orig_path.is_file():
        raise GGPatcherError(f"Original ROM not found: {orig_path}")

    orig_bytes = orig_path.read_bytes()
    pair_hex8 = _is_gba_target(args.system, orig_bytes, orig_path.name)

    entries: list[str | CheatEntry] = []
    for raw in args.code or []:
        entries.extend(parse_code_tokens(raw, pair_hex8=pair_hex8))
    for cheat_path in args.cheat_file or []:
        p = Path(cheat_path)
        if not p.is_file():
            raise GGPatcherError(f"Cheat file not found: {p}")
        entries.extend(load_cheat_file(p, enabled_only=args.enabled_only))

    if args.modified is not None and not entries:
        mod_path = Path(args.modified)
        if not mod_path.is_file():
            raise GGPatcherError(f"Modified ROM not found: {mod_path}")
        preview = build_hex_diff_preview(
            orig_bytes,
            mod_path.read_bytes(),
            max_hunks=args.max_hunks,
        )
    elif entries and args.modified is None:
        bank = parse_int_auto(args.bank) if args.bank is not None else None
        _, preview = preview_patch_diff(
            orig_bytes,
            entries,
            system=args.system,
            filename=orig_path.name,
            bank=bank,
            all_matches=args.all_matches,
            force=args.force,
            fix_checksum=not args.no_checksum,
            strict=not args.skip_invalid,
            max_hunks=args.max_hunks,
            snes_map=args.snes_map,
            nes_bank_size=args.nes_bank_size,
        )
    else:
        raise GGPatcherError(
            "Provide either two ROM paths (ORIGINAL MODIFIED) or ORIGINAL with -c/-f codes."
        )

    if args.json:
        print(json.dumps(_diff_preview_to_dict(preview), indent=2))
    else:
        print(format_hex_diff_text(preview))
    return 0


def _cmd_export_cheats(args: argparse.Namespace) -> int:
    entries: list[CheatEntry] = []
    for raw in args.code or []:
        for tok in parse_code_tokens(raw):
            entries.append(CheatEntry(code=tok))
    for cheat_path in args.cheat_file or []:
        p = Path(cheat_path)
        if not p.is_file():
            raise GGPatcherError(f"Cheat file not found: {p}")
        entries.extend(load_cheat_file(p, enabled_only=args.enabled_only))

    if not entries:
        raise GGPatcherError("No codes provided. Use --code and/or --cheat-file.")

    out_path = export_cheat_file(args.output, entries, fmt=args.format)
    print(f"Exported {len(entries)} cheat(s) -> {out_path}")
    return 0


def _cmd_batch_patch(args: argparse.Namespace) -> int:
    rom_files = collect_rom_files(args.inputs, recursive=args.recursive)
    if not rom_files:
        raise GGPatcherError("No ROM files matched the provided input path(s).")

    pair_hex8 = _is_gba_target(args.system, filename=str(rom_files[0]) if rom_files else None)
    entries: list[str | CheatEntry] = []
    for raw in args.code or []:
        entries.extend(parse_code_tokens(raw, pair_hex8=pair_hex8))
    for cheat_path in args.cheat_file or []:
        p = Path(cheat_path)
        if not p.is_file():
            raise GGPatcherError(f"Cheat file not found: {p}")
        entries.extend(load_cheat_file(p, enabled_only=args.enabled_only))

    bank = parse_int_auto(args.bank) if args.bank is not None else None
    results = batch_patch_roms(
        rom_files,
        entries if entries else None,
        patch_file=args.patch_file,
        output_dir=args.out_dir,
        in_place=args.in_place,
        backup=not args.no_backup,
        verify_patch_checksums=not args.ignore_checksums,
        system=args.system,
        bank=bank,
        all_matches=args.all_matches,
        force=args.force,
        fix_checksum=not args.no_checksum,
        strict=not args.skip_invalid,
        dry_run=args.dry_run,
        snes_map=args.snes_map,
        nes_bank_size=args.nes_bank_size,
    )

    if args.json:
        payload = [
            {
                "rom": str(r.rom_path),
                "output_path": str(r.output_path) if r.output_path else None,
                "success": r.success,
                "bytes_modified": r.bytes_modified,
                "applied_count": r.applied_count,
                "warnings": list(r.warnings),
                "error": r.error,
            }
            for r in results
        ]
        print(json.dumps(payload, indent=2))
    else:
        for r in results:
            tag = "OK" if r.success else "ERR"
            detail = (
                f"{r.bytes_modified} byte(s) changed -> {r.output_path or '(dry-run)'}"
                if r.success
                else r.error
            )
            print(f"[{tag}] {r.rom_path.name:<28} {detail}")

    return 0 if all(r.success for r in results) else 2


def _make_patch_create_cmd(fmt: str) -> Callable[[argparse.Namespace], int]:
    def _handler(args: argparse.Namespace) -> int:
        return _cmd_binary_patch_create(args, fmt)

    return _handler


def _make_patch_apply_cmd(fmt: str) -> Callable[[argparse.Namespace], int]:
    def _handler(args: argparse.Namespace) -> int:
        return _cmd_binary_patch_apply(args, fmt)

    return _handler


def _cmd_gui(args: argparse.Namespace) -> int:
    from ggrompatcher.gui import launch_gui

    return launch_gui(initial_rom=args.rom)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ggrompatcher",
        description=(
            "GGROMPatcher — Apply Game Genie, PAR, and GameShark patches and "
            "IPS/BPS/UPS/PPF/APS binary patches across GB/GBC, GBA, Game Gear, Master System, "
            "NES, SNES, and Genesis/Mega Drive ROMs."
        ),
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    # gui
    p_gui = sub.add_parser("gui", help="Launch the desktop graphical interface.")
    p_gui.add_argument("rom", nargs="?", help="Optional ROM file to open on startup.")
    p_gui.set_defaults(func=_cmd_gui)

    # decode
    p_dec = sub.add_parser("decode", help="Decode one or more Game Genie / PAR / GSA codes.")
    p_dec.add_argument("codes", nargs="+", metavar="CODE", help="Game Genie / PAR / GSA code(s).")
    p_dec.add_argument(
        "-s", "--system", help="Console system (gb, gba, gg, sms, nes, snes, genesis, raw)."
    )
    p_dec.add_argument("--rom", help="Optional ROM used to auto-detect system.")
    p_dec.add_argument("--json", action="store_true", help="Output JSON.")
    p_dec.set_defaults(func=_cmd_decode)

    # encode
    p_enc = sub.add_parser("encode", help="Encode an address and value into a Game Genie/PAR code.")
    p_enc.add_argument("-s", "--system", required=True, help="Console system.")
    p_enc.add_argument("-a", "--addr", required=True, help="Target address in hex (e.g. 0x14BC).")
    p_enc.add_argument("-v", "--value", required=True, help="Replacement value in hex (e.g. 0x00).")
    p_enc.add_argument("-c", "--compare", help="Optional compare byte in hex (GB/GG/SMS/NES).")
    p_enc.add_argument(
        "--format",
        default="gg",
        choices=["gg", "par", "rocky", "gsa", "gsa1", "par3"],
        help="Code format: 'gg', 'par'/'rocky', or GBA 'gsa'/'gsa1'/'par3'.",
    )
    p_enc.add_argument("--json", action="store_true", help="Output JSON.")
    p_enc.set_defaults(func=_cmd_encode)

    # inspect
    p_ins = sub.add_parser(
        "inspect", help="Inspect ROM header metadata, checksums, hashes, and DAT matches."
    )
    p_ins.add_argument("rom", help="Path to ROM file.")
    p_ins.add_argument("-s", "--system", help="Override auto-detected console system.")
    p_ins.add_argument(
        "--snes-map",
        default="auto",
        choices=["auto", "lorom", "hirom", "exhirom", "exlorom"],
        help="SNES ROM memory map override.",
    )
    p_ins.add_argument(
        "--hashes",
        action="store_true",
        help="Compute CRC32, MD5, SHA-1, and SHA-256 (plus headerless hashes).",
    )
    p_ins.add_argument(
        "--dat",
        help="Verify ROM hashes against a Logiqx/No-Intro XML .dat or JSON ROM database.",
    )
    p_ins.add_argument("--json", action="store_true", help="Output JSON.")
    p_ins.set_defaults(func=_cmd_inspect)

    # patch
    p_pat = sub.add_parser("patch", help="Apply Game Genie codes or cheat files to a ROM.")
    p_pat.add_argument("rom", help="Input ROM file path.")
    p_pat.add_argument("-c", "--code", action="append", help="Game Genie code (or '+' chain).")
    p_pat.add_argument(
        "-f", "--cheat-file", action="append", help="Path to .txt, .gg, or .cht file."
    )
    p_pat.add_argument(
        "--enabled-only",
        action="store_true",
        help="Only import cheats marked enabled in RetroArch .cht files.",
    )
    p_pat.add_argument("-o", "--output", help="Output patched ROM path.")
    p_pat.add_argument(
        "-i",
        "--in-place",
        action="store_true",
        help="Overwrite the input ROM in place (creates .bak backup by default).",
    )
    p_pat.add_argument("--no-backup", action="store_true", help="Do not create .bak when using -i.")
    p_pat.add_argument("-s", "--system", help="Override auto-detected console system.")
    p_pat.add_argument(
        "--snes-map",
        default="auto",
        choices=["auto", "lorom", "hirom", "exhirom", "exlorom"],
        help="SNES ROM memory map override.",
    )
    p_pat.add_argument(
        "--nes-bank-size",
        default="auto",
        choices=["auto", "8k", "16k", "32k"],
        help="NES PRG bank granularity override.",
    )
    p_pat.add_argument(
        "--bank", help="Explicit bank number (decimal or 0x hex) for ambiguous codes."
    )
    p_pat.add_argument(
        "--all-matches",
        action="store_true",
        help="Apply to all matching/candidate banks when multiple banks match.",
    )
    p_pat.add_argument(
        "--force",
        action="store_true",
        help="Force patching even if compare byte does not match.",
    )
    p_pat.add_argument(
        "--no-checksum",
        action="store_true",
        help="Skip automatic ROM checksum recalculation.",
    )
    p_pat.add_argument(
        "--skip-invalid",
        action="store_true",
        help="Write patched ROM even if some codes fail validation or are ambiguous.",
    )
    p_pat.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve and validate codes without writing output files.",
    )
    p_pat.add_argument("--ips-out", help="Also export an .ips patch file.")
    p_pat.add_argument("--bps-out", help="Also export a .bps patch file.")
    p_pat.add_argument("--ups-out", help="Also export a .ups patch file.")
    p_pat.add_argument("--ppf-out", help="Also export a .ppf patch file.")
    p_pat.add_argument("--aps-out", help="Also export an .aps patch file.")
    p_pat.add_argument(
        "--preview-diff",
        action="store_true",
        help="Show a hex diff preview of modified bytes.",
    )
    p_pat.add_argument(
        "--export-cheats",
        help="Export parsed queue codes to a .txt, .gg, or RetroArch .cht file.",
    )
    p_pat.add_argument("--json", action="store_true", help="Output JSON.")
    p_pat.set_defaults(func=_cmd_patch)

    # fix-checksum
    p_chk = sub.add_parser("fix-checksum", help="Recalculate and repair ROM header checksums.")
    p_chk.add_argument("rom", help="Input ROM file path.")
    p_chk.add_argument("-o", "--output", help="Output ROM file path.")
    p_chk.add_argument("-i", "--in-place", action="store_true", help="Modify ROM in place.")
    p_chk.add_argument("--no-backup", action="store_true", help="Disable .bak backup with -i.")
    p_chk.add_argument("-s", "--system", help="Override auto-detected console system.")
    p_chk.add_argument(
        "--snes-map",
        default="auto",
        choices=["auto", "lorom", "hirom", "exhirom", "exlorom"],
        help="SNES ROM memory map override.",
    )
    p_chk.add_argument("--json", action="store_true", help="Output JSON.")
    p_chk.set_defaults(func=_cmd_fix_checksum)

    # ips-create / bps-create / ups-create / ppf-create / aps-create
    for fmt_name in ("ips", "bps", "ups", "ppf", "aps"):
        p_pc = sub.add_parser(
            f"{fmt_name}-create",
            help=f"Create a {fmt_name.upper()} patch from original & modified ROMs.",
        )
        p_pc.add_argument("original", help="Original unpatched ROM file.")
        p_pc.add_argument("modified", help="Modified ROM file.")
        p_pc.add_argument("-o", "--output", required=True, help=f"Output .{fmt_name} file path.")
        if fmt_name == "bps":
            p_pc.add_argument("--metadata", default="", help="Optional BPS UTF-8 metadata.")
        elif fmt_name == "ppf":
            p_pc.add_argument(
                "--version", type=int, default=3, choices=[1, 2, 3], help="PPF version (1, 2, 3)."
            )
            p_pc.add_argument(
                "--description",
                default="Created with GGROMPatcher",
                help="50-char PPF description.",
            )
            p_pc.add_argument(
                "--blockcheck", action="store_true", help="Embed 1024-byte PPF blockcheck."
            )
            p_pc.add_argument(
                "--undo", action="store_true", help="Embed PPF 3.0 undo data for unpatching."
            )
            p_pc.add_argument("--diz", default="", help="Optional PPF 3.0 FILE_ID.DIZ note.")
        elif fmt_name == "aps":
            p_pc.add_argument(
                "--description",
                default="Created with GGROMPatcher",
                help="50-char APS description.",
            )
            p_pc.add_argument(
                "--variant",
                default="auto",
                choices=["auto", "n64", "simple", "gba"],
                help="APS variant: auto (N64 if detected else simple), n64, simple, or gba (APS1).",
            )
            p_pc.add_argument("--v64", action="store_true", help="Mark N64 APS origin as .v64.")
        p_pc.set_defaults(func=_make_patch_create_cmd(fmt_name))

        p_pa = sub.add_parser(
            f"{fmt_name}-apply",
            help=f"Apply a {fmt_name.upper()} patch to a ROM.",
        )
        p_pa.add_argument("rom", help="Original ROM file.")
        p_pa.add_argument("patch", metavar=fmt_name.upper(), help=f"{fmt_name.upper()} patch file.")
        p_pa.add_argument("-o", "--output", help="Output patched ROM file.")
        p_pa.add_argument("-i", "--in-place", action="store_true", help="Overwrite ROM in place.")
        p_pa.add_argument("--no-backup", action="store_true", help="Disable .bak backup with -i.")
        if fmt_name in {"bps", "ups", "ppf", "aps"}:
            p_pa.add_argument(
                "--ignore-checksums",
                action="store_true",
                help=f"Skip {fmt_name.upper()} source/target validation.",
            )
        if fmt_name == "ppf":
            p_pa.add_argument(
                "--undo",
                action="store_true",
                help="Revert patch using embedded PPF 3.0 undo data.",
            )
        p_pa.add_argument(
            "--fix-checksum",
            action="store_true",
            help=f"Recalculate console checksum after applying {fmt_name.upper()}.",
        )
        p_pa.add_argument("-s", "--system", help="Console system for --fix-checksum.")
        p_pa.set_defaults(func=_make_patch_apply_cmd(fmt_name))

    # diff
    p_diff = sub.add_parser(
        "diff",
        help="Show hex diff hunks between original & modified ROMs or planned cheat patches.",
    )
    p_diff.add_argument("original", help="Original ROM file.")
    p_diff.add_argument("modified", nargs="?", help="Modified ROM file (optional if -c/-f used).")
    p_diff.add_argument("-c", "--code", action="append", help="Game Genie/PAR code(s) to preview.")
    p_diff.add_argument("-f", "--cheat-file", action="append", help="Cheat file(s) to preview.")
    p_diff.add_argument("--enabled-only", action="store_true", help="Only import enabled cheats.")
    p_diff.add_argument("-s", "--system", help="Override console system.")
    p_diff.add_argument(
        "--snes-map",
        default="auto",
        choices=["auto", "lorom", "hirom", "exhirom", "exlorom"],
    )
    p_diff.add_argument("--nes-bank-size", default="auto", choices=["auto", "8k", "16k", "32k"])
    p_diff.add_argument("--bank", help="Explicit bank index.")
    p_diff.add_argument("--all-matches", action="store_true", help="Patch all matching banks.")
    p_diff.add_argument("--force", action="store_true", help="Force on compare mismatch.")
    p_diff.add_argument("--no-checksum", action="store_true", help="Skip checksum recalculation.")
    p_diff.add_argument("--skip-invalid", action="store_true", help="Skip non-matching codes.")
    p_diff.add_argument("--max-hunks", type=int, default=256, help="Maximum hunks to display.")
    p_diff.add_argument("--json", action="store_true", help="Output JSON.")
    p_diff.set_defaults(func=_cmd_diff)

    # export-cheats
    p_exp = sub.add_parser(
        "export-cheats", help="Convert/export codes to .txt, .gg, or RetroArch .cht."
    )
    p_exp.add_argument("-c", "--code", action="append", help="Cheat code(s).")
    p_exp.add_argument("-f", "--cheat-file", action="append", help="Input cheat file(s).")
    p_exp.add_argument("--enabled-only", action="store_true", help="Only keep enabled entries.")
    p_exp.add_argument("-o", "--output", required=True, help="Output .txt/.gg/.cht path.")
    p_exp.add_argument("--format", choices=["txt", "gg", "cht"], help="Override output format.")
    p_exp.set_defaults(func=_cmd_export_cheats)

    # batch-patch
    p_bp = sub.add_parser(
        "batch-patch",
        help="Batch apply Game Genie/PAR codes or a binary patch across multiple ROMs/folders.",
    )
    p_bp.add_argument("inputs", nargs="+", metavar="ROM_OR_DIR", help="ROM file(s) or folder(s).")
    p_bp.add_argument("-c", "--code", action="append", help="Game Genie/PAR code(s).")
    p_bp.add_argument("-f", "--cheat-file", action="append", help="Input cheat file(s).")
    p_bp.add_argument("--enabled-only", action="store_true", help="Only import enabled cheats.")
    p_bp.add_argument(
        "--patch-file", help="Apply an .ips, .bps, .ups, .ppf, or .aps patch to each ROM."
    )
    p_bp.add_argument("--out-dir", help="Directory for output ROMs.")
    p_bp.add_argument("-i", "--in-place", action="store_true", help="Patch ROMs in place.")
    p_bp.add_argument("--no-backup", action="store_true", help="Disable .bak backup with -i.")
    p_bp.add_argument("--recursive", action="store_true", help="Recurse into directories.")
    p_bp.add_argument("-s", "--system", help="Override console system.")
    p_bp.add_argument(
        "--snes-map",
        default="auto",
        choices=["auto", "lorom", "hirom", "exhirom", "exlorom"],
    )
    p_bp.add_argument("--nes-bank-size", default="auto", choices=["auto", "8k", "16k", "32k"])
    p_bp.add_argument("--bank", help="Explicit bank index.")
    p_bp.add_argument("--all-matches", action="store_true", help="Patch all matching banks.")
    p_bp.add_argument("--force", action="store_true", help="Force on compare mismatch.")
    p_bp.add_argument("--no-checksum", action="store_true", help="Skip checksum recalculation.")
    p_bp.add_argument("--skip-invalid", action="store_true", help="Skip non-matching codes.")
    p_bp.add_argument("--ignore-checksums", action="store_true", help="Skip BPS/UPS CRC checks.")
    p_bp.add_argument("--dry-run", action="store_true", help="Validate without writing files.")
    p_bp.add_argument("--json", action="store_true", help="Output JSON.")
    p_bp.set_defaults(func=_cmd_batch_patch)

    return parser


def _can_launch_default_gui() -> bool:
    if sys.platform in {"win32", "darwin"}:
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arg_list = sys.argv[1:] if argv is None else argv

    if not arg_list:
        if _can_launch_default_gui():
            try:
                from ggrompatcher.gui import is_tk_available, launch_gui

                if is_tk_available():
                    return launch_gui()
            except Exception:
                pass
        parser.print_help()
        return 0

    args = parser.parse_args(arg_list)
    if not hasattr(args, "func"):
        parser.print_help()
        return 0

    try:
        return int(args.func(args))
    except (GGPatcherError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
