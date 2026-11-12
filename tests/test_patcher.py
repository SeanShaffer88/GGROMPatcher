"""Tests for patch planning, bank resolution, validation, conflict handling, and file patching."""

from __future__ import annotations

from pathlib import Path

import pytest

from ggrompatcher.models import ConsoleSystem, GGPatcherError, MatchStatus
from ggrompatcher.patcher import apply_patches, encode_code, patch_rom_file, plan_patches
from tests.conftest import (
    make_gb_rom,
    make_genesis_rom,
    make_nes_rom,
    make_sega8_rom,
    make_snes_rom,
)


def test_gameboy_bank_scanning_compare_match_and_checksum_fix() -> None:
    # 64 KB Game Boy ROM (4 banks). Code target at CPU $515C -> offset 0x115C inside bank.
    # Place compare byte 0x02 ONLY in Bank 2 (file offset 2*0x4000 + 0x115C = 0x915C).
    rom = make_gb_rom(banks=4)
    target_off = 2 * 0x4000 + 0x115C
    rom[target_off] = 0x02

    res = apply_patches(bytes(rom), ["001-5CA-E62"], strict=True)
    assert len(res.applied_patches) == 1
    assert res.applied_patches[0].selected_offsets == [target_off]
    assert res.output_bytes[target_off] == 0x00
    assert res.bytes_modified == 1
    assert all(c.valid for c in res.rom_info_after.checksums)


def test_gameboy_ambiguous_multi_bank_compare_and_overrides() -> None:
    rom = make_gb_rom(banks=4)
    off1 = 1 * 0x4000 + 0x115C
    off3 = 3 * 0x4000 + 0x115C
    rom[off1] = 0x02
    rom[off3] = 0x02

    # Default: ambiguous
    _, _, _, plans = plan_patches(bytes(rom), ["001-5CA-E62"])
    assert plans[0].status == MatchStatus.AMBIGUOUS
    assert not plans[0].can_apply

    with pytest.raises(GGPatcherError, match="validation failed"):
        apply_patches(bytes(rom), ["001-5CA-E62"], strict=True)

    # Disambiguate with --bank 3
    res_bank3 = apply_patches(bytes(rom), ["001-5CA-E62"], bank=3, strict=True)
    assert res_bank3.output_bytes[off3] == 0x00
    assert res_bank3.output_bytes[off1] == 0x02

    # Disambiguate with all_matches=True
    res_all = apply_patches(bytes(rom), ["001-5CA-E62"], all_matches=True, strict=True)
    assert res_all.output_bytes[off1] == 0x00
    assert res_all.output_bytes[off3] == 0x00


def test_compare_mismatch_and_force_and_already_patched() -> None:
    rom = make_gb_rom(banks=2)
    rom[0x14BC] = 0x99  # Code 004-BCE-E66 expects 0x03

    _, _, _, plans = plan_patches(bytes(rom), ["004-BCE-E66"])
    assert plans[0].status == MatchStatus.COMPARE_MISMATCH
    assert not plans[0].can_apply

    forced = apply_patches(bytes(rom), ["004-BCE-E66"], force=True, strict=True)
    assert forced.output_bytes[0x14BC] == 0x00
    assert all(c.valid for c in forced.rom_info_after.checksums)

    # Applying again detects ALREADY_PATCHED
    _, _, _, replans = plan_patches(forced.output_bytes, ["004-BCE-E66"])
    assert replans[0].status == MatchStatus.ALREADY_PATCHED
    assert replans[0].can_apply


def test_nes_mapper4_8k_and_nrom_16k_mirror() -> None:
    # NROM-128 (16 KB PRG): $91D9 mirrors into $11D9 -> file offset 16 + 0x11D9
    nrom = make_nes_rom(prg_banks_16k=1, mapper=0)
    res_nrom = apply_patches(bytes(nrom), ["SXIOPO"], strict=True)
    assert res_nrom.output_bytes[16 + 0x11D9] == 0xAD

    # MMC3 (Mapper 4, 8 KB banks) with trainer (512B): SXULKNSE -> 0xBFB4, val 0xAD, cmp 0x8D
    mmc3 = make_nes_rom(prg_banks_16k=8, mapper=4, trainer=True)
    prg_start = 16 + 512
    # Place compare byte 0x8D inside 8 KB bank #5 at offset (0xBFB4 - 0x8000) & 0x1FFF = 0x1FB4
    target = prg_start + 5 * 0x2000 + 0x1FB4
    mmc3[target] = 0x8D
    res_mmc3 = apply_patches(bytes(mmc3), ["SXULKNSE"], strict=True)
    assert res_mmc3.output_bytes[target] == 0xAD


