"""UPS (Universal Patching System) binary patch creation, validation, and application."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path
from typing import Any

from ggrompatcher.bps import decode_vlq, encode_vlq
from ggrompatcher.models import UpsFormatError

_UPS_MAGIC = b"UPS1"


def create_ups(original: bytes, modified: bytes) -> bytes:
    """Create a UPS1 patch transforming `original` into `modified`."""
    out = bytearray(_UPS_MAGIC)
    out.extend(encode_vlq(len(original)))
    out.extend(encode_vlq(len(modified)))

    src_len = len(original)
    tgt_len = len(modified)
    scan_len = max(src_len, tgt_len)
    cursor = 0
    i = 0

    while i < scan_len:
        orig_b = original[i] if i < src_len else 0
        mod_b = modified[i] if i < tgt_len else 0
        if orig_b == mod_b:
            i += 1
            continue

        skip = i - cursor
        out.extend(encode_vlq(skip))
        while i < scan_len:
            ob = original[i] if i < src_len else 0
            mb = modified[i] if i < tgt_len else 0
            xb = ob ^ mb
            if xb == 0:
                break
            out.append(xb)
            i += 1
        out.append(0x00)
        i += 1
        cursor = i

    src_crc = zlib.crc32(original) & 0xFFFFFFFF
    tgt_crc = zlib.crc32(modified) & 0xFFFFFFFF
    out.extend(struct.pack("<II", src_crc, tgt_crc))
    patch_crc = zlib.crc32(out) & 0xFFFFFFFF
    out.extend(struct.pack("<I", patch_crc))
    return bytes(out)


def inspect_ups(patch_bytes: bytes) -> dict[str, Any]:
    """Read header sizes and CRC32 footer from a UPS1 patch."""
    if len(patch_bytes) < 18 or not patch_bytes.startswith(_UPS_MAGIC):
        raise UpsFormatError("Invalid UPS file: missing 'UPS1' header.")
    body_end = len(patch_bytes) - 12
    pos = 4
    input_size, pos = decode_vlq(patch_bytes, pos, end=body_end, error_cls=UpsFormatError)
    output_size, pos = decode_vlq(patch_bytes, pos, end=body_end, error_cls=UpsFormatError)
    in_crc, out_crc, patch_crc = struct.unpack("<III", patch_bytes[-12:])
    actual_patch_crc = zlib.crc32(patch_bytes[:-4]) & 0xFFFFFFFF
    return {
        "format": "ups",
        "input_size": input_size,
        "output_size": output_size,
        "input_crc32": in_crc,
        "output_crc32": out_crc,
        "patch_crc32": patch_crc,
        "patch_crc_valid": actual_patch_crc == patch_crc,
        "blocks_offset": pos,
    }


def apply_ups(
    original: bytes,
    patch_bytes: bytes,
    *,
    verify_checksums: bool = True,
    allow_reverse: bool = True,
) -> bytes:
    """Apply a UPS1 patch (forward or bidirectional reverse) to `original`."""
    info = inspect_ups(patch_bytes)
    if verify_checksums and not info["patch_crc_valid"]:
        raise UpsFormatError("UPS patch file checksum mismatch (corrupted patch file).")

    input_size = int(info["input_size"])
    output_size = int(info["output_size"])
    in_crc = int(info["input_crc32"])
    out_crc = int(info["output_crc32"])
    actual_src_crc = zlib.crc32(original) & 0xFFFFFFFF

    reverse_mode = False
    if (
        allow_reverse
        and len(original) == output_size
        and actual_src_crc == out_crc
        and not (len(original) == input_size and actual_src_crc == in_crc)
    ):
        reverse_mode = True

    expected_in_size = output_size if reverse_mode else input_size
    expected_in_crc = out_crc if reverse_mode else in_crc
    target_size = input_size if reverse_mode else output_size
    expected_out_crc = in_crc if reverse_mode else out_crc

    if verify_checksums:
        if len(original) != expected_in_size:
            raise UpsFormatError(
                f"Input ROM size mismatch: expected {expected_in_size:,} bytes, "
                f"got {len(original):,}."
            )
        if actual_src_crc != expected_in_crc:
            raise UpsFormatError(
                f"Input ROM CRC32 mismatch: expected 0x{expected_in_crc:08X}, "
                f"got 0x{actual_src_crc:08X}."
            )

    body_end = len(patch_bytes) - 12
    pos = int(info["blocks_offset"])

    output = bytearray(target_size)
    copy_len = min(len(original), target_size)
    output[:copy_len] = original[:copy_len]

    rel_offset = 0
    while pos < body_end:
        skip, pos = decode_vlq(patch_bytes, pos, end=body_end, error_cls=UpsFormatError)
        rel_offset += skip
        while True:
            if pos >= body_end:
                raise UpsFormatError("Unexpected end of UPS XOR block before 0x00 terminator.")
            xb = patch_bytes[pos]
            pos += 1
            if xb == 0x00:
                rel_offset += 1
                break
            if rel_offset < target_size:
                output[rel_offset] ^= xb
            rel_offset += 1

    result = bytes(output)
    if verify_checksums:
        actual_out_crc = zlib.crc32(result) & 0xFFFFFFFF
        if actual_out_crc != expected_out_crc:
            raise UpsFormatError(
                f"Output ROM CRC32 mismatch: expected 0x{expected_out_crc:08X}, "
                f"got 0x{actual_out_crc:08X}."
            )
    return result


def write_ups_file(path: str | Path, original: bytes, modified: bytes) -> Path:
    """Create a UPS1 patch from `original` -> `modified` and write it to `path`."""
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(create_ups(original, modified))
    return dest
