"""BPS (Beat Patching System) binary patch creation, validation, and application."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path
from typing import Any

from ggrompatcher.models import BpsFormatError, PatchFormatError

_BPS_MAGIC = b"BPS1"


def encode_vlq(value: int) -> bytes:
    """Encode a non-negative integer using byuu/Near's biased BPS/UPS VLQ scheme."""
    if value < 0:
        raise ValueError(f"VLQ integer cannot be negative: {value}")
    out = bytearray()
    data = value
    while True:
        x = data & 0x7F
        data >>= 7
        if data == 0:
            out.append(0x80 | x)
            break
        out.append(x)
        data -= 1
    return bytes(out)


def decode_vlq(
    data: bytes,
    pos: int,
    *,
    end: int | None = None,
    error_cls: type[PatchFormatError] = BpsFormatError,
) -> tuple[int, int]:
    """Decode one biased BPS/UPS VLQ integer starting at `pos`."""
    limit = len(data) if end is None else end
    value = 0
    shift = 1
    while True:
        if pos >= limit:
            raise error_cls("Unexpected end of patch while decoding variable-length integer.")
        byte = data[pos]
        pos += 1
        value += (byte & 0x7F) * shift
        if byte & 0x80:
            return value, pos
        shift <<= 7
        value += shift


def _encode_signed(delta: int) -> bytes:
    sign = 1 if delta < 0 else 0
    return encode_vlq((abs(delta) << 1) | sign)


def _decode_signed(data: bytes, pos: int, *, end: int) -> tuple[int, int]:
    raw, next_pos = decode_vlq(data, pos, end=end, error_cls=BpsFormatError)
    magnitude = raw >> 1
    return (-magnitude if (raw & 1) else magnitude), next_pos


def _build_source_index(source: bytes, window: int = 8) -> dict[bytes, int]:
    index: dict[bytes, int] = {}
    if len(source) < window:
        return index
    # Index first occurrence of non-overlapping/stepped chunks for fast delta SourceCopy matching.
    step = 1 if len(source) <= 262_144 else 4
    for off in range(0, len(source) - window + 1, step):
        key = source[off : off + window]
        if key not in index:
            index[key] = off
    return index


def create_bps(original: bytes, modified: bytes, *, metadata: str = "") -> bytes:
    """Create a BPS1 patch transforming `original` into `modified`."""
    out = bytearray(_BPS_MAGIC)
    out.extend(encode_vlq(len(original)))
    out.extend(encode_vlq(len(modified)))
    meta_bytes = metadata.encode("utf-8")
    out.extend(encode_vlq(len(meta_bytes)))
    out.extend(meta_bytes)

    src_len = len(original)
    tgt_len = len(modified)
    pos = 0
    src_rel_off = 0
    tgt_rel_off = 0
    src_index: dict[bytes, int] | None = None
    match_window = 8

    def _flush_literal(lit: bytearray) -> None:
        if lit:
            out.extend(encode_vlq(((len(lit) - 1) << 2) | 1))
            out.extend(lit)
            lit.clear()

    literal_buf = bytearray()

    while pos < tgt_len:
        # 1. Identical byte run at the exact same offset -> SourceRead (0)
        if pos < src_len and original[pos] == modified[pos]:
            _flush_literal(literal_buf)
            run = 1
            limit = min(src_len, tgt_len)
            while pos + run < limit and original[pos + run] == modified[pos + run]:
                run += 1
            out.extend(encode_vlq(((run - 1) << 2) | 0))
            pos += run
            continue

        # 2. Repeated identical byte run >= 6 bytes -> TargetRead(1) + TargetCopy(3) RLE
        if pos + 5 < tgt_len and modified[pos : pos + 6] == bytes([modified[pos]]) * 6:
            _flush_literal(literal_buf)
            byte_val = modified[pos]
            run = 6
            while pos + run < tgt_len and modified[pos + run] == byte_val:
                run += 1
            out.extend(encode_vlq(1))  # TargetRead length=1
            out.append(byte_val)
            copy_len = run - 1
            out.extend(encode_vlq(((copy_len - 1) << 2) | 3))
            out.extend(_encode_signed(pos - tgt_rel_off))
            tgt_rel_off = pos + copy_len
            pos += run
            continue

        # 3. Check shifted match in source (>= 8 bytes) via lazy index when sizes differ
        if src_len != tgt_len and pos + match_window <= tgt_len:
            if src_index is None:
                src_index = _build_source_index(original, window=match_window)
            src_match = src_index.get(modified[pos : pos + match_window])
            if src_match is not None and src_match != pos:
                run = match_window
                while (
                    pos + run < tgt_len
                    and src_match + run < src_len
                    and original[src_match + run] == modified[pos + run]
                ):
                    run += 1
                _flush_literal(literal_buf)
                out.extend(encode_vlq(((run - 1) << 2) | 2))
                out.extend(_encode_signed(src_match - src_rel_off))
                src_rel_off = src_match + run
                pos += run
                continue

        literal_buf.append(modified[pos])
        pos += 1

    _flush_literal(literal_buf)

    src_crc = zlib.crc32(original) & 0xFFFFFFFF
    tgt_crc = zlib.crc32(modified) & 0xFFFFFFFF
    out.extend(struct.pack("<II", src_crc, tgt_crc))
    patch_crc = zlib.crc32(out) & 0xFFFFFFFF
    out.extend(struct.pack("<I", patch_crc))
    return bytes(out)


