"""NES / Famicom Game Genie codec and iNES / NES 2.0 PRG-ROM mapper."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ggrompatcher.models import (
    CandidateOffset,
    CodeFormatError,
    ConsoleSystem,
    DecodedCode,
    RomFormatError,
    RomInfo,
)
from ggrompatcher.systems.base import SystemHandler, clean_code_chars

NES_ALPHABET = "APZLGITYEOXUKSVN"
_NES_CHAR_TO_VAL = {ch: idx for idx, ch in enumerate(NES_ALPHABET)}
_INES_MAGIC = b"NES\x1a"
_HEX_CHARS = frozenset("0123456789ABCDEF")
_ROCKY_SHIFTS = (
    3,
    13,
    14,
    1,
    6,
    9,
    5,
    0,
    12,
    7,
    2,
    8,
    10,
    11,
    4,
    19,
    21,
    23,
    22,
    20,
    17,
    16,
    18,
    29,
    31,
    24,
    26,
    25,
    30,
    27,
    28,
)
_ROCKY_KEY = 0x7E5EE93A
_ROCKY_XOR = 0x5C184B91

# Common PRG-ROM bank granularities by iNES mapper number.
_MAPPERS_8K = frozenset(
    {
        4,
        5,
        9,
        12,
        14,
        18,
        19,
        21,
        22,
        23,
        24,
        25,
        26,
        32,
        33,
        37,
        44,
        45,
        47,
        48,
        49,
        52,
        64,
        65,
        67,
        68,
        69,
        73,
        74,
        75,
        76,
        80,
        82,
        83,
        85,
        88,
        90,
        91,
        95,
        112,
        114,
        115,
        118,
        119,
        154,
        158,
        187,
        189,
        191,
        192,
        194,
        195,
        198,
        206,
        207,
        209,
        211,
        245,
    }
)
_MAPPERS_16K = frozenset(
    {
        1,
        2,
        10,
        13,
        15,
        16,
        70,
        71,
        72,
        78,
        86,
        89,
        92,
        93,
        94,
        97,
        105,
        152,
        153,
        155,
        159,
        180,
        184,
    }
)
_MAPPERS_32K = frozenset(
    {
        0,
        3,
        7,
        11,
        34,
        38,
        66,
        77,
        79,
        81,
        87,
        113,
        140,
        144,
        145,
        146,
        148,
        149,
        177,
        185,
        240,
        241,
    }
)


def _parse_ines_header(rom: bytes) -> dict[str, Any]:
    if len(rom) >= 16 and rom[:4] == _INES_MAGIC:
        flags6 = rom[6]
        flags7 = rom[7]
        is_nes2 = (flags7 & 0x0C) == 0x08
        has_trainer = bool(flags6 & 0x04)
        prg_start = 16 + (512 if has_trainer else 0)

        if is_nes2:
            msb = rom[9] & 0x0F
            if msb == 0x0F:
                exp = (rom[4] >> 2) & 0x3F
                mul = (rom[4] & 0x03) * 2 + 1
                prg_size = (1 << exp) * mul
            else:
                prg_size = ((msb << 8) | rom[4]) * 0x4000
            mapper = (flags6 >> 4) | (flags7 & 0xF0) | ((rom[8] & 0x0F) << 8)
            fmt = "NES 2.0"
        else:
            # Guard against legacy headers with ASCII tags like "DiskDude!" in bytes 7..15.
            dirty_tail = any(b != 0 for b in rom[12:16])
            mapper = (flags6 >> 4) if dirty_tail else ((flags6 >> 4) | (flags7 & 0xF0))
            prg_size = rom[4] * 0x4000 if rom[4] > 0 else max(0, len(rom) - prg_start)
            fmt = "iNES"

        available = max(0, len(rom) - prg_start)
        prg_size = min(prg_size, available)
        return {
            "has_header": True,
            "header_format": fmt,
            "mapper": mapper,
            "has_trainer": has_trainer,
            "prg_start": prg_start,
            "prg_size": prg_size,
            "is_nes2": is_nes2,
        }

    return {
        "has_header": False,
        "header_format": "Headerless NES PRG",
        "mapper": None,
        "has_trainer": False,
        "prg_start": 0,
        "prg_size": len(rom),
        "is_nes2": False,
    }


class NesHandler(SystemHandler):
    """Nintendo Entertainment System / Famicom ROM handler."""

    system = ConsoleSystem.NES

    def detect_confidence(self, rom: bytes, filename: str | None = None) -> int:
        ext = Path(filename).suffix.lower() if filename else ""
        if len(rom) >= 16 and rom[:4] == _INES_MAGIC:
            return 100
        if ext in {".nes", ".unf"}:
            return 75
        return 0

    def _decode_rocky(self, raw_code: str, clean_hex: str, description: str) -> DecodedCode:
        if len(clean_hex) != 8 or not all(ch in _HEX_CHARS for ch in clean_hex):
            raise CodeFormatError(
                f"Invalid NES Pro Action Rocky code '{raw_code}': expected 8 hex digits "
                "(e.g. 15C93C0A or PAR:9123?DE:BD)."
            )
        enc = int(clean_hex, 16) >> 1
        key = _ROCKY_KEY
        decoded = 0
        for i in range(30, -1, -1):
            if (key ^ enc) & 0x40000000:
                decoded |= 1 << _ROCKY_SHIFTS[i]
                key ^= _ROCKY_XOR
            enc = (enc << 1) & 0xFFFFFFFF
            key = (key << 1) & 0xFFFFFFFF

        address = (decoded & 0x7FFF) | 0x8000
        compare = (decoded >> 16) & 0xFF
        value = (decoded >> 24) & 0xFF
        return DecodedCode(
            raw_code=raw_code.strip(),
            normalized_code=clean_hex,
            system=self.system,
            address=address,
            value=bytes([value]),
            compare=bytes([compare]),
            description=description,
        )

    def _decode_par_cpu(self, raw_code: str, body: str, description: str) -> DecodedCode:
        compact = body.strip().upper().replace(" ", "")
        if ":" in compact:
            left, val_hex = compact.split(":", 1)
            if "?" in left:
                addr_hex, cmp_hex = left.split("?", 1)
                cmp_bytes: bytes | None = bytes([int(cmp_hex, 16)])
            else:
                addr_hex = left
                cmp_bytes = None
            addr = int(addr_hex, 16)
            val = int(val_hex, 16)
            if not 0 <= val <= 0xFF:
                raise CodeFormatError(f"NES PAR value out of range: '{raw_code}'.")
            if addr < 0x8000:
                raise CodeFormatError(
                    f"NES Pro Action code '{raw_code}' targets CPU RAM/PPU "
                    f"(0x{addr:04X} < 0x8000), not cartridge PRG-ROM."
                )
            if addr > 0xFFFF:
                raise CodeFormatError(f"NES address 0x{addr:X} out of 16-bit CPU range.")
            cmp_part = f"?{cmp_bytes.hex().upper()}" if cmp_bytes else ""
            return DecodedCode(
                raw_code=raw_code.strip(),
                normalized_code=f"PAR:{addr:04X}{cmp_part}:{val:02X}",
                system=self.system,
                address=addr,
                value=bytes([val]),
                compare=cmp_bytes,
                description=description,
            )
        return self._decode_rocky(raw_code, clean_code_chars(compact), description)

    def decode_code(self, code: str, description: str = "") -> DecodedCode:
        raw_upper = code.strip().upper()
        for prefix in ("PAR:", "ROCKY:", "AR:"):
            if raw_upper.startswith(prefix):
                return self._decode_par_cpu(code, raw_upper[len(prefix) :], description)
        for prefix in ("PAR ", "ROCKY ", "AR "):
            if raw_upper.startswith(prefix):
                return self._decode_par_cpu(code, raw_upper[len(prefix) :], description)

        clean = clean_code_chars(code)
        if (
            len(clean) == 8
            and all(ch in _HEX_CHARS for ch in clean)
            and any(ch not in _NES_CHAR_TO_VAL for ch in clean)
        ):
            # Auto-detect Pro Action Rocky when any hex digit/letter isn't in NES Game Genie.
            return self._decode_rocky(code, clean, description)

        if len(clean) not in (6, 8):
            raise CodeFormatError(
                f"Invalid NES Game Genie code '{code}': expected 6 or 8 letters "
                "(e.g. SXIOPO or ZEXPYGLA), or 8 hex digits for Pro Action Rocky."
            )
        bad = [ch for ch in clean if ch not in _NES_CHAR_TO_VAL]
        if bad:
            raise CodeFormatError(
                f"Invalid NES Game Genie code '{code}': invalid character(s) "
                f"{', '.join(bad)}. Allowed letters: {NES_ALPHABET}."
            )

        n = [_NES_CHAR_TO_VAL[ch] for ch in clean]
        address = (
            0x8000
            | ((n[3] & 7) << 12)
            | ((n[5] & 7) << 8)
            | ((n[4] & 8) << 8)
            | ((n[2] & 7) << 4)
            | ((n[1] & 8) << 4)
            | (n[4] & 7)
            | (n[3] & 8)
        )

        if len(n) == 6:
            data = ((n[1] & 7) << 4) | ((n[0] & 8) << 4) | (n[0] & 7) | (n[5] & 8)
            compare_bytes: bytes | None = None
        else:
            data = ((n[1] & 7) << 4) | ((n[0] & 8) << 4) | (n[0] & 7) | (n[7] & 8)
            compare_val = ((n[7] & 7) << 4) | ((n[6] & 8) << 4) | (n[6] & 7) | (n[5] & 8)
            compare_bytes = bytes([compare_val])

        return DecodedCode(
            raw_code=code.strip(),
            normalized_code=clean,
            system=self.system,
            address=address,
            value=bytes([data]),
            compare=compare_bytes,
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
        if not 0x8000 <= address <= 0xFFFF:
            raise CodeFormatError(f"Address 0x{address:04X} out of NES PRG space (0x8000-0xFFFF).")
        if not 0 <= value <= 0xFF:
            raise CodeFormatError(
                f"Replacement value 0x{value:X} must be an 8-bit byte (0x00-0xFF)."
            )

        fmt = code_format.strip().lower()
        if fmt in {"par", "rocky"}:
            if compare is None:
                raise CodeFormatError(
                    "NES Pro Action Rocky encoding requires a compare byte (-c / Compare)."
                )
            if not 0 <= compare <= 0xFF:
                raise CodeFormatError(
                    f"Compare value 0x{compare:X} must be an 8-bit byte (0x00-0xFF)."
                )
            d = (address & 0x7FFF) | ((compare & 0xFF) << 16) | ((value & 0xFF) << 24)
            key = _ROCKY_KEY
            encoded = 0
            for i in range(30, -1, -1):
                bit = d >> _ROCKY_SHIFTS[i]
                if ((key >> 30) ^ bit) & 1:
                    encoded |= 2 << i
                if bit & 1:
                    key ^= _ROCKY_XOR
                key = (key << 1) & 0xFFFFFFFF
            return f"{encoded & 0xFFFFFFFF:08X}"
        if fmt != "gg":
            raise CodeFormatError(
                f"Unsupported NES code format '{code_format}'. Use 'gg' or 'par'."
            )

        n0 = ((value >> 4) & 8) | (value & 7)
        n1 = ((address >> 4) & 8) | ((value >> 4) & 7)
        n2 = (address >> 4) & 7
        n3 = (address & 8) | ((address >> 12) & 7)
        n4 = ((address >> 8) & 8) | (address & 7)

        if compare is None:
            n5 = (value & 8) | ((address >> 8) & 7)
            nibbles = [n0, n1, n2, n3, n4, n5]
        else:
            if not 0 <= compare <= 0xFF:
                raise CodeFormatError(
                    f"Compare value 0x{compare:X} must be an 8-bit byte (0x00-0xFF)."
                )
            n5 = (compare & 8) | ((address >> 8) & 7)
            n6 = ((compare >> 4) & 8) | (compare & 7)
            n7 = (value & 8) | ((compare >> 4) & 7)
            nibbles = [n0, n1, n2, n3, n4, n5, n6, n7]

        return "".join(NES_ALPHABET[x] for x in nibbles)

    def _determine_bank_stride(self, prg_size: int, mapper: int | None, override: str) -> int:
        mode = override.strip().lower()
        if mode == "8k":
            return 0x2000
        if mode == "16k":
            return 0x4000
        if mode == "32k":
            return 0x8000
        if mode != "auto":
            raise RomFormatError(
                f"Invalid NES bank size mode '{override}'. Use auto, 8k, 16k, or 32k."
            )

        if prg_size <= 0x4000:
            return 0x4000
        if prg_size <= 0x8000:
            return 0x8000
        if mapper in _MAPPERS_8K:
            return 0x2000
        if mapper in _MAPPERS_16K:
            return 0x4000
        if mapper in _MAPPERS_32K:
            return 0x8000
        # Unknown banked mapper: scan 8 KB pages so MMC3-style codes are always found.
        return 0x2000

    def resolve_candidates(
        self, rom: bytes, decoded: DecodedCode, **options: Any
    ) -> list[CandidateOffset]:
        info = _parse_ines_header(rom)
        prg_start: int = info["prg_start"]
        prg_size: int = info["prg_size"]
        mapper: int | None = info["mapper"]
        if prg_size <= 0:
            return []

        rel = decoded.address - 0x8000
        if prg_size <= 0x4000:
            off = prg_start + (rel & 0x3FFF)
            if off >= prg_start + prg_size:
                return []
            cur = rom[off : off + 1]
            matches = (cur == decoded.compare) if decoded.compare is not None else None
            return [
                CandidateOffset(
                    file_offset=off,
                    bank=0,
                    current_bytes=cur,
                    matches_compare=matches,
                    label=f"PRG Bank 00 @ 0x{off:06X}",
                )
            ]

        if prg_size <= 0x8000:
            off = prg_start + rel
            if off >= prg_start + prg_size:
                return []
            cur = rom[off : off + 1]
            matches = (cur == decoded.compare) if decoded.compare is not None else None
            return [
                CandidateOffset(
                    file_offset=off,
                    bank=0,
                    current_bytes=cur,
                    matches_compare=matches,
                    label=f"PRG Bank 00 @ 0x{off:06X}",
                )
            ]

        bank_mode = str(options.get("nes_bank_size", "auto"))
        stride = self._determine_bank_stride(prg_size, mapper, bank_mode)
        mask = stride - 1
        inner = rel & mask
        candidates: list[CandidateOffset] = []
        bank_count = (prg_size + stride - 1) // stride
        for bank in range(bank_count):
            off = prg_start + bank * stride + inner
            if off >= prg_start + prg_size:
                break
            cur = rom[off : off + 1]
            matches = (cur == decoded.compare) if decoded.compare is not None else None
            candidates.append(
                CandidateOffset(
                    file_offset=off,
                    bank=bank,
                    current_bytes=cur,
                    matches_compare=matches,
                    label=f"{stride // 1024}K Bank {bank:02X} @ 0x{off:06X}",
                )
            )
        return candidates

    def inspect_rom(self, rom: bytes, **options: Any) -> RomInfo:
        info = _parse_ines_header(rom)
        mapper = info["mapper"]
        header_fmt = info["header_format"]
        if mapper is not None:
            header_fmt = f"{header_fmt} (Mapper {mapper})"

        extra: dict[str, Any] = {
            "mapper": mapper,
            "prg_start": info["prg_start"],
            "prg_size": info["prg_size"],
            "has_trainer": info["has_trainer"],
            "ines_header": info["has_header"],
            "is_nes2": info["is_nes2"],
        }
        return RomInfo(
            system=self.system,
            file_size=len(rom),
            title="(iNES ROM — no internal title)",
            header_format=header_fmt,
            checksums=[],
            extra=extra,
        )
