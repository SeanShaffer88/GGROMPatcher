"""ROM cryptographic/CRC32 hashing and No-Intro XML DAT / JSON database verification."""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
import zlib
from collections.abc import Iterable
from pathlib import Path

from ggrompatcher.models import (
    ConsoleSystem,
    RomDbEntry,
    RomDbMatch,
    RomFormatError,
    RomHashes,
)
from ggrompatcher.systems.genesis import deinterleave_smd, detect_smd_layout

_REGION_RE = re.compile(r"\((USA|Europe|Japan|World|Rev\s*\d+[^)]*|USA,\s*Europe)\)", re.IGNORECASE)


def _crc32_hex(data: bytes) -> str:
    return f"{zlib.crc32(data) & 0xFFFFFFFF:08x}"


def _extract_region(name: str) -> str:
    m = _REGION_RE.search(name)
    return m.group(1) if m else ""


def compute_rom_hashes(
    rom: bytes,
    *,
    system: ConsoleSystem | str | None = None,
) -> RomHashes:
    """Compute CRC32, MD5, SHA-1, and SHA-256 digests (plus headerless digests if headered)."""
    resolved_sys = (
        (system if isinstance(system, ConsoleSystem) else ConsoleSystem.from_str(system))
        if system is not None
        else None
    )

    crc32 = _crc32_hex(rom)
    md5 = hashlib.md5(rom).hexdigest()
    sha1 = hashlib.sha1(rom).hexdigest()
    sha256 = hashlib.sha256(rom).hexdigest()

    header_bytes = 0
    payload: bytes | None = None

    if len(rom) > 16 and rom[:4] == b"NES\x1a" and resolved_sys in {None, ConsoleSystem.NES}:
        header_bytes = 16
        payload = rom[16:]
    elif resolved_sys == ConsoleSystem.SNES and len(rom) > 512 and len(rom) % 1024 == 512:
        header_bytes = 512
        payload = rom[512:]
    elif resolved_sys in {None, ConsoleSystem.GENESIS}:
        smd_layout = detect_smd_layout(rom)
        if smd_layout is not None:
            header_bytes = 512
            payload = deinterleave_smd(rom, layout=smd_layout)

    if payload is None and resolved_sys is None and len(rom) >= 0x8200 and len(rom) % 1024 == 512:
        # Fallback: SNES SMC/SWC 512-byte copier header detection when system omitted.
        from ggrompatcher.systems.snes import SnesHandler

        if SnesHandler().detect_confidence(rom) >= 50:
            header_bytes = 512
            payload = rom[512:]

    if payload is not None:
        return RomHashes(
            size=len(rom),
            crc32=crc32,
            md5=md5,
            sha1=sha1,
            sha256=sha256,
            header_bytes=header_bytes,
            headerless_size=len(payload),
            headerless_crc32=_crc32_hex(payload),
            headerless_md5=hashlib.md5(payload).hexdigest(),
            headerless_sha1=hashlib.sha1(payload).hexdigest(),
        )

    return RomHashes(
        size=len(rom),
        crc32=crc32,
        md5=md5,
        sha1=sha1,
        sha256=sha256,
    )


