"""Game Boy Advance (GBA) cartridge header, checksum, and GameShark/PAR/CodeBreaker codecs."""

from __future__ import annotations

import re
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
from ggrompatcher.systems.base import SystemHandler, read_ascii_title

_GBA_EXTENSIONS = {".gba", ".agb"}
_GBA_HEADER_MIN = 0xC0
_GBA_ROM_BASE = 0x08000000
_GBA_ROM_END = 0x0E000000
_GBA_MAX_ROM_SIZE = 0x02000000  # 32 MiB

# Official 156-byte GBA boot logo bitmap located at 0x04..0x9F.
GBA_NINTENDO_LOGO = bytes(
    [
        0x24,
        0xFF,
        0xAE,
        0x51,
        0x69,
        0x9A,
        0xA2,
        0x21,
        0x3D,
        0x84,
        0x82,
        0x0A,
        0x84,
        0xE4,
        0x09,
        0xAD,
        0x11,
        0x24,
        0x8B,
        0x98,
        0xC0,
        0x81,
        0x7F,
        0x21,
        0xA3,
        0x52,
        0xBE,
        0x19,
        0x93,
        0x09,
        0xCE,
        0x20,
        0x10,
        0x46,
        0x4A,
        0x4A,
        0xF8,
        0x27,
        0x31,
        0xEC,
        0x58,
        0xC7,
        0xE8,
        0x33,
        0x82,
        0xE3,
        0xCE,
        0xBF,
        0x85,
        0xF4,
        0xDF,
        0x94,
        0xCE,
        0x4B,
        0x09,
        0xC1,
        0x94,
        0x56,
        0x8A,
        0xC0,
        0x13,
        0x72,
        0xA7,
        0xFC,
        0x9F,
        0x84,
        0x4D,
        0x73,
        0xA3,
        0xCA,
        0x9A,
        0x61,
        0x58,
        0x97,
        0xA3,
        0x27,
        0xFC,
        0x03,
        0x98,
        0x76,
        0x23,
        0x1D,
        0xC7,
        0x61,
        0x03,
        0x04,
        0xAE,
        0x56,
        0xBF,
        0x38,
        0x84,
        0x00,
        0x40,
        0xA7,
        0x0E,
        0xFD,
        0xFF,
        0x52,
        0xFE,
        0x03,
        0x6F,
        0x95,
        0x30,
        0xF1,
        0x97,
        0xFB,
        0xC0,
        0x85,
        0x60,
        0xD6,
        0x80,
        0x25,
        0xA9,
        0x63,
        0xBE,
        0x03,
        0x01,
        0x4E,
        0x38,
        0xE2,
        0xF9,
        0xA2,
        0x34,
        0xFF,
        0xBB,
        0x3E,
        0x03,
        0x44,
        0x78,
        0x00,
        0x90,
        0xCB,
        0x88,
        0x11,
        0x3A,
        0x94,
        0x65,
        0xC0,
        0x7C,
        0x63,
        0x87,
        0xF0,
        0x3C,
        0xAF,
        0xD6,
        0x25,
        0xE4,
        0x8B,
        0x38,
        0x0A,
        0xAC,
        0x72,
        0x21,
        0xD4,
        0xF8,
        0x07,
    ]
)

# Default TEA seeds from mGBA (src/gba/cheats/gameshark.c & parv3.c).
GBA_GS_V1_SEEDS: tuple[int, int, int, int] = (
    0x09F4FBBD,
    0x9681884A,
    0x352027E9,
    0xF3DEE5A7,
)
GBA_PAR_V3_SEEDS: tuple[int, int, int, int] = (
    0x7AA9648F,
    0x7FAE6994,
    0xC0EFAAD5,
    0x42712C57,
)

