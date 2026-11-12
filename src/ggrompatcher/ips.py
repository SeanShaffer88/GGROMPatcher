"""IPS (International Patching System) binary patch creation and application."""

from __future__ import annotations

from pathlib import Path

from ggrompatcher.models import IpsFormatError

IPS_HEADER = b"PATCH"
IPS_EOF = b"EOF"
_EOF_OFFSET = 0x454F46  # ASCII "EOF" — record offsets must never equal this literal value.
_MAX_IPS_OFFSET = 0xFFFFFF
_MAX_RECORD_LEN = 0xFFFF


def create_ips(original: bytes, modified: bytes, *, use_rle: bool = True) -> bytes:
    """Create a standard IPS patch encoding the difference from `original` to `modified`."""
    max_len = len(modified)
    if max_len > _MAX_IPS_OFFSET + 1:
        raise IpsFormatError(
            f"Target ROM size ({max_len} bytes) exceeds 16 MiB standard IPS 24-bit limit."
        )

    # Identify changed intervals, treating bytes past len(original) as changed.
    hunks: list[tuple[int, int]] = []
    i = 0
    while i < max_len:
        orig_byte = original[i] if i < len(original) else None
        if orig_byte != modified[i]:
            start = i
            i += 1
            while i < max_len:
                nxt = original[i] if i < len(original) else None
                if nxt == modified[i]:
                    # Coalesce tiny unchanged gaps (<= 5 bytes, cheaper than starting a new record).
                    gap = 1
                    while i + gap < max_len and gap <= 5:
                        g_orig = original[i + gap] if i + gap < len(original) else None
                        if g_orig != modified[i + gap]:
                            break
                        gap += 1
                    if i + gap < max_len and gap <= 5:
                        i += gap
                        continue
                    break
                i += 1
            hunks.append((start, i))
        else:
            i += 1

    out = bytearray(IPS_HEADER)
    for start, end in hunks:
        pos = start
        while pos < end:
            # Avoid starting a record at offset 0x454F46 ("EOF") by backing up 1 byte.
            if pos == _EOF_OFFSET:
                pos -= 1
            chunk_end = min(end, pos + _MAX_RECORD_LEN)
            chunk = modified[pos:chunk_end]
            if pos > _MAX_IPS_OFFSET:
                raise IpsFormatError(
                    f"Patch offset 0x{pos:X} exceeds 16 MiB standard IPS 24-bit limit."
                )

            if use_rle and len(chunk) >= 8 and chunk == bytes([chunk[0]]) * len(chunk):
                out.extend(pos.to_bytes(3, "big"))
                out.extend(b"\x00\x00")
                out.extend(len(chunk).to_bytes(2, "big"))
                out.append(chunk[0])
            else:
                out.extend(pos.to_bytes(3, "big"))
                out.extend(len(chunk).to_bytes(2, "big"))
                out.extend(chunk)
            pos = chunk_end

    out.extend(IPS_EOF)
    if len(modified) < len(original):
        # Standard Lunar IPS extension: optional 3-byte target truncation size after EOF.
        out.extend(len(modified).to_bytes(3, "big"))
    return bytes(out)


def apply_ips(rom: bytes, patch: bytes) -> bytes:
    """Apply an IPS binary patch to `rom`, returning the patched bytes."""
    if not patch.startswith(IPS_HEADER):
        raise IpsFormatError("Invalid IPS patch: missing 'PATCH' header.")

    out = bytearray(rom)
    pos = len(IPS_HEADER)
    found_eof = False

    while pos < len(patch):
        if pos + 3 > len(patch):
            raise IpsFormatError("Unexpected end of IPS patch while reading record offset.")
        marker = patch[pos : pos + 3]
        if marker == IPS_EOF:
            pos += 3
            found_eof = True
            break

        offset = int.from_bytes(marker, "big")
        pos += 3
        if pos + 2 > len(patch):
            raise IpsFormatError("Unexpected end of IPS patch while reading record length.")
        length = int.from_bytes(patch[pos : pos + 2], "big")
        pos += 2

        if length == 0:
            # RLE record: 2-byte count + 1-byte fill value.
            if pos + 3 > len(patch):
                raise IpsFormatError("Unexpected end of IPS patch inside RLE record.")
            rle_len = int.from_bytes(patch[pos : pos + 2], "big")
            fill_byte = patch[pos + 2]
            pos += 3
            end = offset + rle_len
            if end > len(out):
                out.extend(b"\x00" * (end - len(out)))
            out[offset:end] = bytes([fill_byte]) * rle_len
        else:
            if pos + length > len(patch):
                raise IpsFormatError("Unexpected end of IPS patch inside data record.")
            end = offset + length
            if end > len(out):
                out.extend(b"\x00" * (end - len(out)))
            out[offset:end] = patch[pos : pos + length]
            pos += length

    if not found_eof:
        raise IpsFormatError("Invalid IPS patch: missing 'EOF' marker.")

    if len(patch) - pos == 3:
        trunc_len = int.from_bytes(patch[pos : pos + 3], "big")
        if trunc_len < len(out):
            del out[trunc_len:]
    elif pos != len(patch):
        raise IpsFormatError(
            f"Malformed IPS patch: {len(patch) - pos} trailing byte(s) after 'EOF'."
        )

    return bytes(out)


def write_ips_file(path: str | Path, original: bytes, modified: bytes) -> Path:
    """Create and write an IPS file comparing `original` to `modified`."""
    dest = Path(path)
    dest.write_bytes(create_ips(original, modified))
    return dest
