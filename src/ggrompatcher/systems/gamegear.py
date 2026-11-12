"""Sega Game Gear and Sega Master System Game Genie codec, mapper, and checksum support."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ggrompatcher.models import (
    CandidateOffset,
    ChecksumEntry,
    CodeFormatError,
    ConsoleSystem,
    DecodedCode,
    RomInfo,
)
from ggrompatcher.systems.base import SystemHandler
from ggrompatcher.systems.gameboy import decode_gb_gg_code, encode_gb_gg_code

_TMR_SEGA = b"TMR SEGA"
_HEADER_LOCATIONS = (0x7FF0, 0x3FF0, 0x1FF0)
_BANK_SIZE = 0x4000

# Official SMS/GG BIOS checksum ranges indexed by low nibble of byte at header + 0x0F.
# Each entry maps to (lower_end_exclusive, upper_end_exclusive_or_None).
_SMS_CHECKSUM_RANGES: dict[int, tuple[int, int | None]] = {
    0xA: (0x1FF0, None),  # 8 KB
    0xB: (0x3FF0, None),  # 16 KB
    0xC: (0x7FF0, None),  # 32 KB
    0xD: (0xBFF0, None),  # 48 KB
    0xE: (0x7FF0, 0x10000),  # 64 KB
    0xF: (0x7FF0, 0x20000),  # 128 KB
    0x0: (0x7FF0, 0x40000),  # 256 KB
    0x1: (0x7FF0, 0x80000),  # 512 KB
    0x2: (0x7FF0, 0x100000),  # 1024 KB
}

_REGION_NAMES: dict[int, str] = {
    0x3: "SMS Japan",
    0x4: "SMS Export",
    0x5: "GG Japan",
    0x6: "GG Export",
    0x7: "GG International",
}


def find_tmr_sega_header(rom: bytes) -> int | None:
    """Return offset of the 'TMR SEGA' header if present at 0x7FF0, 0x3FF0, or 0x1FF0."""
    for loc in _HEADER_LOCATIONS:
        if len(rom) >= loc + 16 and rom[loc : loc + 8] == _TMR_SEGA:
            return loc
    return None


def _compute_tmr_checksum(rom: bytes, header_off: int) -> int | None:
    if len(rom) < header_off + 16:
        return None
    size_nibble = rom[header_off + 0x0F] & 0x0F
    if size_nibble not in _SMS_CHECKSUM_RANGES:
        return None
    low_end, high_end = _SMS_CHECKSUM_RANGES[size_nibble]
    total = sum(rom[: min(low_end, len(rom))])
    # For 48 KB ($D), if header sits at 0x7FF0, skip the 16-byte header itself when summing.
    if size_nibble == 0xD and header_off == 0x7FF0 and len(rom) > 0x8000:
        total = sum(rom[:0x7FF0]) + sum(rom[0x8000 : min(0xBFF0, len(rom))])
    elif high_end is not None and len(rom) > 0x8000:
        total += sum(rom[0x8000 : min(high_end, len(rom))])
    return total & 0xFFFF


class _Sega8BitHandler(SystemHandler):
    """Shared Game Genie logic for 8-bit Sega Z80 consoles (Game Gear / Master System)."""

    _extensions: frozenset[str]

    def decode_code(self, code: str, description: str = "") -> DecodedCode:
        return decode_gb_gg_code(
            code=code,
            system=self.system,
            max_address=0xBFFF,
            description=description,
        )

    def encode_code(
        self,
        address: int,
        value: int,
        compare: int | None = None,
        *,
        code_format: str = "gg",
    ) -> str:
        if code_format.strip().lower() != "gg":
            raise CodeFormatError(
                f"{self.system.display_name} ROM patching only supports Game Genie ('gg') encoding."
            )
        return encode_gb_gg_code(
            address=address,
            value=value,
            compare=compare,
            system=self.system,
            max_address=0xBFFF,
        )

    def resolve_candidates(
        self, rom: bytes, decoded: DecodedCode, **options: Any
    ) -> list[CandidateOffset]:
        addr = decoded.address
        rom_len = len(rom)

        # First 1 KB (0x0000-0x03FF) is permanently mapped to ROM bank 0 on Sega mapper.
        if addr < 0x0400:
            if addr >= rom_len:
                return []
            cur = rom[addr : addr + 1]
            matches = (cur == decoded.compare) if decoded.compare is not None else None
            return [
                CandidateOffset(
                    file_offset=addr,
                    bank=0,
                    current_bytes=cur,
                    matches_compare=matches,
                    label=f"Bank 00 (fixed) @ 0x{addr:06X}",
                )
            ]

        default_bank = addr // _BANK_SIZE  # 0 for 0400-3FFF, 1 for 4000-7FFF, 2 for 8000-BFFF
        rel = addr & (_BANK_SIZE - 1)
        bank_count = max(1, (rom_len + _BANK_SIZE - 1) // _BANK_SIZE)

        # Unbanked ROM (<= 48 KB): slot maps 1:1 to ROM offset.
        if rom_len <= 0xC000:
            if addr >= rom_len:
                return []
            cur = rom[addr : addr + 1]
            matches = (cur == decoded.compare) if decoded.compare is not None else None
            return [
                CandidateOffset(
                    file_offset=addr,
                    bank=default_bank,
                    current_bytes=cur,
                    matches_compare=matches,
                    label=f"Bank {default_bank:02X} @ 0x{addr:06X}",
                )
            ]

        # Banked ROM (> 48 KB): check default slot bank first, then remaining 16 KB banks.
        ordered_banks = [b for b in [default_bank, *range(bank_count)] if b < bank_count]
        seen_banks: set[int] = set()
        candidates: list[CandidateOffset] = []
        for bank in ordered_banks:
            if bank in seen_banks:
                continue
            seen_banks.add(bank)
            off = bank * _BANK_SIZE + rel
            if off >= rom_len:
                continue
            cur = rom[off : off + 1]
            matches = (cur == decoded.compare) if decoded.compare is not None else None
            candidates.append(
                CandidateOffset(
                    file_offset=off,
                    bank=bank,
                    current_bytes=cur,
                    matches_compare=matches,
                    label=f"Bank {bank:02X} @ 0x{off:06X}",
                )
            )
        return candidates

    def verify_checksums(self, rom: bytes, **options: Any) -> list[ChecksumEntry]:
        header_off = find_tmr_sega_header(rom)
        if header_off is None:
            return []
        computed = _compute_tmr_checksum(rom, header_off)
        if computed is None:
            return []
        chk_off = header_off + 0x0A
        stored = rom[chk_off] | (rom[chk_off + 1] << 8)
        return [
            ChecksumEntry(
                name="TMR SEGA Checksum",
                offset=chk_off,
                stored=stored,
                computed=computed,
                width=2,
                valid=stored == computed,
            )
        ]

    def fix_checksums(self, rom: bytearray, **options: Any) -> list[ChecksumEntry]:
        header_off = find_tmr_sega_header(bytes(rom))
        if header_off is None:
            return []
        computed = _compute_tmr_checksum(bytes(rom), header_off)
        if computed is None:
            return []
        chk_off = header_off + 0x0A
        rom[chk_off] = computed & 0xFF
        rom[chk_off + 1] = (computed >> 8) & 0xFF
        return self.verify_checksums(bytes(rom))

    def inspect_rom(self, rom: bytes, **options: Any) -> RomInfo:
        header_off = find_tmr_sega_header(rom)
        extra: dict[str, Any] = {
            "bank_count": max(1, (len(rom) + _BANK_SIZE - 1) // _BANK_SIZE),
        }
        if header_off is not None:
            region_code = (rom[header_off + 0x0F] >> 4) & 0xF
            size_nibble = rom[header_off + 0x0F] & 0xF
            version = rom[header_off + 0x0E] & 0xF
            region_name = _REGION_NAMES.get(region_code, f"Unknown (0x{region_code:X})")
            extra["header_offset"] = f"0x{header_off:04X}"
            extra["region"] = region_name
            extra["version"] = version
            extra["size_code"] = f"0x{size_nibble:X}"
            header_format = f"TMR SEGA ({region_name})"
        else:
            header_format = f"{self.system.display_name} (No TMR SEGA header)"

        return RomInfo(
            system=self.system,
            file_size=len(rom),
            title="(No embedded ASCII title)",
            header_format=header_format,
            checksums=self.verify_checksums(rom),
            extra=extra,
        )


class GameGearHandler(_Sega8BitHandler):
    """Sega Game Gear ROM handler."""

    system = ConsoleSystem.GAMEGEAR
    _extensions = frozenset({".gg"})

    def detect_confidence(self, rom: bytes, filename: str | None = None) -> int:
        ext = Path(filename).suffix.lower() if filename else ""
        header_off = find_tmr_sega_header(rom)
        if header_off is not None:
            region = (rom[header_off + 0x0F] >> 4) & 0xF
            if region in (0x5, 0x6, 0x7):
                return 99 if ext == ".gg" else 96
            if ext == ".gg":
                return 92
            if region in (0x3, 0x4):
                return 40
            return 70
        return 75 if ext == ".gg" else 0


class MasterSystemHandler(_Sega8BitHandler):
    """Sega Master System ROM handler."""

    system = ConsoleSystem.MASTERSYSTEM
    _extensions = frozenset({".sms"})

    def detect_confidence(self, rom: bytes, filename: str | None = None) -> int:
        ext = Path(filename).suffix.lower() if filename else ""
        header_off = find_tmr_sega_header(rom)
        if header_off is not None:
            region = (rom[header_off + 0x0F] >> 4) & 0xF
            if region in (0x3, 0x4):
                return 99 if ext == ".sms" else 96
            if ext == ".sms":
                return 92
            if region in (0x5, 0x6, 0x7):
                return 40
            return 65
        return 75 if ext == ".sms" else 0
