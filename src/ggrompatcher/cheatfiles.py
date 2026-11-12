"""Parsers for Game Genie code strings, multi-code text lists, .gg files, and RetroArch .cht."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

_HEX3_RE = re.compile(r"^[0-9A-Fa-f]{3}$")
_GG_HYPHEN_RE = re.compile(
    r"^(?:[0-9A-Za-z]{3}-[0-9A-Za-z]{3}(?:-[0-9A-Za-z]{3})?|[0-9A-Za-z]{4}-[0-9A-Za-z]{4})$"
)
_GG_COMPACT_RE = re.compile(r"^(?:[0-9A-Za-z]{6}|[0-9A-Za-z]{8}|[0-9A-Za-z]{9})$")
_RAW_TOKEN_RE = re.compile(
    r"^(?:0x)?[0-9A-Fa-f]+(?:\?[0-9A-Fa-f]+)?[:=](?:[0-9A-Fa-f]+(?:\?[0-9A-Fa-f]+)?)$",
    re.IGNORECASE,
)
_PREFIXED_CODE_RE = re.compile(
    r"^(?:PAR|ROCKY|GS|AR):[0-9A-Za-z]+(?:[?:=-][0-9A-Za-z]+)*$",
    re.IGNORECASE,
)

_CHT_LINE_RE = re.compile(r"^\s*cheat(\d+)_([a-zA-Z0-9_]+)\s*=\s*(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class CheatEntry:
    """Single parsed code token along with its optional description and enabled flag."""

    code: str
    description: str = ""
    enabled: bool = True


def _strip_quotes(value: str) -> str:
    val = value.strip()
    if len(val) >= 2 and val[0] == val[-1] and val[0] in {'"', "'"}:
        return val[1:-1]
    return val


def _is_code_token(token: str) -> bool:
    return bool(
        _GG_HYPHEN_RE.match(token)
        or _GG_COMPACT_RE.match(token)
        or _RAW_TOKEN_RE.match(token)
        or _PREFIXED_CODE_RE.match(token)
    )


def parse_code_tokens(text: str) -> list[str]:
    """Split a code expression (e.g. `A+B`, `003 E0E E6E`, `SXIOPO, AATOZE`) into codes."""
    if not text.strip():
        return []

    # Normalize '+' and ',' to spaces, preserve newlines as boundaries.
    codes: list[str] = []
    for raw_segment in re.split(r"[\r\n+,;]+", text):
        parts = raw_segment.strip().split()
        if not parts:
            continue
        i = 0
        while i < len(parts):
            # Rejoin space-separated 3-hex-digit GB/GG codes (e.g. "003 E0E E6E" or "004 BCE").
            if (
                i + 2 < len(parts)
                and _HEX3_RE.match(parts[i])
                and _HEX3_RE.match(parts[i + 1])
                and _HEX3_RE.match(parts[i + 2])
            ):
                codes.append(f"{parts[i]}-{parts[i + 1]}-{parts[i + 2]}")
                i += 3
            elif i + 1 < len(parts) and _HEX3_RE.match(parts[i]) and _HEX3_RE.match(parts[i + 1]):
                codes.append(f"{parts[i]}-{parts[i + 1]}")
                i += 2
            elif (
                i + 1 < len(parts)
                and parts[i].upper() in {"PAR", "ROCKY", "GS", "AR"}
                and not _is_code_token(parts[i])
            ):
                codes.append(f"{parts[i].upper()}:{parts[i + 1]}")
                i += 2
            else:
                codes.append(parts[i])
                i += 1
    return codes


def parse_retroarch_cht(content: str, *, enabled_only: bool = False) -> list[CheatEntry]:
    """Parse RetroArch `.cht` cheat file contents into `CheatEntry` items."""
    cheats: dict[int, dict[str, str]] = {}
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith(";"):
            continue
        m = _CHT_LINE_RE.match(stripped)
        if not m:
            continue
        idx = int(m.group(1))
        key = m.group(2).lower()
        val = _strip_quotes(m.group(3))
        cheats.setdefault(idx, {})[key] = val

    entries: list[CheatEntry] = []
    for idx in sorted(cheats):
        item = cheats[idx]
        code_raw = item.get("code", "").strip()
        if not code_raw:
            continue
        desc = item.get("desc", "").strip()
        enable_val = item.get("enable", "true").strip().lower()
        enabled = enable_val in {"true", "1", "yes", "on"}
        if enabled_only and not enabled:
            continue
        for token in parse_code_tokens(code_raw):
            entries.append(CheatEntry(code=token, description=desc, enabled=enabled))
    return entries


def parse_cheat_text(content: str, *, enabled_only: bool = False) -> list[CheatEntry]:
    """Parse plain-text, `.gg`, or RetroArch `.cht` cheat definitions into `CheatEntry` items."""
    if _CHT_LINE_RE.search(content):
        return parse_retroarch_cht(content, enabled_only=enabled_only)

    entries: list[CheatEntry] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";", "//")):
            continue

        # Extract inline comment as description if present.
        comment_desc = ""
        for marker in ("#", ";", "//"):
            if marker in line:
                before, after = line.split(marker, 1)
                if before.strip():
                    line = before.strip()
                    comment_desc = after.strip()
                    break

        # Support `Description: CODE1+CODE2` when left side isn't a raw/prefixed code token.
        first_word = line.split()[0]
        if (
            ":" in line
            and not _RAW_TOKEN_RE.match(first_word)
            and not _PREFIXED_CODE_RE.match(first_word)
        ):
            left, right = line.split(":", 1)
            right_tokens = parse_code_tokens(right)
            if right_tokens and all(_is_code_token(tok) for tok in right_tokens):
                desc = comment_desc or left.strip()
                for tok in right_tokens:
                    entries.append(CheatEntry(code=tok, description=desc))
                continue

        # Split around `+` and check leading/trailing code tokens vs description text.
        # First handle space-separated 3-hex-digit GB/GG codes at start of line.
        words = line.split()
        if (
            len(words) >= 3
            and _HEX3_RE.match(words[0])
            and _HEX3_RE.match(words[1])
            and _HEX3_RE.match(words[2])
        ):
            code = f"{words[0]}-{words[1]}-{words[2]}"
            desc = comment_desc or " ".join(words[3:]).lstrip("-: ").strip()
            entries.append(CheatEntry(code=code, description=desc))
            continue

        if len(words) >= 2 and _HEX3_RE.match(words[0]) and _HEX3_RE.match(words[1]):
            code = f"{words[0]}-{words[1]}"
            desc = comment_desc or " ".join(words[2:]).lstrip("-: ").strip()
            entries.append(CheatEntry(code=code, description=desc))
            continue

        # Collect leading code tokens (including `A+B` chains with spaces around `+`).
        code_tokens: list[str] = []
        desc_start_idx = 0
        expecting_code = True
        for idx, word in enumerate(words):
            if word == "+":
                expecting_code = True
                desc_start_idx = idx + 1
                continue
            sub_parts = [p.strip(",") for p in word.split("+") if p.strip(",")]
            if expecting_code and sub_parts and all(_is_code_token(p) for p in sub_parts):
                code_tokens.extend(sub_parts)
                desc_start_idx = idx + 1
                expecting_code = word.endswith("+") or (
                    idx + 1 < len(words) and words[idx + 1] == "+"
                )
            else:
                break

        if code_tokens:
            desc = comment_desc or " ".join(words[desc_start_idx:]).lstrip("-: ").strip()
            for tok in code_tokens:
                entries.append(CheatEntry(code=tok, description=desc))
            continue

        # Fallback: treat trailing valid code token(s) as code and preceding text as description.
        trailing: list[str] = []
        cutoff = len(words)
        for idx in range(len(words) - 1, -1, -1):
            sub_parts = [p.strip(",") for p in words[idx].split("+") if p.strip(",")]
            if sub_parts and all(_is_code_token(p) for p in sub_parts):
                trailing = sub_parts + trailing
                cutoff = idx
            elif words[idx] == "+" and trailing:
                cutoff = idx
            else:
                break

        if trailing:
            desc = comment_desc or " ".join(words[:cutoff]).rstrip("-: ").strip()
            for tok in trailing:
                entries.append(CheatEntry(code=tok, description=desc))
            continue

        # Last resort: hand raw line directly to decoder later so caller gets clear error context.
        for tok in parse_code_tokens(line):
            entries.append(CheatEntry(code=tok, description=comment_desc))

    return entries


def load_cheat_file(path: str | Path, *, enabled_only: bool = False) -> list[CheatEntry]:
    """Read and parse a `.txt`, `.gg`, or `.cht` cheat file from disk."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    return parse_cheat_text(text, enabled_only=enabled_only)


