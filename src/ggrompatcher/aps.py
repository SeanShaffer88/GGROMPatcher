"""APS binary patch support for APS-N64/Simple (`APS10`) and APS-GBA (`APS1`)."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

from ggrompatcher.models import ApsFormatError

APS_N64_MAGIC = b"APS10"
APS_GBA_MAGIC = b"APS1"
APS_HEADER_SIMPLE = 0
APS_HEADER_N64 = 1
APS_GBA_BLOCK_SIZE = 0x10000  # 64 KiB
_APS_GBA_RECORD_SIZE = 4 + 2 + 2 + APS_GBA_BLOCK_SIZE


def crc16_ccitt_false(data: bytes | bytearray | memoryview) -> int:
    """Compute CRC-16/CCITT-FALSE (poly=0x1021, init=0xFFFF) used by APS-GBA."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if (crc & 0x8000) else (crc << 1) & 0xFFFF
    return crc


@dataclass(frozen=True)
class ApsN64Record:
    """One APS10 record (simple byte slice or RLE run)."""

    offset: int
    data: bytes | None = None
    rle_byte: int | None = None
    rle_length: int = 0


@dataclass(frozen=True)
class ApsGbaRecord:
    """One 64 KiB XOR block record inside an APS-GBA (`APS1`) patch."""

    offset: int
    source_crc16: int
    target_crc16: int
    xor_bytes: bytes


@dataclass(frozen=True)
class ApsPatchInfo:
    """Parsed structural summary of an APS-N64 (`APS10`) or APS-GBA (`APS1`) patch."""

    variant: str  # "aps10" or "aps-gba"
    description: str
    output_size: int
    record_count: int
    header_type: int = APS_HEADER_SIMPLE
    source_size: int | None = None
    n64_format: int | None = None
    n64_cart_id: str = ""
    n64_crc: bytes = b""


def _parse_aps10(
    patch_bytes: bytes,
) -> tuple[ApsPatchInfo, list[ApsN64Record]]:
    if len(patch_bytes) < 61:
        raise ApsFormatError("Truncated APS10 patch header (minimum 61 bytes).")

    header_type = patch_bytes[5]
    encoding_method = patch_bytes[6]
    if encoding_method != 0:
        raise ApsFormatError(f"Unsupported APS10 encoding method: {encoding_method}.")

    description = patch_bytes[7:57].decode("latin-1").rstrip(" \x00")
    pos = 57

    n64_format: int | None = None
    n64_cart_id = ""
    n64_crc = b""

    if header_type == APS_HEADER_N64:
        if len(patch_bytes) < pos + 17 + 4:
            raise ApsFormatError("Truncated APS10 N64 validation header.")
        n64_format = patch_bytes[pos]
        n64_cart_id = patch_bytes[pos + 1 : pos + 4].decode("latin-1", errors="replace")
        n64_crc = patch_bytes[pos + 4 : pos + 12]
        pos += 17  # 1 format + 3 cartId + 8 crc + 5 padding
    elif header_type != APS_HEADER_SIMPLE:
        raise ApsFormatError(f"Unsupported APS10 header type: {header_type}.")

    if len(patch_bytes) < pos + 4:
        raise ApsFormatError("Truncated APS10 output size field.")
    output_size = struct.unpack_from("<I", patch_bytes, pos)[0]
    pos += 4

    records: list[ApsN64Record] = []
    end = len(patch_bytes)
    while pos < end:
        if pos + 5 > end:
            raise ApsFormatError("Truncated APS10 record header.")
        offset = struct.unpack_from("<I", patch_bytes, pos)[0]
        length = patch_bytes[pos + 4]
        pos += 5
        if length == 0:
            if pos + 2 > end:
                raise ApsFormatError("Truncated APS10 RLE record.")
            rle_byte = patch_bytes[pos]
            rle_len = patch_bytes[pos + 1]
            pos += 2
            records.append(ApsN64Record(offset=offset, rle_byte=rle_byte, rle_length=rle_len))
        else:
            if pos + length > end:
                raise ApsFormatError("Truncated APS10 data record.")
            data = patch_bytes[pos : pos + length]
            pos += length
            records.append(ApsN64Record(offset=offset, data=data))

    info = ApsPatchInfo(
        variant="aps10",
        description=description,
        output_size=output_size,
        record_count=len(records),
        header_type=header_type,
        n64_format=n64_format,
        n64_cart_id=n64_cart_id,
        n64_crc=n64_crc,
    )
    return info, records


