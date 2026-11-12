"""Game Boy / Game Boy Color Game Genie codec, mapper, and checksum support."""

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
from ggrompatcher.systems.base import (
    SystemHandler,
    clean_code_chars,
    read_ascii_title,
    rol8,
    ror8,
)

# Official 48-byte Nintendo logo at 0x0104..0x0133 in Game Boy ROM headers.
NINTENDO_LOGO = bytes.fromhex(
    "CEED6666CC0D000B03730083000C000D"
    "0008111F8889000EDCCC6EE6DDDDD999"
    "BBBB67636E0EECCCDDDC999FBBB9333E"
)

_HEX_DIGITS = frozenset("0123456789ABCDEF")
_GB_EXTENSIONS = {".gb", ".gbc", ".sgb"}
_BANK_SIZE = 0x4000


def decode_gb_gg_code(
    code: str,
    system: ConsoleSystem,
    max_address: int,
    description: str = "",
) -> DecodedCode:
    """Decode a 6- or 9-digit Game Boy / Game Gear / Master System Game Genie code."""
    raw_upper = code.strip().upper()
    has_gs_prefix = raw_upper.startswith(("GS:", "GS ", "AR:", "AR ", "PAR:", "PAR "))
    body = raw_upper.split(":", 1)[1] if ":" in raw_upper and has_gs_prefix else raw_upper
    if has_gs_prefix and " " in body and ":" not in raw_upper:
        body = body.split(None, 1)[1]
    clean = clean_code_chars(body.replace(":", "") if has_gs_prefix else body)

    if has_gs_prefix or (len(clean) == 8 and all(ch in _HEX_DIGITS for ch in clean)):
        if len(clean) == 8 and all(ch in _HEX_DIGITS for ch in clean):
            if system in (ConsoleSystem.GAMEGEAR, ConsoleSystem.MASTERSYSTEM):
                # Sega 8-bit PAR format: 00AAAALL (2-hex mode, 4-hex big-endian addr, 2-hex value)
                gs_type = int(clean[0:2], 16)
                gs_addr = int(clean[2:6], 16)
                gs_val = int(clean[6:8], 16)
            else:
                # Game Boy GameShark format: TTVVAALL (type, val, little-endian addr)
                gs_type = int(clean[0:2], 16)
                gs_val = int(clean[2:4], 16)
                gs_addr = int(clean[6:8] + clean[4:6], 16)
            raise CodeFormatError(
                f"{system.display_name} GameShark/Action Replay code '{code}' targets "
                f"runtime RAM/WRAM (type=0x{gs_type:02X}, addr=0x{gs_addr:04X}, "
                f"val=0x{gs_val:02X}); static ROM patching requires Game Genie "
                "(6/9 hex digits) or raw file offset (OFFSET:HEX)."
            )
        raise CodeFormatError(
            f"{system.display_name} GameShark/Action Replay code '{code}' targets "
            "runtime RAM and cannot statically patch ROM files."
        )

    if len(clean) not in (6, 9):
        raise CodeFormatError(
            f"Invalid {system.display_name} code '{code}': expected 6 or 9 hex digits "
            f"(e.g. ABC-DEF or ABC-DEF-GHI)."
        )
    if not all(ch in _HEX_DIGITS for ch in clean):
        raise CodeFormatError(
            f"Invalid {system.display_name} code '{code}': must contain only hex digits 0-9, A-F."
        )

    nibbles = [int(ch, 16) for ch in clean]
    new_val = (nibbles[0] << 4) | nibbles[1]
    address = ((nibbles[5] ^ 0xF) << 12) | (nibbles[2] << 8) | (nibbles[3] << 4) | nibbles[4]

    if address > max_address:
        raise CodeFormatError(
            f"{system.display_name} code '{code}' decodes to address 0x{address:04X}, "
            f"outside cartridge ROM space (0x0000-0x{max_address:04X})."
        )

    warnings: list[str] = []
    compare_bytes: bytes | None = None
    if len(clean) == 9:
        gi = (nibbles[6] << 4) | nibbles[8]
        compare_val = ror8(gi, 2) ^ 0xBA
        compare_bytes = bytes([compare_val])
        expected_h = nibbles[6] ^ 0x8
        if nibbles[7] != expected_h:
            warnings.append(
                f"Cloak digit H is '{nibbles[7]:X}' (expected '{expected_h:X}'); "
                "check for a typo in the last 3 digits."
            )
        normalized = f"{clean[0:3]}-{clean[3:6]}-{clean[6:9]}"
    else:
        normalized = f"{clean[0:3]}-{clean[3:6]}"

    return DecodedCode(
        raw_code=code.strip(),
        normalized_code=normalized,
        system=system,
        address=address,
        value=bytes([new_val]),
        compare=compare_bytes,
        description=description,
        warnings=tuple(warnings),
    )


