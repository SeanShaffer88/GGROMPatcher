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