def _parse_aps_gba(
    patch_bytes: bytes,
) -> tuple[ApsPatchInfo, list[ApsGbaRecord]]:
    if len(patch_bytes) < 12 or (len(patch_bytes) - 12) % _APS_GBA_RECORD_SIZE != 0:
        raise ApsFormatError(
            "Invalid APS-GBA ('APS1') patch length: must equal 12 + N * 65544 bytes."
        )
    source_size, target_size = struct.unpack_from("<II", patch_bytes, 4)
    records: list[ApsGbaRecord] = []
    pos = 12
    while pos < len(patch_bytes):
        offset, src_crc, dst_crc = struct.unpack_from("<IHH", patch_bytes, pos)
        pos += 8
        xor_bytes = patch_bytes[pos : pos + APS_GBA_BLOCK_SIZE]
        pos += APS_GBA_BLOCK_SIZE
        records.append(
            ApsGbaRecord(
                offset=offset,
                source_crc16=src_crc,
                target_crc16=dst_crc,
                xor_bytes=xor_bytes,
            )
        )

    info = ApsPatchInfo(
        variant="aps-gba",
        description="APS-GBA 64KiB XOR Block Patch",
        output_size=target_size,
        source_size=source_size,
        record_count=len(records),
    )
    return info, records


def inspect_aps(patch_bytes: bytes) -> ApsPatchInfo:
    """Parse and inspect an APS-N64 (`APS10`) or APS-GBA (`APS1`) patch."""
    if patch_bytes.startswith(APS_N64_MAGIC):
        info, _ = _parse_aps10(patch_bytes)
        return info
    if patch_bytes.startswith(APS_GBA_MAGIC):
        info, _ = _parse_aps_gba(patch_bytes)
        return info
    raise ApsFormatError("Invalid APS patch: missing 'APS10' or 'APS1' magic header.")


def create_aps(
    original: bytes,
    modified: bytes,
    *,
    description: str = "Created with GGROMPatcher",
    variant: str = "auto",
    n64_v64: bool = False,
) -> bytes:
    """Create an APS (`APS10` or `APS1` GBA) patch transforming `original` into `modified`."""
    mode = variant.strip().lower()
    if mode not in {"auto", "aps10", "simple", "n64", "gba", "aps1"}:
        raise ApsFormatError(
            f"Unsupported APS variant '{variant}'. Expected auto, n64, simple, or gba."
        )

    if mode in {"gba", "aps1"}:
        if len(original) > 0xFFFFFFFF or len(modified) > 0xFFFFFFFF:
            raise ApsFormatError("ROM exceeds 4 GiB APS 32-bit size limit.")
        out = bytearray(APS_GBA_MAGIC)
        out.extend(struct.pack("<II", len(original), len(modified)))
        max_len = max(len(original), len(modified))
        for off in range(0, max_len, APS_GBA_BLOCK_SIZE):
            src_blk = original[off : off + APS_GBA_BLOCK_SIZE].ljust(APS_GBA_BLOCK_SIZE, b"\x00")
            dst_blk = modified[off : off + APS_GBA_BLOCK_SIZE].ljust(APS_GBA_BLOCK_SIZE, b"\x00")
            if src_blk == dst_blk:
                continue
            src_crc = crc16_ccitt_false(src_blk)
            dst_crc = crc16_ccitt_false(dst_blk)
            xor_blk = bytes(a ^ b for a, b in zip(src_blk, dst_blk, strict=True))
            out.extend(struct.pack("<IHH", off, src_crc, dst_crc))
            out.extend(xor_blk)
        return bytes(out)

    if len(modified) > 0xFFFFFFFF:
        raise ApsFormatError("Modified ROM exceeds 4 GiB APS10 32-bit size limit.")

    is_n64 = mode == "n64" or (
        mode == "auto" and len(original) >= 0x40 and original[:4] == b"\x80\x37\x12\x40"
    )
    if is_n64 and len(original) < 0x40:
        raise ApsFormatError("N64 APS header mode requires at least 0x40 bytes in original ROM.")

    desc_bytes = description.encode("latin-1", errors="replace")[:50].ljust(50, b" ")
    out = bytearray(APS_N64_MAGIC)
    out.append(APS_HEADER_N64 if is_n64 else APS_HEADER_SIMPLE)
    out.append(0x00)  # encodingMethod = 0
    out.extend(desc_bytes)

    if is_n64:
        out.append(0x00 if n64_v64 else 0x01)
        out.extend(original[0x3C:0x3F])
        out.extend(original[0x10:0x18])
        out.extend(b"\x00" * 5)

    out.extend(struct.pack("<I", len(modified)))

    i = 0
    while i < len(modified):
        orig_b = original[i] if i < len(original) else 0x00
        if orig_b == modified[i]:
            i += 1
            continue
        start = i
        diff = bytearray()
        is_rle = True
        first = modified[i]
        while i < len(modified) and len(diff) < 0xFF:
            ob = original[i] if i < len(original) else 0x00
            if ob == modified[i]:
                break
            b = modified[i]
            if b != first:
                is_rle = False
            diff.append(b)
            i += 1

        out.extend(struct.pack("<I", start))
        if is_rle and len(diff) > 2:
            out.extend(bytes([0x00, first, len(diff)]))
        else:
            out.append(len(diff))
            out.extend(diff)

    return bytes(out)


