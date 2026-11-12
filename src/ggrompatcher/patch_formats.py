"""Unified binary patch format dispatch across IPS, BPS, and UPS."""

from __future__ import annotations

from pathlib import Path

from ggrompatcher.bps import apply_bps, create_bps
from ggrompatcher.ips import apply_ips, create_ips
from ggrompatcher.models import PatchFormatError
from ggrompatcher.ups import apply_ups, create_ups

SUPPORTED_PATCH_FORMATS = ("ips", "bps", "ups")


def detect_patch_format(
    patch_bytes: bytes | None = None,
    filename: str | Path | None = None,
) -> str:
    """Identify `'ips'`, `'bps'`, or `'ups'` from magic bytes or file extension."""
    if patch_bytes is not None:
        if patch_bytes.startswith(b"PATCH"):
            return "ips"
        if patch_bytes.startswith(b"BPS1"):
            return "bps"
        if patch_bytes.startswith(b"UPS1"):
            return "ups"

    if filename is not None:
        ext = Path(filename).suffix.lower().lstrip(".")
        if ext in SUPPORTED_PATCH_FORMATS:
            return ext

    raise PatchFormatError(
        "Unrecognized patch format. Supported binary patch formats: .ips (PATCH), "
        ".bps (BPS1), and .ups (UPS1)."
    )


def create_binary_patch(
    original: bytes,
    modified: bytes,
    *,
    fmt: str = "ips",
    metadata: str = "",
) -> bytes:
    """Generate an IPS, BPS, or UPS patch transforming `original` into `modified`."""
    kind = fmt.strip().lower().lstrip(".")
    if kind == "ips":
        return create_ips(original, modified)
    if kind == "bps":
        return create_bps(original, modified, metadata=metadata)
    if kind == "ups":
        return create_ups(original, modified)
    raise PatchFormatError(f"Unsupported patch format '{fmt}'. Expected ips, bps, or ups.")


def apply_binary_patch(
    original: bytes,
    patch_bytes: bytes,
    *,
    fmt: str | None = None,
    filename: str | Path | None = None,
    verify_checksums: bool = True,
) -> bytes:
    """Apply an IPS, BPS, or UPS patch to `original` bytes."""
    kind = fmt.strip().lower().lstrip(".") if fmt else detect_patch_format(patch_bytes, filename)
    if kind == "ips":
        return apply_ips(original, patch_bytes)
    if kind == "bps":
        return apply_bps(original, patch_bytes, verify_checksums=verify_checksums)
    if kind == "ups":
        return apply_ups(original, patch_bytes, verify_checksums=verify_checksums)
    raise PatchFormatError(f"Unsupported patch format '{kind}'. Expected ips, bps, or ups.")


def write_binary_patch_file(
    path: str | Path,
    original: bytes,
    modified: bytes,
    *,
    fmt: str | None = None,
    metadata: str = "",
) -> Path:
    """Write an IPS, BPS, or UPS patch to `path` (format inferred from suffix if omitted)."""
    dest = Path(path)
    kind = fmt.strip().lower().lstrip(".") if fmt else detect_patch_format(filename=dest)
    payload = create_binary_patch(original, modified, fmt=kind, metadata=metadata)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(payload)
    return dest
