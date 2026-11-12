"""Tests for console auto-detection and ROM header inspection."""

from __future__ import annotations

from ggrompatcher.models import ConsoleSystem
from ggrompatcher.patcher import inspect_rom
from ggrompatcher.systems import detect_system
from ggrompatcher.systems.genesis import deinterleave_smd
from tests.conftest import (
    make_gb_rom,
    make_genesis_rom,
    make_nes_rom,
    make_sega8_rom,
    make_snes_rom,
)


def test_detect_gameboy_by_logo_without_extension() -> None:
    rom = bytes(make_gb_rom(banks=2, title="LINK", cgb=0x80))
    sys_type, conf = detect_system(rom)
    assert sys_type == ConsoleSystem.GAMEBOY
    assert conf == 100
    info = inspect_rom(rom)
    assert info.title == "LINK"
    assert "CGB" in info.header_format


def test_detect_gamegear_and_sms_from_tmr_header() -> None:
    gg_rom = bytes(make_sega8_rom(banks=4, region_code=0x6))
    sms_rom = bytes(make_sega8_rom(banks=2, region_code=0x4))

    sys_gg, _ = detect_system(gg_rom)
    sys_sms, _ = detect_system(sms_rom)
    assert sys_gg == ConsoleSystem.GAMEGEAR
    assert sys_sms == ConsoleSystem.MASTERSYSTEM


def test_detect_nes_ines_and_nes2_and_trainer() -> None:
    ines = bytes(make_nes_rom(prg_banks_16k=4, mapper=1, trainer=True))
    sys_type, conf = detect_system(ines)
    assert sys_type == ConsoleSystem.NES
    assert conf == 100
    info = inspect_rom(ines)
    assert info.extra["mapper"] == 1
    assert info.extra["has_trainer"] is True
    assert info.extra["prg_start"] == 16 + 512
    assert info.extra["prg_size"] == 4 * 0x4000

    nes2 = bytes(make_nes_rom(prg_banks_16k=8, mapper=260, nes2=True))
    info2 = inspect_rom(nes2)
    assert info2.extra["mapper"] == 260
    assert info2.extra["is_nes2"] is True


def test_detect_snes_lorom_hirom_exhirom_and_smc() -> None:
    lo = bytes(make_snes_rom(size=0x80000, hirom=False, smc_header=True, title="MARIO WORLD"))
    hi = bytes(make_snes_rom(size=0x100000, hirom=True, smc_header=False, title="CHRONO TEST"))
    exhi = bytes(make_snes_rom(size=0x600000, exhirom=True, title="TALES EXHIROM"))

    assert detect_system(lo)[0] == ConsoleSystem.SNES
    info_lo = inspect_rom(lo)
    assert info_lo.extra["snes_map"] == "lorom"
    assert info_lo.extra["smc_header"] is True
    assert info_lo.title == "MARIO WORLD"

    assert detect_system(hi)[0] == ConsoleSystem.SNES
    info_hi = inspect_rom(hi)
    assert info_hi.extra["snes_map"] == "hirom"
    assert info_hi.extra["smc_header"] is False

    assert detect_system(exhi)[0] == ConsoleSystem.SNES
    info_ex = inspect_rom(exhi)
    assert info_ex.extra["snes_map"] == "exhirom"


def test_detect_genesis_plain_and_both_smd_layouts() -> None:
    plain = bytes(make_genesis_rom(size=0x20000, title="SONIC BIN"))
    smd_even = bytes(
        make_genesis_rom(size=0x20000, title="SONIC SMD EVEN", smd_layout="even_first")
    )
    smd_odd = bytes(make_genesis_rom(size=0x20000, title="SONIC SMD ODD", smd_layout="odd_first"))

    assert detect_system(plain)[0] == ConsoleSystem.GENESIS
    assert detect_system(smd_even)[0] == ConsoleSystem.GENESIS
    assert detect_system(smd_odd)[0] == ConsoleSystem.GENESIS

    info_even = inspect_rom(smd_even)
    info_odd = inspect_rom(smd_odd)
    assert info_even.title == "SONIC SMD EVEN"
    assert info_odd.title == "SONIC SMD ODD"
    assert info_even.extra["smd_interleaved"] is True
    assert info_odd.extra["smd_interleaved"] is True

    # Confirm wrong deinterleave direction scrambles SEGA into ESAG.
    wrong = deinterleave_smd(smd_even, layout="odd_first")
    assert wrong[0x0100:0x0104] == b"ESAG"
