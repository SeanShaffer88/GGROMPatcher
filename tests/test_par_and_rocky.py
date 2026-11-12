"""Tests for Pro Action Replay, Pro Action Rocky, and GameShark handling."""

from __future__ import annotations

import pytest

from ggrompatcher.models import CodeFormatError, ConsoleSystem
from ggrompatcher.patcher import apply_patches, decode_code, encode_code
from tests.conftest import (
    make_gb_rom,
    make_genesis_rom,
    make_nes_rom,
    make_sega8_rom,
    make_snes_rom,
)


@pytest.mark.parametrize(
    ("code", "address", "value", "compare"),
    [
        ("15C93C0A", 0x9123, 0xBD, 0xDE),  # Classic SMB SLXPLOVS equivalent
        ("FCBDD274", 0x8000, 0x00, 0x00),
        ("00000000", 0xE5DA, 0x3F, 0xD4),
        ("AD3A9314", 0x8FCE, 0xAD, 0xB9),
    ],
)
def test_nes_pro_action_rocky_vectors(code: str, address: int, value: int, compare: int) -> None:
    dec = decode_code(code, system=ConsoleSystem.NES)
    assert dec.address == address
    assert dec.value == bytes([value])
    assert dec.compare == bytes([compare])
    assert dec.normalized_code == code
    assert encode_code(ConsoleSystem.NES, address, value, compare, code_format="par") == code


def test_nes_par_prefixed_and_ram_rejection() -> None:
    dec = decode_code("PAR:8010?03:FF", system=ConsoleSystem.NES)
    assert dec.address == 0x8010
    assert dec.value == b"\xff"
    assert dec.compare == b"\x03"

    with pytest.raises(CodeFormatError, match="RAM"):
        decode_code("PAR:005A:09", system=ConsoleSystem.NES)

    with pytest.raises(CodeFormatError, match="compare byte"):
        encode_code(ConsoleSystem.NES, 0x9123, 0xBD, code_format="par")

    rom = make_nes_rom(prg_banks_16k=2)
    off = 16 + (0x9123 - 0x8000)
    rom[off] = 0xDE
    res = apply_patches(bytes(rom), ["15C93C0A"], strict=True)
    assert res.output_bytes[off] == 0xBD


def test_snes_par_rom_and_wram_rejection() -> None:
    dec = decode_code("PAR:008000:EA", system=ConsoleSystem.SNES)
    assert dec.address == 0x008000
    assert dec.value == b"\xea"
    assert encode_code(ConsoleSystem.SNES, 0x008000, 0xEA, code_format="par") == "PAR:008000:EA"

    with pytest.raises(CodeFormatError, match="WRAM"):
        decode_code("AR:7E0100:FF", system=ConsoleSystem.SNES)

    rom = make_snes_rom(hirom=False, smc_header=False)
    res = apply_patches(bytes(rom), ["PAR:008000:EA"], strict=True)
    assert res.output_bytes[0] == 0xEA


def test_genesis_par_rom_smd_and_ram_rejection() -> None:
    dec = decode_code("PAR:000338:6004", system=ConsoleSystem.GENESIS)
    assert dec.address == 0x000338
    assert dec.value == b"\x60\x04"
    assert encode_code(ConsoleSystem.GENESIS, 0x000338, 0x6004, code_format="par") == (
        "PAR:000338:6004"
    )

    dec_8 = decode_code("AR:000339:99", system=ConsoleSystem.GENESIS)
    assert dec_8.address == 0x000339
    assert dec_8.value == b"\x99"

    with pytest.raises(CodeFormatError, match="68000 RAM"):
        decode_code("PAR:FF1234:0009", system=ConsoleSystem.GENESIS)

    plain = make_genesis_rom(size=0x20000)
    smd = make_genesis_rom(size=0x20000, smd_layout="even_first")
    res = apply_patches(bytes(smd), ["PAR:000338:6004"], filename="sonic.smd", strict=True)
    assert len(res.output_bytes) == len(smd)
    assert res.output_bytes != bytes(smd)

    res_plain = apply_patches(bytes(plain), ["PAR:000338:6004"], filename="sonic.bin", strict=True)
    assert res_plain.output_bytes[0x0338:0x033A] == b"\x60\x04"


def test_gb_and_gamegear_runtime_ram_codes_give_clear_error() -> None:
    # GameShark GB code: type 01, value 09, addr C300 (little endian 00 C3 -> 010900C3)
    with pytest.raises(CodeFormatError, match=r"RAM.*0xC300"):
        decode_code("010900C3", system=ConsoleSystem.GAMEBOY)
    with pytest.raises(CodeFormatError, match="RAM"):
        decode_code("GS:010900C3", system=ConsoleSystem.GAMEBOY)

    # Game Gear / SMS Action Replay RAM code
    with pytest.raises(CodeFormatError, match=r"RAM.*0xC021"):
        decode_code("00C02109", system=ConsoleSystem.GAMEGEAR)
    with pytest.raises(CodeFormatError, match="RAM"):
        decode_code("AR:00C021:09", system=ConsoleSystem.MASTERSYSTEM)

    # Verify strict patch surfaces informative error
    gb = make_gb_rom(banks=2)
    with pytest.raises(CodeFormatError, match="RAM"):
        apply_patches(bytes(gb), ["010900C3"], strict=True)

    gg = make_sega8_rom(banks=2, region_code=0x6)
    with pytest.raises(CodeFormatError, match="RAM"):
        apply_patches(bytes(gg), ["00C02109"], filename="game.gg", strict=True)