_PAR_ADDR_RE = re.compile(
    r"^(?:(?:PAR|AR|RAW):)?(?:0X)?([0-9A-F]{6,8})(?:\?([0-9A-F]{2}|[0-9A-F]{4}|[0-9A-F]{8}))?[:=]([0-9A-F]{2}|[0-9A-F]{4}|[0-9A-F]{8})$",
    re.IGNORECASE,
)
_HEX_PAIR_8_8_RE = re.compile(
    r"^(?:(GSA1|GS1|GSA|GS|PAR3|AR3):)?([0-9A-F]{8})[\s:-]*([0-9A-F]{8})$",
    re.IGNORECASE,
)
_HEX_PAIR_8_4_RE = re.compile(
    r"^(?:(CBA|CB):)?([0-9A-F]{8})[\s:-]*([0-9A-F]{4})$",
    re.IGNORECASE,
)
_PAR3_QUAD_RE = re.compile(
    r"^(?:(PAR3|AR3):)?([0-9A-F]{8})[\s:/-]+([0-9A-F]{8})[\s:/-]+([0-9A-F]{8})[\s:/-]+([0-9A-F]{8})$",
    re.IGNORECASE,
)


def compute_gba_header_checksum(rom: bytes | bytearray) -> int:
    """Compute the 8-bit GBA header complement checksum stored at 0xBD."""
    if len(rom) < 0xBE:
        raise ValueError("GBA ROM must be at least 0xBE bytes to compute header checksum.")
    return (-(0x19 + sum(rom[0xA0:0xBD]))) & 0xFF


def _tea_encrypt_pair(
    op1: int,
    op2: int,
    seeds: tuple[int, int, int, int] = GBA_GS_V1_SEEDS,
) -> tuple[int, int]:
    s0, s1, s2, s3 = seeds
    acc = 0
    for _ in range(32):
        acc = (acc + 0x9E3779B9) & 0xFFFFFFFF
        op1 = (op1 + ((((op2 << 4) + s0) ^ (op2 + acc) ^ ((op2 >> 5) + s1)) & 0xFFFFFFFF)) & (
            0xFFFFFFFF
        )
        op2 = (op2 + ((((op1 << 4) + s2) ^ (op1 + acc) ^ ((op1 >> 5) + s3)) & 0xFFFFFFFF)) & (
            0xFFFFFFFF
        )
    return op1, op2


def _tea_decrypt_pair(
    op1: int,
    op2: int,
    seeds: tuple[int, int, int, int] = GBA_GS_V1_SEEDS,
) -> tuple[int, int]:
    s0, s1, s2, s3 = seeds
    acc = (0x9E3779B9 * 32) & 0xFFFFFFFF
    for _ in range(32):
        op2 = (op2 - ((((op1 << 4) + s2) ^ (op1 + acc) ^ ((op1 >> 5) + s3)) & 0xFFFFFFFF)) & (
            0xFFFFFFFF
        )
        op1 = (op1 - ((((op2 << 4) + s0) ^ (op2 + acc) ^ ((op2 >> 5) + s1)) & 0xFFFFFFFF)) & (
            0xFFFFFFFF
        )
        acc = (acc - 0x9E3779B9) & 0xFFFFFFFF
    return op1, op2


def _describe_gba_region(addr: int) -> str:
    region = (addr >> 24) & 0xFF
    if region == 0x00:
        return "System BIOS (0x00000000-0x00003FFF)"
    if region == 0x02:
        return "EWRAM runtime RAM (0x02000000-0x0203FFFF)"
    if region == 0x03:
        return "IWRAM runtime RAM (0x03000000-0x03007FFF)"
    if region == 0x04:
        return "I/O registers (0x04000000-0x040003FF)"
    if region in {0x05, 0x06, 0x07}:
        return "Palette/VRAM/OAM graphics RAM (0x05000000-0x070003FF)"
    if 0x08 <= region <= 0x0D:
        return "Cartridge ROM space (0x08000000-0x0DFFFFFF)"
    if region == 0x0E:
        return "Cartridge SRAM/Flash save memory (0x0E000000+)"
    return f"non-ROM memory region (0x{addr:08X})"


def _par_addr_from_op(word: int) -> int:
    return (word & 0xFFFFF) | ((word << 4) & 0x0F000000)


