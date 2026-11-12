"""Raw hexadecimal file-offset patch handler (`OFFSET:VAL`, `OFFSET?CMP:VAL`, `OFFSET=CMP?VAL`)."""

from __future__ import annotations

import re
from typing import Any

from ggrompatcher.models import (
    CandidateOffset,
    CodeFormatError,
    ConsoleSystem,
    DecodedCode,
    RomInfo,
)
from ggrompatcher.systems.base import SystemHandler

# Supported formats:
#   0014BC:00
#   00033A:6002
#   0014BC?03:00
#   0014BC=00          (higan)
#   0014BC=03?00       (higan with compare)
_RAW_COLON_RE = re.compile(
    r"^(?:0X)?([0-9A-F]+)(?:\?([0-9A-F]+))?:([0-9A-F]+)$",
    re.IGNORECASE,
)
_RAW_HIGAN_RE = re.compile(
    r"^(?:0X)?([0-9A-F]+)=(?:([0-9A-F]+)\?)?([0-9A-F]+)$",
    re.IGNORECASE,
)


def _parse_hex_bytes(hex_str: str, label: str, code: str) -> bytes:
    if len(hex_str) % 2 != 0 or len(hex_str) == 0:
        raise CodeFormatError(
            f"Invalid raw patch '{code}': {label} '{hex_str}' "
            "must have an even number of hex digits."
        )
    return bytes.fromhex(hex_str)


class RawHandler(SystemHandler):
    """Direct file-offset hexadecimal patching handler."""

    system = ConsoleSystem.RAW

    def detect_confidence(self, rom: bytes, filename: str | None = None) -> int:
        return 1

    def decode_code(self, code: str, description: str = "") -> DecodedCode:
        stripped = code.strip().replace(" ", "")
        m = _RAW_COLON_RE.match(stripped)
        if m:
            addr_hex, cmp_hex, val_hex = m.group(1), m.group(2), m.group(3)
        else:
            m = _RAW_HIGAN_RE.match(stripped)
            if not m:
                raise CodeFormatError(
                    f"Invalid raw patch '{code}': expected OFFSET:HEX, OFFSET?CMP:HEX, "
                    "OFFSET=HEX, or OFFSET=CMP?HEX."
                )
            addr_hex, cmp_hex, val_hex = m.group(1), m.group(2), m.group(3)

        address = int(addr_hex, 16)
        value = _parse_hex_bytes(val_hex, "replacement value", code)
        compare = _parse_hex_bytes(cmp_hex, "compare value", code) if cmp_hex is not None else None
        if compare is not None and len(compare) != len(value):
            raise CodeFormatError(
                f"Invalid raw patch '{code}': compare length ({len(compare)} bytes) "
                f"must match value length ({len(value)} bytes)."
            )

        if compare is not None:
            normalized = f"{address:06X}?{compare.hex().upper()}:{value.hex().upper()}"
        else:
            normalized = f"{address:06X}:{value.hex().upper()}"

        return DecodedCode(
            raw_code=code.strip(),
            normalized_code=normalized,
            system=self.system,
            address=address,
            value=value,
            compare=compare,
            description=description,
        )

    def encode_code(self, address: int, value: int, compare: int | None = None) -> str:
        if address < 0:
            raise CodeFormatError(f"Offset 0x{address:X} must be non-negative.")
        if not 0 <= value <= 0xFFFF:
            raise CodeFormatError(f"Value 0x{value:X} must fit in 1 or 2 bytes (0x00-0xFFFF).")
        val_hex = f"{value:04X}" if value > 0xFF else f"{value:02X}"
        if compare is not None:
            if not 0 <= compare <= 0xFFFF:
                raise CodeFormatError(f"Compare 0x{compare:X} must fit in 1 or 2 bytes.")
            cmp_hex = f"{compare:04X}" if len(val_hex) == 4 else f"{compare:02X}"
            return f"{address:06X}?{cmp_hex}:{val_hex}"
        return f"{address:06X}:{val_hex}"

    def resolve_candidates(
        self, rom: bytes, decoded: DecodedCode, **options: Any
    ) -> list[CandidateOffset]:
        off = decoded.address
        end = off + len(decoded.value)
        if off < 0 or end > len(rom):
            return []
        cur = rom[off:end]
        matches = (cur == decoded.compare) if decoded.compare is not None else None
        return [
            CandidateOffset(
                file_offset=off,
                bank=None,
                current_bytes=cur,
                matches_compare=matches,
                label=f"File Offset 0x{off:06X}",
            )
        ]

    def inspect_rom(self, rom: bytes, **options: Any) -> RomInfo:
        return RomInfo(
            system=self.system,
            file_size=len(rom),
            title="(Raw Binary Image)",
            header_format="Raw Binary",
            checksums=[],
            extra={},
        )