def encode_gb_gg_code(
    address: int,
    value: int,
    compare: int | None,
    system: ConsoleSystem,
    max_address: int,
) -> str:
    """Encode an address, replacement byte, and optional compare byte into GB/GG format."""
    if not 0 <= address <= max_address:
        raise CodeFormatError(
            f"Address 0x{address:04X} out of {system.display_name} ROM range "
            f"(0x0000-0x{max_address:04X})."
        )
    if not 0 <= value <= 0xFF:
        raise CodeFormatError(f"Replacement value 0x{value:X} must be an 8-bit byte (0x00-0xFF).")

    ab = f"{value:02X}"
    cde = f"{address & 0xFFF:03X}"
    f_digit = f"{((address >> 12) ^ 0xF):X}"
    base = f"{ab}{cde[0]}-{cde[1:]}{f_digit}"

    if compare is None:
        return base

    if not 0 <= compare <= 0xFF:
        raise CodeFormatError(f"Compare value 0x{compare:X} must be an 8-bit byte (0x00-0xFF).")

    rotated = rol8(compare ^ 0xBA, 2)
    g = (rotated >> 4) & 0xF
    h = g ^ 0x8
    i = rotated & 0xF
    return f"{base}-{g:X}{h:X}{i:X}"


class GameBoyHandler(SystemHandler):
    """Game Boy & Game Boy Color ROM handler."""

    system = ConsoleSystem.GAMEBOY

    def detect_confidence(self, rom: bytes, filename: str | None = None) -> int:
        ext = Path(filename).suffix.lower() if filename else ""
        if len(rom) >= 0x150:
            if rom[0x104:0x134] == NINTENDO_LOGO:
                return 100
            if rom[0x104:0x10C] == NINTENDO_LOGO[:8] and ext in _GB_EXTENSIONS:
                return 95
        if ext in _GB_EXTENSIONS:
            return 72
        return 0

    def decode_code(self, code: str, description: str = "") -> DecodedCode:
        return decode_gb_gg_code(
            code=code,
            system=self.system,
            max_address=0x7FFF,
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
            max_address=0x7FFF,
        )

    def resolve_candidates(
        self, rom: bytes, decoded: DecodedCode, **options: Any
    ) -> list[CandidateOffset]:
        addr = decoded.address
        rom_len = len(rom)
        if addr < 0x4000:
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
                    label=f"Bank 00 @ 0x{addr:06X}",
                )
            ]

        bank_count = max(2, (rom_len + _BANK_SIZE - 1) // _BANK_SIZE)
        rel = addr - 0x4000
        candidates: list[CandidateOffset] = []
        for bank in range(1, bank_count):
            off = bank * _BANK_SIZE + rel
            if off >= rom_len:
                break
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
        if len(rom) < 0x150:
            return []

        header_stored = rom[0x014D]
        header_calc = 0
        for b in rom[0x0134:0x014D]:
            header_calc = (header_calc - b - 1) & 0xFF

        global_stored = (rom[0x014E] << 8) | rom[0x014F]
        global_calc = (sum(rom[:0x014E]) + sum(rom[0x0150:])) & 0xFFFF

        return [
            ChecksumEntry(
                name="Header Checksum",
                offset=0x014D,
                stored=header_stored,
                computed=header_calc,
                width=1,
                valid=header_stored == header_calc,
            ),
            ChecksumEntry(
                name="Global Checksum",
                offset=0x014E,
                stored=global_stored,
                computed=global_calc,
                width=2,
                valid=global_stored == global_calc,
            ),
        ]

    def fix_checksums(self, rom: bytearray, **options: Any) -> list[ChecksumEntry]:
        if len(rom) < 0x150:
            return []

        # Must compute and write header checksum FIRST because global checksum covers 0x014D.
        header_calc = 0
        for b in rom[0x0134:0x014D]:
            header_calc = (header_calc - b - 1) & 0xFF
        rom[0x014D] = header_calc

        global_calc = (sum(rom[:0x014E]) + sum(rom[0x0150:])) & 0xFFFF
        rom[0x014E] = (global_calc >> 8) & 0xFF
        rom[0x014F] = global_calc & 0xFF

        return self.verify_checksums(bytes(rom))

    def inspect_rom(self, rom: bytes, **options: Any) -> RomInfo:
        title = "(Unknown)"
        header_format = "Game Boy (Raw/Minimal)"
        extra: dict[str, Any] = {
            "bank_count": max(1, (len(rom) + _BANK_SIZE - 1) // _BANK_SIZE),
        }

        if len(rom) >= 0x150:
            cgb_flag = rom[0x0143]
            title_end = 0x0143 if cgb_flag in (0x80, 0xC0) else 0x0144
            parsed_title = read_ascii_title(rom[0x0134:title_end])
            if parsed_title:
                title = parsed_title

            if cgb_flag == 0xC0:
                header_format = "Game Boy Color (CGB-only)"
            elif cgb_flag == 0x80:
                header_format = "Game Boy / CGB Dual"
            elif rom[0x0146] == 0x03:
                header_format = "Super Game Boy (SGB)"
            else:
                header_format = "Game Boy (DMG)"

            extra["cgb_flag"] = f"0x{cgb_flag:02X}"
            extra["cartridge_type"] = f"0x{rom[0x0147]:02X}"
            extra["rom_size_code"] = f"0x{rom[0x0148]:02X}"
            extra["logo_valid"] = rom[0x0104:0x0134] == NINTENDO_LOGO

        return RomInfo(
            system=self.system,
            file_size=len(rom),
            title=title,
            header_format=header_format,
            checksums=self.verify_checksums(rom),
            extra=extra,
        )
