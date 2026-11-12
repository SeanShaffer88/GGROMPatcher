"""Base interface and shared utilities for console ROM systems."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ggrompatcher.models import (
    CandidateOffset,
    ChecksumEntry,
    ConsoleSystem,
    DecodedCode,
    RomInfo,
)


class SystemHandler(ABC):
    """Abstract base class for console-specific Game Genie codecs and ROM handlers."""

    system: ConsoleSystem

    @abstractmethod
    def detect_confidence(self, rom: bytes, filename: str | None = None) -> int:
        """Return a confidence score in [0, 100] that `rom` belongs to this console."""

    @abstractmethod
    def inspect_rom(self, rom: bytes, **options: Any) -> RomInfo:
        """Parse header metadata and current checksum status."""

    @abstractmethod
    def decode_code(self, code: str, description: str = "") -> DecodedCode:
        """Decode a Game Genie code string into address, value, and optional compare."""

    @abstractmethod
    def encode_code(self, address: int, value: int, compare: int | None = None) -> str:
        """Encode address, replacement value, and optional compare byte into a code."""

    @abstractmethod
    def resolve_candidates(
        self, rom: bytes, decoded: DecodedCode, **options: Any
    ) -> list[CandidateOffset]:
        """Map a decoded code's address into candidate ROM file offsets."""

    def verify_checksums(self, rom: bytes, **options: Any) -> list[ChecksumEntry]:
        """Verify ROM checksums without modifying `rom`."""
        return []

    def fix_checksums(self, rom: bytearray, **options: Any) -> list[ChecksumEntry]:
        """Recalculate and update ROM checksums in place, returning updated entries."""
        return []

    def normalize_input_rom(
        self, rom: bytes, filename: str | None = None
    ) -> tuple[bytes, dict[str, Any]]:
        """Optionally convert container formats (e.g. SMD) into flat patchable ROM bytes."""
        return rom, {}

    def finalize_output_rom(
        self, rom: bytes, context: dict[str, Any], output_filename: str | None = None
    ) -> bytes:
        """Optionally re-pack ROM bytes if requested by output extension or container."""
        return rom


def clean_code_chars(code: str) -> str:
    """Strip whitespace and hyphens and uppercase a code string."""
    return "".join(ch for ch in code.strip() if ch not in {"-", " ", "\t", "_"}).upper()


def read_ascii_title(data: bytes) -> str:
    """Extract a readable ASCII title from cartridge header bytes."""
    chars: list[str] = []
    for b in data:
        if 0x20 <= b <= 0x7E:
            chars.append(chr(b))
        elif b == 0:
            break
    return "".join(chars).strip()


def rol8(value: int, bits: int) -> int:
    """Rotate an 8-bit integer left by `bits` positions."""
    value &= 0xFF
    bits %= 8
    return ((value << bits) | (value >> (8 - bits))) & 0xFF


def ror8(value: int, bits: int) -> int:
    """Rotate an 8-bit integer right by `bits` positions."""
    value &= 0xFF
    bits %= 8
    return ((value >> bits) | (value << (8 - bits))) & 0xFF