class GbaHandler(SystemHandler):
    """Handler for Game Boy Advance ROM images and GameShark/PAR/CodeBreaker codes."""

    system = ConsoleSystem.GBA

    def detect_confidence(self, rom: bytes, filename: str | None = None) -> int:
        score = 0
        if filename:
            ext = Path(filename).suffix.lower()
            if ext in _GBA_EXTENSIONS:
                score += 25

        if len(rom) >= _GBA_HEADER_MIN:
            if rom[0x04:0xA0] == GBA_NINTENDO_LOGO:
                score += 65
            if rom[0xB2] == 0x96:
                score += 15
            if rom[0xBD] == compute_gba_header_checksum(rom):
                score += 5
            if rom[0x03] == 0xEA:
                score += 5

        return score

    def inspect_rom(self, rom: bytes, **options: Any) -> RomInfo:
        if len(rom) < _GBA_HEADER_MIN:
            return RomInfo(
                system=self.system,
                file_size=len(rom),
                title="",
                header_format="Truncated GBA ROM",
                checksums=[],
            )

        title = read_ascii_title(rom[0xA0:0xAC])
        game_code = read_ascii_title(rom[0xAC:0xB0])
        maker_code = read_ascii_title(rom[0xB0:0xB2])
        fixed_byte = rom[0xB2]
        unit_code = rom[0xB3]
        version = rom[0xBC]
        stored_chk = rom[0xBD]
        computed_chk = compute_gba_header_checksum(rom)
        logo_ok = rom[0x04:0xA0] == GBA_NINTENDO_LOGO

        checksums = [
            ChecksumEntry(
                name="Header Complement Checksum",
                offset=0xBD,
                stored=stored_chk,
                computed=computed_chk,
                width=1,
                valid=(stored_chk == computed_chk),
            )
        ]

        return RomInfo(
            system=self.system,
            file_size=len(rom),
            title=title or game_code,
            header_format="GBA Cartridge Header",
            checksums=checksums,
            extra={
                "game_code": game_code,
                "maker_code": maker_code,
                "fixed_0x96_valid": fixed_byte == 0x96,
                "unit_code": f"0x{unit_code:02X}",
                "software_version": version,
                "nintendo_logo_valid": logo_ok,
            },
        )

    def fix_checksums(self, rom: bytearray, **options: Any) -> list[ChecksumEntry]:
        if len(rom) < _GBA_HEADER_MIN:
            return []
        stored_chk = rom[0xBD]
        computed_chk = compute_gba_header_checksum(rom)
        rom[0xBD] = computed_chk
        return [
            ChecksumEntry(
                name="Header Complement Checksum",
                offset=0xBD,
                stored=stored_chk,
                computed=computed_chk,
                width=1,
                valid=True,
            )
        ]

    def _decode_par_literal(
        self, raw: str, addr_hex: str, cmp_hex: str | None, val_hex: str, description: str
    ) -> DecodedCode:
        addr = int(addr_hex, 16)
        if not (_GBA_ROM_BASE <= addr < _GBA_ROM_END):
            region = _describe_gba_region(addr)
            raise CodeFormatError(
                f"GBA PAR/AR address 0x{addr:08X} targets {region}, not Cartridge ROM "
                "(0x08000000-0x0DFFFFFF). Runtime RAM/I/O codes cannot be permanently "
                "patched into a static GBA ROM file (use raw file offsets `OFFSET:VAL` "
                "or ROM bus `PAR:08xxxxxx:VAL`)."
            )
        file_offset = addr & 0x01FFFFFF
        width = len(val_hex) // 2
        val_int = int(val_hex, 16)
        val_bytes = val_int.to_bytes(width, "little")
        cmp_bytes: bytes | None = None
        if cmp_hex is not None:
            if len(cmp_hex) != len(val_hex):
                raise CodeFormatError(
                    f"GBA PAR code '{raw}' compare width ({len(cmp_hex) // 2} bytes) "
                    f"must match value width ({width} bytes)."
                )
            cmp_bytes = int(cmp_hex, 16).to_bytes(width, "little")

        val_norm = val_hex.upper()
        if cmp_hex is not None:
            normalized = f"PAR:{_GBA_ROM_BASE | file_offset:08X}?{cmp_hex.upper()}:{val_norm}"
        else:
            normalized = f"PAR:{_GBA_ROM_BASE | file_offset:08X}:{val_norm}"

        return DecodedCode(
            raw_code=raw,
            normalized_code=normalized,
            system=self.system,
            address=file_offset,
            value=val_bytes,
            compare=cmp_bytes,
            description=description,
        )

    def _decode_gsa_pair(
        self, raw: str, prefix: str | None, op1: int, op2: int, description: str
    ) -> DecodedCode:
        pref = prefix.upper() if prefix else ""

        # Allow explicit or raw unencrypted GameShark Advance ROM patch (type 0x6).
        if pref not in {"GSA1", "GS1"} and (op1 >> 28) == 0x6 and (op2 & 0xFFFF0000) == 0:
            halfword_offset = op1 & 0x00FFFFFF
            rom_offset = halfword_offset << 1
            if rom_offset < _GBA_MAX_ROM_SIZE:
                val16 = op2 & 0xFFFF
                return DecodedCode(
                    raw_code=raw,
                    normalized_code=f"GSA:{op1:08X} {op2:08X}",
                    system=self.system,
                    address=rom_offset,
                    value=val16.to_bytes(2, "little"),
                    compare=None,
                    description=description,
                )

        # Try TEA decryption with GameShark Advance v1/v2 seeds.
        dec_op1, dec_op2 = _tea_decrypt_pair(op1, op2, GBA_GS_V1_SEEDS)
        if (dec_op1 >> 28) == 0x6 and (dec_op2 & 0xFFFF0000) == 0:
            rom_offset = (dec_op1 & 0x00FFFFFF) << 1
            if rom_offset < _GBA_MAX_ROM_SIZE:
                val16 = dec_op2 & 0xFFFF
                return DecodedCode(
                    raw_code=raw,
                    normalized_code=f"GSA1:{op1:08X} {op2:08X}",
                    system=self.system,
                    address=rom_offset,
                    value=val16.to_bytes(2, "little"),
                    compare=None,
                    description=description,
                )

        # Provide accurate diagnostics if the code targets runtime RAM rather than ROM.
        dec_type = dec_op1 >> 28
        dec_addr = dec_op1 & 0x0FFFFFFF
        if dec_type in {0x0, 0x1, 0x2, 0x3, 0x8, 0xD, 0xE} and 0x02000000 <= dec_addr < 0x08000000:
            raise CodeFormatError(
                f"GameShark Advance v1/v2 code '{raw}' decrypts to {dec_op1:08X} {dec_op2:08X}, "
                f"targeting {_describe_gba_region(dec_addr)}. Runtime RAM/I/O codes cannot be "
                "statically patched into a GBA ROM."
            )

        raw_type = op1 >> 28
        raw_addr = op1 & 0x0FFFFFFF
        if raw_type in {0x0, 0x1, 0x2, 0x3, 0x8, 0xD, 0xE} and 0x02000000 <= raw_addr < 0x08000000:
            raise CodeFormatError(
                f"Raw GameShark Advance code '{raw}' (type 0x{raw_type:X}) targets "
                f"{_describe_gba_region(raw_addr)}. Runtime RAM/I/O codes cannot be "
                "statically patched into a GBA ROM."
            )

        if pref in {"PAR3", "AR3"}:
            par3_op1, par3_op2 = _tea_decrypt_pair(op1, op2, GBA_PAR_V3_SEEDS)
            par3_addr = _par_addr_from_op(par3_op1)
            if 0x02000000 <= par3_addr < 0x08000000:
                raise CodeFormatError(
                    f"Action Replay v3 code '{raw}' decrypts to {par3_op1:08X} {par3_op2:08X}, "
                    f"targeting {_describe_gba_region(par3_addr)}. Runtime RAM codes cannot "
                    "be patched into a static GBA ROM."
                )

        raise CodeFormatError(
            f"GBA code '{raw}' is not a valid static ROM patch (expected GameShark v1/v2 "
            "type 0x6 `6AAAAAAA 0000VVVV`, encrypted GSA1 equivalent, PAR3 two-line ROM patch, "
            "or `PAR:08AAAAAA:VV[VV]`)."
        )

    def decode_code(self, code: str, description: str = "") -> DecodedCode:
        raw = code.strip()
        if not raw:
            raise CodeFormatError("Empty GBA patch code.")

        m_par = _PAR_ADDR_RE.match(raw.replace(" ", ""))
        if m_par:
            return self._decode_par_literal(
                raw, m_par.group(1), m_par.group(2), m_par.group(3), description
            )

        # PAR v3 two-pair ROM patch: `PAR3:00000000 18AAAAAA VVVVVVVV 00000000` (raw or encrypted).
        m_quad = _PAR3_QUAD_RE.match(raw)
        if m_quad:
            w1, w2, w3, w4 = (int(m_quad.group(i), 16) for i in range(2, 6))
            for encrypted, (a1, a2, b1, b2) in (
                (False, (w1, w2, w3, w4)),
                (
                    True,
                    (
                        *_tea_decrypt_pair(w1, w2, GBA_PAR_V3_SEEDS),
                        *_tea_decrypt_pair(w3, w4, GBA_PAR_V3_SEEDS),
                    ),
                ),
            ):
                if a1 == 0x00000000 and (a2 & 0xF8000000) == 0x18000000 and b2 == 0x00000000:
                    rom_offset = (a2 & 0x00FFFFFF) << 1
                    val16 = b1 & 0xFFFF
                    tag = "PAR3" if encrypted else "PAR3-RAW"
                    return DecodedCode(
                        raw_code=raw,
                        normalized_code=f"{tag}:{w1:08X} {w2:08X} {w3:08X} {w4:08X}",
                        system=self.system,
                        address=rom_offset,
                        value=val16.to_bytes(2, "little"),
                        compare=None,
                        description=description,
                    )
            raise CodeFormatError(
                f"Action Replay v3 4-word code '{raw}' is not a valid ROM patch pair "
                "(expected 00000000 18AAAAAA 0000VVVV 00000000 or TEA-encrypted equivalent)."
            )

        # CodeBreaker 8+4 format (`XXXXXXXX YYYY`) -> RAM/hook diagnostic.
        m_cb = _HEX_PAIR_8_4_RE.match(raw)
        if m_cb:
            op1 = int(m_cb.group(2), 16)
            op2 = int(m_cb.group(3), 16)
            cb_type = op1 >> 28
            addr = op1 & 0x0FFFFFFF
            if cb_type == 0x9:
                raise CodeFormatError(
                    f"CodeBreaker master encryption key '{op1:08X} {op2:04X}' only reseeds "
                    "the runtime CodeBreaker cipher and has no standalone ROM patch bytes."
                )
            raise CodeFormatError(
                f"CodeBreaker GBA code '{op1:08X} {op2:04X}' (type 0x{cb_type:X}) targets "
                f"{_describe_gba_region(addr)}. CodeBreaker runtime RAM/hook codes cannot be "
                "baked into a static GBA ROM."
            )

        # GameShark Advance v1/v2 or PAR3 8+8 format (`XXXXXXXX XXXXXXXX`).
        m_gs = _HEX_PAIR_8_8_RE.match(raw)
        if m_gs:
            return self._decode_gsa_pair(
                raw,
                m_gs.group(1),
                int(m_gs.group(2), 16),
                int(m_gs.group(3), 16),
                description,
            )

        raise CodeFormatError(
            f"Unrecognized GBA code '{code}'. Supported formats: GameShark Advance v1/v2 ROM "
            "patch (`6AAAAAAA 0000VVVV` or TEA-encrypted `XXXXXXXX XXXXXXXX`), ROM-bus PAR "
            "(`PAR:08AAAAAA:VV` / `PAR:08AAAAAA:VVVV`), PAR v3 ROM patch (`PAR3:...`), or raw "
            "`OFFSET:HEX`."
        )

    def encode_code(
        self,
        address: int,
        value: int,
        compare: int | None = None,
        *,
        code_format: str = "gg",
    ) -> str:
        fmt = code_format.strip().lower()
        rom_offset = address & 0x01FFFFFF if _GBA_ROM_BASE <= address < _GBA_ROM_END else address
        if not (0 <= rom_offset < _GBA_MAX_ROM_SIZE):
            raise CodeFormatError(
                f"GBA ROM offset 0x{address:X} out of valid 32 MiB GBA range (0x000000-0x1FFFFFF)."
            )

        if fmt in {"gs", "gsa", "gs-enc", "gsa1", "gs1"}:
            if compare is not None:
                raise CodeFormatError("GameShark Advance v1/v2 ROM patch codes have no compare.")
            if rom_offset % 2 != 0:
                raise CodeFormatError(
                    f"GameShark Advance ROM patch address 0x{rom_offset:06X} "
                    "must be 2-byte aligned."
                )
            if not (0 <= value <= 0xFFFF):
                raise CodeFormatError(
                    f"GameShark Advance ROM patch value 0x{value:X} must fit in 16 bits."
                )
            op1 = 0x60000000 | (rom_offset >> 1)
            op2 = value & 0xFFFF
            if fmt in {"gs-enc", "gsa1", "gs1"}:
                enc1, enc2 = _tea_encrypt_pair(op1, op2, GBA_GS_V1_SEEDS)
                return f"GSA1:{enc1:08X} {enc2:08X}"
            return f"GSA:{op1:08X} {op2:08X}"

        if fmt in {"par3", "par3-raw"}:
            if compare is not None:
                raise CodeFormatError("Action Replay v3 ROM patch codes have no compare byte.")
            if rom_offset % 2 != 0:
                raise CodeFormatError(
                    f"Action Replay v3 ROM patch address 0x{rom_offset:06X} must be 2-byte aligned."
                )
            if not (0 <= value <= 0xFFFF):
                raise CodeFormatError(
                    f"Action Replay v3 ROM patch value 0x{value:X} must fit in 16 bits."
                )
            op1 = 0x00000000
            op2 = 0x18000000 | (rom_offset >> 1)
            op3 = value & 0xFFFF
            op4 = 0x00000000
            if fmt == "par3":
                e1, e2 = _tea_encrypt_pair(op1, op2, GBA_PAR_V3_SEEDS)
                e3, e4 = _tea_encrypt_pair(op3, op4, GBA_PAR_V3_SEEDS)
                return f"PAR3:{e1:08X} {e2:08X} {e3:08X} {e4:08X}"
            return f"PAR3:{op1:08X} {op2:08X} {op3:08X} {op4:08X}"

        if fmt not in {"gg", "par", "ar", "raw"}:
            raise CodeFormatError(
                f"GBA supports 'gg'/'par', 'gsa' (GameShark v1/v2), 'gsa1' (encrypted GSA), "
                f"or 'par3', got '{code_format}'."
            )

        if 0 <= value <= 0xFF and (compare is None or 0 <= compare <= 0xFF):
            width_hex = 2
        elif 0 <= value <= 0xFFFF and (compare is None or 0 <= compare <= 0xFFFF):
            width_hex = 4
        elif 0 <= value <= 0xFFFFFFFF and (compare is None or 0 <= compare <= 0xFFFFFFFF):
            width_hex = 8
        else:
            raise CodeFormatError(f"GBA value 0x{value:X} or compare out of 32-bit range.")

        bus_addr = _GBA_ROM_BASE | rom_offset
        if compare is not None:
            return f"PAR:{bus_addr:08X}?{compare:0{width_hex}X}:{value:0{width_hex}X}"
        return f"PAR:{bus_addr:08X}:{value:0{width_hex}X}"

    def resolve_candidates(
        self, rom: bytes, decoded: DecodedCode, **options: Any
    ) -> list[CandidateOffset]:
        offset = decoded.address
        end = offset + len(decoded.value)
        if offset < 0 or end > len(rom):
            return []
        cur = rom[offset:end]
        matches_cmp = (cur == decoded.compare) if decoded.compare is not None else None
        return [
            CandidateOffset(
                file_offset=offset,
                bank=None,
                current_bytes=cur,
                matches_compare=matches_cmp,
                label=f"GBA ROM 0x{_GBA_ROM_BASE | offset:08X} (file 0x{offset:06X})",
            )
        ]
