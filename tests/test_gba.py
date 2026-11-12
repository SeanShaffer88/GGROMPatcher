"""Tests for GBA ROM detection, header checksums, and GameShark/PAR/CodeBreaker codecs."""

from __future__ import annotations

import pytest

from ggrompatcher.cheatfiles import parse_code_tokens
from ggrompatcher.models import CodeFormatError, ConsoleSystem
from ggrompatcher.patcher import apply_patches, decode_code, encode_code, inspect_rom
from ggrompatcher.systems import detect_system
from ggrompatcher.systems.gba import (
    GBA_GS_V1_SEEDS,
    _tea_decrypt_pair,
    _tea_encrypt_pair,
    compute_gba_header_checksum,
)
from tests.conftest import make_gba_rom


def test_gba_detection_inspection_and_checksum_repair() -> None:
    rom = make_gba_rom(title="GOLDEN SUN", game_code="AGSE", maker_code="01", version=2)
    sys_type, conf = detect_system(bytes(rom))
    assert sys_type == ConsoleSystem.GBA
    assert conf >= 85

    info = inspect_rom(bytes(rom))
    assert info.system == ConsoleSystem.GBA
    assert info.title == "GOLDEN SUN"
    assert info.extra["game_code"] == "AGSE"
    assert info.extra["maker_code"] == "01"
    assert info.extra["nintendo_logo_valid"] is True
    assert info.extra["fixed_0x96_valid"] is True
    assert info.extra["software_version"] == 2
    assert info.checksums[0].valid is True

    # Corrupt complement checksum and verify repair.
    rom[0xBD] ^= 0xFF
    bad_info = inspect_rom(bytes(rom))
    assert bad_info.checksums[0].valid is False

    res = apply_patches(bytes(rom), ["PAR:08001000:EA"], strict=True)
    assert res.output_bytes[0x1000] == 0xEA
    assert res.rom_info_after.checksums[0].valid is True
    assert res.output_bytes[0xBD] == compute_gba_header_checksum(res.output_bytes)


def test_gba_gameshark_v1_raw_and_encrypted_roundtrip() -> None:
    # TEA round-trip
    enc1, enc2 = _tea_encrypt_pair(0x60000800, 0x00001234, GBA_GS_V1_SEEDS)
    dec1, dec2 = _tea_decrypt_pair(enc1, enc2, GBA_GS_V1_SEEDS)
    assert (dec1, dec2) == (0x60000800, 0x00001234)

    # Raw GSA ROM patch type 0x6 -> offset = (0x000800 << 1) = 0x1000, value LE = 34 12
    raw_code = encode_code(ConsoleSystem.GBA, 0x001000, 0x1234, code_format="gsa")
    assert raw_code == "GSA:60000800 00001234"
    d_raw = decode_code("60000800 00001234", system=ConsoleSystem.GBA)
    assert d_raw.address == 0x001000
    assert d_raw.value == b"\x34\x12"

    # Encrypted GSA1 round-trip (both prefixed and unprefixed!)
    enc_code = encode_code(ConsoleSystem.GBA, 0x001000, 0x1234, code_format="gsa1")
    assert enc_code == f"GSA1:{enc1:08X} {enc2:08X}"
    d_enc = decode_code(enc_code, system=ConsoleSystem.GBA)
    assert d_enc.address == 0x001000
    assert d_enc.value == b"\x34\x12"
    d_unprefixed = decode_code(f"{enc1:08X} {enc2:08X}", system=ConsoleSystem.GBA)
    assert d_unprefixed.address == 0x001000
    assert d_unprefixed.value == b"\x34\x12"

    # Unaligned address fails cleanly for GSA type 0x6
    with pytest.raises(CodeFormatError, match="2-byte aligned"):
        encode_code(ConsoleSystem.GBA, 0x001001, 0x1234, code_format="gsa")


def test_gba_par_v3_and_rom_bus_codes() -> None:
    par3_code = encode_code(ConsoleSystem.GBA, 0x002400, 0xABCD, code_format="par3")
    dec_par3 = decode_code(par3_code, system=ConsoleSystem.GBA)
    assert dec_par3.address == 0x002400
    assert dec_par3.value == b"\xcd\xab"

    par3_raw = encode_code(ConsoleSystem.GBA, 0x002400, 0xABCD, code_format="par3-raw")
    assert par3_raw == "PAR3:00000000 18001200 0000ABCD 00000000"
    dec_raw = decode_code(par3_raw, system=ConsoleSystem.GBA)
    assert dec_raw.address == 0x002400
    assert dec_raw.value == b"\xcd\xab"

    # Cartridge ROM Wait State 0/1/2 mapping (8-bit, 16-bit LE, 32-bit LE)
    par_16 = encode_code(ConsoleSystem.GBA, 0x001234, 0xBEEF, compare=0xDEAD, code_format="par")
    assert par_16 == "PAR:08001234?DEAD:BEEF"
    d_par = decode_code(par_16, system=ConsoleSystem.GBA)
    assert d_par.address == 0x001234
    assert d_par.value == b"\xef\xbe"
    assert d_par.compare == b"\xad\xde"

    # Wait State 1 mirror (0x0Axxxxxx) maps to same cartridge ROM offset
    d_ws1 = decode_code("AR:0A001234:11223344", system=ConsoleSystem.GBA)
    assert d_ws1.address == 0x001234
    assert d_ws1.value == b"\x44\x33\x22\x11"


def test_gba_ram_and_codebreaker_diagnostics() -> None:
    with pytest.raises(CodeFormatError, match="EWRAM"):
        decode_code("PAR:02025840:63", system=ConsoleSystem.GBA)
    with pytest.raises(CodeFormatError, match="IWRAM"):
        decode_code("PAR:03001234:FF", system=ConsoleSystem.GBA)

    with pytest.raises(CodeFormatError, match=r"CodeBreaker.*EWRAM"):
        decode_code("82025840 0044", system=ConsoleSystem.GBA)
    with pytest.raises(CodeFormatError, match="master encryption key"):
        decode_code("92345678 1234", system=ConsoleSystem.GBA)

    # Encrypted GameShark v1 EWRAM code should decrypt and identify EWRAM cleanly
    enc_ram1, enc_ram2 = _tea_encrypt_pair(0x02025840, 0x00000063, GBA_GS_V1_SEEDS)
    with pytest.raises(CodeFormatError, match="EWRAM"):
        decode_code(f"{enc_ram1:08X} {enc_ram2:08X}", system=ConsoleSystem.GBA)


def test_gba_token_splitting_and_header_patch_fixes_checksum() -> None:
    tokens = parse_code_tokens("60000800 00001234 + PAR:080000A0:41", pair_hex8=True)
    assert tokens == ["60000800 00001234", "PAR:080000A0:41"]

    rom = make_gba_rom(size=0x20000, title="ADVANCE")
    orig_chk = rom[0xBD]
    # Patch first title byte (in 0xA0..0xBC) + ROM body halfword using space-separated string!
    res = apply_patches(
        bytes(rom),
        "60000800 00001234\nPAR:080000A0:5A",
        filename="game.gba",
        strict=True,
    )
    assert res.output_bytes[0x1000:0x1002] == b"\x34\x12"
    assert res.output_bytes[0xA0] == 0x5A
    assert res.output_bytes[0xBD] != orig_chk
    assert res.rom_info_after.checksums[0].valid is True
