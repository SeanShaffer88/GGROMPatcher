"""Tests for ROM checksum calculation and verification across all consoles."""

from __future__ import annotations

from ggrompatcher.models import ConsoleSystem
from ggrompatcher.patcher import fix_rom_checksums, inspect_rom
from tests.conftest import (
    make_gb_rom,
    make_genesis_rom,
    make_sega8_rom,
    make_snes_rom,
)


def test_gameboy_header_and_global_checksum_order() -> None:
    rom = make_gb_rom(banks=2, fix_chk=False)
    rom[0x0200] = 0xAA
    rom[0x4005] = 0x55

    before = inspect_rom(bytes(rom), system=ConsoleSystem.GAMEBOY)
    assert any(not c.valid for c in before.checksums)

    _, after, fixed = fix_rom_checksums(bytes(rom), system=ConsoleSystem.GAMEBOY)
    assert all(c.valid for c in after.checksums)

    # Corrupting a title byte should invalidate both header & global checksums.
    corrupted = bytearray(fixed)
    corrupted[0x0134] ^= 0x01
    bad = inspect_rom(bytes(corrupted), system=ConsoleSystem.GAMEBOY)
    assert all(not c.valid for c in bad.checksums)


def test_sms_gamegear_checksum_little_endian_and_sizes() -> None:
    for banks, nibble in ((2, 0xC), (4, 0xE), (8, 0xF)):
        rom = make_sega8_rom(banks=banks, region_code=0x4, size_nibble=nibble, fix_chk=False)
        rom[0x0100] = 0x12
        if len(rom) > 0x8000:
            rom[0x9000] = 0x34

        _, after, fixed = fix_rom_checksums(bytes(rom), system=ConsoleSystem.MASTERSYSTEM)
        assert len(after.checksums) == 1
        assert after.checksums[0].valid is True
        # Check little-endian encoding at 0x7FFA..0x7FFB
        stored = fixed[0x7FFA] | (fixed[0x7FFB] << 8)
        assert stored == after.checksums[0].computed


def test_snes_checksum_power_of_two_and_3mb_mirrored_with_smc() -> None:
    # 512 KB power-of-2 LoROM with 512B SMC header
    rom_lo = make_snes_rom(size=0x80000, hirom=False, smc_header=True, fix_chk=False)
    rom_lo[512 + 0x1234] = 0x99
    _, after_lo, _fixed_lo = fix_rom_checksums(bytes(rom_lo), system=ConsoleSystem.SNES)
    assert all(c.valid for c in after_lo.checksums)
    # Complement ^ Checksum == 0xFFFF
    chk_entry = {c.name: c for c in after_lo.checksums}
    assert (chk_entry["SNES Complement"].stored ^ chk_entry["SNES Checksum"].stored) == 0xFFFF

    # 3 MB (0x300000) non-power-of-2 HiROM: upper 1 MB mirrored twice in hardware checksum.
    rom_3mb = make_snes_rom(size=0x300000, hirom=True, smc_header=False, fix_chk=False)
    rom_3mb[0x250000] = 0x05  # Inside upper 1 MB chunk -> contributes 2 * 5 = 10 to checksum!
    _, after_3mb, fixed_3mb = fix_rom_checksums(bytes(rom_3mb), system=ConsoleSystem.SNES)
    assert all(c.valid for c in after_3mb.checksums)
    base_sum = after_3mb.checksums[1].computed

    rom_3mb_plus1 = bytearray(fixed_3mb)
    rom_3mb_plus1[0x250000] = 0x06
    info_changed = inspect_rom(bytes(rom_3mb_plus1), system=ConsoleSystem.SNES)
    # Incrementing a byte in the upper 1MB of a 3MB ROM increments checksum by 2!
    assert (info_changed.checksums[1].computed - base_sum) & 0xFFFF == 2


def test_genesis_big_endian_checksum() -> None:
    rom = make_genesis_rom(size=0x20000, fix_chk=False)
    rom[0x0200] = 0x12
    rom[0x0201] = 0x34
    _, after, fixed = fix_rom_checksums(bytes(rom), system=ConsoleSystem.GENESIS)
    assert len(after.checksums) == 1
    assert after.checksums[0].valid is True
    assert after.checksums[0].computed == 0x1234
    assert fixed[0x018E] == 0x12
    assert fixed[0x018F] == 0x34
