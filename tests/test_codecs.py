"""Tests for Game Genie encoding/decoding across all supported console formats."""

from __future__ import annotations

import pytest

from ggrompatcher.models import CodeFormatError, ConsoleSystem
from ggrompatcher.patcher import decode_code, encode_code


class TestGameBoyAndGameGearCodecs:
    @pytest.mark.parametrize(
        ("code", "expected_addr", "expected_val", "expected_cmp"),
        [
            ("004-BCE-E66", 0x14BC, 0x00, 0x03),
            ("007-01A-3BE", 0x5701, 0x00, 0x35),
            ("3A8-E7C-2A2", 0x38E7, 0x3A, 0x32),
            ("001-5CA-E62", 0x515C, 0x00, 0x02),
            ("013-02F-E6E", 0x0302, 0x01, 0x01),
            ("00F-88D-3B7", 0x2F88, 0x00, 0x77),
            ("FF1-23E", 0x1123, 0xFF, None),
            ("004BCEE66", 0x14BC, 0x00, 0x03),
            ("004 bce e66", 0x14BC, 0x00, 0x03),
        ],
    )
    def test_gb_known_vectors(
        self, code: str, expected_addr: int, expected_val: int, expected_cmp: int | None
    ) -> None:
        dec = decode_code(code, system=ConsoleSystem.GAMEBOY)
        assert dec.address == expected_addr
        assert dec.value_int == expected_val
        assert dec.compare_int == expected_cmp
        assert not dec.warnings

    def test_gb_roundtrip_exhaustive_boundaries(self) -> None:
        for addr in (0x0000, 0x0002, 0x014D, 0x3FFF, 0x4000, 0x5701, 0x7FFF):
            for val in (0x00, 0x3A, 0xAD, 0xFF):
                code6 = encode_code(ConsoleSystem.GAMEBOY, addr, val)
                dec6 = decode_code(code6, system=ConsoleSystem.GAMEBOY)
                assert (dec6.address, dec6.value_int, dec6.compare_int) == (addr, val, None)
                for cmp_b in (0x00, 0x03, 0x35, 0xBA, 0xFF):
                    code9 = encode_code(ConsoleSystem.GAMEBOY, addr, val, cmp_b)
                    dec9 = decode_code(code9, system=ConsoleSystem.GAMEBOY)
                    assert (dec9.address, dec9.value_int, dec9.compare_int) == (addr, val, cmp_b)
                    assert not dec9.warnings

    def test_gb_cloak_nibble_warning(self) -> None:
        # 004-BCE-E66 has G=E, H=6 (= E^8). Changing H to 7 should warn without failing.
        dec = decode_code("004-BCE-E76", system=ConsoleSystem.GAMEBOY)
        assert dec.address == 0x14BC
        assert dec.compare_int == 0x03
        assert len(dec.warnings) == 1
        assert "Cloak digit H" in dec.warnings[0]

    def test_gg_allows_8000_to_bfff_while_gb_rejects(self) -> None:
        # F=7 -> 7 ^ F = 8 -> 0x8123
        gg_dec = decode_code("AA1-237-E66", system=ConsoleSystem.GAMEGEAR)
        assert gg_dec.address == 0x8123
        with pytest.raises(CodeFormatError, match="outside cartridge ROM space"):
            decode_code("AA1-237-E66", system=ConsoleSystem.GAMEBOY)


class TestNesCodec:
    @pytest.mark.parametrize(
        ("code", "expected_addr", "expected_val", "expected_cmp"),
        [
            ("AKELVS", 0xBD86, 0x48, None),
            ("AKE-LVS", 0xBD86, 0x48, None),
            ("SXIOPO", 0x91D9, 0xAD, None),
            ("AATOZE", 0x906A, 0x08, None),
            ("AATOZA", 0x906A, 0x00, None),
            ("ZEXPYGLA", 0x94A7, 0x02, 0x03),
            ("SXULKNSE", 0xBFB4, 0xAD, 0x8D),
        ],
    )
    def test_nes_known_vectors(
        self, code: str, expected_addr: int, expected_val: int, expected_cmp: int | None
    ) -> None:
        dec = decode_code(code, system=ConsoleSystem.NES)
        assert dec.address == expected_addr
        assert dec.value_int == expected_val
        assert dec.compare_int == expected_cmp

    def test_nes_roundtrip(self) -> None:
        for addr in (0x8000, 0x91D9, 0x94A7, 0xBD86, 0xC000, 0xFFFF):
            for val in (0x00, 0x48, 0xAD, 0xFF):
                c6 = encode_code(ConsoleSystem.NES, addr, val)
                d6 = decode_code(c6, system=ConsoleSystem.NES)
                assert (d6.address, d6.value_int, d6.compare_int) == (addr, val, None)
                for cmp_b in (0x00, 0x03, 0x8D, 0xFF):
                    c8 = encode_code(ConsoleSystem.NES, addr, val, cmp_b)
                    d8 = decode_code(c8, system=ConsoleSystem.NES)
                    assert (d8.address, d8.value_int, d8.compare_int) == (addr, val, cmp_b)

    def test_nes_invalid_chars_rejected(self) -> None:
        with pytest.raises(CodeFormatError, match="invalid character"):
            decode_code("ABCDEF", system=ConsoleSystem.NES)


