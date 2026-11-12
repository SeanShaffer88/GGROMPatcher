"""PlayStation Patch Format (PPF 1.0 / 2.0 / 3.0) encoder, inspector, and applier."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

from ggrompatcher.models import PpfFormatError

PPF_BIN_IMAGE = 0x00
PPF_GI_IMAGE = 0x01
_BLOCKCHECK_SIZE = 1024
_BLOCKCHECK_OFFSET_BIN = 0x9320
_BLOCKCHECK_OFFSET_GI = 0x80A0
_FILE_ID_BEGIN = b"@BEGIN_FILE_ID.DIZ"
_FILE_ID_END = b"@END_FILE_ID.DIZ"
_MAX_FILE_ID_LEN = 3072


@dataclass(frozen=True)
class PpfRecord:
    """Single contiguous byte patch record inside a PPF file."""

    offset: int
    data: bytes
    undo_data: bytes | None = None


@dataclass(frozen=True)
class PpfPatchInfo:
    """Parsed structural summary of a PPF 1.0/2.0/3.0 patch."""

    version: int
    description: str
    image_type: int
    has_blockcheck: bool
    has_undo: bool
    input_size: int | None
    record_count: int
    records: tuple[PpfRecord, ...]
    blockcheck: bytes | None = None
    file_id_diz: str = ""


def _parse_ppf(patch_bytes: bytes) -> PpfPatchInfo:
    if len(patch_bytes) < 56:
        raise PpfFormatError("PPF patch is too short (minimum 56 bytes for header).")

    magic = patch_bytes[:5]
    enc = patch_bytes[5]
    expected = {b"PPF10": (1, 0), b"PPF20": (2, 1), b"PPF30": (3, 2)}
    if magic not in expected or enc != expected[magic][1]:
        raise PpfFormatError(
            "Invalid PPF magic/encoding header (expected PPF10\\x00, PPF20\\x01, or PPF30\\x02)."
        )
    version = expected[magic][0]
    description = patch_bytes[6:56].decode("latin-1").rstrip(" \x00")
    pos = 56

    image_type = PPF_BIN_IMAGE
    has_blockcheck = False
    has_undo = False
    input_size: int | None = None
    blockcheck: bytes | None = None

    if version == 2:
        if len(patch_bytes) < pos + 4 + _BLOCKCHECK_SIZE:
            raise PpfFormatError("Truncated PPF 2.0 header / blockcheck section.")
        input_size = struct.unpack_from("<I", patch_bytes, pos)[0]
        pos += 4
        has_blockcheck = True
        blockcheck = patch_bytes[pos : pos + _BLOCKCHECK_SIZE]
        pos += _BLOCKCHECK_SIZE
    elif version == 3:
        if len(patch_bytes) < pos + 4:
            raise PpfFormatError("Truncated PPF 3.0 extended header.")
        image_type = patch_bytes[pos]
        has_blockcheck = patch_bytes[pos + 1] != 0
        has_undo = patch_bytes[pos + 2] != 0
        # pos + 3 is reserved dummy byte
        pos += 4
        if image_type not in {PPF_BIN_IMAGE, PPF_GI_IMAGE}:
            raise PpfFormatError(f"Unsupported PPF 3.0 image type: 0x{image_type:02X}.")
        if has_blockcheck:
            if len(patch_bytes) < pos + _BLOCKCHECK_SIZE:
                raise PpfFormatError("Truncated PPF 3.0 1024-byte blockcheck section.")
            blockcheck = patch_bytes[pos : pos + _BLOCKCHECK_SIZE]
            pos += _BLOCKCHECK_SIZE

    records: list[PpfRecord] = []
    file_id_diz = ""
    end = len(patch_bytes)

    while pos < end:
        if patch_bytes.startswith(_FILE_ID_BEGIN, pos):
            diz_start = pos + len(_FILE_ID_BEGIN)
            diz_end = patch_bytes.find(_FILE_ID_END, diz_start)
            if diz_end < 0:
                raise PpfFormatError("PPF FILE_ID.DIZ block missing @END_FILE_ID.DIZ marker.")
            diz_bytes = patch_bytes[diz_start:diz_end]
            file_id_diz = diz_bytes.decode("latin-1", errors="replace")
            break

        if version == 3:
            if pos + 9 > end:
                raise PpfFormatError("Truncated PPF 3.0 patch record header.")
            offset = struct.unpack_from("<Q", patch_bytes, pos)[0]
            pos += 8
        else:
            if pos + 5 > end:
                raise PpfFormatError(f"Truncated PPF {version}.0 patch record header.")
            offset = struct.unpack_from("<I", patch_bytes, pos)[0]
            pos += 4

        length = patch_bytes[pos]
        pos += 1
        needed = length * (2 if has_undo else 1)
        if pos + needed > end:
            raise PpfFormatError(f"Truncated PPF record payload at offset 0x{offset:X}.")
        data = patch_bytes[pos : pos + length]
        pos += length
        undo_data: bytes | None = None
        if has_undo:
            undo_data = patch_bytes[pos : pos + length]
            pos += length
        records.append(PpfRecord(offset=offset, data=data, undo_data=undo_data))

    return PpfPatchInfo(
        version=version,
        description=description,
        image_type=image_type,
        has_blockcheck=has_blockcheck,
        has_undo=has_undo,
        input_size=input_size,
        record_count=len(records),
        records=tuple(records),
        blockcheck=blockcheck,
        file_id_diz=file_id_diz,
    )


def inspect_ppf(patch_bytes: bytes) -> PpfPatchInfo:
    """Parse and inspect a PPF (1.0/2.0/3.0) patch without modifying any ROM."""
    return _parse_ppf(patch_bytes)


def create_ppf(
    original: bytes,
    modified: bytes,
    *,
    description: str = "Created with GGROMPatcher",
    version: int = 3,
    image_type: int = PPF_BIN_IMAGE,
    blockcheck: bool = False,
    undo: bool = False,
    file_id_diz: str = "",
) -> bytes:
    """Create a PPF 1.0, 2.0, or 3.0 binary patch transforming `original` into `modified`."""
    if version not in {1, 2, 3}:
        raise PpfFormatError(f"PPF version must be 1, 2, or 3 (got {version}).")
    if version < 3 and undo:
        raise PpfFormatError("PPF undo data is only supported in PPF 3.0.")
    if image_type not in {PPF_BIN_IMAGE, PPF_GI_IMAGE}:
        raise PpfFormatError(f"PPF image_type must be 0 (BIN) or 1 (GI), got {image_type}.")

    desc_bytes = description.encode("latin-1", errors="replace")[:50].ljust(50, b" ")
    out = bytearray()
    out.extend(f"PPF{version}0".encode("ascii"))
    out.append(version - 1)
    out.extend(desc_bytes)

    check_off = _BLOCKCHECK_OFFSET_GI if image_type == PPF_GI_IMAGE else _BLOCKCHECK_OFFSET_BIN

    if version == 2:
        if len(original) > 0xFFFFFFFF:
            raise PpfFormatError("Original file exceeds 4 GiB limit for PPF 2.0.")
        out.extend(struct.pack("<I", len(original)))
        chk_slice = original[check_off : check_off + _BLOCKCHECK_SIZE].ljust(
            _BLOCKCHECK_SIZE, b"\x00"
        )
        out.extend(chk_slice)
    elif version == 3:
        use_check = 1 if blockcheck else 0
        use_undo = 1 if undo else 0
        out.extend(bytes([image_type, use_check, use_undo, 0x00]))
        if use_check:
            if len(original) < check_off + _BLOCKCHECK_SIZE:
                raise PpfFormatError(
                    f"PPF 3.0 blockcheck requires original file of at least "
                    f"0x{check_off + _BLOCKCHECK_SIZE:X} bytes."
                )
            out.extend(original[check_off : check_off + _BLOCKCHECK_SIZE])

    if len(modified) < len(original):
        raise PpfFormatError(
            "PPF cannot truncate files shorter than the original; use BPS, UPS, or APS instead."
        )

    target_len = len(modified)
    if version < 3 and target_len > 0xFFFFFFFF:
        raise PpfFormatError(f"File offset exceeds 4 GiB limit for PPF {version}.0.")

    i = 0
    last_written_end = 0
    while i < len(modified):
        orig_b = original[i] if i < len(original) else 0x00
        if orig_b == modified[i]:
            i += 1
            continue
        start = i
        chunk_mod = bytearray()
        chunk_orig = bytearray()
        while i < len(modified) and len(chunk_mod) < 0xFF:
            ob = original[i] if i < len(original) else 0x00
            if ob == modified[i]:
                break
            chunk_mod.append(modified[i])
            chunk_orig.append(ob)
            i += 1
        if version == 3:
            out.extend(struct.pack("<QB", start, len(chunk_mod)))
        else:
            out.extend(struct.pack("<IB", start, len(chunk_mod)))
        out.extend(chunk_mod)
        if undo:
            out.extend(chunk_orig)
        last_written_end = i

    # Preserve zero-filled expansion length if modified ends with 0x00 beyond original.
    if len(modified) > max(len(original), last_written_end):
        last_off = len(modified) - 1
        if version == 3:
            out.extend(struct.pack("<QB", last_off, 1))
        else:
            out.extend(struct.pack("<IB", last_off, 1))
        out.append(0x00)
        if undo:
            out.append(0x00)

    if file_id_diz:
        diz_bytes = file_id_diz.encode("latin-1", errors="replace")[:_MAX_FILE_ID_LEN]
        out.extend(_FILE_ID_BEGIN)
        out.extend(diz_bytes)
        out.extend(_FILE_ID_END)
        out.extend(struct.pack("<HH", len(diz_bytes), 0))

    return bytes(out)


def apply_ppf(
    original: bytes,
    patch_bytes: bytes,
    *,
    verify_checksums: bool = True,
    undo: bool = False,
) -> bytes:
    """Apply (or revert with `undo=True`) a PPF 1.0/2.0/3.0 patch to `original`."""
    info = _parse_ppf(patch_bytes)

    if undo and not info.has_undo:
        raise PpfFormatError("PPF patch does not contain undo data.")

    if verify_checksums and not undo:
        if info.version == 2 and info.input_size is not None and len(original) != info.input_size:
            raise PpfFormatError(
                f"PPF 2.0 source size mismatch: patch expects {info.input_size} bytes, "
                f"got {len(original)}."
            )
        if info.has_blockcheck and info.blockcheck is not None:
            check_off = (
                _BLOCKCHECK_OFFSET_GI if info.image_type == PPF_GI_IMAGE else _BLOCKCHECK_OFFSET_BIN
            )
            if len(original) >= check_off + _BLOCKCHECK_SIZE:
                actual = original[check_off : check_off + _BLOCKCHECK_SIZE]
                if actual != info.blockcheck:
                    raise PpfFormatError(
                        f"PPF {info.version}.0 blockcheck mismatch at offset 0x{check_off:X}."
                    )

    needed_size = len(original)
    for rec in info.records:
        needed_size = max(needed_size, rec.offset + len(rec.data))

    buf = bytearray(original)
    if len(buf) < needed_size:
        buf.extend(b"\x00" * (needed_size - len(buf)))

    for rec in info.records:
        payload = rec.undo_data if (undo and rec.undo_data is not None) else rec.data
        buf[rec.offset : rec.offset + len(payload)] = payload

    return bytes(buf)


def write_ppf_file(
    path: str | Path,
    original: bytes,
    modified: bytes,
    *,
    description: str = "Created with GGROMPatcher",
    version: int = 3,
    blockcheck: bool = False,
    undo: bool = False,
    file_id_diz: str = "",
) -> Path:
    """Write a `.ppf` patch transforming `original` into `modified`."""
    dest = Path(path)
    payload = create_ppf(
        original,
        modified,
        description=description,
        version=version,
        blockcheck=blockcheck,
        undo=undo,
        file_id_diz=file_id_diz,
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(payload)
    return dest