def format_cheat_txt(entries: Sequence[CheatEntry]) -> str:
    """Format `entries` as a human-readable `.txt` / `.gg` cheat sheet."""
    lines = ["# GGROMPatcher Cheat List", ""]
    for entry in entries:
        prefix = "" if entry.enabled else "# [DISABLED] "
        if entry.description:
            lines.append(f"{prefix}{entry.code}  # {entry.description}")
        else:
            lines.append(f"{prefix}{entry.code}")
    return "\n".join(lines) + "\n"


def format_retroarch_cht(entries: Sequence[CheatEntry]) -> str:
    """Format `entries` in RetroArch `.cht` format."""
    lines = [f"cheats = {len(entries)}", ""]
    for idx, entry in enumerate(entries):
        desc_safe = entry.description.replace('"', "'")
        code_safe = entry.code.replace('"', "'")
        enable_str = "true" if entry.enabled else "false"
        lines.extend(
            [
                f'cheat{idx}_desc = "{desc_safe}"',
                f'cheat{idx}_code = "{code_safe}"',
                f"cheat{idx}_enable = {enable_str}",
                "",
            ]
        )
    return "\n".join(lines)


def export_cheat_file(
    path: str | Path,
    entries: Sequence[CheatEntry],
    *,
    fmt: str | None = None,
) -> Path:
    """Write `entries` to `.txt`, `.gg`, or RetroArch `.cht` (format inferred from extension)."""
    dest = Path(path)
    kind = fmt.strip().lower().lstrip(".") if fmt else dest.suffix.lower().lstrip(".")
    if kind == "cht":
        content = format_retroarch_cht(entries)
    elif kind in {"txt", "gg", ""}:
        content = format_cheat_txt(entries)
    else:
        raise ValueError(f"Unsupported cheat export format '{kind}'. Use txt, gg, or cht.")

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")
    return dest
