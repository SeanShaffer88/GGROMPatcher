"""Core data models and exceptions for GGROMPatcher."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class GGPatcherError(Exception):
    """Base exception for all GGROMPatcher errors."""


class CodeFormatError(GGPatcherError):
    """Raised when a Game Genie or raw patch code cannot be parsed or encoded."""


class RomFormatError(GGPatcherError):
    """Raised when a ROM file is invalid or incompatible with the requested operation."""


class IpsFormatError(GGPatcherError):
    """Raised when an IPS patch file is malformed or cannot be generated."""


class ConsoleSystem(str, Enum):
    """Supported retro console systems and raw mode."""

    GAMEBOY = "gameboy"
    GAMEGEAR = "gamegear"
    MASTERSYSTEM = "mastersystem"
    NES = "nes"
    SNES = "snes"
    GENESIS = "genesis"
    RAW = "raw"

    @property
    def display_name(self) -> str:
        names = {
            ConsoleSystem.GAMEBOY: "Game Boy / GBC",
            ConsoleSystem.GAMEGEAR: "Sega Game Gear",
            ConsoleSystem.MASTERSYSTEM: "Sega Master System",
            ConsoleSystem.NES: "NES / Famicom",
            ConsoleSystem.SNES: "Super Nintendo (SNES)",
            ConsoleSystem.GENESIS: "Sega Genesis / Mega Drive",
            ConsoleSystem.RAW: "Raw Hex (File Offset)",
        }
        return names[self]

    @property
    def has_checksum(self) -> bool:
        return self in {
            ConsoleSystem.GAMEBOY,
            ConsoleSystem.GAMEGEAR,
            ConsoleSystem.MASTERSYSTEM,
            ConsoleSystem.SNES,
            ConsoleSystem.GENESIS,
        }

    @classmethod
    def from_str(cls, raw: str) -> ConsoleSystem:
        key = raw.strip().lower().replace("-", "_").replace(" ", "_")
        aliases: dict[str, ConsoleSystem] = {
            "gb": cls.GAMEBOY,
            "gbc": cls.GAMEBOY,
            "sgb": cls.GAMEBOY,
            "gameboy": cls.GAMEBOY,
            "game_boy": cls.GAMEBOY,
            "game_boy_color": cls.GAMEBOY,
            "gg": cls.GAMEGEAR,
            "gamegear": cls.GAMEGEAR,
            "game_gear": cls.GAMEGEAR,
            "sms": cls.MASTERSYSTEM,
            "mastersystem": cls.MASTERSYSTEM,
            "master_system": cls.MASTERSYSTEM,
            "mark3": cls.MASTERSYSTEM,
            "nes": cls.NES,
            "fc": cls.NES,
            "famicom": cls.NES,
            "nintendo": cls.NES,
            "snes": cls.SNES,
            "sfc": cls.SNES,
            "smc": cls.SNES,
            "super_nintendo": cls.SNES,
            "supernintendo": cls.SNES,
            "super_nes": cls.SNES,
            "gen": cls.GENESIS,
            "genesis": cls.GENESIS,
            "md": cls.GENESIS,
            "megadrive": cls.GENESIS,
            "mega_drive": cls.GENESIS,
            "smd": cls.GENESIS,
            "raw": cls.RAW,
            "hex": cls.RAW,
        }
        if key in aliases:
            return aliases[key]
        supported = ", ".join(s.value for s in cls)
        raise ValueError(f"Unknown system '{raw}'. Supported systems: {supported}")


class MatchStatus(str, Enum):
    """Status of resolving a decoded Game Genie code against a specific ROM."""

    READY = "ready"
    ALREADY_PATCHED = "already_patched"
    COMPARE_MISMATCH = "compare_mismatch"
    AMBIGUOUS = "ambiguous"
    OUT_OF_RANGE = "out_of_range"


@dataclass(frozen=True)
class DecodedCode:
    """Decoded Game Genie (or raw hex) patch code."""

    raw_code: str
    normalized_code: str
    system: ConsoleSystem
    address: int
    value: bytes
    compare: bytes | None = None
    description: str = ""
    warnings: tuple[str, ...] = ()

    @property
    def value_int(self) -> int:
        return int.from_bytes(self.value, "big")

    @property
    def compare_int(self) -> int | None:
        return int.from_bytes(self.compare, "big") if self.compare is not None else None


@dataclass(frozen=True)
class CandidateOffset:
    """One potential ROM file offset where a decoded code could apply."""

    file_offset: int
    bank: int | None
    current_bytes: bytes
    matches_compare: bool | None
    label: str


@dataclass
class PlannedPatch:
    """Resolution plan for a single code against a loaded ROM."""

    code: DecodedCode
    candidates: list[CandidateOffset] = field(default_factory=list)
    selected_offsets: list[int] = field(default_factory=list)
    status: MatchStatus = MatchStatus.OUT_OF_RANGE
    message: str = ""
    can_apply: bool = False


@dataclass(frozen=True)
class ChecksumEntry:
    """Header or global ROM checksum status."""

    name: str
    offset: int
    stored: int
    computed: int
    width: int
    valid: bool


@dataclass
class RomInfo:
    """Inspected metadata and checksum state for a ROM image."""

    system: ConsoleSystem
    file_size: int
    title: str
    header_format: str
    checksums: list[ChecksumEntry] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class PatchResult:
    """Complete result after planning or applying patches to a ROM."""

    rom_info_before: RomInfo
    rom_info_after: RomInfo
    planned_patches: list[PlannedPatch]
    applied_patches: list[PlannedPatch]
    skipped_patches: list[PlannedPatch]
    checksums_updated: list[ChecksumEntry]
    bytes_modified: int
    output_bytes: bytes
    warnings: list[str] = field(default_factory=list)
