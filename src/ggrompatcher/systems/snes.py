"""Super Nintendo (SNES) Game Genie codec, LoROM/HiROM/ExHiROM mapper, and checksum support."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ggrompatcher.models import (
    CandidateOffset,
    ChecksumEntry,
    CodeFormatError,
    ConsoleSystem,
    DecodedCode,
    RomFormatError,
    RomInfo,
)
from ggrompatcher.systems.base import SystemHandler, clean_code_chars, read_ascii_title

SNES_ALPHABET = "DF4709156BC8A23E"
_SNES_CHAR_TO_VAL = {ch: idx for idx, ch in enumerate(SNES_ALPHABET)}
_SNES_EXTENSIONS = {".sfc", ".smc", ".swc", ".fig"}

_VALID_INIT_OPCODES = frozenset(
    {
        0x78,  # SEI
        0x18,  # CLC
        0xC2,  # REP
        0xE2,  # SEP
        0x4C,  # JMP abs
        0x5C,  # JML
        0x20,  # JSR
        0x9C,  # STZ
        0xA9,  # LDA imm
        0xA2,  # LDX imm
        0xA0,  # LDY imm
        0x4B,  # PHK
    }
)


def _has_smc_header(rom: bytes) -> bool:
    return len(rom) >= 0x8200 and (len(rom) % 1024) == 512


def _score_snes_header(body: bytes, header_off: int, expected_kind: str) -> int:
    if len(body) < header_off + 0x40:
        return -100

    score = 0
    comp = body[header_off + 0x1C] | (body[header_off + 0x1D] << 8)
    chk = body[header_off + 0x1E] | (body[header_off + 0x1F] << 8)
    if (chk ^ comp) == 0xFFFF:
        score += 8

    map_mode = body[header_off + 0x15]
    mode_low = map_mode & 0x0F
    if expected_kind == "lorom":
        if mode_low in (0x0, 0x2, 0x3):
            score += 5
        elif (map_mode & 0x01) == 0:
            score += 2
    elif expected_kind == "hirom":
        if mode_low in (0x1, 0x5):
            score += 5
        elif (map_mode & 0x01) == 1:
            score += 2
    elif expected_kind == "exhirom":
        if mode_low == 0x5:
            score += 7
    elif expected_kind == "exlorom" and mode_low == 0x2:
        score += 5

    rom_size_byte = body[header_off + 0x17]
    if 6 <= rom_size_byte <= 14:
        score += 2

    reset_vec = body[header_off + 0x3C] | (body[header_off + 0x3D] << 8)
    if reset_vec >= 0x8000:
        score += 2
        reset_pc = reset_vec & 0x7FFF if expected_kind == "lorom" else reset_vec
        if reset_pc < len(body) and body[reset_pc] in _VALID_INIT_OPCODES:
            score += 3

    title = body[header_off : header_off + 21]
    printable = sum(1 for b in title if 0x20 <= b <= 0x7E)
    if printable >= 16:
        score += 3
    elif printable >= 10:
        score += 1

    return score


def detect_snes_layout(rom: bytes, override: str = "auto") -> tuple[int, str, int]:
    """Return `(smc_base_offset, map_kind, header_offset_within_body)`."""
    base = 512 if _has_smc_header(rom) else 0
    body = memoryview(rom)[base:]
    mode = override.strip().lower()

    if mode != "auto":
        mapping_offsets = {
            "lorom": 0x007FC0,
            "hirom": 0x00FFC0,
            "exhirom": 0x40FFC0 if len(body) > 0x40FFC0 else 0x00FFC0,
            "exlorom": 0x407FC0 if len(body) > 0x407FC0 else 0x007FC0,
        }
        if mode not in mapping_offsets:
            raise RomFormatError(
                f"Invalid SNES mapping mode '{override}'. "
                "Use auto, lorom, hirom, exhirom, or exlorom."
            )
        return base, mode, mapping_offsets[mode]

    candidates: list[tuple[int, str, int]] = []
    if len(body) >= 0x8000:
        candidates.append((_score_snes_header(bytes(body), 0x007FC0, "lorom"), "lorom", 0x007FC0))
    if len(body) >= 0x10000:
        candidates.append((_score_snes_header(bytes(body), 0x00FFC0, "hirom"), "hirom", 0x00FFC0))
    if len(body) > 0x410000:
        candidates.append(
            (_score_snes_header(bytes(body), 0x40FFC0, "exhirom"), "exhirom", 0x40FFC0)
        )

    if not candidates:
        return base, "lorom", 0x007FC0

    candidates.sort(key=lambda item: item[0], reverse=True)
    _, best_kind, best_hdr = candidates[0]
    return base, best_kind, best_hdr


def _mirror_sum(data: bytes | bytearray | memoryview) -> int:
    """Compute SNES byte sum with hardware power-of-2 mirroring for non-power-of-2 ROMs."""
    size = len(data)
    if size == 0:
        return 0
    # Tiny/non-32KB-aligned homebrew ROMs: direct linear sum.
    if size & 0x7FFF:
        return sum(data)

    largest_p2 = 1 << (size.bit_length() - 1)
    if size == largest_p2:
        return sum(data)

    first_half = sum(data[:largest_p2])
    rem = data[largest_p2:]
    rem_len = len(rem)
    rem_sum = _mirror_sum(rem)
    # Mirror the remainder power-of-2 block until its mirrored total matches `largest_p2`.
    rem_p2 = 1 << (rem_len.bit_length() - 1)
    if rem_len != rem_p2:
        rem_p2 <<= 1
    repeat = max(1, largest_p2 // rem_p2)
    return first_half + rem_sum * repeat


def _compute_snes_checksum(body: bytearray | bytes, header_off: int) -> int:
    if len(body) < header_off + 0x20:
        return sum(body) & 0xFFFF
    buf = bytearray(body)
    # Complement + Checksum always byte-sum to 0x01FE, matching FF FF 00 00.
    buf[header_off + 0x1C : header_off + 0x20] = b"\xff\xff\x00\x00"
    return _mirror_sum(buf) & 0xFFFF


def _snes_addr_to_pc(address: int, map_kind: str, rom_body_len: int) -> int | None:
    bank = (address >> 16) & 0xFF
    off = address & 0xFFFF

    if bank in (0x7E, 0x7F):
        return None  # System WRAM

    if map_kind == "lorom":
        if off < 0x8000:
            return None
        pc = ((bank & 0x7F) * 0x8000) + (off & 0x7FFF)
        return pc if pc < rom_body_len else None

    if map_kind == "exlorom":
        if off < 0x8000:
            return None
        pc = ((bank & 0x7F) * 0x8000) + (off & 0x7FFF)
        if bank & 0x80:
            pc += 0x400000
        return pc if pc < rom_body_len else None

    if map_kind == "hirom":
        if 0x40 <= bank <= 0x7D or 0xC0 <= bank <= 0xFF:
            pc = ((bank & 0x3F) * 0x10000) + off
            return pc if pc < rom_body_len else None
        if off >= 0x8000:
            pc = ((bank & 0x3F) * 0x10000) + off
            return pc if pc < rom_body_len else None
        return None

    if map_kind == "exhirom":
        if 0xC0 <= bank <= 0xFF:
            pc = ((bank & 0x3F) * 0x10000) + off
            return pc if pc < rom_body_len else None
        if 0x80 <= bank <= 0xBF and off >= 0x8000:
            pc = ((bank & 0x3F) * 0x10000) + off
            return pc if pc < rom_body_len else None
        if 0x40 <= bank <= 0x7D:
            pc = 0x400000 + ((bank & 0x3F) * 0x10000) + off
            return pc if pc < rom_body_len else None
        if 0x00 <= bank <= 0x3F and off >= 0x8000:
            pc = 0x400000 + ((bank & 0x3F) * 0x10000) + off
            return pc if pc < rom_body_len else None
        return None

    return None


class SnesHandler(SystemHandler):
    """Super Nintendo (SNES / Super Famicom) ROM handler."""

    system = ConsoleSystem.SNES

    def detect_confidence(self, rom: bytes, filename: str | None = None) -> int:
        ext = Path(filename).suffix.lower() if filename else ""
        base = 512 if _has_smc_header(rom) else 0
        body = rom[base:]
        best_score = max(
            _score_snes_header(body, 0x007FC0, "lorom") if len(body) >= 0x8000 else -100,
            _score_snes_header(body, 0x00FFC0, "hirom") if len(body) >= 0x10000 else -100,
            _score_snes_header(body, 0x40FFC0, "exhirom") if len(body) > 0x410000 else -100,
        )
        if best_score >= 12:
            return 98 if ext in _SNES_EXTENSIONS else 92
        if best_score >= 8:
            return 90 if ext in _SNES_EXTENSIONS else 75
        if ext in _SNES_EXTENSIONS:
            return 78
        return 0

    def _decode_par(self, raw_code: str, body: str, description: str) -> DecodedCode:
        clean = clean_code_chars(body.replace(":", ""))
        if len(clean) != 8 or any(ch not in "0123456789ABCDEF" for ch in clean):
            raise CodeFormatError(
                f"Invalid SNES Pro Action Replay code '{raw_code}': "
                "expected 8 hex digits (e.g. PAR:808000EA or PAR:808000:EA)."
            )
        address = int(clean[:6], 16)
        data = int(clean[6:], 16)
        bank = (address >> 16) & 0xFF
        off = address & 0xFFFF
        if bank in (0x7E, 0x7F) or ((bank & 0x7F) <= 0x3F and off < 0x2000):
            raise CodeFormatError(
                f"SNES Pro Action Replay code '{raw_code}' targets WRAM "
                f"(0x{address:06X}), not cartridge ROM."
            )
        return DecodedCode(
            raw_code=raw_code.strip(),
            normalized_code=f"PAR:{address:06X}:{data:02X}",
            system=self.system,
            address=address,
            value=bytes([data]),
            compare=None,
            description=description,
        )

    def decode_code(self, code: str, description: str = "") -> DecodedCode:
        raw_upper = code.strip().upper()
        for prefix in ("PAR:", "AR:", "PAR ", "AR "):
            if raw_upper.startswith(prefix):
                return self._decode_par(code, raw_upper[len(prefix) :], description)

        clean = clean_code_chars(code)
        if len(clean) != 8:
            raise CodeFormatError(
                f"Invalid SNES Game Genie code '{code}': expected 8 characters (e.g. C222-D4DD)."
            )
        bad = [ch for ch in clean if ch not in _SNES_CHAR_TO_VAL]
        if bad:
            raise CodeFormatError(
                f"Invalid SNES Game Genie code '{code}': invalid character(s) "
                f"{', '.join(bad)}. Allowed characters: {SNES_ALPHABET}."
            )

        v = 0
        for ch in clean:
            v = (v << 4) | _SNES_CHAR_TO_VAL[ch]
        data = (v >> 24) & 0xFF
        e = v & 0xFFFFFF
        address = (
            (((e >> 10) & 0xF) << 20)
            | (((e >> 2) & 0xF) << 16)
            | (((e >> 20) & 0xF) << 12)
            | ((e & 0x3) << 10)
            | (((e >> 14) & 0x3) << 8)
            | (((e >> 16) & 0xF) << 4)
            | (((e >> 8) & 0x3) << 2)
            | ((e >> 6) & 0x3)
        )
        normalized = f"{clean[:4]}-{clean[4:]}"
        return DecodedCode(
            raw_code=code.strip(),
            normalized_code=normalized,
            system=self.system,
            address=address,
            value=bytes([data]),
            compare=None,
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
        if not 0 <= address <= 0xFFFFFF:
            raise CodeFormatError(
                f"Address 0x{address:06X} out of 24-bit SNES range (0x000000-0xFFFFFF)."
            )
        if not 0 <= value <= 0xFF:
            raise CodeFormatError(
                f"Replacement value 0x{value:X} must be an 8-bit byte (0x00-0xFF)."
            )
        if compare is not None:
            raise CodeFormatError("SNES Game Genie/PAR codes do not support a compare byte.")

        fmt = code_format.strip().lower()
        if fmt in {"par", "ar"}:
            return f"PAR:{address:06X}:{value:02X}"
        if fmt != "gg":
            raise CodeFormatError(
                f"Unsupported SNES code format '{code_format}'. Use 'gg' or 'par'."
            )

        enc_addr = (
            (((address >> 12) & 0xF) << 20)
            | (((address >> 4) & 0xF) << 16)
            | (((address >> 8) & 0x3) << 14)
            | (((address >> 20) & 0xF) << 10)
            | (((address >> 2) & 0x3) << 8)
            | ((address & 0x3) << 6)
            | (((address >> 16) & 0xF) << 2)
            | ((address >> 10) & 0x3)
        )
        v = ((value & 0xFF) << 24) | enc_addr
        chars = "".join(SNES_ALPHABET[(v >> (28 - 4 * i)) & 0xF] for i in range(8))
        return f"{chars[:4]}-{chars[4:]}"

    def resolve_candidates(
        self, rom: bytes, decoded: DecodedCode, **options: Any
    ) -> list[CandidateOffset]:
        snes_map = str(options.get("snes_map", "auto"))
        base, map_kind, _ = detect_snes_layout(rom, override=snes_map)
        body_len = len(rom) - base
        pc = _snes_addr_to_pc(decoded.address, map_kind, body_len)
        if pc is None:
            return []
        file_offset = base + pc
        if file_offset >= len(rom):
            return []
        cur = rom[file_offset : file_offset + 1]
        return [
            CandidateOffset(
                file_offset=file_offset,
                bank=(decoded.address >> 16) & 0xFF,
                current_bytes=cur,
                matches_compare=None,
                label=f"{map_kind.upper()} @ 0x{file_offset:06X}",
            )
        ]

    def verify_checksums(self, rom: bytes, **options: Any) -> list[ChecksumEntry]:
        snes_map = str(options.get("snes_map", "auto"))
        base, _, header_off = detect_snes_layout(rom, override=snes_map)
        body = rom[base:]
        if len(body) < header_off + 0x20:
            return []

        stored_comp = body[header_off + 0x1C] | (body[header_off + 0x1D] << 8)
        stored_chk = body[header_off + 0x1E] | (body[header_off + 0x1F] << 8)
        computed_chk = _compute_snes_checksum(body, header_off)
        computed_comp = computed_chk ^ 0xFFFF

        return [
            ChecksumEntry(
                name="SNES Complement",
                offset=base + header_off + 0x1C,
                stored=stored_comp,
                computed=computed_comp,
                width=2,
                valid=stored_comp == computed_comp,
            ),
            ChecksumEntry(
                name="SNES Checksum",
                offset=base + header_off + 0x1E,
                stored=stored_chk,
                computed=computed_chk,
                width=2,
                valid=stored_chk == computed_chk,
            ),
        ]

    def fix_checksums(self, rom: bytearray, **options: Any) -> list[ChecksumEntry]:
        snes_map = str(options.get("snes_map", "auto"))
        base, _, header_off = detect_snes_layout(bytes(rom), override=snes_map)
        if len(rom) - base < header_off + 0x20:
            return []
        computed_chk = _compute_snes_checksum(rom[base:], header_off)
        computed_comp = computed_chk ^ 0xFFFF
        abs_comp = base + header_off + 0x1C
        rom[abs_comp] = computed_comp & 0xFF
        rom[abs_comp + 1] = (computed_comp >> 8) & 0xFF
        rom[abs_comp + 2] = computed_chk & 0xFF
        rom[abs_comp + 3] = (computed_chk >> 8) & 0xFF
        return self.verify_checksums(bytes(rom), **options)

    def inspect_rom(self, rom: bytes, **options: Any) -> RomInfo:
        snes_map = str(options.get("snes_map", "auto"))
        base, map_kind, header_off = detect_snes_layout(rom, override=snes_map)
        body = rom[base:]
        title = "(Unknown)"
        if len(body) >= header_off + 21:
            parsed = read_ascii_title(body[header_off : header_off + 21])
            if parsed:
                title = parsed

        smc_note = " + 512B SMC header" if base == 512 else ""
        header_format = f"SNES {map_kind.upper()}{smc_note}"
        extra: dict[str, Any] = {
            "snes_map": map_kind,
            "smc_header": base == 512,
            "smc_header_bytes": base,
            "header_offset": base + header_off,
        }
        if len(body) >= header_off + 0x20:
            extra["map_mode_byte"] = f"0x{body[header_off + 0x15]:02X}"
            extra["rom_size_byte"] = f"0x{body[header_off + 0x17]:02X}"

        return RomInfo(
            system=self.system,
            file_size=len(rom),
            title=title,
            header_format=header_format,
            checksums=self.verify_checksums(rom, **options),
            extra=extra,
        )
