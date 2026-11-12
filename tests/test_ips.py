"""Tests for IPS patch generation and application."""

from __future__ import annotations

import pytest

from ggrompatcher.ips import IPS_EOF, IPS_HEADER, apply_ips, create_ips
from ggrompatcher.models import IpsFormatError


def test_ips_create_and_apply_roundtrip_with_rle() -> None:
    orig = bytearray(1024)
    mod = bytearray(orig)
    mod[10] = 0xEA
    mod[200:225] = b"\xff" * 25  # Should emit an RLE record

    patch = create_ips(bytes(orig), bytes(mod))
    assert patch.startswith(IPS_HEADER)
    assert patch.endswith(IPS_EOF)
    assert apply_ips(bytes(orig), patch) == bytes(mod)


def test_ips_handles_eof_offset_collision_0x454f46() -> None:
    # Ensure patching exact byte at 0x454F46 does NOT encode offset 0x454F46 ("EOF")!
    size = 0x460000
    orig = bytearray(size)
    mod = bytearray(orig)
    mod[0x454F46] = 0x77

    patch = create_ips(bytes(orig), bytes(mod))
    # First record offset after 'PATCH' should start at 0x454F45, not 0x454F46.
    assert patch[5:8] == bytes.fromhex("454F45")
    assert apply_ips(bytes(orig), patch) == bytes(mod)


def test_ips_extends_rom_and_truncates_when_smaller() -> None:
    orig = b"\x00" * 32
    extended = orig + b"\xab\xcd\xef"
    patch_ext = create_ips(orig, extended)
    assert apply_ips(orig, patch_ext) == extended

    shorter = b"\x11" * 16
    patch_short = create_ips(orig, shorter)
    assert apply_ips(orig, patch_short) == shorter


def test_ips_splits_large_chunks_over_64kb() -> None:
    orig = bytes(range(256)) * 400
    mod = bytes((b ^ 0xAA) for b in orig)
    patch = create_ips(orig, mod, use_rle=False)
    assert apply_ips(orig, patch) == mod


def test_ips_invalid_inputs_raise() -> None:
    with pytest.raises(IpsFormatError, match="missing 'PATCH'"):
        apply_ips(b"\x00" * 16, b"BADPATCH")
    with pytest.raises(IpsFormatError, match="missing 'EOF'"):
        apply_ips(b"\x00" * 16, b"PATCH\x00\x00\x01\x00\x01\xaa")
    with pytest.raises(IpsFormatError, match="exceeds 16 MiB"):
        create_ips(b"", b"\x01" * (0x1000000 + 2))
