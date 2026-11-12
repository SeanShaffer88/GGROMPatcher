"""Shared synthetic ROM builders for GGROMPatcher tests."""

from __future__ import annotations

import pytest

from ggrompatcher.systems.gameboy import NINTENDO_LOGO, GameBoyHandler
from ggrompatcher.systems.gamegear import GameGearHandler, MasterSystemHandler
from ggrompatcher.systems.gba import GBA_NINTENDO_LOGO, GbaHandler
from ggrompatcher.systems.genesis import GenesisHandler, interleave_smd
from ggrompatcher.systems.snes import SnesHandler


def make_gba_rom(
    *,
    size: int = 0x20000,
    title: str = "GOLDEN TEST",
    game_code: str = "AGFE",
    maker_code: str = "01",
    version: int = 0,
    fix_chk: bool = True,
) -> bytearray:
    rom = bytearray(max(size, 0xC0))
    rom[0x00:0x04] = b"\x2e\x00\x00\xea"  # ARM B entry branch
    rom[0x04:0xA0] = GBA_NINTENDO_LOGO
    rom[0xA0:0xAC] = title.encode("ascii")[:12].ljust(12, b"\x00")
    rom[0xAC:0xB0] = game_code.encode("ascii")[:4].ljust(4, b"\x00")
    rom[0xB0:0xB2] = maker_code.encode("ascii")[:2].ljust(2, b"0")
    rom[0xB2] = 0x96
    rom[0xBC] = version & 0xFF
    if fix_chk:
        GbaHandler().fix_checksums(rom)
    return rom


def make_gb_rom(
    *,
    banks: int = 2,
    title: str = "TESTGB",
    cgb: int = 0x00,
    fix_chk: bool = True,
) -> bytearray:
    rom = bytearray(banks * 0x4000)
    rom[0x0104:0x0134] = NINTENDO_LOGO
    t = title.encode("ascii")[:15]
    rom[0x0134 : 0x0134 + len(t)] = t
    rom[0x0143] = cgb
    rom[0x0148] = {2: 0x00, 4: 0x01, 8: 0x02}.get(banks, 0x03)
    if fix_chk:
        GameBoyHandler().fix_checksums(rom)
    return rom


def make_sega8_rom(
    *,
    banks: int = 2,
    region_code: int = 0x6,
    size_nibble: int | None = None,
    fix_chk: bool = True,
) -> bytearray:
    rom = bytearray(banks * 0x4000)
    hdr = 0x7FF0 if len(rom) >= 0x8000 else (0x3FF0 if len(rom) >= 0x4000 else 0x1FF0)
    rom[hdr : hdr + 8] = b"TMR SEGA"
    if size_nibble is None:
        size_nibble = {2: 0xC, 4: 0xE, 8: 0xF, 16: 0x0}.get(banks, 0xC)
    rom[hdr + 0x0F] = ((region_code & 0xF) << 4) | (size_nibble & 0xF)
    if fix_chk:
        handler = GameGearHandler() if region_code in (5, 6, 7) else MasterSystemHandler()
        handler.fix_checksums(rom)
    return rom


def make_nes_rom(
    *,
    prg_banks_16k: int = 2,
    chr_banks_8k: int = 1,
    mapper: int = 0,
    trainer: bool = False,
    nes2: bool = False,
) -> bytearray:
    hdr = bytearray(16)
    hdr[0:4] = b"NES\x1a"
    hdr[4] = prg_banks_16k & 0xFF
    hdr[5] = chr_banks_8k & 0xFF
    hdr[6] = ((mapper & 0x0F) << 4) | (0x04 if trainer else 0x00)
    hdr[7] = (mapper & 0xF0) | (0x08 if nes2 else 0x00)
    if nes2:
        hdr[8] = (mapper >> 8) & 0x0F
        hdr[9] = ((prg_banks_16k >> 8) & 0x0F) | (((chr_banks_8k >> 8) & 0x0F) << 4)
    body_len = (512 if trainer else 0) + prg_banks_16k * 0x4000 + chr_banks_8k * 0x2000
    return hdr + bytearray(body_len)


def make_snes_rom(
    *,
    size: int = 0x80000,
    hirom: bool = False,
    exhirom: bool = False,
    smc_header: bool = False,
    title: str = "TEST SNES GAME",
    fix_chk: bool = True,
) -> bytearray:
    body = bytearray(size)
    if exhirom:
        hdr = 0x40FFC0
        map_mode = 0x25
    elif hirom:
        hdr = 0x00FFC0
        map_mode = 0x21
    else:
        hdr = 0x007FC0
        map_mode = 0x20

    t = title.encode("ascii").ljust(21, b" ")[:21]
    body[hdr : hdr + 21] = t
    body[hdr + 0x15] = map_mode
    body[hdr + 0x17] = max(5, (size // 1024).bit_length() - 1)
    # Reset vector pointing to $8000 with SEI opcode (0x78)
    body[hdr + 0x3C] = 0x00
    body[hdr + 0x3D] = 0x80
    reset_pc = 0x8000 if (hirom or exhirom) else 0x0000
    if reset_pc < len(body):
        body[reset_pc] = 0x78

    rom = bytearray(512) + body if smc_header else body
    if fix_chk:
        override = "exhirom" if exhirom else ("hirom" if hirom else "lorom")
        SnesHandler().fix_checksums(rom, snes_map=override)
    return rom


def make_genesis_rom(
    *,
    size: int = 0x20000,
    title: str = "SONIC TEST GENESIS",
    sega_tag: bytes = b"SEGA GENESIS    ",
    smd_layout: str | None = None,
    fix_chk: bool = True,
) -> bytearray:
    rom = bytearray(size)
    rom[0x0100:0x0110] = sega_tag[:16].ljust(16, b" ")
    t = title.encode("ascii")[:48].ljust(48, b" ")
    rom[0x0120:0x0150] = t
    rom[0x0150:0x0180] = t
    rom[0x01F0:0x01F3] = b"JUE"
    if fix_chk:
        GenesisHandler().fix_checksums(rom)
    if smd_layout is not None:
        return bytearray(interleave_smd(bytes(rom), layout=smd_layout))
    return rom


@pytest.fixture
def gb_rom() -> bytearray:
    return make_gb_rom(banks=4)
