"""Tests for ROM cryptographic/CRC32 hashing and No-Intro XML DAT / JSON databases."""

from __future__ import annotations

import hashlib
import json
import zlib
from pathlib import Path

import pytest

from ggrompatcher.models import ConsoleSystem, RomFormatError
from ggrompatcher.romdb import RomDatabase, compute_rom_hashes
from tests.conftest import make_gba_rom, make_genesis_rom, make_nes_rom, make_snes_rom


def test_compute_rom_hashes_headered_and_headerless() -> None:
    gba = bytes(make_gba_rom(size=0x10000))
    h_gba = compute_rom_hashes(gba, system=ConsoleSystem.GBA)
    assert h_gba.size == len(gba)
    assert h_gba.crc32 == f"{zlib.crc32(gba) & 0xFFFFFFFF:08x}"
    assert h_gba.md5 == hashlib.md5(gba).hexdigest()
    assert h_gba.sha1 == hashlib.sha1(gba).hexdigest()
    assert h_gba.sha256 == hashlib.sha256(gba).hexdigest()
    assert h_gba.header_bytes == 0
    assert h_gba.headerless_crc32 is None

    # iNES 16-byte header stripping for headerless No-Intro matching
    nes = bytes(make_nes_rom(prg_banks_16k=2))
    h_nes = compute_rom_hashes(nes)
    assert h_nes.header_bytes == 16
    assert h_nes.headerless_size == len(nes) - 16
    assert h_nes.headerless_sha1 == hashlib.sha1(nes[16:]).hexdigest()

    # SNES 512-byte SMC header stripping
    snes = bytes(make_snes_rom(size=0x40000, smc_header=True))
    h_snes = compute_rom_hashes(snes)
    assert h_snes.header_bytes == 512
    assert h_snes.headerless_size == 0x40000
    assert h_snes.headerless_crc32 == f"{zlib.crc32(snes[512:]) & 0xFFFFFFFF:08x}"

    # Genesis SMD 512-byte deinterleaved hash
    plain_gen = bytes(make_genesis_rom(size=0x20000))
    smd_gen = bytes(make_genesis_rom(size=0x20000, smd_layout="even_first"))
    h_smd = compute_rom_hashes(smd_gen)
    assert h_smd.header_bytes == 512
    assert h_smd.headerless_sha1 == hashlib.sha1(plain_gen).hexdigest()


def test_rom_database_logiqx_xml_and_json_verification(tmp_path: Path) -> None:
    gba = bytes(make_gba_rom(size=0x10000, title="GOLDEN"))
    nes = bytes(make_nes_rom(prg_banks_16k=2))
    nes_body = nes[16:]

    gba_sha1 = hashlib.sha1(gba).hexdigest().upper()
    nes_body_crc = f"{zlib.crc32(nes_body) & 0xFFFFFFFF:08X}"

    dat_xml = f"""<?xml version="1.0" encoding="utf-8"?>
<datafile>
  <header>
    <name>No-Intro Test Collection</name>
  </header>
  <game name="Golden Test (USA)">
    <rom name="Golden Test (USA).gba" size="{len(gba)}" sha1="{gba_sha1}" />
  </game>
  <machine name="Mapper Zero Demo (Japan)">
    <rom name="Mapper Zero Demo (Japan).nes" size="{len(nes_body)}" crc="{nes_body_crc}" />
  </machine>
</datafile>
"""
    dat_file = tmp_path / "nointro.dat"
    dat_file.write_text(dat_xml, encoding="utf-8")

    db = RomDatabase.from_file(dat_file)
    assert len(db) == 2
    assert db.name == "No-Intro Test Collection"

    _, matches_gba = db.verify_rom(gba, system=ConsoleSystem.GBA)
    assert len(matches_gba) == 1
    assert matches_gba[0].entry.game_title == "Golden Test (USA)"
    assert matches_gba[0].entry.region == "USA"
    assert matches_gba[0].matched_by == "sha1"
    assert matches_gba[0].via_headerless is False

    # Headered NES ROM matches headerless DAT entry automatically!
    _, matches_nes = db.verify_rom(nes)
    assert len(matches_nes) == 1
    assert matches_nes[0].entry.game_title == "Mapper Zero Demo (Japan)"
    assert matches_nes[0].matched_by == "crc32+size"
    assert matches_nes[0].via_headerless is True

    # JSON DB format + CRC size mismatch protection
    json_path = tmp_path / "roms.json"
    json_path.write_text(
        json.dumps(
            {
                "name": "Custom JSON DB",
                "system": "gba",
                "entries": [
                    {
                        "name": "Golden Test.gba",
                        "game_title": "Golden Test (Rev 1)",
                        "size": len(gba),
                        "md5": hashlib.md5(gba).hexdigest(),
                    },
                    {
                        "name": "Wrong Size Collision.gba",
                        "size": len(gba) + 999,
                        "crc32": f"{zlib.crc32(gba) & 0xFFFFFFFF:08x}",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    jdb = RomDatabase.from_file(json_path)
    _, j_matches = jdb.verify_rom(gba)
    assert len(j_matches) == 1
    assert j_matches[0].entry.game_title == "Golden Test (Rev 1)"
    assert j_matches[0].matched_by == "md5"

    bad_dat = tmp_path / "bad.dat"
    bad_dat.write_text("<datafile><broken>", encoding="utf-8")
    with pytest.raises(RomFormatError, match="Failed to parse XML DAT"):
        RomDatabase.from_file(bad_dat)