def inspect_bps(patch_bytes: bytes) -> dict[str, Any]:
    """Read header metadata and CRC32 footer from a BPS1 patch."""
    if len(patch_bytes) < 19 or not patch_bytes.startswith(_BPS_MAGIC):
        raise BpsFormatError("Invalid BPS file: missing 'BPS1' header.")
    body_end = len(patch_bytes) - 12
    pos = 4
    source_size, pos = decode_vlq(patch_bytes, pos, end=body_end)
    target_size, pos = decode_vlq(patch_bytes, pos, end=body_end)
    meta_size, pos = decode_vlq(patch_bytes, pos, end=body_end)
    if pos + meta_size > body_end:
        raise BpsFormatError("Corrupt BPS metadata length.")
    metadata = patch_bytes[pos : pos + meta_size].decode("utf-8", errors="replace")
    src_crc, tgt_crc, patch_crc = struct.unpack("<III", patch_bytes[-12:])
    actual_patch_crc = zlib.crc32(patch_bytes[:-4]) & 0xFFFFFFFF
    return {
        "format": "bps",
        "source_size": source_size,
        "target_size": target_size,
        "metadata": metadata,
        "source_crc32": src_crc,
        "target_crc32": tgt_crc,
        "patch_crc32": patch_crc,
        "patch_crc_valid": actual_patch_crc == patch_crc,
        "actions_offset": pos + meta_size,
    }


def apply_bps(
    original: bytes,
    patch_bytes: bytes,
    *,
    verify_checksums: bool = True,
) -> bytes:
    """Apply a BPS1 patch to `original` and return the patched ROM bytes."""
    info = inspect_bps(patch_bytes)
    if verify_checksums and not info["patch_crc_valid"]:
        raise BpsFormatError("BPS patch file checksum mismatch (corrupted patch file).")

    source_size = int(info["source_size"])
    target_size = int(info["target_size"])
    src_crc = int(info["source_crc32"])
    tgt_crc = int(info["target_crc32"])

    if verify_checksums:
        if len(original) != source_size:
            raise BpsFormatError(
                f"Input ROM size mismatch: expected {source_size:,} bytes, got {len(original):,}."
            )
        actual_src_crc = zlib.crc32(original) & 0xFFFFFFFF
        if actual_src_crc != src_crc:
            raise BpsFormatError(
                f"Input ROM CRC32 mismatch: expected 0x{src_crc:08X}, got 0x{actual_src_crc:08X}."
            )

    body_end = len(patch_bytes) - 12
    pos = int(info["actions_offset"])
    output = bytearray(target_size)
    out_pos = 0
    src_rel_off = 0
    tgt_rel_off = 0

    while pos < body_end:
        cmd_raw, pos = decode_vlq(patch_bytes, pos, end=body_end)
        action = cmd_raw & 3
        length = (cmd_raw >> 2) + 1
        if out_pos + length > target_size:
            raise BpsFormatError("BPS action writes past declared target file size.")

        if action == 0:  # SourceRead
            if out_pos + length > len(original):
                raise BpsFormatError("BPS SourceRead reads past end of source ROM.")
            output[out_pos : out_pos + length] = original[out_pos : out_pos + length]
            out_pos += length
        elif action == 1:  # TargetRead
            if pos + length > body_end:
                raise BpsFormatError("BPS TargetRead runs past end of patch body.")
            output[out_pos : out_pos + length] = patch_bytes[pos : pos + length]
            pos += length
            out_pos += length
        elif action == 2:  # SourceCopy
            delta, pos = _decode_signed(patch_bytes, pos, end=body_end)
            src_rel_off += delta
            if src_rel_off < 0 or src_rel_off + length > len(original):
                raise BpsFormatError("BPS SourceCopy offset out of source ROM bounds.")
            output[out_pos : out_pos + length] = original[src_rel_off : src_rel_off + length]
            src_rel_off += length
            out_pos += length
        else:  # action == 3: TargetCopy
            delta, pos = _decode_signed(patch_bytes, pos, end=body_end)
            tgt_rel_off += delta
            if tgt_rel_off < 0 or tgt_rel_off >= out_pos:
                raise BpsFormatError("BPS TargetCopy offset out of written target bounds.")
            for _ in range(length):
                if tgt_rel_off >= out_pos:
                    raise BpsFormatError("BPS TargetCopy offset overran output cursor.")
                output[out_pos] = output[tgt_rel_off]
                out_pos += 1
                tgt_rel_off += 1

    if out_pos != target_size:
        raise BpsFormatError(
            f"BPS output size mismatch: wrote {out_pos:,} bytes, expected {target_size:,}."
        )

    result = bytes(output)
    if verify_checksums:
        actual_tgt_crc = zlib.crc32(result) & 0xFFFFFFFF
        if actual_tgt_crc != tgt_crc:
            raise BpsFormatError(
                f"Output ROM CRC32 mismatch: expected 0x{tgt_crc:08X}, got 0x{actual_tgt_crc:08X}."
            )
    return result


def write_bps_file(
    path: str | Path,
    original: bytes,
    modified: bytes,
    *,
    metadata: str = "",
) -> Path:
    """Create a BPS1 patch from `original` -> `modified` and write it to `path`."""
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(create_bps(original, modified, metadata=metadata))
    return dest
