"""Sega Genesis / Mega Drive Game Genie codec, SMD deinterleaver, and checksum support."""

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
from ggrompatcher.systems.base import SystemHandler, clean_code_chars, read_ascii_title

GENESIS_ALPHABET = "ABCDEFGHJKLMNPRSTVWXYZ0123456789"
_GEN_CHAR_TO_VAL = {ch: idx for idx, ch in enumerate(GENESIS_ALPHABET)}
_GEN_EXTENSIONS = {".bin", ".md", ".gen", ".68k", ".smd"}
_SMD_BLOCK = 16384
_SMD_HALF = 8192


def _has_sega_magic(plain: bytes) -> bool:
    if len(plain) < 0x110:
        return False
    hdr = plain[0x100:0x110]
    return hdr.startswith(b"SEGA") or hdr.startswith(b" SEGA")


def _deinterleave_block(block: bytes, even_first: bool) -> bytes:
    out = bytearray(_SMD_BLOCK)
    if even_first:
        out[0::2] = block[:_SMD_HALF]
        out[1::2] = block[_SMD_HALF:]
    else:
        out[0::2] = block[_SMD_HALF:]
        out[1::2] = block[:_SMD_HALF]
    return bytes(out)


def _interleave_block(block: bytes, even_first: bool) -> bytes:
    out = bytearray(_SMD_BLOCK)
    if even_first:
        out[:_SMD_HALF] = block[0::2]
        out[_SMD_HALF:] = block[1::2]
    else:
        out[:_SMD_HALF] = block[1::2]
        out[_SMD_HALF:] = block[0::2]
    return bytes(out)


def detect_smd_layout(rom: bytes) -> str | None:
    """Return `'even_first'` or `'odd_first'` if `rom` is Super Magic Drive interleaved."""
    if len(rom) < 512 + _SMD_BLOCK or (len(rom) - 512) % _SMD_BLOCK != 0:
        return None
    if _has_sega_magic(rom):
        return None

    first_block = rom[512 : 512 + _SMD_BLOCK]
    for layout, even_first in (("even_first", True), ("odd_first", False)):
        decoded_block = _deinterleave_block(first_block, even_first=even_first)
        if _has_sega_magic(decoded_block):
            return layout
    return None


def deinterleave_smd(rom: bytes, layout: str = "even_first") -> bytes:
    """Convert an SMD interleaved ROM (512-byte header + 16KB blocks) to plain binary."""
    even_first = layout == "even_first"
    out = bytearray()
    for off in range(512, len(rom), _SMD_BLOCK):
        out.extend(_deinterleave_block(rom[off : off + _SMD_BLOCK], even_first=even_first))
    return bytes(out)


def interleave_smd(plain: bytes, layout: str = "even_first", header: bytes | None = None) -> bytes:
    """Convert a plain binary Genesis ROM to SMD format with a 512-byte copier header."""
    if len(plain) % _SMD_BLOCK != 0:
        pad = _SMD_BLOCK - (len(plain) % _SMD_BLOCK)
        plain = plain + b"\x00" * pad

    even_first = layout == "even_first"
    blocks = len(plain) // _SMD_BLOCK
    if header is not None and len(header) == 512:
        hdr = bytearray(header)
    else:
        hdr = bytearray(512)
        hdr[0] = blocks & 0xFF
        hdr[1] = 0x03
        hdr[2] = 0x00
        hdr[8] = 0xAA
        hdr[9] = 0xBB
        hdr[10] = 0x06

    out = bytearray(hdr)
    for off in range(0, len(plain), _SMD_BLOCK):
        out.extend(_interleave_block(plain[off : off + _SMD_BLOCK], even_first=even_first))
    return bytes(out)


def _compute_genesis_checksum(rom: bytes) -> int:
    total = 0
    end = len(rom)
    for off in range(0x0200, end - 1, 2):
        total += (rom[off] << 8) | rom[off + 1]
    if (end - 0x0200) % 2 == 1 and end > 0x0200:
        total += rom[-1] << 8
    return total & 0xFFFF