class TestSnesCodec:
    @pytest.mark.parametrize(
        ("code", "expected_addr", "expected_val"),
        [
            ("ABCD-EFFF", 0xC4A704, 0xC9),
            ("C222-D4DD", 0x00D0D8, 0xAD),
            ("D865-6464", 0x008A7A, 0x0B),
            ("DF66-67A4", 0x008A8F, 0x01),
            ("776B-6D04", 0x008A91, 0x33),
            ("DDB4-6F07", 0x009E25, 0x00),
            ("14B4-6F07", 0x009E25, 0x62),
        ],
    )
    def test_snes_known_vectors(self, code: str, expected_addr: int, expected_val: int) -> None:
        dec = decode_code(code, system=ConsoleSystem.SNES)
        assert dec.address == expected_addr
        assert dec.value_int == expected_val
        assert encode_code(ConsoleSystem.SNES, expected_addr, expected_val) == code

    def test_snes_rejects_compare(self) -> None:
        with pytest.raises(CodeFormatError, match="do not support a compare"):
            encode_code(ConsoleSystem.SNES, 0x008000, 0xEA, compare=0x01)


class TestGenesisCodec:
    @pytest.mark.parametrize(
        ("code", "expected_addr", "expected_val"),
        [
            ("ABD5-78F7", 0xBE47BD, 0x1F00),
            ("ATBT-AA32", 0x000338, 0x6004),
            ("AJBT-AA34", 0x00033A, 0x6002),
            ("ATKA-ABGC", 0x0012C2, 0x8004),
            ("AVKA-ABCC", 0x005242, 0x8004),
            ("AVSA-ABFG", 0x005EA6, 0x8004),
            ("ATKT-ABBE", 0x001324, 0x8004),
        ],
    )
    def test_genesis_known_vectors(self, code: str, expected_addr: int, expected_val: int) -> None:
        dec = decode_code(code, system=ConsoleSystem.GENESIS)
        assert dec.address == expected_addr
        assert dec.value == expected_val.to_bytes(2, "big")
        assert encode_code(ConsoleSystem.GENESIS, expected_addr, expected_val) == code

    def test_genesis_odd_address_warning(self) -> None:
        dec = decode_code("ABD5-78F7", system=ConsoleSystem.GENESIS)
        assert dec.address % 2 == 1
        assert any("odd" in w.lower() for w in dec.warnings)

    def test_genesis_rejects_invalid_letters(self) -> None:
        with pytest.raises(CodeFormatError, match="invalid character"):
            decode_code("IOQU-AA32", system=ConsoleSystem.GENESIS)


class TestRawCodec:
    @pytest.mark.parametrize(
        ("code", "addr", "val", "cmp_b"),
        [
            ("0014BC:00", 0x14BC, b"\x00", None),
            ("0x00033A:6002", 0x033A, b"\x60\x02", None),
            ("14BC?03:00", 0x14BC, b"\x00", b"\x03"),
            ("14BC=03?00", 0x14BC, b"\x00", b"\x03"),
            ("14BC=AA", 0x14BC, b"\xaa", None),
        ],
    )
    def test_raw_variants(self, code: str, addr: int, val: bytes, cmp_b: bytes | None) -> None:
        dec = decode_code(code, system=ConsoleSystem.RAW)
        assert dec.address == addr
        assert dec.value == val
        assert dec.compare == cmp_b
