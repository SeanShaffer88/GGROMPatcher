# GGROMPatcher

**GGROMPatcher** is a zero-dependency, cross-platform Game Genie ROM patching suite featuring both a **Tkinter Desktop GUI** and a **CLI / Python library**. It detects ROM headers automatically, scans banked ROMs for compare-byte matches, applies single or batch Game Genie patches, recalculates console checksums, creates `.bak` backups, and exports/applies `.ips` patches.

## Features

- **Multi-System Support**:
  - **Game Boy / Game Boy Color (`.gb`, `.gbc`, `.sgb`)**: 6- and 9-character codes (`XXX-XXX`, `XXX-XXX-XXX`, or space/unhyphenated forms), 16 KB bank scanning across `$0000-$7FFF`, Nintendo logo validation, header checksum (`0x014D`) and global checksum (`0x014E-0x014F`) recalculation.
  - **Sega Game Gear & Master System (`.gg`, `.sms`)**: 6- and 9-character codes across `$0000-$BFFF` 16 KB slots, `TMR SEGA` header parsing (`0x7FF0`/`0x3FF0`/`0x1FF0`), and little-endian BIOS checksum recalculation across all size codes (`$A`–`$2`).
  - **NES / Famicom (`.nes`)**: 6- and 8-character codes (`APZLGITYEOXUKSVN`), iNES & NES 2.0 header parsing (with DiskDude guard and 512-byte trainer offset handling), mapper-aware `8k` / `16k` / `32k` PRG bank mapping and mirroring.
  - **Super Nintendo (`.sfc`, `.smc`, `.swc`, `.fig`)**: 8-character codes (`XXXX-XXXX`), automatic 512-byte SMC copier header detection, scored LoROM / HiROM / ExHiROM / ExLoROM address mapping (with manual override), and power-of-2 mirrored ROM checksum + complement recalculation.
  - **Sega Genesis / Mega Drive (`.bin`, `.md`, `.gen`, `.smd`, `.68k`)**: 8-character 16-bit word codes (`XXXX-XXXX`), plain binary and `.smd` Super Magic Drive interleaved container support (supporting both even-first and odd-first 16 KB interleaving conventions), and `0x018E` big-endian word checksum recalculation.
  - **Raw File Offset Patches**: Direct file-offset patches in `OFFSET:HEX`, `OFFSET?CMP:HEX`, `OFFSET=HEX`, or `OFFSET=CMP?HEX` notation.
- **Compare-Byte Bank Scanning & Disambiguation**: Automatically finds which ROM bank(s) hold the expected compare byte; prompts you to select a bank (`--bank` / GUI dropdown) or patch all matches (`--all-matches`) when multiple banks match, or override mismatches with `--force`.
- **Batch Cheat Parsing**: Supports plain text lists, `.gg` files, RetroArch `.cht` files (`--enabled-only` supported), `+`-chained multi-part codes, and multi-code tokens.
- **IPS Patch Tools**: Generate compressed `.ips` patches (with RLE encoding, >64 KB chunk splitting, and `0x454F46` EOF-offset collision avoidance) and apply `.ips` patches (with Lunar IPS 3-byte post-EOF truncation support and optional checksum repair).
- **Safe File Handling**: Writes `<name>.patched.<ext>` by default, creates `<name>.bak` backups for in-place edits, and offers `--dry-run` preview mode.

## Installation

Requires **Python 3.10+** (zero third-party runtime dependencies).

```bash
pip install .
```

For editable/development installation with test and lint tooling:

```bash
pip install -e ".[dev]"
```

### Desktop GUI (`tkinter`) Note

The GUI uses Python's standard-library `tkinter`. On some Linux distributions or Homebrew Python setups, `tkinter` is packaged separately:

- **Debian / Ubuntu**: `sudo apt-get install python3-tk`
- **Fedora / RHEL**: `sudo dnf install python3-tkinter`
- **macOS (Homebrew)**: `brew install python-tk`

If `tkinter` is unavailable, `ggrompatcher` CLI commands still work without it.

## Desktop GUI

Launch the desktop application via:

```bash
ggrompatcher gui
# or open a ROM immediately:
ggrompatcher gui path/to/game.gb
# or via the GUI entry point:
ggrompatcher-gui
```

Running `ggrompatcher` with no arguments also opens the GUI when a graphical display and `tkinter` are available (otherwise it prints CLI help).

### GUI Tabs & Shortcuts

1. **Patch ROM**:
   - Load any ROM (`Ctrl+O`), inspect detected format/title/checksums, and queue single or `+`-chained codes or import `.txt`/`.gg`/`.cht` cheat files (`Ctrl+I`).
   - Live decode preview validates codes as you type.
   - Select any queued row to toggle enabled state, inspect status (`READY`, `ALREADY PATCHED`, `CHOOSE BANK`, `MISMATCH`, `OUT OF RANGE`), or pin a specific bank/file offset from the **Bank / Offset** dropdown.
   - Save a patched copy (`Ctrl+S`), patch in-place (with optional `.bak`), fix checksums standalone, or export an `.ips` file.
