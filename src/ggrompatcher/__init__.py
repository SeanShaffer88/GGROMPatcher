"""GGROMPatcher — Multi-system Game Genie ROM patcher, encoder/decoder, and IPS tool."""

from ggrompatcher.cheatfiles import CheatEntry, load_cheat_file, parse_cheat_text, parse_code_tokens
from ggrompatcher.ips import apply_ips, create_ips, write_ips_file
from ggrompatcher.models import (
    CandidateOffset,
    ChecksumEntry,
    CodeFormatError,
    ConsoleSystem,
    DecodedCode,
    GGPatcherError,
    IpsFormatError,
    MatchStatus,
    PatchResult,
    PlannedPatch,
    RomFormatError,
    RomInfo,
)
from ggrompatcher.patcher import (
    apply_patches,
    decode_code,
    default_patched_path,
    encode_code,
    fix_rom_checksums,
    inspect_rom,
    patch_rom_file,
    plan_patches,
)
from ggrompatcher.systems import detect_system, get_handler

__all__ = [
    "CandidateOffset",
    "CheatEntry",
    "ChecksumEntry",
    "CodeFormatError",
    "ConsoleSystem",
    "DecodedCode",
    "GGPatcherError",
    "IpsFormatError",
    "MatchStatus",
    "PatchResult",
    "PlannedPatch",
    "RomFormatError",
    "RomInfo",
    "apply_ips",
    "apply_patches",
    "create_ips",
    "decode_code",
    "default_patched_path",
    "detect_system",
    "encode_code",
    "fix_rom_checksums",
    "get_handler",
    "inspect_rom",
    "load_cheat_file",
    "parse_cheat_text",
    "parse_code_tokens",
    "patch_rom_file",
    "plan_patches",
    "write_ips_file",
]
