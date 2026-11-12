"""Unified binary patch format dispatch across IPS, BPS, UPS, PPF, and APS."""

from __future__ import annotations

from pathlib import Path

from ggrompatcher.aps import apply_aps, create_aps
from ggrompatcher.bps import apply_bps, create_bps
from ggrompatcher.ips import apply_ips, create_ips
from ggrompatcher.models import PatchFormatError
from ggrompatcher.ppf import apply_ppf, create_ppf
from ggrompatcher.ups import apply_ups, create_ups

SUPPORTED_PATCH_FORMATS = ("ips", "bps", "ups", "ppf", "aps")


def detect_patch_format(
    patch_bytes: bytes | None = None,
    filename: str | Path | None = None,
) -> str:
    """Identify `'ips'`, `'bps'`, `'ups'`, `'ppf'`, or `'aps'` from magic bytes or extension."""
    if patch_bytes is not None:
        if patch_bytes.startswith(b"PATCH"):
            return "ips"
        if patch_bytes.startswith(b"BPS1"):
            return "bps"
        if patch_bytes.startswith(b"UPS1"):
            return "ups"
        if patch_bytes.startswith((b"PPF10", b"PPF20", b"PPF30")):
            return "ppf"
        if patch_bytes.startswith((b"APS10", b"APS1")):
            return "aps"

    if filename is not None:
        ext = Path(filename).suffix.lower().lstrip(".")
        if ext in SUPPORTED_PATCH_FORMATS:
            return ext

    raise PatchFormatError(
        "Unrecognized patch format. Supported binary patch formats: .ips (PATCH), "
        ".bps (BPS1), .ups (UPS1), .ppf (PPF10/20/30), and .aps (APS10/APS1)."
    )


def create_binary_patch(
    original: bytes,
    modified: bytes,
    *,
    fmt: str = "ips",
    metadata: str = "",
    ppf_version: int = 3,
    ppf_undo: bool = False,
    aps_variant: str = "auto",
) -> bytes:
    """Generate an IPS, BPS, UPS, PPF, or APS patch transforming `original` into `modified`."""
    kind = fmt.strip().lower().lstrip(".")
    if kind == "ips":
        return create_ips(original, modified)
    if kind == "bps":
        return create_bps(original, modified, metadata=metadata)
    if kind == "ups":
        return create_ups(original, modified)
    if kind == "ppf":
        desc = metadata or "Created with GGROMPatcher"
        return create_ppf(
            original,
            modified,
            description=desc,
            version=ppf_version,
            undo=ppf_undo,
        )
    if kind == "aps":
        desc = metadata or "Created with GGROMPatcher"
        return create_aps(
            original,
            modified,
            description=desc,
            variant=aps_variant,
        )
    raise PatchFormatError(
        f"Unsupported patch format '{fmt}'. Expected ips, bps, ups, ppf, or aps."
    )


def apply_binary_patch(
    original: bytes,
    patch_bytes: bytes,
    *,
    fmt: str | None = None,
    filename: str | Path | None = None,
    verify_checksums: bool = True,
    undo: bool = False,
) -> bytes:
    """Apply an IPS, BPS, UPS, PPF, or APS patch to `original` bytes."""
    kind = fmt.strip().lower().lstrip(".") if fmt else detect_patch_format(patch_bytes, filename)
    if kind == "ips":
        return apply_ips(original, patch_bytes)
    if kind == "bps":
        return apply_bps(original, patch_bytes, verify_checksums=verify_checksums)
    if kind == "ups":
        return apply_ups(original, patch_bytes, verify_checksums=verify_checksums)
    if kind == "ppf":
        return apply_ppf(original, patch_bytes, verify_checksums=verify_checksums, undo=undo)
    if kind == "aps":
        return apply_aps(original, patch_bytes, verify_checksums=verify_checksums)
    raise PatchFormatError(
        f"Unsupported patch format '{kind}'. Expected ips, bps, ups, ppf, or aps."
    )


def write_binary_patch_file(
    path: str | Path,
    original: bytes,
    modified: bytes,
    *,
    fmt: str | None = None,
    metadata: str = "",
    ppf_version: int = 3,
    ppf_undo: bool = False,
    aps_variant: str = "auto",
) -> Path:
    """Write an IPS, BPS, UPS, PPF, or APS patch to `path` (format inferred if omitted)."""
    dest = Path(path)
    kind = fmt.strip().lower().lstrip(".") if fmt else detect_patch_format(filename=dest)
    payload = create_binary_patch(
        original,
        modified,
        fmt=kind,
        metadata=metadata,
        ppf_version=ppf_version,
        ppf_undo=ppf_undo,
        aps_variant=aps_variant,
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(payload)
    return dest