2. **Decoder / Encoder**: Instant bidirectional Game Genie code <-> hex address/value/compare converter with one-click copy or **Add to Queue**.
3. **IPS Tools**: Apply an `.ips` patch to a ROM (with optional header checksum repair) or generate an `.ips` diff from original + modified ROM files.

## CLI Reference

All commands support `-h` / `--help` for full details.

### `inspect` — Inspect ROM Headers & Checksums

```bash
ggrompatcher inspect path/to/rom.gb
ggrompatcher inspect path/to/rom.sfc --snes-map hirom --json
```

### `decode` & `encode` — Convert Game Genie Codes

```bash
ggrompatcher decode -s gb 004-BCE-E66
ggrompatcher decode --rom path/to/game.nes SXIOPO ZEXPYGLA
ggrompatcher encode -s genesis -a 0x000338 -v 0x6004 --json
```

System aliases (`-s` / `--system`): `gb`/`gbc`/`gameboy`, `gg`/`gamegear`, `sms`/`mastersystem`, `nes`/`famicom`, `snes`/`sfc`/`smc`, `genesis`/`md`/`megadrive`, `raw`/`hex`.

### `patch` — Apply Codes or Cheat Files to a ROM

```bash
# Dry run preview first
ggrompatcher patch game.gb -c 004-BCE-E66 --dry-run

# Save patched ROM + export IPS patch
ggrompatcher patch game.gb -c 004-BCE-E66 -o game.patched.gb --ips-out game.ips

# Batch import RetroArch .cht (enabled entries only), resolve bank ambiguity
ggrompatcher patch game.gb -f cheats.cht --enabled-only --bank 2

# Patch all banks matching the compare byte, or modify in-place with .bak backup
ggrompatcher patch game.gb -c 001-5CA-E62 --all-matches -i

# Override mapper/bank detection for SNES or NES
ggrompatcher patch game.nes -c SXULKNSE --nes-bank-size 8k
ggrompatcher patch game.smc -c DDB4-6F07 --snes-map lorom
```

Key flags:
- `-c`, `--code`: Game Genie code, `+`-chained codes, or raw offset expression (repeatable).
- `-f`, `--cheat-file`: `.txt`, `.gg`, or `.cht` file (repeatable).
- `--enabled-only`: Only import enabled cheats from RetroArch `.cht` files.
- `-o`, `--output`: Output ROM path (defaults to `<name>.patched.<ext>`).
- `-i`, `--in-place` / `--no-backup`: Modify input file directly (creates `.bak` unless `--no-backup`).
- `--bank N`: Pin explicit bank index (decimal or `0x` hex) when multiple banks match.
- `--all-matches`: Apply across all matching/candidate banks.
- `--force`: Apply even if the ROM byte does not match the code's compare value.
- `--no-checksum`: Skip automatic ROM header checksum recalculation.
- `--skip-invalid`: Write valid patches even if some codes fail validation.
- `--dry-run`: Validate and resolve offsets without writing files.
- `--ips-out FILE`: Export an `.ips` patch alongside the patched ROM.
- `--json`: Machine-readable JSON output.

### `fix-checksum` — Repair ROM Checksums

```bash
ggrompatcher fix-checksum game.bin -o game.fixed.bin
ggrompatcher fix-checksum game.sfc -i
```

### `ips-create` & `ips-apply` — IPS Patch Utilities

```bash
ggrompatcher ips-create original.gb modified.gb -o hack.ips
ggrompatcher ips-apply original.gb hack.ips -o patched.gb --fix-checksum
```

## Cheat File Formats

- **Plain text / `.gg`**: Supports comments (`#`, `;`, `//`), codes with trailing descriptions (`004-BCE-E66 Infinite Health`), `Description: CODE` lines, space-separated Game Boy/Game Gear triads (`003 E0E E6E`), and `+`-chained codes (`OZTLLX+AATLGZ`).
- **RetroArch `.cht`**: Parses `cheatN_code`, `cheatN_desc`, and `cheatN_enable`.
- **Raw offsets**: `0x14BC:00`, `14BC?03:00` (with compare byte), `14BC=00`, or `14BC=03?00`.

## Python Library Usage

```python
from ggrompatcher import ConsoleSystem, apply_patches, decode_code, encode_code

decoded = decode_code("004-BCE-E66", system=ConsoleSystem.GAMEBOY)
print(hex(decoded.address), decoded.value.hex(), decoded.compare.hex())

code = encode_code(ConsoleSystem.NES, 0x94A7, 0x02, compare=0x03)

rom_bytes = open("game.gb", "rb").read()
result = apply_patches(rom_bytes, ["004-BCE-E66"], strict=True)
open("game.patched.gb", "wb").write(result.output_bytes)
```

## Development

Run tests, linting, formatting checks, and strict type checks:

```bash
pytest
ruff check src tests
ruff format --check src tests
mypy src tests
```

## License

MIT License — see [LICENSE](LICENSE).