def apply_aps(
    original: bytes,
    patch_bytes: bytes,
    *,
    verify_checksums: bool = True,
) -> bytes:
    """Apply an APS (`APS10` N64/Simple or `APS1` GBA) patch to `original`."""
    if patch_bytes.startswith(APS_N64_MAGIC):
        info, n64_records = _parse_aps10(patch_bytes)
        if verify_checksums and info.header_type == APS_HEADER_N64:
            if len(original) < 0x40:
                raise ApsFormatError("Original ROM is too short to validate APS10 N64 header.")
            actual_cart = original[0x3C:0x3F].decode("latin-1", errors="replace")
            actual_crc = original[0x10:0x18]
            if actual_cart != info.n64_cart_id or actual_crc != info.n64_crc:
                raise ApsFormatError(
                    "APS10 N64 source ROM header mismatch "
                    f"(expected cart '{info.n64_cart_id}', got '{actual_cart}')."
                )

        out = bytearray(info.output_size)
        copy_len = min(len(original), info.output_size)
        out[:copy_len] = original[:copy_len]

        for n64_rec in n64_records:
            if n64_rec.rle_byte is not None:
                end = n64_rec.offset + n64_rec.rle_length
                if end > len(out):
                    raise ApsFormatError(
                        f"APS10 RLE record at 0x{n64_rec.offset:X}+{n64_rec.rle_length} "
                        "exceeds output size."
                    )
                out[n64_rec.offset : end] = bytes([n64_rec.rle_byte]) * n64_rec.rle_length
            elif n64_rec.data is not None:
                end = n64_rec.offset + len(n64_rec.data)
                if end > len(out):
                    raise ApsFormatError(
                        f"APS10 record at 0x{n64_rec.offset:X}+{len(n64_rec.data)} "
                        "exceeds output size."
                    )
                out[n64_rec.offset : end] = n64_rec.data
        return bytes(out)

    if patch_bytes.startswith(APS_GBA_MAGIC):
        info, gba_records = _parse_aps_gba(patch_bytes)
        if verify_checksums and info.source_size is not None and len(original) != info.source_size:
            raise ApsFormatError(
                f"APS-GBA source size mismatch: expected {info.source_size} bytes, "
                f"got {len(original)}."
            )

        out = bytearray(info.output_size)
        copy_len = min(len(original), info.output_size)
        out[:copy_len] = original[:copy_len]

        for gba_rec in gba_records:
            src_blk = original[gba_rec.offset : gba_rec.offset + APS_GBA_BLOCK_SIZE].ljust(
                APS_GBA_BLOCK_SIZE, b"\x00"
            )
            if verify_checksums and crc16_ccitt_false(src_blk) != gba_rec.source_crc16:
                raise ApsFormatError(
                    f"APS-GBA source block CRC-16 mismatch at offset 0x{gba_rec.offset:X}."
                )
            patched_blk = bytes(a ^ b for a, b in zip(src_blk, gba_rec.xor_bytes, strict=True))
            if verify_checksums and crc16_ccitt_false(patched_blk) != gba_rec.target_crc16:
                raise ApsFormatError(
                    f"APS-GBA target block CRC-16 mismatch at offset 0x{gba_rec.offset:X}."
                )
            write_len = min(APS_GBA_BLOCK_SIZE, max(0, info.output_size - gba_rec.offset))
            if write_len > 0:
                out[gba_rec.offset : gba_rec.offset + write_len] = patched_blk[:write_len]
        return bytes(out)

    raise ApsFormatError("Invalid APS patch: missing 'APS10' or 'APS1' magic header.")


def write_aps_file(
    path: str | Path,
    original: bytes,
    modified: bytes,
    *,
    description: str = "Created with GGROMPatcher",
    variant: str = "auto",
) -> Path:
    """Write an `.aps` patch transforming `original` into `modified`."""
    dest = Path(path)
    payload = create_aps(original, modified, description=description, variant=variant)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(payload)
    return dest