class RomDatabase:
    """In-memory ROM checksum database loaded from Logiqx/No-Intro XML DAT or JSON files."""

    def __init__(
        self,
        entries: Iterable[RomDbEntry] = (),
        *,
        name: str = "",
    ) -> None:
        self.name = name
        self._entries: list[RomDbEntry] = []
        self._by_sha1: dict[str, list[RomDbEntry]] = {}
        self._by_md5: dict[str, list[RomDbEntry]] = {}
        self._by_crc: dict[str, list[RomDbEntry]] = {}
        for entry in entries:
            self.add_entry(entry)

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def entries(self) -> tuple[RomDbEntry, ...]:
        """Return all registered database entries."""
        return tuple(self._entries)

    def add_entry(self, entry: RomDbEntry) -> None:
        """Normalize hashes and index `entry` for O(1) lookup."""
        normalized = RomDbEntry(
            name=entry.name,
            game_title=entry.game_title or entry.name,
            system=entry.system,
            region=entry.region or _extract_region(entry.game_title or entry.name),
            size=entry.size,
            crc32=entry.crc32.strip().lower().zfill(8) if entry.crc32.strip() else "",
            md5=entry.md5.strip().lower(),
            sha1=entry.sha1.strip().lower(),
            headerless=entry.headerless,
        )
        self._entries.append(normalized)
        if normalized.sha1:
            self._by_sha1.setdefault(normalized.sha1, []).append(normalized)
        if normalized.md5:
            self._by_md5.setdefault(normalized.md5, []).append(normalized)
        if normalized.crc32:
            self._by_crc.setdefault(normalized.crc32, []).append(normalized)

    @classmethod
    def from_dat_text(cls, content: str, *, default_system: str = "") -> RomDatabase:
        """Parse a Logiqx / No-Intro / Redump XML `.dat` string into a `RomDatabase`."""
        try:
            root = ET.fromstring(content)
        except ET.ParseError as exc:
            raise RomFormatError(f"Failed to parse XML DAT database: {exc}") from exc

        header_name = ""
        header_el = root.find("header")
        if header_el is not None:
            name_el = header_el.find("name")
            if name_el is not None and name_el.text:
                header_name = name_el.text.strip()

        db = cls(name=header_name)
        inferred_system = default_system or header_name

        for container_tag in ("game", "machine"):
            for game_el in root.findall(f".//{container_tag}"):
                game_title = (game_el.get("name") or game_el.findtext("description") or "").strip()
                for rom_el in game_el.findall("rom"):
                    rom_name = (rom_el.get("name") or game_title).strip()
                    if not rom_name:
                        continue
                    size_raw = rom_el.get("size", "").strip()
                    try:
                        size_val = int(size_raw) if size_raw else None
                    except ValueError:
                        size_val = None
                    crc = (rom_el.get("crc") or rom_el.get("crc32") or "").strip()
                    md5 = (rom_el.get("md5") or "").strip()
                    sha1 = (rom_el.get("sha1") or "").strip()
                    if not (crc or md5 or sha1):
                        continue
                    db.add_entry(
                        RomDbEntry(
                            name=rom_name,
                            game_title=game_title or rom_name,
                            system=inferred_system,
                            region=_extract_region(game_title or rom_name),
                            size=size_val,
                            crc32=crc,
                            md5=md5,
                            sha1=sha1,
                        )
                    )
        return db

    @classmethod
    def from_json_text(cls, content: str, *, default_system: str = "") -> RomDatabase:
        """Parse a JSON ROM database file (`[...]`, `{entries: [...]}`, or `{roms: [...]}`)."""
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise RomFormatError(f"Failed to parse JSON ROM database: {exc}") from exc

        db_name = ""
        system_hint = default_system
        if isinstance(payload, dict):
            db_name = str(payload.get("name", "")).strip()
            system_hint = str(payload.get("system", default_system)).strip()
            items = payload.get("entries") or payload.get("roms") or []
        elif isinstance(payload, list):
            items = payload
        else:
            raise RomFormatError(
                "JSON ROM database must be a list or object with 'entries'/'roms'."
            )

        db = cls(name=db_name)
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", item.get("game_title", ""))).strip()
            if not name:
                continue
            raw_size = item.get("size")
            size_val = int(raw_size) if raw_size is not None else None
            db.add_entry(
                RomDbEntry(
                    name=name,
                    game_title=str(item.get("game_title", name)).strip(),
                    system=str(item.get("system", system_hint)).strip(),
                    region=str(item.get("region", "")).strip(),
                    size=size_val,
                    crc32=str(item.get("crc32", item.get("crc", ""))),
                    md5=str(item.get("md5", "")),
                    sha1=str(item.get("sha1", "")),
                    headerless=bool(item.get("headerless", False)),
                )
            )
        return db

    @classmethod
    def from_file(cls, path: str | Path, *, default_system: str = "") -> RomDatabase:
        """Load a ROM database from `.dat`, `.xml`, or `.json` on disk."""
        src = Path(path)
        if not src.is_file():
            raise RomFormatError(f"ROM database file not found: {src}")
        text = src.read_text(encoding="utf-8", errors="replace")
        if src.suffix.lower() == ".json" or text.lstrip().startswith(("[", "{")):
            return cls.from_json_text(text, default_system=default_system)
        return cls.from_dat_text(text, default_system=default_system)

    def lookup_hashes(self, hashes: RomHashes) -> list[RomDbMatch]:
        """Return matching `RomDbMatch` entries ordered by hash specificity."""
        matches: list[RomDbMatch] = []
        seen_ids: set[int] = set()

        def _collect(
            sha1_val: str | None,
            md5_val: str | None,
            crc_val: str | None,
            size_val: int | None,
            via_headerless: bool,
        ) -> None:
            if sha1_val and sha1_val in self._by_sha1:
                for entry in self._by_sha1[sha1_val]:
                    if id(entry) not in seen_ids:
                        seen_ids.add(id(entry))
                        matches.append(
                            RomDbMatch(
                                entry=entry, matched_by="sha1", via_headerless=via_headerless
                            )
                        )
            if md5_val and md5_val in self._by_md5:
                for entry in self._by_md5[md5_val]:
                    if id(entry) not in seen_ids:
                        seen_ids.add(id(entry))
                        matches.append(
                            RomDbMatch(entry=entry, matched_by="md5", via_headerless=via_headerless)
                        )
            if crc_val and crc_val in self._by_crc:
                for entry in self._by_crc[crc_val]:
                    if id(entry) in seen_ids:
                        continue
                    if entry.size is not None and size_val is not None and entry.size != size_val:
                        continue
                    seen_ids.add(id(entry))
                    by = (
                        "crc32+size"
                        if (entry.size is not None and size_val is not None)
                        else "crc32"
                    )
                    matches.append(
                        RomDbMatch(entry=entry, matched_by=by, via_headerless=via_headerless)
                    )

        _collect(hashes.sha1, hashes.md5, hashes.crc32, hashes.size, False)
        if hashes.header_bytes > 0:
            _collect(
                hashes.headerless_sha1,
                hashes.headerless_md5,
                hashes.headerless_crc32,
                hashes.headerless_size,
                True,
            )
        return matches

    def verify_rom(
        self,
        rom: bytes,
        *,
        system: ConsoleSystem | str | None = None,
    ) -> tuple[RomHashes, list[RomDbMatch]]:
        """Compute `RomHashes` for `rom` and return `(hashes, matches)` against this DB."""
        hashes = compute_rom_hashes(rom, system=system)
        return hashes, self.lookup_hashes(hashes)