class GenesisHandler(SystemHandler):
    """Sega Genesis / Mega Drive ROM handler."""

    system = ConsoleSystem.GENESIS

    def detect_confidence(self, rom: bytes, filename: str | None = None) -> int:
        ext = Path(filename).suffix.lower() if filename else ""
        if _has_sega_magic(rom):
            return 99 if ext in _GEN_EXTENSIONS else 96
        if detect_smd_layout(rom) is not None:
            return 97
        if ext in _GEN_EXTENSIONS:
            return 70
        return 0

    def normalize_input_rom(
        self, rom: bytes, filename: str | None = None
    ) -> tuple[bytes, dict[str, Any]]:
        smd_layout = detect_smd_layout(rom)
        if smd_layout is not None:
            return deinterleave_smd(rom, layout=smd_layout), {
                "smd_interleaved": True,
                "smd_layout": smd_layout,
                "smd_header": rom[:512],
            }
        return rom, {"smd_interleaved": False}

    def finalize_output_rom(
        self, rom: bytes, context: dict[str, Any], output_filename: str | None = None
    ) -> bytes:
        if (
            context.get("smd_interleaved")
            and output_filename
            and Path(output_filename).suffix.lower() == ".smd"
        ):
            return interleave_smd(
                rom,
                layout=str(context.get("smd_layout", "even_first")),
                header=context.get("smd_header"),
            )
        return rom

    def decode_code(self, code: str, description: str = "") -> DecodedCode:
        clean = clean_code_chars(code)
        if len(clean) != 8:
            raise CodeFormatError(
                f"Invalid Genesis Game Genie code '{code}': expected 8 characters (e.g. ATBT-AA32)."
            )
        bad = [ch for ch in clean if ch not in _GEN_CHAR_TO_VAL]
        if bad:
            raise CodeFormatError(
                f"Invalid Genesis Game Genie code '{code}': invalid character(s) "
                f"{', '.join(bad)}. Allowed characters: {GENESIS_ALPHABET}."
            )

        v = 0
        for ch in clean:
            v = (v << 5) | _GEN_CHAR_TO_VAL[ch]

        clear = (
            (((v >> 16) & 0xFF) << 32)
            | (((v >> 24) & 0xFF) << 24)
            | ((v & 0xFF) << 16)
            | (((v >> 8) & 0x7) << 13)
            | (((v >> 11) & 0x1F) << 8)
            | ((v >> 32) & 0xFF)
        )
        address = (clear >> 16) & 0xFFFFFF
        word = clear & 0xFFFF

        warnings: list[str] = []
        if address % 2 != 0:
            warnings.append(
                f"Address 0x{address:06X} is odd; 68000 word patches normally target even offsets."
            )

        normalized = f"{clean[:4]}-{clean[4:]}"
        return DecodedCode(
            raw_code=code.strip(),
            normalized_code=normalized,
            system=self.system,
            address=address,
            value=bytes([(word >> 8) & 0xFF, word & 0xFF]),
            compare=None,
            description=description,
            warnings=tuple(warnings),
        )

    def encode_code(self, address: int, value: int, compare: int | None = None) -> str:
        if not 0 <= address <= 0xFFFFFF:
            raise CodeFormatError(
                f"Address 0x{address:06X} out of 24-bit Genesis range (0x000000-0xFFFFFF)."
            )
        if not 0 <= value <= 0xFFFF:
            raise CodeFormatError(
                f"Replacement value 0x{value:X} must be a 16-bit word (0x0000-0xFFFF)."
            )
        if compare is not None:
            raise CodeFormatError("Genesis Game Genie codes do not support a compare value.")

        clear = ((address & 0xFFFFFF) << 16) | (value & 0xFFFF)
        v = (
            ((clear & 0xFF) << 32)
            | (((clear >> 24) & 0xFF) << 24)
            | (((clear >> 32) & 0xFF) << 16)
            | (((clear >> 8) & 0x1F) << 11)
            | (((clear >> 13) & 0x7) << 8)
            | ((clear >> 16) & 0xFF)
        )
        chars = "".join(GENESIS_ALPHABET[(v >> (35 - 5 * i)) & 0x1F] for i in range(8))
        return f"{chars[:4]}-{chars[4:]}"

    def resolve_candidates(
        self, rom: bytes, decoded: DecodedCode, **options: Any
    ) -> list[CandidateOffset]:
        off = decoded.address
        if off < 0 or off + 2 > len(rom):
            return []
        cur = rom[off : off + 2]
        return [
            CandidateOffset(
                file_offset=off,
                bank=None,
                current_bytes=cur,
                matches_compare=None,
                label=f"ROM @ 0x{off:06X}",
            )
        ]

    def verify_checksums(self, rom: bytes, **options: Any) -> list[ChecksumEntry]:
        if len(rom) < 0x0202:
            return []
        stored = (rom[0x018E] << 8) | rom[0x018F]
        computed = _compute_genesis_checksum(rom)
        return [
            ChecksumEntry(
                name="Genesis Checksum",
                offset=0x018E,
                stored=stored,
                computed=computed,
                width=2,
                valid=stored == computed,
            )
        ]

    def fix_checksums(self, rom: bytearray, **options: Any) -> list[ChecksumEntry]:
        if len(rom) < 0x0202:
            return []
        computed = _compute_genesis_checksum(bytes(rom))
        rom[0x018E] = (computed >> 8) & 0xFF
        rom[0x018F] = computed & 0xFF
        return self.verify_checksums(bytes(rom))

    def inspect_rom(self, rom: bytes, **options: Any) -> RomInfo:
        smd_layout = options.get("smd_layout")
        if smd_layout:
            header_fmt = f"Genesis / Mega Drive SMD ({smd_layout})"
        else:
            header_fmt = "Genesis / Mega Drive Binary"

        title = "(Unknown)"
        extra: dict[str, Any] = {
            "smd_interleaved": bool(options.get("smd_interleaved", False)),
        }
        if len(rom) >= 0x200:
            sys_name = read_ascii_title(rom[0x0100:0x0110])
            domestic = read_ascii_title(rom[0x0120:0x0150])
            overseas = read_ascii_title(rom[0x0150:0x0180])
            title = overseas or domestic or "(Unknown)"
            serial = read_ascii_title(rom[0x0180:0x018E])
            region = read_ascii_title(rom[0x01F0:0x0200])
            extra["system_tag"] = sys_name
            extra["serial"] = serial
            extra["region"] = region

        return RomInfo(
            system=self.system,
            file_size=len(rom),
            title=title,
            header_format=header_fmt,
            checksums=self.verify_checksums(rom),
            extra=extra,
        )
