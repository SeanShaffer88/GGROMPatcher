"""Tests for BPS, UPS, and unified binary patch dispatch."""

from __future__ import annotations

import zlib
from pathlib import Path

import pytest

from ggrompatcher.bps import apply_bps, create_bps, decode_vlq, encode_vlq, inspect_bps
from ggrompatcher.models import BpsFormatError, PatchFormatError, UpsFormatError
from ggrompatcher.patch_formats import (
    apply_binary_patch,
    create_binary_patch,
    detect_patch_format,
    write_binary_patch_file,
)
from ggrompatcher.ups import apply_ups, create_ups, inspect_ups


def test_vlq_roundtrip_and_boundaries() -> None:
    for val in (0, 1, 127, 128, 129, 255, 16384, 16512, 0x123456):
        encoded = encode_vlq(val)
        decoded, pos = decode_vlq(encoded, 0)
        assert decoded == val
        assert pos == len(encoded)

    with pytest.raises(PatchFormatError, match="Unexpected end"):
        decode_vlq(b"\x01", 0)
    with pytest.raises(ValueError, match="negative"):
        encode_vlq(-1)


def test_bps_roundtrip_rle_metadata_resize_and_errors() -> None:
    orig = bytes(range(256)) * 4 + b"\x11\x22\x33\x44" * 32
    mod = bytearray(orig)
    mod[10:26] = b"\xaa" * 16  # RLE / TargetCopy pattern
    mod[300] = 0x99
    mod.extend(b"\xde\xad\xbe\xef" * 8)  # Grow target beyond source
    mod_bytes = bytes(mod)

    patch = create_bps(orig, mod_bytes, metadata="v1.2 Test Patch")
    assert patch.startswith(b"BPS1")
    meta = inspect_bps(patch)
    assert meta["source_size"] == len(orig)
    assert meta["target_size"] == len(mod_bytes)
    assert meta["metadata"] == "v1.2 Test Patch"
    assert meta["source_crc32"] == (zlib.crc32(orig) & 0xFFFFFFFF)
    assert meta["target_crc32"] == (zlib.crc32(mod_bytes) & 0xFFFFFFFF)

    assert apply_bps(orig, patch) == mod_bytes

    # Shrinking target test
    smaller = orig[:200]
    patch_shrink = create_bps(orig, smaller)
    assert apply_bps(orig, patch_shrink) == smaller

    # Corrupted source ROM raises BpsFormatError unless verify_checksums=False
    bad_orig = bytearray(orig)
    bad_orig[0] ^= 0xFF
    with pytest.raises(BpsFormatError, match="CRC32 mismatch"):
        apply_bps(bytes(bad_orig), patch)
    out_unverified = apply_bps(bytes(bad_orig), patch, verify_checksums=False)
    assert len(out_unverified) == len(mod_bytes)

    # Corrupted patch CRC raises
    corrupt_patch = patch[:-1] + bytes([patch[-1] ^ 0x01])
    with pytest.raises(BpsFormatError, match="checksum mismatch"):
        apply_bps(orig, corrupt_patch)


def test_ups_roundtrip_bidirectional_and_errors() -> None:
    orig = b"\x00\x01\x02\x03" * 256
    mod = bytearray(orig)
    mod[5] = 0xAA
    mod[100:120] = b"\x55" * 20
    mod_bytes = bytes(mod) + b"\x99\x88\x77\x66"

    patch = create_ups(orig, mod_bytes)
    assert patch.startswith(b"UPS1")

    meta = inspect_ups(patch)
    assert meta["input_size"] == len(orig)
    assert meta["output_size"] == len(mod_bytes)

    # Forward & reverse application!
    assert apply_ups(orig, patch) == mod_bytes
    assert apply_ups(mod_bytes, patch) == orig

    # Shrunk target reverse application!
    smaller = orig[:400]
    patch_small = create_ups(orig, smaller)
    assert apply_ups(orig, patch_small) == smaller
    assert apply_ups(smaller, patch_small) == orig

    # Corrupt input fails verification unless disabled
    with pytest.raises(UpsFormatError, match="CRC32 mismatch"):
        apply_ups(b"\xff" * len(orig), patch)

    with pytest.raises(UpsFormatError, match="checksum mismatch"):
        apply_ups(orig, patch[:-1] + bytes([patch[-1] ^ 0xFF]))


def test_unified_patch_formats_dispatch(tmp_path: Path) -> None:
    orig = b"\x10" * 512
    mod = b"\x10" * 200 + b"\x99" + b"\x10" * 311

    for ext in (".ips", ".bps", ".ups"):
        p = tmp_path / f"test{ext}"
        written = write_binary_patch_file(p, orig, mod, metadata="meta")
        patch_data = written.read_bytes()
        assert detect_patch_format(patch_data) == ext.lstrip(".")
        assert detect_patch_format(filename=p) == ext.lstrip(".")
        assert apply_binary_patch(orig, patch_data, filename=p.name) == mod

    direct_bps = create_binary_patch(orig, mod, fmt="bps")
    assert apply_binary_patch(orig, direct_bps) == mod

    with pytest.raises(PatchFormatError, match="Unsupported patch format"):
        create_binary_patch(orig, mod, fmt="xdelta")
