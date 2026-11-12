"""Tests for cheat list and RetroArch .cht file parsing."""

from __future__ import annotations

from pathlib import Path

from ggrompatcher.cheatfiles import load_cheat_file, parse_cheat_text, parse_code_tokens


def test_parse_code_tokens_variants() -> None:
    assert parse_code_tokens("OZTLLX+AATLGZ+SZLIVO") == ["OZTLLX", "AATLGZ", "SZLIVO"]
    assert parse_code_tokens("003 E0E E6E") == ["003-E0E-E6E"]
    assert parse_code_tokens("004 BCE") == ["004-BCE"]
    assert parse_code_tokens("D865-6464, DF66-67A4; 776B-6D04") == [
        "D865-6464",
        "DF66-67A4",
        "776B-6D04",
    ]


def test_parse_plain_text_and_gg_formats() -> None:
    text = """
    # Super Mario Bros & Sonic cheats
    SXIOPO  Infinite Lives
    OZTLLX+AATLGZ+SZLIVO - Always stay big
    003 E0E E6E   Infinite Lives GG
    Invincibility: AE0T-CABE
    ATBT-AA32 # Master Code
    """
    entries = parse_cheat_text(text)
    assert [(e.code, e.description) for e in entries] == [
        ("SXIOPO", "Infinite Lives"),
        ("OZTLLX", "Always stay big"),
        ("AATLGZ", "Always stay big"),
        ("SZLIVO", "Always stay big"),
        ("003-E0E-E6E", "Infinite Lives GG"),
        ("AE0T-CABE", "Invincibility"),
        ("ATBT-AA32", "Master Code"),
    ]


def test_parse_retroarch_cht_and_enabled_only(tmp_path: Path) -> None:
    cht_content = """
    cheats = 2

    cheat0_desc = "Infinite Lives"
    cheat0_code = "SXIOPO"
    cheat0_enable = true

    cheat1_desc = "Stay Big"
    cheat1_code = "OZTLLX+AATLGZ"
    cheat1_enable = false
    """
    p = tmp_path / "mario.cht"
    p.write_text(cht_content, encoding="utf-8")

    all_entries = load_cheat_file(p, enabled_only=False)
    assert len(all_entries) == 3
    assert all_entries[0].enabled is True
    assert all_entries[1].enabled is False
    assert all_entries[1].description == "Stay Big"

    enabled_entries = load_cheat_file(p, enabled_only=True)
    assert len(enabled_entries) == 1
    assert enabled_entries[0].code == "SXIOPO"
