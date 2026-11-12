"""Tests for PPF (1.0/2.0/3.0), APS-N64 (APS10), and APS-GBA (APS1) binary patches."""

from __future__ import annotations

from pathlib import Path

import pytest

from ggrompatcher.aps import apply_aps, crc16_ccitt_false, create_aps, inspect_aps
from ggrompatcher.models import ApsFormatError, PpfFormatError
from ggrompatcher.patch_formats import (
    apply_binary_patch,
    create_binary_patch,
    detect_patch_format,
    write_binary_patch_file,
)
from ggrompatcher.ppf import apply_ppf, create_ppf, inspect_ppf


def test_crc16_ccitt_false_known_vector() -> None:
    assert crc16_ccitt_false(b"123456789") == 0x29B1


def test_ppf_v1_v2_v3_roundtrip_undo_blockcheck_and_diz() -> None:
    orig = bytes(i & 0xFF for i in range(0xA000))
    mod = bytearray(orig)
    mod[0x100:0x110] = b"\xaa\xbb" * 8
    mod[0x9800] = 0x77
    mod.extend(b"\x01\x02\x00\x00")
    mod_bytes = bytes(mod)

    # PPF 1.0
    p1 = create_ppf(orig, mod_bytes, version=1, description="PPF1 Test")
    assert p1.startswith(b"PPF10\x00")
    i1 = inspect_ppf(p1)
    assert i1.version == 1
    assert i1.description == "PPF1 Test"
    assert apply_ppf(orig, p1) == mod_bytes

    # PPF 2.0 (always has u32 source size + 1024-byte blockcheck at 0x9320)
    p2 = create_ppf(orig, mod_bytes, version=2, description="PPF2 Test")
    assert p2.startswith(b"PPF20\x01")
    i2 = inspect_ppf(p2)
    assert i2.version == 2
    assert i2.input_size == len(orig)
    assert i2.has_blockcheck is True
    assert apply_ppf(orig, p2) == mod_bytes

    bad_size = orig + b"\x00"
    with pytest.raises(PpfFormatError, match="size mismatch"):
        apply_ppf(bad_size, p2)

    bad_blk = bytearray(orig)
    bad_blk[0x9330] ^= 0xFF
    with pytest.raises(PpfFormatError, match="blockcheck mismatch"):
        apply_ppf(bytes(bad_blk), p2)

    # PPF 3.0 with blockcheck + undo data + FILE_ID.DIZ
    p3 = create_ppf(
        orig,
        mod_bytes,
        version=3,
        description="PPF3 Undo Test",
        blockcheck=True,
        undo=True,
        file_id_diz="Retro Patch v3.0",
    )
    assert p3.startswith(b"PPF30\x02")
    i3 = inspect_ppf(p3)
    assert i3.version == 3
    assert i3.has_blockcheck is True
    assert i3.has_undo is True
    assert i3.file_id_diz == "Retro Patch v3.0"

    patched = apply_ppf(orig, p3)
    assert patched == mod_bytes
    # Undo restores original bytes (within original file length)
    reverted = apply_ppf(patched, p3, undo=True)
    assert reverted[: len(orig)] == orig

    with pytest.raises(PpfFormatError, match="does not contain undo"):
        apply_ppf(patched, p1, undo=True)

    with pytest.raises(PpfFormatError, match="cannot truncate"):
        create_ppf(orig, orig[:-10])


def test_aps10_simple_and_n64_roundtrip_and_validation() -> None:
    # Simple mode with RLE + growth
    orig = b"\x00" * 512
    mod = bytearray(orig)
    mod[20:40] = b"\x55" * 20  # RLE run
    mod[100:104] = b"\x11\x22\x33\x44"  # simple hunk
    mod.extend(b"\x99\x00\x00")
    mod_bytes = bytes(mod)

    patch_simple = create_aps(orig, mod_bytes, description="Simple APS10")
    assert patch_simple.startswith(b"APS10")
    info_s = inspect_aps(patch_simple)
    assert info_s.variant == "aps10"
    assert info_s.description == "Simple APS10"
    assert info_s.output_size == len(mod_bytes)
    assert apply_aps(orig, patch_simple) == mod_bytes

    # N64 mode (big-endian .z64 magic 80 37 12 40, cartId @ 0x3C, CRC @ 0x10)
    n64_orig = bytearray(1024)
    n64_orig[0:4] = b"\x80\x37\x12\x40"
    n64_orig[0x10:0x18] = b"\xaa\xbb\xcc\xdd\x11\x22\x33\x44"
    n64_orig[0x3C:0x3F] = b"SM6"
    n64_mod = bytearray(n64_orig)
    n64_mod[0x200:0x210] = b"\xff" * 16

    patch_n64 = create_aps(bytes(n64_orig), bytes(n64_mod), description="SM64 Hack")
    info_n64 = inspect_aps(patch_n64)
    assert info_n64.header_type == 1
    assert info_n64.n64_cart_id == "SM6"
    assert apply_aps(bytes(n64_orig), patch_n64) == bytes(n64_mod)

    wrong_cart = bytearray(n64_orig)
    wrong_cart[0x3C:0x3F] = b"ZLD"
    with pytest.raises(ApsFormatError, match="header mismatch"):
        apply_aps(bytes(wrong_cart), patch_n64)
    # Bypassing verification still applies bytes
    assert (
        apply_aps(bytes(wrong_cart), patch_n64, verify_checksums=False)[0x200:0x210] == b"\xff" * 16
    )


def test_aps_gba_64k_block_xor_and_crc16_validation() -> None:
    orig = bytes((i * 13) & 0xFF for i in range(0x20000))  # 128 KiB (two 64 KiB blocks)
    mod = bytearray(orig)
    mod[0x1234] ^= 0x5A  # Only first 64 KiB block modified
    mod_bytes = bytes(mod)

    patch = create_aps(orig, mod_bytes, variant="gba")
    assert patch.startswith(b"APS1")
    assert not patch.startswith(b"APS10")
    info = inspect_aps(patch)
    assert info.variant == "aps-gba"
    assert info.record_count == 1
    assert info.source_size == len(orig)
    assert info.output_size == len(mod_bytes)

    assert apply_aps(orig, patch) == mod_bytes

    bad_orig = bytearray(orig)
    bad_orig[0x10] ^= 0x01
    with pytest.raises(ApsFormatError, match="CRC-16 mismatch"):
        apply_aps(bytes(bad_orig), patch)


def test_unified_dispatch_includes_ppf_and_aps(tmp_path: Path) -> None:
    orig = b"\x20" * 512
    mod = b"\x20" * 100 + b"\x7f\x80\x81" + b"\x20" * 409

    for ext in (".ppf", ".aps"):
        p = tmp_path / f"rom{ext}"
        written = write_binary_patch_file(p, orig, mod, metadata="dispatch test")
        raw = written.read_bytes()
        assert detect_patch_format(raw) == ext.lstrip(".")
        assert detect_patch_format(filename=p) == ext.lstrip(".")
        assert apply_binary_patch(orig, raw, filename=p.name) == mod

    assert apply_binary_patch(orig, create_binary_patch(orig, mod, fmt="ppf", ppf_undo=True)) == mod
    assert apply_binary_patch(orig, create_binary_patch(orig, mod, fmt="aps")) == mod
