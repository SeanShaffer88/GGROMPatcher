"""Build a self-contained pure-Python .pyz executable archive using stdlib zipapp."""

from __future__ import annotations

import argparse
import shutil
import tempfile
import zipapp
from pathlib import Path


def build_zipapp(output: str | Path = "dist/ggrompatcher.pyz") -> Path:
    """Package `src/ggrompatcher` into a standalone executable `.pyz` archive."""
    repo_root = Path(__file__).resolve().parent.parent
    pkg_src = repo_root / "src" / "ggrompatcher"
    if not pkg_src.is_dir():
        raise FileNotFoundError(f"Package directory not found: {pkg_src}")

    out_path = Path(output)
    if not out_path.is_absolute():
        out_path = repo_root / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        stage = Path(tmpdir) / "app"
        stage.mkdir()
        shutil.copytree(
            pkg_src,
            stage / "ggrompatcher",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
        zipapp.create_archive(
            stage,
            target=out_path,
            interpreter="/usr/bin/env python3",
            main="ggrompatcher.cli:main",
            compressed=True,
        )
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build standalone ggrompatcher.pyz zipapp.")
    parser.add_argument(
        "-o",
        "--output",
        default="dist/ggrompatcher.pyz",
        help="Output .pyz file path (default: dist/ggrompatcher.pyz).",
    )
    args = parser.parse_args(argv)
    built = build_zipapp(args.output)
    print(f"Built zipapp -> {built} ({built.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
