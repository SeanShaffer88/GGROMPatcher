"""Tests for cheat export, hex diff previews, batch ROM patching, and zipapp build."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scripts.build_zipapp import build_zipapp

from ggrompatcher.cheatfiles import CheatEntry, export_cheat_file, load_cheat_file
from ggrompatcher.patch_formats import write_binary_patch_file
from ggrompatcher.patcher import (
    batch_patch_roms,
    collect_rom_files,
    format_hex_diff_text,
    preview_patch_diff,
)
from tests.conftest import make_gb_rom, make_nes_rom


def test_export_cheat_file_txt_gg_and_retroarch_roundtrip(tmp_path: Path) -> None:
    entries = [
        CheatEntry(code="004-BCE-E66", description="Infinite Health", enabled=True),
        CheatEntry(code="SXIOPO", description="Infinite Lives", enabled=False),
        CheatEntry(code="PAR:8010?03:FF", description="Custom PAR", enabled=True),
    ]

    txt_path = export_cheat_file(tmp_path / "list.txt", entries)
    loaded_txt = load_cheat_file(txt_path)
    assert [e.code for e in loaded_txt] == ["004-BCE-E66", "PAR:8010?03:FF"]
    assert "[DISABLED] SXIOPO" in txt_path.read_text(encoding="utf-8")

    gg_path = export_cheat_file(tmp_path / "list.gg", entries)
    loaded_gg = load_cheat_file(gg_path)
    assert [e.code for e in loaded_gg] == ["004-BCE-E66", "PAR:8010?03:FF"]

    cht_path = export_cheat_file(tmp_path / "retro.cht", entries)
    loaded_cht = load_cheat_file(cht_path)
    assert [(e.code, e.description, e.enabled) for e in loaded_cht] == [
        ("004-BCE-E66", "Infinite Health", True),
        ("SXIOPO", "Infinite Lives", False),
        ("PAR:8010?03:FF", "Custom PAR", True),
    ]


def test_hex_diff_preview_and_truncation() -> None:
    rom = make_gb_rom(banks=2)
    rom[0x14BC] = 0x03
    rom[0x0500] = 0xAA

    res, preview = preview_patch_diff(bytes(rom), ["004-BCE-E66", "0500=FF"])
    assert res.bytes_modified == 2
    assert preview.bytes_changed == 4  # 2 patch bytes + 2 updated global checksum bytes
    assert preview.total_hunks >= 2
    rendered = format_hex_diff_text(preview)
    assert "0x0014BC" in rendered
    assert "- " in rendered
    assert "+ " in rendered

    _, truncated = preview_patch_diff(bytes(rom), ["004-BCE-E66", "0500=FF"], max_hunks=1)
    assert truncated.truncated is True
    assert len(truncated.hunks) == 1
    assert "truncated" in format_hex_diff_text(truncated)


def test_collect_and_batch_patch_roms_codes_and_binary_patches(tmp_path: Path) -> None:
    sub = tmp_path / "roms" / "nested"
    sub.mkdir(parents=True)

    gb1 = make_gb_rom(banks=2)
    gb1[0x14BC] = 0x03
    gb1_path = tmp_path / "roms" / "a.gb"
    gb1_path.write_bytes(bytes(gb1))

    gb2 = make_gb_rom(banks=2)
    gb2[0x14BC] = 0x03
    gb2_path = sub / "b.gb"
    gb2_path.write_bytes(bytes(gb2))

    # Non-ROM file should be ignored by folder collection
    (tmp_path / "roms" / "notes.txt").write_text("ignore me", encoding="utf-8")

    non_rec = collect_rom_files([tmp_path / "roms"], recursive=False)
    assert non_rec == [gb1_path]
    rec = collect_rom_files([tmp_path / "roms"], recursive=True)
    assert rec == [gb1_path, gb2_path]

    # Dry run does not write outputs
    out_dir = tmp_path / "out"
    dry_results = batch_patch_roms(
        rec,
        ["004-BCE-E66"],
        output_dir=out_dir,
        dry_run=True,
    )
    assert all(r.success for r in dry_results)
    assert not out_dir.exists()

    # Actual batch write with codes
    results = batch_patch_roms(rec, ["004-BCE-E66"], output_dir=out_dir)
    assert len(results) == 2
    assert all(r.success and r.output_path is not None for r in results)
    for r in results:
        assert r.output_path is not None
        assert r.output_path.read_bytes()[0x14BC] == 0x00

    # Batch apply a BPS patch across two identical NES ROMs + verify partial failure isolation
    nes = make_nes_rom(prg_banks_16k=2)
    nes_mod = bytearray(nes)
    nes_mod[0x1200] = 0xEA
    bps_path = write_binary_patch_file(tmp_path / "hack.bps", bytes(nes), bytes(nes_mod))

    nes_a = tmp_path / "nes_a.nes"
    nes_b = tmp_path / "nes_bad.nes"
    nes_a.write_bytes(bytes(nes))
    nes_b.write_bytes(b"NOT_A_VALID_MATCHING_ROM")

    out_bps_dir = tmp_path / "bps_out"
    bps_results = batch_patch_roms([nes_a, nes_b], patch_file=bps_path, output_dir=out_bps_dir)
    assert bps_results[0].success is True
    assert (out_bps_dir / "nes_a.nes").read_bytes() == bytes(nes_mod)
    assert bps_results[1].success is False
    assert "CRC32" in bps_results[1].error or "size" in bps_results[1].error


def test_build_zipapp_and_run_standalone(tmp_path: Path) -> None:
    pyz = build_zipapp(tmp_path / "ggrompatcher.pyz")
    assert pyz.is_file()
    proc = subprocess.run(
        [sys.executable, str(pyz), "decode", "-s", "gb", "004-BCE-E66"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "0x0014BC" in proc.stdout