def test_snes_lorom_smc_and_hirom_patching() -> None:
    # SMW C222-D4DD -> CPU 0x00D0D8 -> LoROM PC 0x50D8 (+ 512 SMC = 0x52D8)
    snes_lo = make_snes_rom(size=0x80000, hirom=False, smc_header=True)
    res_lo = apply_patches(bytes(snes_lo), ["C222-D4DD"], strict=True)
    assert res_lo.output_bytes[512 + 0x50D8] == 0xAD
    assert all(c.valid for c in res_lo.rom_info_after.checksums)

    # HiROM: code at $C4:A704 = ABCD-EFFF -> value 0xC9 -> PC 0x04A704
    snes_hi = make_snes_rom(size=0x100000, hirom=True, smc_header=False)
    res_hi = apply_patches(bytes(snes_hi), ["ABCD-EFFF"], strict=True)
    assert res_hi.output_bytes[0x04A704] == 0xC9
    assert all(c.valid for c in res_hi.rom_info_after.checksums)


def test_genesis_plain_and_smd_roundtrip_and_checksum() -> None:
    # ATBT-AA32 -> 0x000338:6004
    gen_bin = make_genesis_rom(size=0x20000)
    res_bin = apply_patches(bytes(gen_bin), ["ATBT-AA32"], strict=True)
    assert res_bin.output_bytes[0x000338:0x00033A] == b"\x60\x04"
    assert all(c.valid for c in res_bin.rom_info_after.checksums)

    # SMD input saved back as .smd keeps valid SMD structure and updated checksum upon reload
    gen_smd = make_genesis_rom(size=0x20000, smd_layout="even_first")
    res_smd = apply_patches(bytes(gen_smd), ["ATBT-AA32"], output_filename="out.smd", strict=True)
    _, _, _, check_plans = plan_patches(res_smd.output_bytes, ["ATBT-AA32"])
    assert check_plans[0].status == MatchStatus.ALREADY_PATCHED


def test_gamegear_slot2_8000_range_patching() -> None:
    gg = make_sega8_rom(banks=8, region_code=0x6)
    code = encode_code(ConsoleSystem.GAMEGEAR, 0x8234, 0xEA, 0x42)
    off = 5 * 0x4000 + 0x0234
    gg[off] = 0x42
    res = apply_patches(bytes(gg), [code], strict=True)
    assert res.output_bytes[off] == 0xEA
    assert all(c.valid for c in res.rom_info_after.checksums)


def test_overlapping_patch_warning() -> None:
    rom = make_gb_rom(banks=2)
    res = apply_patches(bytes(rom), ["011-23E", "FF1-23E"], strict=True)
    assert res.output_bytes[0x1123] == 0xFF
    assert any("overwritten" in w.lower() for w in res.warnings)


def test_patch_rom_file_inplace_backup_and_ips_export(tmp_path: Path) -> None:
    rom = make_gb_rom(banks=2)
    rom[0x14BC] = 0xFF
    rom_bytes = bytes(rom)
    rom_path = tmp_path / "game.gb"
    rom_path.write_bytes(rom_bytes)
    ips_path = tmp_path / "game.ips"

    code = encode_code(ConsoleSystem.GAMEBOY, 0x14BC, 0x00)
    res, out_path = patch_rom_file(
        rom_path,
        [code],
        in_place=True,
        backup=True,
        ips_output_path=ips_path,
        strict=True,
    )
    assert out_path == rom_path
    assert (tmp_path / "game.gb.bak").read_bytes() == rom_bytes
    assert rom_path.read_bytes()[0x14BC] == 0x00
    assert ips_path.is_file()
    assert res.bytes_modified >= 1
