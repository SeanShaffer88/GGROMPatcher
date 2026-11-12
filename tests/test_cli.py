"""End-to-end CLI tests across all subcommands."""

from __future__ import annotations

import json
from pathlib import Path

from ggrompatcher.cli import main
from tests.conftest import make_gb_rom, make_nes_rom


def test_cli_decode_and_encode_json(capsys: object) -> None:
    rc = main(["decode", "--system", "gb", "--json", "004-BCE-E66"])
    assert rc == 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    data = json.loads(captured.out)
    assert data[0]["address"] == "0x0014BC"
    assert data[0]["value"] == "0x00"
    assert data[0]["compare"] == "0x03"

    rc2 = main(["encode", "-s", "nes", "-a", "0x91D9", "-v", "0xAD"])
    assert rc2 == 0
    captured2 = capsys.readouterr()  # type: ignore[attr-defined]
    assert captured2.out.strip() == "SXIOPO"


def test_cli_inspect_and_patch_dry_run_and_write(tmp_path: Path, capsys: object) -> None:
    rom = make_gb_rom(banks=4)
    target_off = 2 * 0x4000 + 0x115C
    rom[target_off] = 0x02
    rom_path = tmp_path / "mario.gb"
    rom_path.write_bytes(bytes(rom))

    assert main(["inspect", str(rom_path), "--json"]) == 0
    info = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert info["system"] == "gameboy"

    # Dry run does not write output
    out_rom = tmp_path / "mario.patched.gb"
    assert main(["patch", str(rom_path), "-c", "001-5CA-E62", "--dry-run"]) == 0
    capsys.readouterr()  # type: ignore[attr-defined]
    assert not out_rom.exists()

    # Actual patch + IPS export
    ips_out = tmp_path / "mario.ips"
    rc = main(
        [
            "patch",
            str(rom_path),
            "-c",
            "001-5CA-E62",
            "-o",
            str(out_rom),
            "--ips-out",
            str(ips_out),
            "--json",
        ]
    )
    assert rc == 0
    res_json = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert res_json["applied_count"] == 1
    assert out_rom.read_bytes()[target_off] == 0x00
    assert ips_out.is_file()


def test_cli_fix_checksum_and_ips_roundtrip(tmp_path: Path) -> None:
    rom = make_gb_rom(banks=2, fix_chk=False)
    rom[0x0300] = 0x44
    rom_path = tmp_path / "broken.gb"
    rom_path.write_bytes(bytes(rom))
    fixed_path = tmp_path / "fixed.gb"

    assert main(["fix-checksum", str(rom_path), "-o", str(fixed_path)]) == 0
    assert main(["inspect", str(fixed_path)]) == 0

    # IPS create + apply
    nes_orig = make_nes_rom(prg_banks_16k=2)
    orig_p = tmp_path / "orig.nes"
    mod_p = tmp_path / "mod.nes"
    ips_p = tmp_path / "diff.ips"
    applied_p = tmp_path / "applied.nes"
    orig_p.write_bytes(bytes(nes_orig))

    assert main(["patch", str(orig_p), "-c", "SXIOPO+AATOZE", "-o", str(mod_p)]) == 0
    assert main(["ips-create", str(orig_p), str(mod_p), "-o", str(ips_p)]) == 0
    assert main(["ips-apply", str(orig_p), str(ips_p), "-o", str(applied_p)]) == 0
    assert applied_p.read_bytes() == mod_p.read_bytes()


def test_cli_no_args_headless_and_errors(capsys: object) -> None:
    # In headless sandbox without DISPLAY/tkinter, main([]) exits 0 with help
    assert main([]) == 0
    out = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "GGROMPatcher" in out

    # Missing file returns exit code 1 cleanly
    assert main(["inspect", "/nonexistent/rom.gb"]) == 1


def test_cli_bps_ups_diff_export_cheats_and_batch_patch(tmp_path: Path, capsys: object) -> None:
    rom1 = make_gb_rom(banks=2)
    rom1[0x14BC] = 0x03
    rom2 = make_gb_rom(banks=2)
    rom2[0x14BC] = 0x03

    r1_p = tmp_path / "g1.gb"
    r2_p = tmp_path / "g2.gb"
    r1_p.write_bytes(bytes(rom1))
    r2_p.write_bytes(bytes(rom2))

    # encode --format par
    assert (
        main(["encode", "-s", "nes", "--format", "par", "-a", "0x9123", "-v", "0xBD", "-c", "0xDE"])
        == 0
    )
    assert capsys.readouterr().out.strip() == "15C93C0A"  # type: ignore[attr-defined]

    # diff subcommand JSON + text
    assert main(["diff", str(r1_p), "-c", "004-BCE-E66", "--json"]) == 0
    diff_json = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert diff_json["bytes_changed"] >= 1
    assert diff_json["hunks"]

    # patch with --preview-diff --bps-out --ups-out --export-cheats
    out_gb = tmp_path / "g1.patched.gb"
    bps_p = tmp_path / "g1.bps"
    ups_p = tmp_path / "g1.ups"
    cht_p = tmp_path / "g1.cht"
    rc = main(
        [
            "patch",
            str(r1_p),
            "-c",
            "004-BCE-E66",
            "-o",
            str(out_gb),
            "--bps-out",
            str(bps_p),
            "--ups-out",
            str(ups_p),
            "--preview-diff",
            "--export-cheats",
            str(cht_p),
        ]
    )
    assert rc == 0
    capsys.readouterr()  # type: ignore[attr-defined]
    assert out_gb.is_file()
    assert bps_p.is_file()
    assert ups_p.is_file()
    assert cht_p.is_file()

    # bps-create / bps-apply / ups-create / ups-apply
    bps2 = tmp_path / "manual.bps"
    ups2 = tmp_path / "manual.ups"
    app_bps = tmp_path / "applied_bps.gb"
    app_ups = tmp_path / "applied_ups.gb"
    rev_ups = tmp_path / "reversed_ups.gb"
    assert main(["bps-create", str(r1_p), str(out_gb), "-o", str(bps2), "--metadata", "demo"]) == 0
    assert main(["bps-apply", str(r1_p), str(bps2), "-o", str(app_bps)]) == 0
    assert app_bps.read_bytes() == out_gb.read_bytes()
    assert main(["ups-create", str(r1_p), str(out_gb), "-o", str(ups2)]) == 0
    assert main(["ups-apply", str(r1_p), str(ups2), "-o", str(app_ups)]) == 0
    assert app_ups.read_bytes() == out_gb.read_bytes()
    assert main(["ups-apply", str(app_ups), str(ups2), "-o", str(rev_ups)]) == 0
    assert rev_ups.read_bytes() == r1_p.read_bytes()

    # export-cheats standalone
    txt_p = tmp_path / "out.txt"
    assert main(["export-cheats", "-c", "004-BCE-E66", "-o", str(txt_p)]) == 0
    assert "004-BCE-E66" in txt_p.read_text(encoding="utf-8")
    capsys.readouterr()  # type: ignore[attr-defined]

    # batch-patch
    batch_dir = tmp_path / "batch_out"
    rc_bp = main(
        [
            "batch-patch",
            str(r1_p),
            str(r2_p),
            "-c",
            "004-BCE-E66",
            "--out-dir",
            str(batch_dir),
            "--json",
        ]
    )
    assert rc_bp == 0
    bp_json = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert len(bp_json) == 2
    assert all(item["success"] for item in bp_json)
    assert (batch_dir / "g1.gb").read_bytes()[0x14BC] == 0x00
    assert (batch_dir / "g2.gb").read_bytes()[0x14BC] == 0x00
