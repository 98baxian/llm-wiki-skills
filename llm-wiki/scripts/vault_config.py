"""Tiny readers for the vault CLAUDE.md, the single config + schema file."""
from __future__ import annotations

import re
from pathlib import Path


def section_lines(root, heading: str) -> list[str]:
    """Lines under ``## <heading>`` up to the next ``## `` heading (case-insensitive)."""
    try:
        text = (Path(root) / "CLAUDE.md").read_text(encoding="utf-8")
    except OSError:
        return []
    out: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.startswith("## "):
            if inside:
                break
            inside = line[3:].strip().lower() == heading.lower()
            continue
        if inside:
            out.append(line)
    return out


def key_values(lines: list[str]) -> dict[str, str]:
    """Parse ``key: value  # comment`` lines; keys are lower-cased, quotes stripped."""
    kv: dict[str, str] = {}
    for line in lines:
        m = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line.strip())
        if m:
            value = re.sub(r"\s+#.*$", "", m.group(2)).strip().strip("\"'")
            kv[m.group(1).lower()] = value
    return kv


def private_zones(root) -> list[str]:
    """Folders listed as bullets (a backticked path) under ``## Private zones``."""
    zones = []
    for line in section_lines(root, "Private zones"):
        m = re.match(r"^\s*-\s*`([^`]+)`", line)
        if m:
            zones.append(m.group(1).strip().rstrip("/"))
    return zones


def in_private_zone(root, path) -> bool:
    root_p = Path(root).resolve()
    target = Path(path).resolve()
    for zone in private_zones(root_p):
        zone_p = (root_p / zone).resolve()
        if target == zone_p or zone_p in target.parents:
            return True
    return False
