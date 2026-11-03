"""Domain extensions declared in the vault CLAUDE.md: page kinds and typed relations.

``type:`` in a page's frontmatter stays one of the built-in page types (concept, entity, ...). A vault
can add a finer ``kind:`` (gene, variant, drug, ...) and say what each kind must carry, under
``## Page types`` in CLAUDE.md::

    ## Page types
    - variant: dir=concepts; required=hgvs_c,acmg_class; enum.acmg_class=Pathogenic|VUS|Benign
    - gene: dir=entities; required=gene_symbol

A page may also list typed, evidenced edges in frontmatter, one flow mapping per line::

    relations:
      - {type: treats, target: entities/drug-x, evidence: "raw/lit/p1.md#ev-3", confidence: high, as_of: 2026-11-01}
      - {type: associated_with, target: "mondo:0005148"}

``target`` is a wiki page (``category/stem``) or an external id with a prefix (``mondo:0005148``),
which is not resolved. The allowed relation names are the bullets under ``## Relation types``; when
that section is absent any name is accepted.

Nothing here runs for a vault that declares none of it.
"""
from __future__ import annotations

import re
from pathlib import Path

from vault_config import section_lines

_BULLET_RE = re.compile(r"^\s*[-*]\s*`?([A-Za-z][\w-]*)`?\s*(?::\s*(.*))?$")
_CURIE_RE = re.compile(r"^[A-Za-z][\w.-]*:\S+$")
RELATION_KEYS = ("type", "target", "evidence", "confidence", "as_of")


def load_page_types(root) -> dict[str, dict]:
    """``{kind: {"dir": str|None, "required": [..], "enum": {field: [values]}}}``."""
    kinds: dict[str, dict] = {}
    for line in section_lines(root, "Page types"):
        m = _BULLET_RE.match(line)
        if not m:
            continue
        spec = {"dir": None, "required": [], "enum": {}}
        for part in (m.group(2) or "").split(";"):
            key, sep, value = part.strip().partition("=")
            key, value = key.strip().lower(), value.strip().strip("`")
            if not sep:
                continue
            if key == "dir":
                spec["dir"] = value.strip("/")
            elif key == "required":
                spec["required"] = [v.strip() for v in value.split(",") if v.strip()]
            elif key.startswith("enum."):
                spec["enum"][key[5:]] = [v.strip() for v in value.split("|") if v.strip()]
        kinds[m.group(1)] = spec
    return kinds


def load_relation_types(root) -> set[str]:
    return {m.group(1) for line in section_lines(root, "Relation types")
            if (m := _BULLET_RE.match(line))}


def is_external_id(target: str) -> bool:
    return bool(_CURIE_RE.match(target)) and "/" not in target.split(":", 1)[0]


def _split_top_level(body: str) -> list[str]:
    parts, buf, quote = [], [], ""
    for ch in body:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch == ",":
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return parts


def parse_relation(item: str) -> dict | None:
    """Parse ``{k: v, k: "v"}``; None when it is not a flow mapping of ``key: value`` pairs."""
    item = item.strip()
    if not (item.startswith("{") and item.endswith("}")):
        return None
    out: dict[str, str] = {}
    for part in _split_top_level(item[1:-1]):
        if not part.strip():
            continue
        key, sep, value = part.partition(":")
        if not sep or not key.strip():
            return None
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key.strip()] = value
    return out


def parse_relations(path: Path) -> tuple[list[dict], list[str]]:
    """The ``relations:`` list of a page: (parsed relations, unparseable item texts)."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---"):
        return [], []
    end = text.find("\n---", 3)
    if end == -1:
        return [], []
    relations: list[dict] = []
    bad: list[str] = []
    inside = False
    for line in text[3:end].splitlines():
        stripped = line.strip()
        if re.match(r"^relations\s*:\s*$", line):
            inside = True
            continue
        if not inside:
            continue
        if stripped.startswith("- "):
            rel = parse_relation(stripped[2:])
            if rel is None:
                bad.append(stripped)
            else:
                relations.append(rel)
        elif stripped and not stripped.startswith("#"):
            inside = False
    return relations, bad
