"""Tests for the headless GuiController and GUI entry-point fallback."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import ggrompatcher.gui as gui_mod
from ggrompatcher.gui import launch_gui
from ggrompatcher.gui.controller import GuiController
from ggrompatcher.models import ConsoleSystem, MatchStatus
from tests.conftest import make_gb_rom


def test_gui_controller_full_workflow(tmp_path: Path) -> None:
    rom = make_gb_rom(banks=4, title="ZELDA TEST")
    off1 = 1 * 0x4000 + 0x115C
    off2 = 2 * 0x4000 + 0x115C
    rom[off1] = 0x02
    rom[off2] = 0x02

    rom_path = tmp_path / "zelda.gb"
    rom_path.write_bytes(bytes(rom))

    ctrl = GuiController()
    info = ctrl.load_rom(rom_path)
    assert info.system == ConsoleSystem.GAMEBOY
    assert info.title == "ZELDA TEST"

    # Live decoder & encoder
    live = ctrl.live_decode("001-5CA-E62")
    assert live.valid
    assert "0x515C" in live.summary

    encoded = ctrl.live_encode(ConsoleSystem.GAMEBOY, "0x14BC", "0x00", "0x03")
    assert encoded == "004-BCE-E66"

    # Queue ambiguous code & resolve via offset override
    ctrl.add_codes_from_text("001-5CA-E62")
    rows = ctrl.build_patch_rows()
    assert len(rows) == 1
    assert rows[0].status == MatchStatus.AMBIGUOUS
    assert len(rows[0].candidate_labels) == 3  # banks 1, 2, 3

    ctrl.set_offset_override(0, off2)
    rows_resolved = ctrl.build_patch_rows()
    assert rows_resolved[0].status == MatchStatus.READY
    assert rows_resolved[0].selected_offset == off2

    # Add second code and toggle disable/remove
    ctrl.add_codes_from_text("011-23E")
    assert len(ctrl.entries) == 2
    assert ctrl.toggle_entry(1) is False
    disabled_rows = ctrl.build_patch_rows()
    assert disabled_rows[1].status_text == "DISABLED"
    assert ctrl.toggle_entry(1) is True

    # Apply & save to patched file + IPS
    out_path = tmp_path / "zelda.patched.gb"
    ips_path = tmp_path / "zelda.ips"
    result, dest = ctrl.apply_and_save(output_path=out_path, ips_output_path=ips_path)
    assert dest == out_path
    assert len(result.applied_patches) == 2
    assert out_path.read_bytes()[off2] == 0x00
    assert out_path.read_bytes()[off1] == 0x02
    assert out_path.read_bytes()[0x1123] == 0x01
    assert ips_path.is_file()

    # Remove row 0 and confirm override remapping clears cleanly
    ctrl.remove_indices([0])
    assert len(ctrl.entries) == 1
    assert ctrl.offset_overrides == {}

    # Import cheat file
    cht = tmp_path / "cheats.txt"
    cht.write_text("021-23E  Change byte\n", encoding="utf-8")
    imported = ctrl.import_cheat_file(cht)
    assert len(imported) == 1
    assert len(ctrl.entries) == 2

    ctrl.clear_codes()
    assert ctrl.entries == []


def test_gui_controller_fix_checksum_and_ips_tools(tmp_path: Path) -> None:
    broken = make_gb_rom(banks=2, fix_chk=False)
    broken[0x0350] = 0xAB
    rom_path = tmp_path / "chk.gb"
    rom_path.write_bytes(bytes(broken))

    ctrl = GuiController()
    info = ctrl.load_rom(rom_path)
    assert any(not c.valid for c in info.checksums)

    _, after, out_p = ctrl.fix_checksum_to_file(in_place=True)
    assert out_p == rom_path
    assert all(c.valid for c in after.checksums)
    assert (tmp_path / "chk.gb.bak").is_file()


def test_launch_gui_handles_missing_tk_cleanly(capsys: object, monkeypatch: Any) -> None:
    monkeypatch.setattr(gui_mod, "is_tk_available", lambda: False)
    rc = launch_gui()
    assert rc == 1
    err = capsys.readouterr().err  # type: ignore[attr-defined]
    assert "tkinter" in err


def test_gui_controller_diff_export_bps_ups_drop_and_batch(tmp_path: Path) -> None:
    rom1 = make_gb_rom(banks=2)
    rom1[0x14BC] = 0x03
    rom2 = make_gb_rom(banks=2)
    rom2[0x14BC] = 0x03

    r1 = tmp_path / "first.gb"
    r2 = tmp_path / "second.gb"
    r1.write_bytes(bytes(rom1))
    r2.write_bytes(bytes(rom2))
    cht = tmp_path / "list.cht"
    cht.write_text('cheat0_code = "004-BCE-E66"\n', encoding="utf-8")

    ctrl = GuiController()
    ctrl.load_rom(r1)
    ctrl.add_codes_from_text("004-BCE-E66  Health")

    # Pre-save diff preview
    res, preview = ctrl.preview_current_diff()
    assert res.bytes_modified >= 1
    assert preview.bytes_changed == res.bytes_modified

    # Export cheat sheet
    exported_cht = ctrl.export_cheats_to_file(tmp_path / "exported.cht")
    assert exported_cht.is_file()

    # Export BPS & UPS patches via apply_and_save and round-trip via apply_patch_to_file
    bps_file = tmp_path / "out.bps"
    _, written_bps = ctrl.apply_and_save(patch_output_path=bps_file)
    assert written_bps == bps_file and bps_file.is_file()

    applied_via_bps = ctrl.apply_patch_to_file(r1, bps_file, tmp_path / "from_bps.gb")
    assert applied_via_bps.read_bytes()[0x14BC] == 0x00

    # PAR live encode
    rocky = ctrl.live_encode(ConsoleSystem.NES, "0x9123", "0xBD", "0xDE", code_format="par")
    assert rocky == "15C93C0A"

    # Drop payload parsing & classification (with braced path containing space)
    spaced_rom = tmp_path / "space rom.gb"
    spaced_rom.write_bytes(bytes(rom2))
    parsed_drop = ctrl.parse_drop_paths(f"{{{spaced_rom}}} {cht} {bps_file}")
    assert parsed_drop == [spaced_rom, cht, bps_file]
    classified = ctrl.classify_dropped_files(parsed_drop)
    assert classified["roms"] == [spaced_rom]
    assert classified["cheats"] == [cht]
    assert classified["patches"] == [bps_file]

    # Batch ROMs add / remove / run
    added = ctrl.add_batch_roms([r1, r2, spaced_rom])
    assert len(added) == 3
    ctrl.remove_batch_indices([2])
    assert len(ctrl.batch_rom_paths) == 2

    batch_out = tmp_path / "batch_out"
    batch_results = ctrl.run_batch_patch(output_dir=batch_out)
    assert len(batch_results) == 2
    assert all(item.success for item in batch_results)
    assert (batch_out / "first.gb").read_bytes()[0x14BC] == 0x00
    assert (batch_out / "second.gb").read_bytes()[0x14BC] == 0x00

    ctrl.clear_batch_roms()
    assert ctrl.batch_rom_paths == []
