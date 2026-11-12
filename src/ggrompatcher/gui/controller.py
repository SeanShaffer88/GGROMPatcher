"""Headless presentation/controller layer for the GGROMPatcher desktop GUI."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ggrompatcher.cheatfiles import CheatEntry, load_cheat_file, parse_cheat_text
from ggrompatcher.ips import apply_ips, write_ips_file
from ggrompatcher.models import (
    ConsoleSystem,
    DecodedCode,
    GGPatcherError,
    MatchStatus,
    PatchResult,
    PlannedPatch,
    RomFormatError,
    RomInfo,
)
from ggrompatcher.patcher import (
    apply_patches,
    decode_code,
    encode_code,
    fix_rom_checksums,
    inspect_rom,
    patch_rom_file,
    plan_patches,
)
from ggrompatcher.systems import detect_system


@dataclass
class PatchRowView:
    """View model representing one row in the GUI patch queue."""

    index: int
    code: str
    description: str
    enabled: bool
    address_hex: str
    value_hex: str
    compare_hex: str
    target_summary: str
    current_hex: str
    status: MatchStatus
    status_text: str
    message: str
    can_apply: bool
    candidate_labels: list[tuple[int, str]] = field(default_factory=list)
    selected_offset: int | None = None


@dataclass
class LiveDecodeView:
    """Result of decoding live user input in the code box or converter tab."""

    valid: bool
    summary: str
    decoded: list[DecodedCode] = field(default_factory=list)
    error: str = ""


class GuiController:
    """Stateful presenter managing ROM loading, code queuing, bank selection, and patching."""

    def __init__(self) -> None:
        self.rom_path: Path | None = None
        self.rom_bytes: bytes | None = None
        self.detected_system: ConsoleSystem | None = None
        self.system_override: ConsoleSystem | None = None  # None = Auto-detect
        self.snes_map: str = "auto"
        self.nes_bank_size: str = "auto"
        self.rom_info: RomInfo | None = None

        self.entries: list[CheatEntry] = []
        self.offset_overrides: dict[int, int] = {}
        self.all_matches: bool = False
        self.force_mismatch: bool = False
        self.fix_checksum: bool = True
        self.create_backup: bool = True
        self.skip_invalid: bool = False

    @property
    def active_system(self) -> ConsoleSystem | None:
        return self.system_override or self.detected_system

    def load_rom(self, path: str | Path) -> RomInfo:
        """Load a ROM file from disk, detect its console, and inspect headers/checksums."""
        rom_p = Path(path)
        if not rom_p.is_file():
            raise RomFormatError(f"ROM file not found: {rom_p}")

        data = rom_p.read_bytes()
        detected, _ = detect_system(data, filename=rom_p.name)
        self.rom_path = rom_p
        self.rom_bytes = data
        self.detected_system = detected
        self.offset_overrides.clear()
        return self.refresh_rom_info()

    def refresh_rom_info(self) -> RomInfo:
        """Re-inspect the currently loaded ROM using current system/mapper settings."""
        if self.rom_bytes is None or self.rom_path is None:
            raise RomFormatError("No ROM is currently loaded.")
        info = inspect_rom(
            self.rom_bytes,
            system=self.active_system,
            filename=self.rom_path.name,
            snes_map=self.snes_map,
        )
        self.rom_info = info
        return info

    def set_system_override(self, system: ConsoleSystem | None) -> RomInfo | None:
        """Set manual system override (`None` = Auto-detect) and refresh ROM info if loaded."""
        self.system_override = system
        self.offset_overrides.clear()
        if self.rom_bytes is not None:
            return self.refresh_rom_info()
        return None

    def set_snes_map(self, mode: str) -> RomInfo | None:
        self.snes_map = mode
        if self.rom_bytes is not None:
            return self.refresh_rom_info()
        return None

    def set_nes_bank_size(self, mode: str) -> None:
        self.nes_bank_size = mode

    def add_codes_from_text(self, text: str) -> list[CheatEntry]:
        """Parse and append one or more codes (including multi-line or `+` chains)."""
        parsed = parse_cheat_text(text)
        self.entries.extend(parsed)
        return parsed

    def import_cheat_file(
        self, path: str | Path, *, enabled_only: bool = False
    ) -> list[CheatEntry]:
        """Load cheats from a `.txt`, `.gg`, or RetroArch `.cht` file into the queue."""
        loaded = load_cheat_file(path, enabled_only=enabled_only)
        self.entries.extend(loaded)
        return loaded

    def remove_indices(self, indices: list[int]) -> None:
        """Remove entries by 0-based index and remap per-row offset overrides."""
        to_remove = set(indices)
        new_entries: list[CheatEntry] = []
        new_overrides: dict[int, int] = {}
        for old_idx, entry in enumerate(self.entries):
            if old_idx in to_remove:
                continue
            new_idx = len(new_entries)
            new_entries.append(entry)
            if old_idx in self.offset_overrides:
                new_overrides[new_idx] = self.offset_overrides[old_idx]
        self.entries = new_entries
        self.offset_overrides = new_overrides

    def toggle_entry(self, index: int) -> bool:
        """Toggle whether queue item `index` is enabled for patching."""
        if not 0 <= index < len(self.entries):
            raise IndexError(f"Invalid patch row index: {index}")
        item = self.entries[index]
        new_state = not item.enabled
        self.entries[index] = CheatEntry(
            code=item.code,
            description=item.description,
            enabled=new_state,
        )
        return new_state

    def clear_codes(self) -> None:
        """Empty the patch queue."""
        self.entries.clear()
        self.offset_overrides.clear()

    def set_offset_override(self, row_index: int, file_offset: int | None) -> None:
        """Pin a specific candidate file offset for an ambiguous or banked code row."""
        if file_offset is None:
            self.offset_overrides.pop(row_index, None)
        else:
            self.offset_overrides[row_index] = file_offset

    def live_decode(self, text: str, system: ConsoleSystem | None = None) -> LiveDecodeView:
        """Decode user-entered code(s) for instant feedback without raising exceptions."""
        stripped = text.strip()
        if not stripped:
            return LiveDecodeView(valid=False, summary="Enter a Game Genie code to preview.")

        target_sys = system or self.active_system
        if target_sys is None:
            return LiveDecodeView(
                valid=False,
                summary="Load a ROM or choose a console system to decode.",
            )

        try:
            parsed = parse_cheat_text(stripped)
            if not parsed:
                return LiveDecodeView(valid=False, summary="Enter a Game Genie code to preview.")

            decoded_items: list[DecodedCode] = []
            parts: list[str] = []
            for entry in parsed:
                if (":" in entry.code or "=" in entry.code) and target_sys != ConsoleSystem.RAW:
                    dec = decode_code(entry.code, system=ConsoleSystem.RAW)
                else:
                    dec = decode_code(entry.code, system=target_sys)
                decoded_items.append(dec)
                cmp_str = f" (if 0x{dec.compare.hex().upper()})" if dec.compare else ""
                warn_str = f" [! {dec.warnings[0]}]" if dec.warnings else ""
                parts.append(
                    f"{dec.normalized_code}: 0x{dec.address:04X} -> "
                    f"0x{dec.value.hex().upper()}{cmp_str}{warn_str}"
                )
            return LiveDecodeView(valid=True, summary=" | ".join(parts), decoded=decoded_items)
        except GGPatcherError as exc:
            return LiveDecodeView(valid=False, summary=str(exc), error=str(exc))

    def live_encode(
        self,
        system: ConsoleSystem,
        addr_text: str,
        value_text: str,
        compare_text: str = "",
    ) -> str:
        """Encode hex inputs into a Game Genie code string."""
        addr = _parse_hex(addr_text, "Address")
        val = _parse_hex(value_text, "New value")
        cmp_val = _parse_hex(compare_text, "Compare value") if compare_text.strip() else None
        return encode_code(system, addr, val, cmp_val)

    def _active_indices_and_entries(self) -> tuple[list[int], list[CheatEntry]]:
        active_indices: list[int] = []
        active_entries: list[CheatEntry] = []
        for idx, entry in enumerate(self.entries):
            if entry.enabled:
                active_indices.append(idx)
                active_entries.append(entry)
        return active_indices, active_entries

    def _remap_overrides_for_active(self, active_indices: list[int]) -> dict[int, int]:
        remapped: dict[int, int] = {}
        for active_pos, orig_idx in enumerate(active_indices):
            if orig_idx in self.offset_overrides:
                remapped[active_pos] = self.offset_overrides[orig_idx]
        return remapped

    def build_patch_rows(self) -> list[PatchRowView]:
        """Evaluate every entry in the queue against the currently loaded ROM (if any)."""
        rows: list[PatchRowView] = []
        target_sys = self.active_system

        # When no ROM is loaded, still decode entries so users can inspect codes first.
        if self.rom_bytes is None or self.rom_path is None:
            for idx, entry in enumerate(self.entries):
                view = self.live_decode(entry.code, system=target_sys)
                if view.valid and view.decoded:
                    dec = view.decoded[0]
                    rows.append(
                        PatchRowView(
                            index=idx,
                            code=dec.normalized_code,
                            description=entry.description,
                            enabled=entry.enabled,
                            address_hex=f"0x{dec.address:06X}",
                            value_hex=dec.value.hex().upper(),
                            compare_hex=dec.compare.hex().upper() if dec.compare else "—",
                            target_summary="Load a ROM to map offset",
                            current_hex="—",
                            status=MatchStatus.READY if entry.enabled else MatchStatus.OUT_OF_RANGE,
                            status_text="NO ROM" if entry.enabled else "DISABLED",
                            message="Load a ROM to validate and apply.",
                            can_apply=False,
                        )
                    )
                else:
                    rows.append(
                        PatchRowView(
                            index=idx,
                            code=entry.code,
                            description=entry.description,
                            enabled=entry.enabled,
                            address_hex="—",
                            value_hex="—",
                            compare_hex="—",
                            target_summary="—",
                            current_hex="—",
                            status=MatchStatus.OUT_OF_RANGE,
                            status_text="INVALID CODE",
                            message=view.error or view.summary,
                            can_apply=False,
                        )
                    )
            return rows

        # Evaluate each entry individually or together via plan_patches, gracefully handling
        # invalid codes per-row so one typo doesn't break the rest of the table.
        for idx, entry in enumerate(self.entries):
            if not entry.enabled:
                view = self.live_decode(entry.code, system=target_sys)
                opt_dec: DecodedCode | None = (
                    view.decoded[0] if view.valid and view.decoded else None
                )
                rows.append(
                    PatchRowView(
                        index=idx,
                        code=opt_dec.normalized_code if opt_dec else entry.code,
                        description=entry.description,
                        enabled=False,
                        address_hex=f"0x{opt_dec.address:06X}" if opt_dec else "—",
                        value_hex=opt_dec.value.hex().upper() if opt_dec else "—",
                        compare_hex=(
                            opt_dec.compare.hex().upper() if opt_dec and opt_dec.compare else "—"
                        ),
                        target_summary="Skipped (disabled)",
                        current_hex="—",
                        status=MatchStatus.READY,
                        status_text="DISABLED",
                        message="Disabled by user.",
                        can_apply=False,
                    )
                )
                continue

            try:
                ov = {0: self.offset_overrides[idx]} if idx in self.offset_overrides else None
                _, _, _, plans = plan_patches(
                    self.rom_bytes,
                    [entry],
                    system=target_sys,
                    filename=self.rom_path.name,
                    offset_overrides=ov,
                    all_matches=self.all_matches,
                    force=self.force_mismatch,
                    snes_map=self.snes_map,
                    nes_bank_size=self.nes_bank_size,
                )
                plan = plans[0]
                rows.append(self._plan_to_row(idx, entry.description, plan))
            except GGPatcherError as exc:
                rows.append(
                    PatchRowView(
                        index=idx,
                        code=entry.code,
                        description=entry.description,
                        enabled=True,
                        address_hex="—",
                        value_hex="—",
                        compare_hex="—",
                        target_summary="—",
                        current_hex="—",
                        status=MatchStatus.OUT_OF_RANGE,
                        status_text="INVALID CODE",
                        message=str(exc),
                        can_apply=False,
                    )
                )
        return rows

    def _plan_to_row(self, idx: int, description: str, plan: PlannedPatch) -> PatchRowView:
        dec = plan.code
        cand_labels = [(c.file_offset, c.label) for c in plan.candidates]
        selected_off = plan.selected_offsets[0] if len(plan.selected_offsets) == 1 else None

        if len(plan.selected_offsets) == 1:
            off = plan.selected_offsets[0]
            target_summary = f"0x{off:06X}"
            matched = [c for c in plan.candidates if c.file_offset == off]
            current_hex = matched[0].current_bytes.hex().upper() if matched else "—"
        elif len(plan.selected_offsets) > 1:
            target_summary = f"{len(plan.selected_offsets)} banks"
            current_hex = ", ".join(
                c.current_bytes.hex().upper()
                for c in plan.candidates
                if c.file_offset in set(plan.selected_offsets)
            )[:18]
        elif len(plan.candidates) == 1:
            target_summary = f"0x{plan.candidates[0].file_offset:06X}"
            current_hex = plan.candidates[0].current_bytes.hex().upper()
        elif plan.candidates:
            target_summary = f"{len(plan.candidates)} candidate banks"
            current_hex = "varies"
        else:
            target_summary = "Unmapped"
            current_hex = "—"

        status_labels = {
            MatchStatus.READY: "READY",
            MatchStatus.ALREADY_PATCHED: "ALREADY PATCHED",
            MatchStatus.AMBIGUOUS: "CHOOSE BANK",
            MatchStatus.COMPARE_MISMATCH: "MISMATCH",
            MatchStatus.OUT_OF_RANGE: "OUT OF RANGE",
        }
        return PatchRowView(
            index=idx,
            code=dec.normalized_code,
            description=description,
            enabled=True,
            address_hex=f"0x{dec.address:06X}",
            value_hex=dec.value.hex().upper(),
            compare_hex=dec.compare.hex().upper() if dec.compare else "—",
            target_summary=target_summary,
            current_hex=current_hex,
            status=plan.status,
            status_text=status_labels[plan.status],
            message=plan.message,
            can_apply=plan.can_apply,
            candidate_labels=cand_labels,
            selected_offset=selected_off,
        )

    def apply_and_save(
        self,
        *,
        output_path: str | Path | None = None,
        in_place: bool = False,
        ips_output_path: str | Path | None = None,
    ) -> tuple[PatchResult, Path | None]:
        """Apply enabled queue items and write patched ROM and/or IPS file."""
        if self.rom_path is None or self.rom_bytes is None:
            raise RomFormatError("Please open a ROM file first.")

        active_indices, active_entries = self._active_indices_and_entries()
        if not active_entries:
            raise GGPatcherError("No enabled codes in the patch queue.")

        remapped_offsets = self._remap_overrides_for_active(active_indices)
        strict = not self.skip_invalid

        if ips_output_path is not None and output_path is None and not in_place:
            # Export IPS only without writing a patched ROM file.
            result = apply_patches(
                self.rom_bytes,
                active_entries,
                system=self.active_system,
                filename=self.rom_path.name,
                offset_overrides=remapped_offsets,
                all_matches=self.all_matches,
                force=self.force_mismatch,
                fix_checksum=self.fix_checksum,
                strict=strict,
                snes_map=self.snes_map,
                nes_bank_size=self.nes_bank_size,
            )
            write_ips_file(ips_output_path, self.rom_bytes, result.output_bytes)
            return result, Path(ips_output_path)

        result, dest = patch_rom_file(
            self.rom_path,
            active_entries,
            output_path=output_path,
            in_place=in_place,
            backup=self.create_backup,
            ips_output_path=ips_output_path,
            system=self.active_system,
            offset_overrides=remapped_offsets,
            all_matches=self.all_matches,
            force=self.force_mismatch,
            fix_checksum=self.fix_checksum,
            strict=strict,
            snes_map=self.snes_map,
            nes_bank_size=self.nes_bank_size,
        )

        if in_place and dest is not None:
            self.rom_bytes = dest.read_bytes()
            self.refresh_rom_info()

        return result, dest

    def fix_checksum_to_file(
        self,
        *,
        output_path: str | Path | None = None,
        in_place: bool = False,
    ) -> tuple[RomInfo, RomInfo, Path]:
        """Recalculate ROM checksums and save to disk."""
        if self.rom_path is None or self.rom_bytes is None:
            raise RomFormatError("Please open a ROM file first.")

        dest = self.rom_path if in_place else Path(output_path or self.rom_path)
        before, after, fixed = fix_rom_checksums(
            self.rom_bytes,
            system=self.active_system,
            filename=self.rom_path.name,
            output_filename=dest.name,
            snes_map=self.snes_map,
        )
        if not after.checksums:
            raise GGPatcherError(
                f"{before.system.display_name} does not use an embedded header checksum."
            )

        if in_place and self.create_backup:
            shutil.copy2(self.rom_path, self.rom_path.with_name(f"{self.rom_path.name}.bak"))

        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(fixed)

        if in_place:
            self.rom_bytes = fixed
            self.refresh_rom_info()

        return before, after, dest

    def apply_ips_to_file(
        self,
        rom_path: str | Path,
        ips_path: str | Path,
        output_path: str | Path,
        *,
        fix_checksum: bool = False,
        system: ConsoleSystem | None = None,
    ) -> Path:
        """Apply an `.ips` patch to `rom_path` and write `output_path`."""
        src = Path(rom_path)
        patch = Path(ips_path)
        dest = Path(output_path)
        if not src.is_file():
            raise RomFormatError(f"ROM file not found: {src}")
        if not patch.is_file():
            raise GGPatcherError(f"IPS file not found: {patch}")

        patched = apply_ips(src.read_bytes(), patch.read_bytes())
        if fix_checksum:
            _, _, patched = fix_rom_checksums(
                patched,
                system=system,
                filename=src.name,
                output_filename=dest.name,
                snes_map=self.snes_map,
            )
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(patched)
        return dest

    def create_ips_from_files(
        self,
        original_path: str | Path,
        modified_path: str | Path,
        output_ips_path: str | Path,
    ) -> Path:
        """Generate an `.ips` diff patch comparing two ROM files."""
        orig = Path(original_path)
        mod = Path(modified_path)
        if not orig.is_file():
            raise RomFormatError(f"Original ROM not found: {orig}")
        if not mod.is_file():
            raise RomFormatError(f"Modified ROM not found: {mod}")
        return write_ips_file(output_ips_path, orig.read_bytes(), mod.read_bytes())


def _parse_hex(raw: str, field_name: str) -> int:
    s = raw.strip()
    if s.startswith("$"):
        s = s[1:]
    elif s.lower().startswith("0x"):
        s = s[2:]
    if not s:
        raise ValueError(f"{field_name} cannot be empty.")
    try:
        return int(s, 16)
    except ValueError as exc:
        raise ValueError(f"Invalid hexadecimal {field_name.lower()}: '{raw}'.") from exc
