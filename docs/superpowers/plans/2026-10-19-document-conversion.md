# Document Conversion (PDF to raw) Implementation Plan

> **Verification note (2026-10-19):** every task below was executed in order on a scratch clone of this repo at `2ae677e` - tests written first and seen to fail, then the code, then green, then the commit - ending at 139 passing tests (plus 1 opt-in slow test, also run once against real MinerU 4.0.12 on a 2-page PDF). Where a task shows an edit, the old text was matched exactly.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert PDFs into line-citable markdown under raw/converted/ at ingest time, with MinerU as the default converter, a user-supplied command as an alternative, and pdftotext as a loud fallback.

**Architecture:** A small shared normalizer (textnorm.py) and vault-config reader (vault_config.py) feed a new convert_source.py that resolves a converter, writes script-owned artifacts (markdown, pages.json, convert.json, dropped.txt, images) and is called from fast_ingest.py and batch_ingest.py. Lint gains stale-PDF and lossy-conversion notes.

**Tech Stack:** Python 3.11+ standard library only (zhconv optional); pytest via `uv run --with pytest`; external tools: mineru-kit 4.x (optional), pdftotext/pdfinfo (poppler).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-10-19-document-conversion-design.md` (read it first).
- Scripts are plain modules in `llm-wiki/scripts/`, imported by name; tests live in `tests/` and get that directory on `sys.path` from `tests/conftest.py`.
- Run tests with `uv run --with pytest python -m pytest` (pytest is not installed globally). Baseline: 31 passing tests; they must stay green.
- `raw/converted/` is written only by `convert_source.py`; no LLM edits it. `raw/pdfs/` is human-owned.
- Never pass `--remote`, `--remote-url` or `--api-key` to mineru-kit; documents are never uploaded.
- Docs and code comments in English; no secrets in the repo.
- Commit after every task; do not push.

---

### Task 1: textnorm.py, the shared normalizer

**Files:**
- Create: `llm-wiki/scripts/textnorm.py`
- Test: `tests/test_textnorm.py`

**Interfaces:**
- Consumes: nothing
- Produces: `normalize(s, *, join_hyphen=True) -> str` (case-preserving); `fold_case(s, *, join_hyphen=True) -> str`; `loose(s) -> str`; `normalize_with_map(text, *, fold=False, join_hyphen=True, loose_mode=False) -> (str, list[int])`; `variants(s) -> list[str]`; `contains(haystack, needle) -> bool`; `to_simplified(s) -> str`; constant `HYPHEN_BREAK` (compiled regex).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_textnorm.py`:

```python
"""Tests for textnorm.py: the shared normalizer."""
import textnorm


class TestNormalize:
    def test_keeps_case(self):
        assert textnorm.normalize("The Cell DIVIDES") == "The Cell DIVIDES"

    def test_collapses_whitespace_and_strips(self):
        assert textnorm.normalize("  a \n\t b   c  ") == "a b c"

    def test_nfkc_folds_full_width_and_ligatures(self):
        assert textnorm.normalize("ＡＢＣ１２３，ok") == "ABC123,ok"
        assert textnorm.normalize("ﬁne") == "fine"

    def test_curly_quotes_and_dashes(self):
        assert textnorm.normalize("“quoted” it’s a–b—c") == "\"quoted\" it's a-b-c"

    def test_removes_markdown_backslash_escapes(self):
        assert textnorm.normalize(r"5\*3 percent and \~300 million") == "5*3 percent and ~300 million"

    def test_strips_zero_width_characters(self):
        assert textnorm.normalize("a​b﻿c") == "abc"

    def test_joins_hyphenated_line_break(self):
        assert textnorm.normalize("inter-\nnational rules") == "international rules"

    def test_hyphen_kept_variant(self):
        assert textnorm.normalize("well-\nknown", join_hyphen=False) == "well-known"

    def test_does_not_join_when_hyphen_is_a_list_marker(self):
        assert textnorm.normalize("items -\nnext") == "items - next"

    def test_idempotent(self):
        once = textnorm.normalize("“A”  b-\nc \\* d")
        assert textnorm.normalize(once) == once

    def test_does_not_loosen_exact_matching(self):
        # check 13 relies on case-preserving normalization
        assert "US" not in textnorm.normalize("tell us now")
        assert "ACT" not in textnorm.normalize("act of 1990")


class TestFoldCaseAndLoose:
    def test_fold_case(self):
        assert textnorm.fold_case("The Cell  DIVIDES") == "the cell divides"

    def test_loose_drops_spaces_punctuation_and_markers(self):
        assert textnorm.loose("明年 继续 扩建，**社区图书馆**！") == "明年继续扩建社区图书馆"
        assert textnorm.loose("A-b, C_d.") == "abcd"


class TestVariantsAndContains:
    def test_variants_single_when_no_hyphen_break(self):
        assert textnorm.variants("plain text") == ["plain text"]

    def test_variants_both_forms(self):
        assert textnorm.variants("well-\nknown fact") == ["wellknown fact", "well-known fact"]

    def test_contains_matches_either_hyphen_form(self):
        raw = "a well-\nknown fact and inter-\nnational law"
        assert textnorm.contains(raw, "well-known fact")
        assert textnorm.contains(raw, "international law")
        assert not textnorm.contains(raw, "Well-known fact")


class TestNormalizeWithMap:
    def test_offsets_point_back_to_source_characters(self):
        text = "He said \\*hi\\*  there"
        norm, offs = textnorm.normalize_with_map(text)
        assert norm == "He said *hi* there"
        assert len(norm) == len(offs)
        for ch, off in zip(norm, offs):
            if ch != " ":
                assert text[off] == ch

    def test_collapsed_whitespace_maps_to_first_space(self):
        norm, offs = textnorm.normalize_with_map("a   b")
        assert norm == "a b"
        assert offs == [0, 1, 4]

    def test_leading_and_trailing_space_dropped_with_offsets(self):
        norm, offs = textnorm.normalize_with_map("  ab  ")
        assert norm == "ab"
        assert offs == [2, 3]

    def test_fold_and_loose_modes_keep_lengths_consistent(self):
        for kwargs in ({"fold": True}, {"loose_mode": True}):
            norm, offs = textnorm.normalize_with_map("Ａ b-\nc ＤＥ", **kwargs)
            assert len(norm) == len(offs)

    def test_hyphen_join_offsets_skip_the_break(self):
        text = "inter-\nnational"
        norm, offs = textnorm.normalize_with_map(text)
        assert norm == "international"
        assert text[offs[5]] == "n"  # first char after the removed "-\n"
        assert offs[5] == 7


class TestToSimplified:
    def test_identity_without_zhconv_or_converts_with_it(self):
        out = textnorm.to_simplified("財經")
        assert out in ("財經", "财经")
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run --with pytest python -m pytest tests/test_textnorm.py`

Expected: FAIL — `ModuleNotFoundError: No module named 'textnorm'`

- [ ] **Step 3: Implement**

Create `llm-wiki/scripts/textnorm.py`:

```python
"""Text normalization shared by lint check 13, convert_source and verify_quotes.

Everything here is deterministic and dependency-free. ``to_simplified`` is the one
optional exception: it becomes the identity when ``zhconv`` is not installed.

``normalize`` is case-preserving (lint check 13 depends on that). ``fold_case`` and
``loose`` are looser views used only where a fuzzy comparison is wanted.
"""
from __future__ import annotations

import re
import unicodedata

_ESCAPABLE = set("\\`*_{}[]()#+-.!~|<>")
_ZERO_WIDTH = {"​", "‌", "‍", "⁠", "﻿"}
_PUNCT_MAP = {
    "“": '"', "”": '"', "‘": "'", "’": "'",
    "—": "-", "–": "-", "‐": "-", "‑": "-", "−": "-",
}
HYPHEN_BREAK = re.compile(r"-\r?\n(?=\w)")


def _groups(text: str):
    """Yield (start, chunk): one character plus any combining marks that follow it."""
    i, n = 0, len(text)
    while i < n:
        j = i + 1
        while j < n and unicodedata.combining(text[j]):
            j += 1
        yield i, text[i:j]
        i = j


def normalize_with_map(text: str, *, fold: bool = False, join_hyphen: bool = True,
                       loose_mode: bool = False) -> tuple[str, list[int]]:
    """Normalize ``text`` and return ``(normalized, offsets)``.

    ``offsets[i]`` is the index in ``text`` of the source character that produced
    ``normalized[i]``, so a match can be mapped back to a raw line number.
    ``fold`` casefolds; ``join_hyphen=False`` keeps the hyphen of ``-\\n`` breaks;
    ``loose_mode`` folds case and keeps only letters, digits and CJK characters.
    """
    if loose_mode:
        fold = True
    out: list[str] = []
    offs: list[int] = []
    n = len(text)
    skip_until = -1
    for start, chunk in _groups(text):
        if start < skip_until:
            continue
        if chunk == "\\" and start + 1 < n and text[start + 1] in _ESCAPABLE:
            continue  # drop the backslash of a Markdown escape
        if chunk == "-" and out and out[-1].isalnum():
            m = HYPHEN_BREAK.match(text, start)
            if m:
                if not join_hyphen:
                    out.append("-")
                    offs.append(start)
                skip_until = m.end()
                continue
        for ch in unicodedata.normalize("NFKC", chunk):
            if ch in _ZERO_WIDTH:
                continue
            ch = _PUNCT_MAP.get(ch, ch)
            if ch.isspace():
                if out and out[-1] != " ":
                    out.append(" ")
                    offs.append(start)
                continue
            for c in (ch.casefold() if fold else ch):
                out.append(c)
                offs.append(start)
    if out and out[-1] == " ":
        out.pop()
        offs.pop()
    if loose_mode:
        keep = [k for k, c in enumerate(out) if c.isalnum()]
        return "".join(out[k] for k in keep), [offs[k] for k in keep]
    return "".join(out), offs


def normalize(s: str, *, join_hyphen: bool = True) -> str:
    return normalize_with_map(s, join_hyphen=join_hyphen)[0]


def fold_case(s: str, *, join_hyphen: bool = True) -> str:
    return normalize_with_map(s, fold=True, join_hyphen=join_hyphen)[0]


def loose(s: str) -> str:
    return normalize_with_map(s, loose_mode=True)[0]


def variants(s: str) -> list[str]:
    """Normalized forms of ``s``: hyphen-joined, plus hyphen-kept when ``s`` has a ``-\\n`` break."""
    forms = [normalize(s)]
    if HYPHEN_BREAK.search(s):
        forms.append(normalize(s, join_hyphen=False))
    return forms


def contains(haystack: str, needle: str) -> bool:
    """Case-preserving containment after normalization, trying both hyphen forms."""
    n = normalize(needle)
    return any(n in v for v in variants(haystack))


def to_simplified(s: str) -> str:
    """Traditional -> Simplified Chinese when ``zhconv`` is importable, else ``s`` unchanged."""
    try:
        from zhconv import convert
    except ImportError:
        return s
    return convert(s, "zh-hans")
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `uv run --with pytest python -m pytest tests/test_textnorm.py`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add llm-wiki/scripts/textnorm.py tests/test_textnorm.py
git commit -m "feat(textnorm): shared case-preserving normalizer with offset map"
```


### Task 2: Lint check 13 uses the normalizer

**Files:**
- Modify: `llm-wiki/scripts/lint_wiki.py`
- Test: `tests/test_lint_claims.py`

**Interfaces:**
- Consumes: `textnorm.contains(haystack: str, needle: str) -> bool` from Task 1
- Produces: check 13 tolerant of Markdown escapes, hyphen line breaks and whitespace, still case-sensitive

- [ ] **Step 1: Write the failing tests**

Create `tests/test_lint_claims.py`:

```python
"""Lint check 13 must tolerate Markdown escapes and hyphen breaks, but stay case-sensitive."""
import lint_wiki


def _vault(tmp_path, raw_text, claim):
    (tmp_path / "wiki" / "sources").mkdir(parents=True)
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / "doc.md").write_text(raw_text, encoding="utf-8")
    (tmp_path / "wiki" / "sources" / "doc.md").write_text(
        "---\ntype: source-summary\nclaims_requiring_verification:\n"
        f"  - '\"{claim}\" (raw/doc.md, L1)'\n---\n\n# Doc\n",
        encoding="utf-8",
    )
    return tmp_path


def _errors(root):
    lint_wiki.errors.clear()
    lint_wiki.warnings.clear()
    lint_wiki.suggestions.clear()
    lint_wiki.check_claims_verification(root / "wiki", root)
    return list(lint_wiki.errors)


def test_markdown_escape_in_raw_is_tolerated(tmp_path):
    root = _vault(tmp_path, "growth of 5\\*3 percent\n", "5*3 percent")
    assert _errors(root) == []


def test_hyphen_break_in_raw_is_tolerated(tmp_path):
    root = _vault(tmp_path, "a well-\nknown fact\n", "well-known fact")
    assert _errors(root) == []


def test_whitespace_differences_are_tolerated(tmp_path):
    root = _vault(tmp_path, "the   cell\ndivides\n", "the cell divides")
    assert _errors(root) == []


def test_still_case_sensitive(tmp_path):
    root = _vault(tmp_path, "please tell us now\n", "US")
    assert len(_errors(root)) == 1


def test_absent_claim_still_fails(tmp_path):
    root = _vault(tmp_path, "something else entirely\n", "2% discount")
    assert len(_errors(root)) == 1
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run --with pytest python -m pytest tests/test_lint_claims.py`

Expected: FAIL — the escape, hyphen-break and whitespace tests fail with one lint error each; the two case/absent tests already pass

- [ ] **Step 3: Import the normalizer**

In `llm-wiki/scripts/lint_wiki.py`, replace:

```python
from collections import defaultdict
```

with:

```python
from collections import defaultdict

import textnorm
```

- [ ] **Step 4: Use it in check 13**

In `llm-wiki/scripts/lint_wiki.py`, replace:

```python
            if verbatim not in raw_text:
```

with:

```python
            if not textnorm.contains(raw_text, verbatim):
```

- [ ] **Step 5: Run the tests and verify they pass**

Run: `uv run --with pytest python -m pytest tests/test_lint_claims.py`

Expected: PASS

- [ ] **Step 6: Run the full suite**

Run: `uv run --with pytest python -m pytest `

Expected: PASS — the 31 original tests plus the new ones

- [ ] **Step 7: Commit**

```bash
git add llm-wiki/scripts/lint_wiki.py tests/test_lint_claims.py
git commit -m "fix(lint): check 13 tolerates Markdown escapes and hyphen breaks"
```


### Task 3: vault_config.py, readers for the vault CLAUDE.md

**Files:**
- Create: `llm-wiki/scripts/vault_config.py`
- Test: `tests/test_vault_config.py`

**Interfaces:**
- Consumes: nothing
- Produces: `section_lines(root, heading) -> list[str]`; `key_values(lines) -> dict[str, str]`; `private_zones(root) -> list[str]`; `in_private_zone(root, path) -> bool`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_vault_config.py`:

```python
"""Tests for vault_config.py: the tiny CLAUDE.md readers."""
import vault_config

CLAUDE = """# Test Knowledge Base

## Scope

What this wiki covers:
- AI

## Private zones

Paths containing sensitive data.

- `sensitive_data/`
- `clinic/notes`

## Document conversion

converter: auto        # auto | mineru
tier: basic
command: "mytool {input} {outdir}"

### Sub heading stays inside the section
timeout: 120

## Notes for the LLM
- Language: English
"""


def _vault(tmp_path, text=CLAUDE):
    (tmp_path / "CLAUDE.md").write_text(text, encoding="utf-8")
    return tmp_path


def test_section_lines_stops_at_next_h2(tmp_path):
    lines = vault_config.section_lines(_vault(tmp_path), "Document conversion")
    assert any(l.startswith("converter:") for l in lines)
    assert not any("Language" in l for l in lines)


def test_section_lines_missing_file_or_heading(tmp_path):
    assert vault_config.section_lines(tmp_path, "Document conversion") == []
    assert vault_config.section_lines(_vault(tmp_path), "Nope") == []


def test_key_values_strips_comments_and_quotes(tmp_path):
    kv = vault_config.key_values(vault_config.section_lines(_vault(tmp_path), "Document conversion"))
    assert kv["converter"] == "auto"
    assert kv["tier"] == "basic"
    assert kv["command"] == "mytool {input} {outdir}"
    assert kv["timeout"] == "120"


def test_private_zones_parsed_from_bullets(tmp_path):
    assert vault_config.private_zones(_vault(tmp_path)) == ["sensitive_data", "clinic/notes"]


def test_in_private_zone(tmp_path):
    root = _vault(tmp_path)
    (root / "sensitive_data").mkdir()
    (root / "raw").mkdir()
    assert vault_config.in_private_zone(root, root / "sensitive_data" / "a.pdf")
    assert vault_config.in_private_zone(root, root / "clinic" / "notes" / "deep" / "b.pdf")
    assert not vault_config.in_private_zone(root, root / "raw" / "c.pdf")
    assert not vault_config.in_private_zone(root, root / "sensitive_data_not" / "d.pdf")
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run --with pytest python -m pytest tests/test_vault_config.py`

Expected: FAIL — `ModuleNotFoundError: No module named 'vault_config'`

- [ ] **Step 3: Implement**

Create `llm-wiki/scripts/vault_config.py`:

```python
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
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `uv run --with pytest python -m pytest tests/test_vault_config.py`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add llm-wiki/scripts/vault_config.py tests/test_vault_config.py
git commit -m "feat(vault_config): read sections, key/values and private zones from CLAUDE.md"
```


### Task 4: convert_source.py skeleton: config, slug, hashing

**Files:**
- Create: `llm-wiki/scripts/convert_source.py`
- Test: `tests/test_convert_source.py`

**Interfaces:**
- Consumes: `vault_config.section_lines`, `vault_config.key_values` (Task 3)
- Produces: `ConvertError(message, code)`; `Config(converter, tier, timeout, command)`; `load_config(root) -> Config`; `sanitize_slug(filename) -> str`; `sha256_file(path) -> str`; `now_iso() -> str`; exit-code constants `EXIT_OK/USAGE/FAILED/SCANNED/PRIVATE/EXISTS`; `DEFAULT_TIMEOUT`, `WINDOW_PAGES`, `CPU_TIERS`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_convert_source.py`:

```python
"""Tests for convert_source.py: config, slugs, hashing."""
import pytest

import convert_source


def _vault(tmp_path, body=None):
    text = "# Wiki\n\n## Scope\n\n- AI\n"
    if body is not None:
        text += "\n## Document conversion\n\n" + body + "\n"
    (tmp_path / "CLAUDE.md").write_text(text, encoding="utf-8")
    return tmp_path


class TestConfig:
    def test_defaults_without_section(self, tmp_path):
        cfg = convert_source.load_config(_vault(tmp_path))
        assert (cfg.converter, cfg.tier, cfg.timeout, cfg.command) == ("auto", "basic", 900, "")

    def test_reads_values(self, tmp_path):
        body = 'converter: command\ntier: flash\ntimeout: 60\ncommand: "mytool {input} {outdir}"'
        cfg = convert_source.load_config(_vault(tmp_path, body))
        assert (cfg.converter, cfg.tier, cfg.timeout) == ("command", "flash", 60)
        assert cfg.command == "mytool {input} {outdir}"

    def test_missing_claude_md_gives_defaults(self, tmp_path):
        assert convert_source.load_config(tmp_path).converter == "auto"

    @pytest.mark.parametrize("body", ["converter: nope", "tier: huge", "timeout: soon", "timeout: 0"])
    def test_invalid_values_exit_2(self, tmp_path, body):
        with pytest.raises(convert_source.ConvertError) as exc:
            convert_source.load_config(_vault(tmp_path, body))
        assert exc.value.code == convert_source.EXIT_USAGE


class TestSlug:
    def test_basic(self):
        assert convert_source.sanitize_slug("My Paper (v2): Final!.pdf") == "my-paper-v2-final"

    def test_cjk_is_kept(self):
        assert convert_source.sanitize_slug("第一性原理 笔记.pdf") == "第一性原理-笔记"

    def test_citation_breaking_characters_removed(self):
        slug = convert_source.sanitize_slug("a,b (1); c) d.pdf")
        assert not any(ch in slug for ch in ",();")

    def test_empty_becomes_untitled(self):
        assert convert_source.sanitize_slug("!!!.pdf") == "untitled"


def test_sha256_file(tmp_path):
    f = tmp_path / "x.bin"
    f.write_bytes(b"hello")
    assert convert_source.sha256_file(f) == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run --with pytest python -m pytest tests/test_convert_source.py`

Expected: FAIL — `ModuleNotFoundError: No module named 'convert_source'`

- [ ] **Step 3: Implement**

Create `llm-wiki/scripts/convert_source.py`:

```python
#!/usr/bin/env python3
"""
convert_source.py - convert a PDF into line-citable markdown under raw/converted/.

Usage:
    python3 convert_source.py <wiki-root> <pdf> [--force]

The converter is chosen from the vault CLAUDE.md "## Document conversion" section.
Output, written only by this script (never by an LLM):

    raw/converted/<slug>/<slug>.md            line-citable markdown
    raw/converted/<slug>/<slug>.pages.json    PDF page -> line range (optional)
    raw/converted/<slug>/<slug>.convert.json  provenance
    raw/converted/<slug>/<slug>.dropped.txt   header/footer/footnote text the markdown omits
    raw/converted/<slug>/images/

Exit codes: 0 ok, 2 usage/unavailable, 3 conversion failed, 4 scanned PDF,
5 input inside a private zone, 6 output exists for a different PDF (use --force).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import textnorm
import vault_config

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_FAILED = 3
EXIT_SCANNED = 4
EXIT_PRIVATE = 5
EXIT_EXISTS = 6

DEFAULT_TIMEOUT = 900
WINDOW_PAGES = 8
MIN_PDFTOTEXT_CHARS = 20
VALID_CONVERTERS = {"auto", "mineru", "pdftotext", "command", "none"}
VALID_TIERS = {"flash", "basic", "standard", "advanced"}
CPU_TIERS = {"flash", "basic"}


class ConvertError(Exception):
    def __init__(self, message: str, code: int = EXIT_FAILED):
        super().__init__(message)
        self.code = code


@dataclass
class Config:
    converter: str = "auto"
    tier: str = "basic"
    timeout: int = DEFAULT_TIMEOUT
    command: str = ""


def load_config(root) -> Config:
    kv = vault_config.key_values(vault_config.section_lines(root, "Document conversion"))
    cfg = Config()
    if "converter" in kv:
        if kv["converter"] not in VALID_CONVERTERS:
            raise ConvertError(f"invalid converter {kv['converter']!r}; use one of {sorted(VALID_CONVERTERS)}", EXIT_USAGE)
        cfg.converter = kv["converter"]
    if "tier" in kv:
        if kv["tier"] not in VALID_TIERS:
            raise ConvertError(f"invalid tier {kv['tier']!r}; use one of {sorted(VALID_TIERS)}", EXIT_USAGE)
        cfg.tier = kv["tier"]
    if "timeout" in kv:
        if not kv["timeout"].isdigit() or int(kv["timeout"]) <= 0:
            raise ConvertError(f"invalid timeout {kv['timeout']!r}; use a positive number of seconds", EXIT_USAGE)
        cfg.timeout = int(kv["timeout"])
    if "command" in kv:
        cfg.command = kv["command"]
    return cfg


def sanitize_slug(filename: str) -> str:
    """Folder/file slug from a PDF filename: lower-case, letters and digits (any script) kept."""
    stem = Path(filename).stem
    slug = "".join(ch if ch.isalnum() else "-" for ch in stem.lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug or "untitled"


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `uv run --with pytest python -m pytest tests/test_convert_source.py`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add llm-wiki/scripts/convert_source.py tests/test_convert_source.py
git commit -m "feat(convert_source): config parsing, slugs and hashing"
```


### Task 5: pdftotext converter with exact page map

**Files:**
- Create: `tests/pdf_helpers.py`
- Modify: `llm-wiki/scripts/convert_source.py`
- Modify: `tests/test_convert_source.py`

**Interfaces:**
- Consumes: `ConvertError`, `EXIT_*`, `DEFAULT_TIMEOUT`, `MIN_PDFTOTEXT_CHARS` (Task 4)
- Produces: `Output` dataclass (fields: markdown, converter, version, lossy, pages, match_rate, confidence, page_map, pages_total, dropped_lines, images, dropped_types); `tool_version(exe, flag='-v') -> str`; `pdftotext_pages(pdf, timeout) -> list[str]`; `convert_pdftotext(pdf, timeout) -> Output`; test helper `pdf_helpers.make_pdf(pages: list[str]) -> bytes`

- [ ] **Step 1: Add the tiny-PDF test helper**

Create `tests/pdf_helpers.py`:

```python
"""Build tiny valid PDFs for tests (no third-party libraries)."""


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages: list[str]) -> bytes:
    """One PDF page per string; newlines inside a string become separate text lines."""
    n = len(pages)
    font_id = 3 + 2 * n
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n))
    objs: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode(),
    ]
    for i, text in enumerate(pages):
        page_id = 3 + 2 * i
        content = "BT /F1 12 Tf 14 TL 72 720 Td " + " T* ".join(
            f"({_esc(line)}) Tj" for line in text.split("\n")
        ) + " ET"
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {page_id + 1} 0 R "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>".encode()
        )
        objs.append(f"<< /Length {len(content)} >>\nstream\n{content}\nendstream".encode())
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n"
    out += b"".join(f"{off:010d} 00000 n \n".encode() for off in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_convert_source.py`:

```python
"""pdftotext converter: markdown written page by page, exact page map."""
import shutil

import pytest

from pdf_helpers import make_pdf

import convert_source

needs_poppler = pytest.mark.skipif(shutil.which("pdftotext") is None, reason="pdftotext not installed")


@needs_poppler
class TestPdftotext:
    def _pdf(self, tmp_path, pages):
        path = tmp_path / "paper.pdf"
        path.write_bytes(make_pdf(pages))
        return path

    def test_pages_split_on_form_feed(self, tmp_path):
        pdf = self._pdf(tmp_path, ["Alpha line one\nAlpha line two", "Beta page text here"])
        pages = convert_source.pdftotext_pages(pdf)
        assert len(pages) == 2
        assert "Alpha line two" in pages[0] and "Beta page text" in pages[1]

    def test_markdown_and_exact_page_map(self, tmp_path):
        pdf = self._pdf(tmp_path, ["Alpha line one\nAlpha line two", "Beta page text here"])
        out = convert_source.convert_pdftotext(pdf)
        lines = out.markdown.rstrip("\n").split("\n")
        assert out.converter == "pdftotext" and out.lossy is True
        assert out.page_map == "exact" and out.confidence == "high" and out.pages_total == 2
        p1, p2 = out.pages
        assert (p1["page"], p1["how"]) == (1, "exact") and p2["page"] == 2
        assert "Alpha line one" in lines[p1["line_start"] - 1]
        assert "Beta page text" in lines[p2["line_start"] - 1]
        assert p1["line_end"] + 1 == p2["line_start"]          # contiguous partition
        assert p2["line_end"] == len(lines)

    def test_scanned_pdf_exits_4(self, tmp_path):
        pdf = self._pdf(tmp_path, [" "])
        with pytest.raises(convert_source.ConvertError) as exc:
            convert_source.convert_pdftotext(pdf)
        assert exc.value.code == convert_source.EXIT_SCANNED


def test_missing_pdftotext_exits_2(tmp_path, monkeypatch):
    monkeypatch.setattr(convert_source.shutil, "which", lambda name: None)
    with pytest.raises(convert_source.ConvertError) as exc:
        convert_source.pdftotext_pages(tmp_path / "x.pdf")
    assert exc.value.code == convert_source.EXIT_USAGE
```

- [ ] **Step 3: Run the tests and verify they fail**

Run: `uv run --with pytest python -m pytest tests/test_convert_source.py`

Expected: FAIL — `AttributeError: module 'convert_source' has no attribute 'pdftotext_pages'`

- [ ] **Step 4: Implement**

Append to `llm-wiki/scripts/convert_source.py`:

```python
@dataclass
class Output:
    """What a converter hands back; ``convert()`` turns it into files."""
    markdown: str
    converter: str
    version: str = "unknown"
    lossy: bool = False
    pages: list[dict] | None = None        # [{"page", "line_start", "line_end", "how"}]
    match_rate: float | None = None
    confidence: str | None = None          # "high" | "low"
    page_map: str = "none"                 # "exact" | "aligned" | "none"
    pages_total: int | None = None
    dropped_lines: list[str] = field(default_factory=list)
    images: dict[str, bytes] = field(default_factory=dict)   # "images/x.jpg" -> bytes
    dropped_types: list[str] = field(default_factory=list)


def tool_version(exe: str, flag: str = "-v") -> str:
    try:
        p = subprocess.run([exe, flag], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    first = (p.stderr or p.stdout).strip().splitlines()
    return first[0].strip() if first else "unknown"


def pdftotext_pages(pdf, timeout: int = DEFAULT_TIMEOUT) -> list[str]:
    exe = shutil.which("pdftotext")
    if not exe:
        raise ConvertError("pdftotext not found (install poppler-utils), or configure another converter", EXIT_USAGE)
    try:
        p = subprocess.run([exe, str(pdf), "-"], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ConvertError(f"pdftotext timed out after {timeout}s", EXIT_FAILED)
    if p.returncode != 0:
        raise ConvertError(f"pdftotext failed: {p.stderr.strip()[:200]}", EXIT_FAILED)
    pages = p.stdout.split("\x0c")
    if pages and not pages[-1].strip():
        pages.pop()
    return pages


def convert_pdftotext(pdf, timeout: int = DEFAULT_TIMEOUT) -> Output:
    pages = pdftotext_pages(pdf, timeout)
    if sum(len(re.sub(r"\s", "", p)) for p in pages) < MIN_PDFTOTEXT_CHARS:
        raise ConvertError("scanned PDF: pdftotext found no text layer; needs MinerU or your own converter", EXIT_SCANNED)
    lines: list[str] = []
    ranges: list[dict] = []
    for number, text in enumerate(pages, 1):
        if lines:
            lines.append("")                      # blank separator, owned by the previous page
            ranges[-1]["line_end"] = len(lines)
        body = [ln.rstrip() for ln in text.strip("\n").split("\n")]
        start = len(lines) + 1
        lines.extend(body)
        ranges.append({"page": number, "line_start": start, "line_end": len(lines), "how": "exact"})
    exe = shutil.which("pdftotext") or "pdftotext"
    return Output(
        markdown="\n".join(lines) + "\n", converter="pdftotext", version=tool_version(exe),
        lossy=True, pages=ranges, match_rate=1.0, confidence="high", page_map="exact",
        pages_total=len(pages),
    )
```

- [ ] **Step 5: Run the tests and verify they pass**

Run: `uv run --with pytest python -m pytest tests/test_convert_source.py`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add llm-wiki/scripts/convert_source.py tests/test_convert_source.py tests/pdf_helpers.py
git commit -m "feat(convert_source): pdftotext converter with exact page map"
```


### Task 6: Custom command converter

**Files:**
- Modify: `llm-wiki/scripts/convert_source.py`
- Modify: `tests/test_convert_source.py`

**Interfaces:**
- Consumes: `Output`, `ConvertError`, `EXIT_USAGE/FAILED`, `DEFAULT_TIMEOUT` (Tasks 4-5)
- Produces: `convert_command(template: str, pdf, slug: str, timeout=DEFAULT_TIMEOUT) -> Output`; placeholders `{input}` `{outdir}` `{slug}`, no shell; `page_map` value `"supplied"` when the tool wrote `<slug>.pages.json`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_convert_source.py`:

```python
"""Custom command converter: the user's own tool, run without a shell."""
import json
import shlex
import sys

import pytest

import convert_source

SCRIPT = '''
import json, os, pathlib, sys, time
pdf, outdir, slug = sys.argv[1:4]
if os.environ.get("FAKE_CMD_SLEEP"):
    time.sleep(float(os.environ["FAKE_CMD_SLEEP"]))
if os.environ.get("FAKE_CMD_FAIL"):
    sys.stderr.write("boom from tool")
    sys.exit(1)
out = pathlib.Path(outdir)
if not os.environ.get("FAKE_CMD_NO_MD"):
    (out / (slug + ".md")).write_text("Converted line one\\nConverted line two\\n", encoding="utf-8")
if os.environ.get("FAKE_CMD_PAGES"):
    (out / (slug + ".pages.json")).write_text(json.dumps({
        "match_rate": 1.0, "confidence": "high",
        "pages": [{"page": 1, "line_start": 1, "line_end": 2, "how": "exact"}]}), encoding="utf-8")
    (out / "images").mkdir()
    (out / "images" / "fig.png").write_bytes(b"PNG")
with open(os.environ.get("FAKE_CMD_LOG", os.devnull), "a") as log:
    log.write(pdf + "\\n")
'''


@pytest.fixture
def tool(tmp_path):
    script = tmp_path / "tool.py"
    script.write_text(SCRIPT, encoding="utf-8")
    template = f"{shlex.quote(sys.executable)} {shlex.quote(str(script))} {{input}} {{outdir}} {{slug}}"
    return template


def test_writes_markdown_only(tool, tmp_path):
    pdf = tmp_path / "My Paper (final).pdf"        # spaces and parentheses must survive intact
    pdf.write_bytes(b"%PDF")
    out = convert_source.convert_command(tool, pdf, "my-paper")
    assert out.markdown == "Converted line one\nConverted line two\n"
    assert (out.converter, out.version, out.lossy, out.page_map) == ("command", "custom", False, "none")
    assert out.pages is None and out.images == {}


def test_pages_json_and_images_are_picked_up(tool, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CMD_PAGES", "1")
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF")
    out = convert_source.convert_command(tool, pdf, "x")
    assert out.page_map == "supplied" and out.confidence == "high"
    assert out.pages[0]["page"] == 1
    assert out.images == {"images/fig.png": b"PNG"}


def test_missing_markdown_is_a_failure(tool, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CMD_NO_MD", "1")
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF")
    with pytest.raises(convert_source.ConvertError) as exc:
        convert_source.convert_command(tool, pdf, "x")
    assert exc.value.code == convert_source.EXIT_FAILED and "x.md" in str(exc.value)


def test_nonzero_exit_reports_stderr(tool, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CMD_FAIL", "1")
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF")
    with pytest.raises(convert_source.ConvertError) as exc:
        convert_source.convert_command(tool, pdf, "x")
    assert exc.value.code == convert_source.EXIT_FAILED and "boom from tool" in str(exc.value)


def test_timeout(tool, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CMD_SLEEP", "5")
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF")
    with pytest.raises(convert_source.ConvertError) as exc:
        convert_source.convert_command(tool, pdf, "x", timeout=1)
    assert exc.value.code == convert_source.EXIT_FAILED and "timed out" in str(exc.value)


def test_unknown_program_and_empty_template_exit_2(tmp_path):
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF")
    with pytest.raises(convert_source.ConvertError) as exc:
        convert_source.convert_command("no-such-program-xyz {input} {outdir}", pdf, "x")
    assert exc.value.code == convert_source.EXIT_USAGE
    with pytest.raises(convert_source.ConvertError) as exc:
        convert_source.convert_command("  ", pdf, "x")
    assert exc.value.code == convert_source.EXIT_USAGE
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run --with pytest python -m pytest tests/test_convert_source.py`

Expected: FAIL — `AttributeError: ... no attribute 'convert_command'`

- [ ] **Step 3: Implement**

Append to `llm-wiki/scripts/convert_source.py`:

```python
def convert_command(template: str, pdf, slug: str, timeout: int = DEFAULT_TIMEOUT) -> Output:
    """Run the user's own converter: ``{input}``, ``{outdir}`` and ``{slug}`` are substituted,
    no shell is involved. The tool must write ``{outdir}/<slug>.md``; ``<slug>.pages.json``
    and an ``images/`` directory are optional."""
    if not template.strip():
        raise ConvertError('converter "command" needs a `command:` line under ## Document conversion', EXIT_USAGE)
    pdf = Path(pdf)
    with tempfile.TemporaryDirectory(prefix="llmwiki-cmd-") as tmp:
        outdir = Path(tmp)
        argv = [
            tok.replace("{input}", str(pdf)).replace("{outdir}", str(outdir)).replace("{slug}", slug)
            for tok in shlex.split(template)
        ]
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, cwd=tmp)
        except FileNotFoundError:
            raise ConvertError(f"converter command not found: {argv[0]}", EXIT_USAGE)
        except subprocess.TimeoutExpired:
            raise ConvertError(f"converter command timed out after {timeout}s", EXIT_FAILED)
        if p.returncode != 0:
            raise ConvertError(f"converter command failed ({p.returncode}): {p.stderr.strip()[:300]}", EXIT_FAILED)
        md_path = outdir / f"{slug}.md"
        if not md_path.is_file():
            raise ConvertError(f"converter command did not write {slug}.md into the output directory", EXIT_FAILED)
        out = Output(markdown=md_path.read_text(encoding="utf-8"), converter="command", version="custom")
        pages_path = outdir / f"{slug}.pages.json"
        if pages_path.is_file():
            try:
                data = json.loads(pages_path.read_text(encoding="utf-8"))
                if isinstance(data.get("pages"), list) and data["pages"]:
                    out.pages = data["pages"]
                    out.match_rate = data.get("match_rate")
                    out.confidence = data.get("confidence", "low")
                    out.page_map = "supplied"
                    out.pages_total = len(out.pages)
            except (ValueError, AttributeError):
                pass  # an unreadable pages.json only costs the page map
        image_dir = outdir / "images"
        if image_dir.is_dir():
            for f in sorted(image_dir.rglob("*")):
                if f.is_file():
                    out.images[f"images/{f.relative_to(image_dir).as_posix()}"] = f.read_bytes()
        return out
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `uv run --with pytest python -m pytest tests/test_convert_source.py`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add llm-wiki/scripts/convert_source.py tests/test_convert_source.py
git commit -m "feat(convert_source): user-supplied command converter"
```


### Task 7: Page map: align MinerU blocks with markdown lines

**Files:**
- Modify: `llm-wiki/scripts/convert_source.py`
- Modify: `tests/test_convert_source.py`

**Interfaces:**
- Consumes: `textnorm.fold_case` (Task 1)
- Produces: `flat_text(content) -> str`; `dropped_lines(pages: list[dict]) -> list[str]`; `align_pages(markdown: str, pages: list[dict]) -> (ranges: list[dict], match_rate: float, confidence: str)`; constants `SKIP_TYPES`, `DROPPED_TYPES`, `KEY_LEN`, `MIN_BLOCK_CHARS`, `CONFIDENCE_THRESHOLD`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_convert_source.py`:

```python
"""Page map: align MinerU blocks with markdown lines."""
import convert_source

MD = "\n".join([
    "# Evidence Aggregator for rare disease",                              # 1
    "",                                                                   # 2
    "Retrieving and synthesizing technical information is hard work.",    # 3
    "",                                                                   # 4
    "## Introduction",                                                    # 5
    "",                                                                   # 6
    "Rare diseases affect many people worldwide and diagnosis is slow.",  # 7
    "",                                                                   # 8
    "![](images/page_2_image_4.jpg)",                                     # 9
    "",                                                                   # 10
    "We evaluated the tool with a set of solved cases from the clinic.",  # 11
    "",                                                                   # 12
    "| System | Score |",                                                 # 13
    "| --- | --- |",                                                      # 14
    "| Ours | 91 |",                                                      # 15
    "",                                                                   # 16
    "Retrieving and synthesizing technical information is hard work.",    # 17 (repeated text)
]) + "\n"


def blk(kind, text):
    return {"type": kind, "content": text}


PAGES = [
    {"page_idx": 0, "blocks": [
        blk("header", "bioRxiv preprint doi: 10.1101/2025.03.10"),
        blk("doc_title", "Evidence Aggregator for rare disease"),
        blk("text", "Retrieving and synthesizing technical information is hard work."),
        blk("text", "- PREPRINT - Page 1 of 22"),
        blk("page_number", "1"),
    ]},
    {"page_idx": 1, "blocks": [
        blk("paragraph_title", "Introduction"),
        blk("text", "Rare diseases affect many people worldwide and diagnosis is slow."),
        blk("page_footnote", "1 https://example.org/tool"),
    ]},
    {"page_idx": 2, "blocks": [
        blk("text", "We evaluated the tool with a set of solved cases from the clinic."),
        blk("table", "<table><tr><td>System</td><td>Score</td></tr></table>"),
        blk("text", "Retrieving and synthesizing technical information is hard work."),
    ]},
]


class TestAlign:
    def test_ranges_form_a_partition_with_forward_search(self):
        ranges, rate, confidence = convert_source.align_pages(MD, PAGES)
        assert [(r["page"], r["line_start"], r["line_end"], r["how"]) for r in ranges] == [
            (1, 1, 4, "aligned"), (2, 5, 10, "aligned"), (3, 11, 17, "aligned"),
        ]
        assert round(rate, 3) == round(6 / 7, 3) and confidence == "high"

    def test_repeated_text_maps_to_the_later_occurrence(self):
        ranges, _, _ = convert_source.align_pages(MD, PAGES)
        assert ranges[2]["line_end"] == 17          # not pulled back to line 3

    def test_page_idx_is_absolute_across_windows(self):
        shifted = [dict(p, page_idx=p["page_idx"] + 4) for p in PAGES]
        ranges, _, _ = convert_source.align_pages(MD, shifted)
        assert [r["page"] for r in ranges] == [5, 6, 7]

    def test_page_without_matches_is_interpolated(self):
        pages = PAGES + [{"page_idx": 3, "blocks": [blk("image", "fig")]}]
        ranges, _, _ = convert_source.align_pages(MD, pages)
        last = ranges[-1]
        assert last["how"] == "interpolated" and last["page"] == 4
        assert 1 <= last["line_start"] <= last["line_end"] <= 17

    def test_nothing_matches_gives_low_confidence(self):
        pages = [{"page_idx": 0, "blocks": [blk("text", "completely unrelated sentence here")]}]
        ranges, rate, confidence = convert_source.align_pages(MD, pages)
        assert rate == 0.0 and confidence == "low" and ranges[0]["how"] == "interpolated"

    def test_escapes_and_case_do_not_break_matching(self):
        md = "Growth of 5\\*3 PERCENT was reported in the second quarter.\n"
        pages = [{"page_idx": 0, "blocks": [blk("text", "growth of 5*3 percent was reported in the second quarter.")]}]
        _, rate, _ = convert_source.align_pages(md, pages)
        assert rate == 1.0


class TestDroppedLines:
    def test_collects_margin_blocks_once_per_page(self):
        pages = [
            {"page_idx": 0, "blocks": [blk("header", "Running  header"), blk("header", "Running header"),
                                       blk("text", "body"), blk("footer", "p. 1")]},
            {"page_idx": 1, "blocks": [blk("page_footnote", "1 https://example.org"), blk("page_number", "2")]},
        ]
        assert convert_source.dropped_lines(pages) == [
            "p1\theader\tRunning header", "p1\tfooter\tp. 1",
            "p2\tpage_footnote\t1 https://example.org", "p2\tpage_number\t2",
        ]

    def test_flat_text_handles_nested_content(self):
        assert convert_source.flat_text([{"type": "text", "content": "a"}, {"content": ["b", "c"]}]) == "a b c"
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run --with pytest python -m pytest tests/test_convert_source.py`

Expected: FAIL — `AttributeError: ... no attribute 'align_pages'`

- [ ] **Step 3: Implement**

Append to `llm-wiki/scripts/convert_source.py`:

```python
SKIP_TYPES = {"header", "footer", "page_number", "page_footnote", "table"}
DROPPED_TYPES = ("header", "footer", "page_number", "page_footnote")
KEY_LEN = 25
MIN_BLOCK_CHARS = 12
CONFIDENCE_THRESHOLD = 0.8


def flat_text(content) -> str:
    """Flatten a MinerU block ``content`` (str, list or dict) into plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(flat_text(c) for c in content)
    if isinstance(content, dict):
        return flat_text(content.get("content", ""))
    return ""


def dropped_lines(pages: list[dict]) -> list[str]:
    """``p<N>\\t<type>\\t<text>`` for blocks MinerU leaves out of the markdown, once per page."""
    out: list[str] = []
    for page in pages:
        number = page.get("page_idx", 0) + 1
        seen: set[tuple[str, str]] = set()
        for block in page.get("blocks", []):
            kind = block.get("type")
            if kind not in DROPPED_TYPES:
                continue
            text = " ".join(flat_text(block.get("content", "")).split())
            if not text or (kind, text) in seen:
                continue
            seen.add((kind, text))
            out.append(f"p{number}\t{kind}\t{text}")
    return out


def align_pages(markdown: str, pages: list[dict]) -> tuple[list[dict], float, str]:
    """Map MinerU pages to markdown line ranges.

    Body blocks are searched forward from the previous match (so repeated text maps to
    its later occurrence) by their first ``KEY_LEN`` characters after case folding.
    Ranges are returned as a partition of the markdown: each page runs up to the line
    before the next page starts. Returns ``(ranges, match_rate, confidence)``.
    """
    lines = markdown.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    folded = [textnorm.fold_case(line) for line in lines]
    total = len(lines)
    cursor = 0
    body = matched = 0
    found: list[list[int]] = []
    for page in pages:
        hits: list[int] = []
        for block in page.get("blocks", []):
            if block.get("type") in SKIP_TYPES:
                continue
            text = textnorm.fold_case(flat_text(block.get("content", "")))
            if len(text) < MIN_BLOCK_CHARS:
                continue
            body += 1
            key = text[:KEY_LEN]
            for i in range(cursor, total):
                if key in folded[i]:
                    hits.append(i + 1)
                    cursor = i
                    matched += 1
                    break
        found.append(hits)
    starts: list[int] = []
    last_end = 0
    for hits in found:
        previous = starts[-1] if starts else 1
        if hits:
            starts.append(max(min(hits), previous))
            last_end = max(hits)
        else:
            starts.append(max(last_end + 1, previous))
    if starts:
        starts[0] = 1
    starts = [min(s, max(total, 1)) for s in starts]
    ranges = []
    for k, page in enumerate(pages):
        end = starts[k + 1] - 1 if k + 1 < len(starts) else total
        ranges.append({
            "page": page.get("page_idx", k) + 1,
            "line_start": starts[k],
            "line_end": max(end, starts[k]),
            "how": "aligned" if found[k] else "interpolated",
        })
    rate = matched / body if body else 0.0
    return ranges, rate, "high" if rate >= CONFIDENCE_THRESHOLD else "low"
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `uv run --with pytest python -m pytest tests/test_convert_source.py`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add llm-wiki/scripts/convert_source.py tests/test_convert_source.py
git commit -m "feat(convert_source): forward-search page map and dropped-block text"
```


### Task 8: MinerU converter: page windows, kill-and-halve retry

**Files:**
- Modify: `llm-wiki/scripts/convert_source.py`
- Modify: `tests/test_convert_source.py`

**Interfaces:**
- Consumes: `Output`, `align_pages`, `dropped_lines`, `DROPPED_TYPES`, `CPU_TIERS`, `WINDOW_PAGES` (Tasks 4-7)
- Produces: `WindowKilled`; `WindowResult(markdown, middle, images)`; `find_mineru_kit() -> str | None`; `pdf_page_count(pdf) -> int`; `plan_windows(total, size) -> list[tuple[int, int]]`; `read_kit_zip(path) -> WindowResult`; `run_kit_window(kit, pdf, first, last, tier, timeout) -> WindowResult`; `run_with_halving(runner, first, last) -> list`; `convert_mineru(pdf, tier, timeout, *, runner=None, total_pages=None, window=WINDOW_PAGES) -> Output`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_convert_source.py`:

```python
"""MinerU converter: page windows, kill-and-halve retry, zip reading, schema guard."""
import io
import json
import os
import stat
import sys
import zipfile

import pytest

import convert_source

FAKE_KIT = '''#!__PYTHON__
import json, os, signal, sys, time, zipfile
argv = sys.argv[1:]
out = argv[argv.index("-o") + 1]
span = argv[argv.index("-p") + 1]
first, _, last = span.partition("-")
first = int(first)
last = int(last or first)
with open(os.environ["FAKE_KIT_LOG"], "a") as log:
    log.write(json.dumps({"argv": argv, "cuda": os.environ.get("CUDA_VISIBLE_DEVICES")}) + "\\n")
if os.environ.get("FAKE_KIT_SLEEP"):
    time.sleep(float(os.environ["FAKE_KIT_SLEEP"]))
limit = os.environ.get("FAKE_KIT_KILL_OVER")
if limit and last - first + 1 > int(limit):
    os.kill(os.getpid(), signal.SIGKILL)
md, pages = [], []
for n in range(first, last + 1):
    text = f"Page {n} paragraph with enough words to align."
    md.append(text)
    pages.append({"page_idx": n - 1, "blocks": [
        {"type": "header", "content": "running header"},
        {"type": "text", "content": text},
    ]})
middle = {"schema": "docvortex.middle", "schema_version": os.environ.get("FAKE_KIT_SCHEMA", "2.0"),
          "metadata": {"producer": {"name": "mineru", "version": "9.9.9"}}, "pages": pages}
with zipfile.ZipFile(os.path.join(out, "doc.zip"), "w") as z:
    z.writestr("markdown.md", "\\n\\n".join(md) + "\\n")
    z.writestr("middle_json.json", json.dumps(middle))
    z.writestr("images/img_%d.jpg" % first, b"JPG")
'''


@pytest.fixture
def kit(tmp_path, monkeypatch):
    path = tmp_path / "fake-kit"
    path.write_text(FAKE_KIT.replace("__PYTHON__", sys.executable), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    log = tmp_path / "kit.log"
    monkeypatch.setenv("FAKE_KIT_LOG", str(log))
    monkeypatch.setenv("MINERU_KIT_BIN", str(path))
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    return path, log


def calls(log):
    return [json.loads(line) for line in log.read_text().splitlines()]


def test_plan_windows():
    assert convert_source.plan_windows(20, 8) == [(1, 8), (9, 16), (17, 20)]
    assert convert_source.plan_windows(3, 8) == [(1, 3)]


def test_find_mineru_kit_prefers_env(kit):
    assert convert_source.find_mineru_kit() == str(kit[0])


def test_find_mineru_kit_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("MINERU_KIT_BIN", raising=False)
    monkeypatch.setattr(convert_source.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(convert_source.shutil, "which", lambda name: None)
    assert convert_source.find_mineru_kit() is None


def test_read_kit_zip(tmp_path):
    buf = tmp_path / "x.zip"
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("markdown.md", "# T\n")
        z.writestr("middle_json.json", json.dumps({"pages": []}))
        z.writestr("images/a.jpg", b"A")
        z.writestr("model_output.json", "{}")
    res = convert_source.read_kit_zip(buf)
    assert res.markdown == "# T\n" and res.middle == {"pages": []} and res.images == {"images/a.jpg": b"A"}


def test_window_command_has_no_remote_flags_and_hides_gpu_on_cpu_tiers(kit, tmp_path):
    pdf = tmp_path / "d.pdf"
    pdf.write_bytes(b"%PDF")
    convert_source.run_kit_window(str(kit[0]), pdf, 1, 2, "basic", 60)
    convert_source.run_kit_window(str(kit[0]), pdf, 3, 3, "standard", 60)
    first, second = calls(kit[1])
    assert first["argv"][:2] == ["parse", str(pdf)]
    assert first["argv"][first["argv"].index("-f") + 1] == "zip"
    assert first["argv"][first["argv"].index("-p") + 1] == "1-2"
    assert second["argv"][second["argv"].index("-p") + 1] == "3"
    for call in (first, second):
        assert not any(a.startswith("--remote") or a == "--api-key" for a in call["argv"])
    assert first["cuda"] == ""          # basic tier: GPU hidden
    assert second["cuda"] is None       # standard tier: left alone


def test_killed_window_raises_windowkilled(kit, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_KIT_KILL_OVER", "1")
    pdf = tmp_path / "d.pdf"
    pdf.write_bytes(b"%PDF")
    with pytest.raises(convert_source.WindowKilled):
        convert_source.run_kit_window(str(kit[0]), pdf, 1, 2, "basic", 60)


def test_timeout_is_a_failure(kit, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_KIT_SLEEP", "5")
    pdf = tmp_path / "d.pdf"
    pdf.write_bytes(b"%PDF")
    with pytest.raises(convert_source.ConvertError) as exc:
        convert_source.run_kit_window(str(kit[0]), pdf, 1, 1, "basic", 1)
    assert exc.value.code == convert_source.EXIT_FAILED and "timed out" in str(exc.value)


class TestHalving:
    def test_halves_until_windows_fit(self):
        seen = []

        def runner(first, last):
            seen.append((first, last))
            if last - first + 1 > 2:
                raise convert_source.WindowKilled()
            return (first, last)

        assert convert_source.run_with_halving(runner, 1, 8) == [(1, 2), (3, 4), (5, 6), (7, 8)]
        assert seen[0] == (1, 8)

    def test_single_page_that_dies_fails_with_exit_3(self):
        def runner(first, last):
            raise convert_source.WindowKilled()

        with pytest.raises(convert_source.ConvertError) as exc:
            convert_source.run_with_halving(runner, 4, 4)
        assert exc.value.code == convert_source.EXIT_FAILED and "page 4" in str(exc.value)


class TestConvertMineru:
    def _pdf(self, tmp_path):
        pdf = tmp_path / "d.pdf"
        pdf.write_bytes(b"%PDF")
        return pdf

    def test_end_to_end_with_halving_and_merge(self, kit, tmp_path, monkeypatch):
        monkeypatch.setenv("FAKE_KIT_KILL_OVER", "2")
        out = convert_source.convert_mineru(self._pdf(tmp_path), "basic", 60, total_pages=5, window=4)
        assert [c["argv"][c["argv"].index("-p") + 1] for c in calls(kit[1])] == ["1-4", "1-2", "3-4", "5"]
        assert out.markdown.count("paragraph with enough words") == 5
        assert out.markdown.index("Page 1 ") < out.markdown.index("Page 5 ")
        assert out.converter == "mineru-kit" and out.version == "9.9.9" and out.lossy is False
        assert out.page_map == "aligned" and out.confidence == "high" and out.pages_total == 5
        assert [p["page"] for p in out.pages] == [1, 2, 3, 4, 5]
        assert set(out.images) == {"images/img_1.jpg", "images/img_3.jpg", "images/img_5.jpg"}
        assert out.dropped_lines[0] == "p1\theader\trunning header"

    def test_unknown_schema_skips_page_map_but_succeeds(self, kit, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("FAKE_KIT_SCHEMA", "3.0")
        out = convert_source.convert_mineru(self._pdf(tmp_path), "basic", 60, total_pages=2)
        assert out.pages is None and out.page_map == "none" and out.confidence == "low"
        assert "schema" in capsys.readouterr().err

    def test_missing_kit_exit_2(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MINERU_KIT_BIN", raising=False)
        monkeypatch.setattr(convert_source, "find_mineru_kit", lambda: None)
        with pytest.raises(convert_source.ConvertError) as exc:
            convert_source.convert_mineru(self._pdf(tmp_path), "basic", 60, total_pages=1)
        assert exc.value.code == convert_source.EXIT_USAGE
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run --with pytest python -m pytest tests/test_convert_source.py`

Expected: FAIL — `AttributeError: ... no attribute 'plan_windows'`

- [ ] **Step 3: Implement**

Append to `llm-wiki/scripts/convert_source.py`:

```python
MINERU_SCHEMA = ("docvortex.middle", "2.0")


class WindowKilled(Exception):
    """mineru-kit died from a signal (rc -9 / 137): most likely out of memory."""


@dataclass
class WindowResult:
    markdown: str
    middle: dict
    images: dict[str, bytes]


def find_mineru_kit() -> str | None:
    env = os.environ.get("MINERU_KIT_BIN")
    if env and Path(env).exists():
        return env
    home = Path.home() / ".local" / "bin" / "mineru-kit"
    if home.exists():
        return str(home)
    return shutil.which("mineru-kit")


def pdf_page_count(pdf) -> int:
    exe = shutil.which("pdfinfo")
    if not exe:
        raise ConvertError("pdfinfo not found (install poppler-utils): needed to split the PDF into page windows", EXIT_USAGE)
    p = subprocess.run([exe, str(pdf)], capture_output=True, text=True, timeout=60)
    m = re.search(r"^Pages:\s+(\d+)", p.stdout, re.M)
    if p.returncode != 0 or not m:
        raise ConvertError(f"pdfinfo could not read {pdf}: {p.stderr.strip()[:200]}", EXIT_FAILED)
    return int(m.group(1))


def plan_windows(total: int, size: int = WINDOW_PAGES) -> list[tuple[int, int]]:
    return [(a, min(a + size - 1, total)) for a in range(1, total + 1, size)]


def read_kit_zip(zip_path) -> WindowResult:
    with zipfile.ZipFile(zip_path) as z:
        markdown = z.read("markdown.md").decode("utf-8")
        middle = json.loads(z.read("middle_json.json"))
        images = {n: z.read(n) for n in z.namelist() if n.startswith("images/") and not n.endswith("/")}
    return WindowResult(markdown, middle, images)


def run_kit_window(kit: str, pdf, first: int, last: int, tier: str, timeout: int) -> WindowResult:
    """One ``mineru-kit parse -f zip`` run over pages first..last (1-based, inclusive).

    Never passes --remote / --remote-url / --api-key. CPU tiers run with the GPU hidden.
    """
    env = dict(os.environ)
    if tier in CPU_TIERS:
        env["CUDA_VISIBLE_DEVICES"] = ""
    span = str(first) if first == last else f"{first}-{last}"
    with tempfile.TemporaryDirectory(prefix="llmwiki-kit-") as tmp:
        cmd = [kit, "parse", str(pdf), "-o", tmp, "-f", "zip", "-p", span, "--tier", tier]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            raise ConvertError(f"mineru-kit timed out after {timeout}s on pages {span}", EXIT_FAILED)
        except OSError as e:
            raise ConvertError(f"cannot run mineru-kit: {e}", EXIT_USAGE)
        if p.returncode in (-9, 137):
            raise WindowKilled(f"pages {span}")
        if p.returncode != 0:
            raise ConvertError(f"mineru-kit failed on pages {span}: {p.stderr.strip()[-300:]}", EXIT_FAILED)
        zips = sorted(Path(tmp).glob("*.zip"))
        if not zips:
            raise ConvertError(f"mineru-kit wrote no zip for pages {span}", EXIT_FAILED)
        return read_kit_zip(zips[0])


def run_with_halving(runner, first: int, last: int) -> list:
    """Run a window; if it is killed, retry as two half windows, down to one page."""
    try:
        return [runner(first, last)]
    except WindowKilled:
        if first == last:
            raise ConvertError(f"mineru-kit was killed on page {first} (probably out of memory)", EXIT_FAILED)
        mid = (first + last) // 2
        return run_with_halving(runner, first, mid) + run_with_halving(runner, mid + 1, last)


def convert_mineru(pdf, tier: str = "basic", timeout: int = DEFAULT_TIMEOUT, *, runner=None,
                   total_pages: int | None = None, window: int = WINDOW_PAGES) -> Output:
    if runner is None:
        kit = find_mineru_kit()
        if not kit:
            raise ConvertError("mineru-kit not found (set MINERU_KIT_BIN or install MinerU), or configure another converter", EXIT_USAGE)

        def runner(first, last):
            return run_kit_window(kit, pdf, first, last, tier, timeout)

    total = total_pages or pdf_page_count(pdf)
    results: list[WindowResult] = []
    for first, last in plan_windows(total, window):
        results.extend(run_with_halving(runner, first, last))
    markdown = "\n\n".join(r.markdown.strip("\n") for r in results) + "\n"
    pages = [pg for r in results for pg in r.middle.get("pages", [])]
    images: dict[str, bytes] = {}
    for r in results:
        images.update(r.images)
    producer = results[0].middle.get("metadata", {}).get("producer", {})
    out = Output(
        markdown=markdown, converter="mineru-kit", version=str(producer.get("version", "unknown")),
        lossy=False, pages_total=total, images=images, dropped_types=list(DROPPED_TYPES),
    )
    schema_ok = all(
        (r.middle.get("schema"), r.middle.get("schema_version")) == MINERU_SCHEMA for r in results
    )
    if schema_ok:
        out.pages, out.match_rate, out.confidence = align_pages(markdown, pages)
        out.page_map = "aligned"
        out.dropped_lines = dropped_lines(pages)
    else:
        out.confidence = "low"
        print("warning: unexpected MinerU middle_json schema; page map skipped", file=sys.stderr)
    return out
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `uv run --with pytest python -m pytest tests/test_convert_source.py`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add llm-wiki/scripts/convert_source.py tests/test_convert_source.py
git commit -m "feat(convert_source): MinerU windows with kill-and-halve retry and schema guard"
```


### Task 9: convert(): resolution chain, artifacts, idempotence, CLI

**Files:**
- Modify: `llm-wiki/scripts/convert_source.py`
- Modify: `tests/test_convert_source.py`

**Interfaces:**
- Consumes: everything from Tasks 3-8, `vault_config.in_private_zone`
- Produces: `Result(slug, directory, md_path, converter, lossy, skipped, page_map, source_pdf_sha256)`; `candidate_converters(cfg) -> list[str]`; `convert(pdf, root, *, force=False, config=None, runner=None, total_pages=None, window=WINDOW_PAGES) -> Result`; `main(argv=None) -> int`; CLI `python3 convert_source.py <wiki-root> <pdf> [--force]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_convert_source.py`:

```python
"""convert(): resolution chain, idempotence, collisions, private zones, atomic output, CLI."""
import json
import shlex
import shutil
import sys

import pytest

from pdf_helpers import make_pdf

import convert_source

TOOL = '''
import os, pathlib, sys
pdf, outdir, slug = sys.argv[1:4]
with open(os.environ["FAKE_CMD_LOG"], "a") as log:
    log.write(pdf + "\\n")
if os.environ.get("FAKE_CMD_FAIL"):
    (pathlib.Path(outdir) / "partial.txt").write_text("x")
    sys.exit(1)
(pathlib.Path(outdir) / (slug + ".md")).write_text("Converted line one\\nConverted line two\\n", encoding="utf-8")
'''


@pytest.fixture
def vault(tmp_path, monkeypatch):
    root = tmp_path / "vault"
    (root / "raw" / "pdfs").mkdir(parents=True)
    tool = tmp_path / "tool.py"
    tool.write_text(TOOL, encoding="utf-8")
    log = tmp_path / "cmd.log"
    monkeypatch.setenv("FAKE_CMD_LOG", str(log))
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(tool))} {{input}} {{outdir}} {{slug}}"
    (root / "CLAUDE.md").write_text(
        "# Wiki\n\n## Document conversion\n\nconverter: command\n"
        f"command: {command}\n\n## Private zones\n\n- `sensitive_data/`\n",
        encoding="utf-8",
    )
    (root / "sensitive_data").mkdir()
    return root


def add_pdf(root, name="Paper One.pdf", payload=b"%PDF-1"):
    path = root / "raw" / "pdfs" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def runs(root):
    log = root.parent / "cmd.log"
    return len(log.read_text().splitlines()) if log.exists() else 0


def test_creates_script_owned_artifacts(vault):
    res = convert_source.convert(add_pdf(vault), vault)
    assert res.slug == "paper-one" and not res.skipped
    assert res.md_path == vault / "raw" / "converted" / "paper-one" / "paper-one.md"
    meta = json.loads((res.directory / "paper-one.convert.json").read_text())
    assert meta["schema"] == "llm-wiki.convert/1" and meta["converter"] == "command"
    assert meta["source_pdf"] == "raw/pdfs/Paper One.pdf"
    assert meta["source_pdf_sha256"] == convert_source.sha256_file(vault / "raw" / "pdfs" / "Paper One.pdf")
    assert meta["md_sha256"] == convert_source.sha256_file(res.md_path)
    assert meta["lossy"] is False and meta["page_map"] == "none"


def test_second_call_is_skipped_without_running_the_tool(vault):
    pdf = add_pdf(vault)
    convert_source.convert(pdf, vault)
    again = convert_source.convert(pdf, vault)
    assert again.skipped and runs(vault) == 1


def test_changed_pdf_needs_force(vault):
    pdf = add_pdf(vault)
    first = convert_source.convert(pdf, vault)
    before = first.md_path.read_bytes()
    pdf.write_bytes(b"%PDF-2 changed")
    with pytest.raises(convert_source.ConvertError) as exc:
        convert_source.convert(pdf, vault)
    assert exc.value.code == convert_source.EXIT_EXISTS and first.md_path.read_bytes() == before
    forced = convert_source.convert(pdf, vault, force=True)
    meta = json.loads((forced.directory / "paper-one.convert.json").read_text())
    assert not forced.skipped and meta["source_pdf_sha256"] == convert_source.sha256_file(pdf)
    assert runs(vault) == 2


def test_two_pdfs_with_the_same_name_do_not_collide(vault):
    a = add_pdf(vault, "a/paper.pdf", b"%PDF-A")
    b = add_pdf(vault, "b/paper.pdf", b"%PDF-B")
    ra = convert_source.convert(a, vault)
    rb = convert_source.convert(b, vault)
    assert ra.slug == "paper" and rb.slug.startswith("paper-") and rb.slug != ra.slug
    assert rb.md_path.exists() and ra.md_path.exists()


def test_private_zone_input_is_refused_and_nothing_is_written(vault):
    secret = vault / "sensitive_data" / "report.pdf"
    secret.write_bytes(b"%PDF")
    with pytest.raises(convert_source.ConvertError) as exc:
        convert_source.convert(secret, vault)
    assert exc.value.code == convert_source.EXIT_PRIVATE
    assert not (vault / "raw" / "converted").exists() and runs(vault) == 0


def test_failed_conversion_leaves_no_partial_output(vault, monkeypatch):
    monkeypatch.setenv("FAKE_CMD_FAIL", "1")
    with pytest.raises(convert_source.ConvertError) as exc:
        convert_source.convert(add_pdf(vault), vault)
    assert exc.value.code == convert_source.EXIT_FAILED
    base = vault / "raw" / "converted"
    assert not base.exists() or list(base.iterdir()) == []


def test_converter_none_is_refused(vault):
    cfg = convert_source.Config(converter="none")
    with pytest.raises(convert_source.ConvertError) as exc:
        convert_source.convert(add_pdf(vault), vault, config=cfg)
    assert exc.value.code == convert_source.EXIT_USAGE


class TestAutoChain:
    def test_command_wins_over_other_converters(self, vault, monkeypatch):
        monkeypatch.setattr(convert_source, "find_mineru_kit", lambda: "/x/mineru-kit")
        cfg = convert_source.load_config(vault)
        cfg.converter = "auto"
        assert convert_source.candidate_converters(cfg)[0] == "command"

    def test_order_without_command(self, monkeypatch):
        monkeypatch.setattr(convert_source, "find_mineru_kit", lambda: "/x/mineru-kit")
        monkeypatch.setattr(convert_source.shutil, "which", lambda name: "/usr/bin/pdftotext")
        assert convert_source.candidate_converters(convert_source.Config()) == ["mineru", "pdftotext"]

    def test_nothing_available_exit_2_with_hint(self, monkeypatch):
        monkeypatch.setattr(convert_source, "find_mineru_kit", lambda: None)
        monkeypatch.setattr(convert_source.shutil, "which", lambda name: None)
        with pytest.raises(convert_source.ConvertError) as exc:
            convert_source.candidate_converters(convert_source.Config())
        assert exc.value.code == convert_source.EXIT_USAGE and "mineru-kit" in str(exc.value)


def test_mineru_path_writes_pages_dropped_and_images(vault):
    def runner(first, last):
        pages = [{"page_idx": n - 1, "blocks": [
            {"type": "header", "content": "running header"},
            {"type": "text", "content": f"Page {n} paragraph with enough words to align."},
        ]} for n in range(first, last + 1)]
        md = "\n\n".join(f"Page {n} paragraph with enough words to align." for n in range(first, last + 1))
        middle = {"schema": "docvortex.middle", "schema_version": "2.0",
                  "metadata": {"producer": {"version": "4.0.12"}}, "pages": pages}
        return convert_source.WindowResult(md + "\n", middle, {f"images/p{first}.jpg": b"JPG"})

    cfg = convert_source.Config(converter="mineru")
    res = convert_source.convert(add_pdf(vault), vault, config=cfg, runner=runner, total_pages=3)
    d = res.directory
    pages = json.loads((d / "paper-one.pages.json").read_text())
    assert pages["schema"] == "llm-wiki.pages/1" and pages["confidence"] == "high"
    assert [p["page"] for p in pages["pages"]] == [1, 2, 3]
    assert "p1\theader\trunning header" in (d / "paper-one.dropped.txt").read_text()
    assert (d / "images" / "p1.jpg").read_bytes() == b"JPG"
    meta = json.loads((d / "paper-one.convert.json").read_text())
    assert meta["converter"] == "mineru-kit" and meta["version"] == "4.0.12" and meta["page_map"] == "aligned"
    assert meta["pages_total"] == 3 and meta["dropped"] == ["header", "footer", "page_number", "page_footnote"]


def test_image_names_cannot_escape_the_output_directory(vault):
    def runner(first, last):
        middle = {"schema": "docvortex.middle", "schema_version": "2.0", "pages": []}
        return convert_source.WindowResult("text\n", middle, {"images/../../evil.txt": b"x"})

    cfg = convert_source.Config(converter="mineru")
    res = convert_source.convert(add_pdf(vault), vault, config=cfg, runner=runner, total_pages=1)
    assert not (vault / "raw" / "evil.txt").exists() and not (res.directory.parent / "evil.txt").exists()


@pytest.mark.skipif(shutil.which("pdftotext") is None, reason="pdftotext not installed")
def test_pdftotext_end_to_end_marks_lossy(vault):
    pdf = add_pdf(vault, "scan.pdf", make_pdf(["Alpha line one\nAlpha line two", "Beta page text here"]))
    cfg = convert_source.Config(converter="pdftotext")
    res = convert_source.convert(pdf, vault, config=cfg)
    meta = json.loads((res.directory / "scan.convert.json").read_text())
    assert meta["lossy"] is True and meta["converter"] == "pdftotext" and meta["page_map"] == "exact"
    assert res.lossy is True


class TestCli:
    def test_ok_prints_path_and_returns_0(self, vault, capsys):
        pdf = add_pdf(vault)
        assert convert_source.main([str(vault), str(pdf)]) == 0
        assert "raw/converted/paper-one/paper-one.md" in capsys.readouterr().out

    def test_error_code_is_returned(self, vault, capsys):
        secret = vault / "sensitive_data" / "r.pdf"
        secret.write_bytes(b"%PDF")
        assert convert_source.main([str(vault), str(secret)]) == convert_source.EXIT_PRIVATE
        assert "private zone" in capsys.readouterr().err

    def test_missing_pdf_is_usage_error(self, vault):
        assert convert_source.main([str(vault), str(vault / "nope.pdf")]) == convert_source.EXIT_USAGE
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run --with pytest python -m pytest tests/test_convert_source.py`

Expected: FAIL — `AttributeError: ... no attribute 'convert'`

- [ ] **Step 3: Implement**

Append to `llm-wiki/scripts/convert_source.py`:

```python
@dataclass
class Result:
    slug: str
    directory: Path
    md_path: Path
    converter: str
    lossy: bool
    skipped: bool = False
    page_map: str = "none"
    source_pdf_sha256: str = ""


def candidate_converters(cfg: Config) -> list[str]:
    """Converters to use, best first. Explicit settings name exactly one."""
    if cfg.converter == "none":
        raise ConvertError("conversion is disabled (converter: none)", EXIT_USAGE)
    if cfg.converter != "auto":
        return [cfg.converter]
    names = []
    if cfg.command:
        names.append("command")
    if find_mineru_kit():
        names.append("mineru")
    if shutil.which("pdftotext"):
        names.append("pdftotext")
    if not names:
        raise ConvertError(
            "no converter available: install MinerU (mineru-kit), poppler-utils (pdftotext), "
            "or set `command:` under ## Document conversion in CLAUDE.md", EXIT_USAGE)
    return names


def run_converter(name: str, pdf, slug: str, cfg: Config, runner, total_pages, window) -> Output:
    if name == "command":
        return convert_command(cfg.command, pdf, slug, cfg.timeout)
    if name == "mineru":
        return convert_mineru(pdf, cfg.tier, cfg.timeout, runner=runner, total_pages=total_pages, window=window)
    if name == "pdftotext":
        out = convert_pdftotext(pdf, cfg.timeout)
        print("warning: pdftotext fallback is lossy (no tables, columns or OCR)", file=sys.stderr)
        return out
    raise ConvertError(f"unknown converter {name!r}", EXIT_USAGE)


def read_convert_json(directory: Path) -> dict | None:
    for f in directory.glob("*.convert.json"):
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
    return None


def write_artifacts(directory: Path, slug: str, out: Output, source_rel: str, source_sha: str,
                    tier: str | None = None) -> None:
    directory.mkdir(parents=True)
    md_path = directory / f"{slug}.md"
    md_path.write_text(out.markdown, encoding="utf-8")
    if out.pages is not None or out.confidence is not None:
        pages = {"schema": "llm-wiki.pages/1", "match_rate": round(out.match_rate or 0.0, 4),
                 "confidence": out.confidence or "low", "pages": out.pages or []}
        (directory / f"{slug}.pages.json").write_text(json.dumps(pages, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if out.dropped_lines:
        (directory / f"{slug}.dropped.txt").write_text("\n".join(out.dropped_lines) + "\n", encoding="utf-8")
    base = directory.resolve()
    for rel, data in out.images.items():
        target = (directory / rel).resolve()
        if base not in target.parents:
            continue                      # never write outside the output directory
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    meta = {
        "schema": "llm-wiki.convert/1", "converter": out.converter, "version": out.version,
        "tier": tier, "lossy": out.lossy, "source_pdf": source_rel, "source_pdf_sha256": source_sha,
        "md_sha256": sha256_file(md_path), "converted_at": now_iso(),
        "pages_total": out.pages_total, "page_map": out.page_map, "dropped": out.dropped_types,
    }
    (directory / f"{slug}.convert.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def convert(pdf, root, *, force: bool = False, config: Config | None = None, runner=None,
            total_pages: int | None = None, window: int = WINDOW_PAGES) -> Result:
    root = Path(root).resolve()
    pdf = Path(pdf).resolve()
    if not pdf.is_file():
        raise ConvertError(f"PDF not found: {pdf}", EXIT_USAGE)
    if vault_config.in_private_zone(root, pdf):
        raise ConvertError(f"refusing to convert a file inside a private zone: {pdf}", EXIT_PRIVATE)
    cfg = config or load_config(root)
    sha = sha256_file(pdf)
    try:
        source_rel = pdf.relative_to(root).as_posix()
    except ValueError:
        source_rel = str(pdf)
    base = root / "raw" / "converted"
    slug = sanitize_slug(pdf.name)
    directory = base / slug
    existing = read_convert_json(directory)
    if existing and existing.get("source_pdf") != source_rel and existing.get("source_pdf_sha256") != sha:
        slug = f"{slug}-{sha[:8]}"                      # another PDF already owns this slug
        directory = base / slug
        existing = read_convert_json(directory)

    def result(skipped: bool, meta: dict) -> Result:
        return Result(slug, directory, directory / f"{slug}.md", meta.get("converter", ""),
                      bool(meta.get("lossy")), skipped, meta.get("page_map", "none"), sha)

    if existing and existing.get("source_pdf_sha256") == sha and not force:
        return result(True, existing)
    if directory.exists() and not force:
        raise ConvertError(
            f"{directory} already holds a conversion of an earlier version of this PDF; "
            "re-run with --force (line numbers will change and existing citations will be flagged)", EXIT_EXISTS)

    name = candidate_converters(cfg)[0]
    out = run_converter(name, pdf, slug, cfg, runner, total_pages, window)
    base.mkdir(parents=True, exist_ok=True)
    staging = base / f".tmp-{slug}-{os.getpid()}"
    shutil.rmtree(staging, ignore_errors=True)
    try:
        write_artifacts(staging, slug, out, source_rel, sha, cfg.tier if name == "mineru" else None)
        if directory.exists():
            shutil.rmtree(directory)
        os.replace(staging, directory)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    meta = json.loads((directory / f"{slug}.convert.json").read_text(encoding="utf-8"))
    return result(False, meta)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convert a PDF into line-citable markdown under raw/converted/.")
    parser.add_argument("wiki_root", help="Path to the wiki root directory")
    parser.add_argument("pdf", help="PDF to convert")
    parser.add_argument("--force", action="store_true", help="Reconvert even if raw/converted/<slug>/ exists (line numbers change)")
    args = parser.parse_args(argv)
    try:
        res = convert(args.pdf, args.wiki_root, force=args.force)
    except ConvertError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return e.code
    root = Path(args.wiki_root).resolve()
    state = "already converted" if res.skipped else f"converted with {res.converter}"
    print(f"{state}: {res.md_path.relative_to(root).as_posix()}" + (" (lossy)" if res.lossy else ""))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `uv run --with pytest python -m pytest tests/test_convert_source.py`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add llm-wiki/scripts/convert_source.py tests/test_convert_source.py
git commit -m "feat(convert_source): convert() orchestration, artifacts and CLI"
```


### Task 10: fast_ingest converts PDFs first

**Files:**
- Modify: `llm-wiki/scripts/fast_ingest.py`
- Test: `tests/test_fast_ingest_pdf.py`

**Interfaces:**
- Consumes: `convert_source.load_config(root)`, `convert_source.convert(pdf, root, config=cfg) -> Result`, `convert_source.ConvertError` (Task 9)
- Produces: `fast_ingest._convert_pdf_source(root, pdf_abs) -> (markdown_path, extra_frontmatter)`; stubs from PDFs carry `source_pdf`, `source_pdf_sha256` and (when lossy) `source_lossy: true`; `raw_file` / `raw_sha256` point at the converted markdown; exits with the converter's exit code on failure

- [ ] **Step 1: Write the failing tests**

Create `tests/test_fast_ingest_pdf.py`:

```python
"""fast_ingest converts a PDF first and ingests the converted markdown."""
import re
import shlex
import shutil
import sys

import pytest

from pdf_helpers import make_pdf

import fast_ingest

TOOL = '''
import pathlib, sys
pdf, outdir, slug = sys.argv[1:4]
(pathlib.Path(outdir) / (slug + ".md")).write_text(
    "# Paper One\\n\\nConverted line one\\nConverted line two\\n", encoding="utf-8")
'''


def set_conversion(root, tmp_path, converter="command"):
    """Replace the vault's Document conversion section with one that uses a fake tool."""
    tool = tmp_path / "tool.py"
    tool.write_text(TOOL, encoding="utf-8")
    claude = root / "CLAUDE.md"
    text = re.sub(r"\n## Document conversion\n.*?(?=\n## |\Z)", "", claude.read_text(encoding="utf-8"), flags=re.S)
    text += (
        "\n## Document conversion\n\n"
        f"converter: {converter}\n"
        f"command: {shlex.quote(sys.executable)} {shlex.quote(str(tool))} {{input}} {{outdir}} {{slug}}\n"
    )
    claude.write_text(text, encoding="utf-8")


def stub_text(root):
    stubs = list((root / "wiki" / "sources").glob("*.md"))
    assert len(stubs) == 1
    return stubs[0].read_text(encoding="utf-8")


def test_pdf_is_converted_and_stub_points_at_the_markdown(scaffolded_vault, tmp_path):
    root = scaffolded_vault
    set_conversion(root, tmp_path)
    pdf = root / "raw" / "pdfs" / "paper-one.pdf"
    pdf.write_bytes(b"%PDF-1 fake")
    fast_ingest.fast_ingest(str(root), "raw/pdfs/paper-one.pdf")
    text = stub_text(root)
    md = root / "raw" / "converted" / "paper-one" / "paper-one.md"
    assert md.exists()
    assert 'raw_file: "raw/converted/paper-one/paper-one.md"' in text
    assert f'raw_sha256: "{fast_ingest.compute_sha256(str(md))}"' in text
    assert 'source_pdf: "raw/pdfs/paper-one.pdf"' in text
    assert f'source_pdf_sha256: "{fast_ingest.compute_sha256(str(pdf))}"' in text
    assert "source_lossy" not in text


@pytest.mark.skipif(shutil.which("pdftotext") is None, reason="pdftotext not installed")
def test_lossy_conversion_is_recorded_in_the_stub(scaffolded_vault, tmp_path):
    root = scaffolded_vault
    set_conversion(root, tmp_path, converter="pdftotext")
    (root / "raw" / "pdfs" / "scan.pdf").write_bytes(make_pdf(["Alpha line one\nAlpha line two", "Beta page text here"]))
    fast_ingest.fast_ingest(str(root), "raw/pdfs/scan.pdf")
    assert "source_lossy: true" in stub_text(root)


def test_converter_none_ingests_the_pdf_as_before(scaffolded_vault, tmp_path):
    root = scaffolded_vault
    set_conversion(root, tmp_path, converter="none")
    (root / "raw" / "pdfs" / "paper-one.pdf").write_bytes(b"%PDF-1 fake")
    fast_ingest.fast_ingest(str(root), "raw/pdfs/paper-one.pdf")
    text = stub_text(root)
    assert 'raw_file: "raw/pdfs/paper-one.pdf"' in text and "source_pdf" not in text
    assert not (root / "raw" / "converted").exists()


def test_private_zone_pdf_is_refused(scaffolded_vault, tmp_path):
    root = scaffolded_vault
    set_conversion(root, tmp_path)
    with open(root / "CLAUDE.md", "a", encoding="utf-8") as f:
        f.write("\n## Private zones\n\n- `sensitive_data/`\n")
    (root / "sensitive_data").mkdir()
    (root / "sensitive_data" / "r.pdf").write_bytes(b"%PDF-1")
    with pytest.raises(SystemExit) as exc:
        fast_ingest.fast_ingest(str(root), "sensitive_data/r.pdf")
    assert exc.value.code == 5
    assert not list((root / "wiki" / "sources").glob("*.md"))
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run --with pytest python -m pytest tests/test_fast_ingest_pdf.py`

Expected: FAIL — the stub still points at the PDF (or the run crashes reading binary as text)

- [ ] **Step 3: Add the conversion helper**

In `llm-wiki/scripts/fast_ingest.py`, replace:

```python
def fast_ingest(root: str, raw_file_path: str) -> None:
```

with:

```python
def _convert_pdf_source(root: str, pdf_abs: str) -> tuple[str, str]:
    """Convert a PDF with convert_source before ingesting it.

    Returns ``(path of the markdown to ingest, extra frontmatter lines)``. With
    ``converter: none`` the PDF is ingested unchanged, as before.
    """
    import convert_source

    try:
        cfg = convert_source.load_config(root)
        if cfg.converter == "none":
            print("ℹ  converter: none — ingesting the PDF without conversion (no line citations)")
            return pdf_abs, ""
        res = convert_source.convert(pdf_abs, root, config=cfg)
    except convert_source.ConvertError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(e.code)
    state = "reused" if res.skipped else f"converted with {res.converter}"
    print(f"✓ PDF {state}: {os.path.relpath(res.md_path, root)}" + (" (lossy)" if res.lossy else ""))
    frontmatter = (
        f'source_pdf: "{os.path.relpath(pdf_abs, root)}"\n'
        f'source_pdf_sha256: "{res.source_pdf_sha256}"\n'
    )
    if res.lossy:
        frontmatter += "source_lossy: true\n"
    return str(res.md_path), frontmatter


def fast_ingest(root: str, raw_file_path: str) -> None:
```

- [ ] **Step 4: Convert before anything else reads the file**

In `llm-wiki/scripts/fast_ingest.py`, replace:

```python
    if not os.path.exists(raw_abs):
        print(f"ERROR: File not found: {raw_abs}", file=sys.stderr)
        sys.exit(1)

    raw_rel = os.path.relpath(raw_abs, root)
```

with:

```python
    if not os.path.exists(raw_abs):
        print(f"ERROR: File not found: {raw_abs}", file=sys.stderr)
        sys.exit(1)

    # ── Step 0: a PDF is converted to line-citable markdown first ─────────────
    pdf_fm = ""
    if raw_abs.lower().endswith(".pdf"):
        raw_abs, pdf_fm = _convert_pdf_source(root, raw_abs)

    raw_rel = os.path.relpath(raw_abs, root)
```

- [ ] **Step 5: Record the PDF in both stub templates**

In `llm-wiki/scripts/fast_ingest.py`, replace (two occurrences: personal-writing and source-summary):

```python
raw_sha256: "{sha256}"
last_verified: {today}
```

with:

```python
raw_sha256: "{sha256}"
{pdf_fm}last_verified: {today}
```

- [ ] **Step 6: Run the tests and verify they pass**

Run: `uv run --with pytest python -m pytest tests/test_fast_ingest_pdf.py`

Expected: PASS

- [ ] **Step 7: Run the full suite**

Run: `uv run --with pytest python -m pytest `

Expected: PASS — all earlier tests still pass

- [ ] **Step 8: Commit**

```bash
git add llm-wiki/scripts/fast_ingest.py tests/test_fast_ingest_pdf.py
git commit -m "feat(fast_ingest): convert PDFs to markdown before ingest"
```


### Task 11: batch_ingest: PDFs, double-hash dedupe, no rescans of converted files

**Files:**
- Modify: `llm-wiki/scripts/batch_ingest.py`
- Test: `tests/test_batch_ingest.py`

**Interfaces:**
- Consumes: `fast_ingest.py` accepting a `.pdf` (Task 10)
- Produces: `batch_ingest(root, scan_dir, extensions, pdf_mode=False, dry_run=False, force=False, no_convert=False) -> int`; `collect_known_hashes(root) -> set[str]` includes `source_pdf_sha256`; CLI flag `--no-convert`; `--pdf` kept as a deprecated no-op; `convert_pdf()` removed

- [ ] **Step 1: Write the failing tests**

Create `tests/test_batch_ingest.py`:

```python
"""batch_ingest: PDFs go through fast_ingest (which converts), dedupe by either hash."""
import re
import shlex
import sys

import batch_ingest

TOOL = '''
import pathlib, sys
pdf, outdir, slug = sys.argv[1:4]
(pathlib.Path(outdir) / (slug + ".md")).write_text("# " + slug + "\\n\\nConverted text for " + slug + "\\n", encoding="utf-8")
'''


def set_conversion(root, tmp_path):
    tool = tmp_path / "tool.py"
    tool.write_text(TOOL, encoding="utf-8")
    claude = root / "CLAUDE.md"
    text = re.sub(r"\n## Document conversion\n.*?(?=\n## |\Z)", "", claude.read_text(encoding="utf-8"), flags=re.S)
    text += (
        "\n## Document conversion\n\nconverter: command\n"
        f"command: {shlex.quote(sys.executable)} {shlex.quote(str(tool))} {{input}} {{outdir}} {{slug}}\n"
    )
    claude.write_text(text, encoding="utf-8")


def sources(root):
    return sorted(p.name for p in (root / "wiki" / "sources").glob("*.md"))


def test_pdfs_are_converted_and_ingested_once(scaffolded_vault, tmp_path, capsys):
    root = scaffolded_vault
    set_conversion(root, tmp_path)
    for name in ("alpha.pdf", "beta.pdf"):
        (root / "raw" / "pdfs" / name).write_bytes(b"%PDF-1 " + name.encode())
    assert batch_ingest.batch_ingest(str(root), "raw/pdfs", [".pdf"]) == 0
    assert len(sources(root)) == 2
    assert (root / "raw" / "converted" / "alpha" / "alpha.md").exists()
    capsys.readouterr()
    assert batch_ingest.batch_ingest(str(root), "raw/pdfs", [".pdf"]) == 0
    assert "already ingested" in capsys.readouterr().out
    assert len(sources(root)) == 2


def test_converted_files_are_not_scanned_as_separate_sources(scaffolded_vault, tmp_path):
    root = scaffolded_vault
    set_conversion(root, tmp_path)
    (root / "raw" / "pdfs" / "alpha.pdf").write_bytes(b"%PDF-1 a")
    assert batch_ingest.batch_ingest(str(root), "raw", [".pdf"]) == 0
    assert batch_ingest.batch_ingest(str(root), "raw", [".md"]) == 0       # would pick up raw/converted/*.md
    assert len(sources(root)) == 1


def test_no_convert_skips_pdfs(scaffolded_vault, tmp_path):
    root = scaffolded_vault
    set_conversion(root, tmp_path)
    (root / "raw" / "pdfs" / "alpha.pdf").write_bytes(b"%PDF-1 a")
    assert batch_ingest.batch_ingest(str(root), "raw/pdfs", [".pdf"], no_convert=True) == 0
    assert sources(root) == [] and not (root / "raw" / "converted").exists()


def test_dry_run_creates_nothing(scaffolded_vault, tmp_path, capsys):
    root = scaffolded_vault
    set_conversion(root, tmp_path)
    (root / "raw" / "pdfs" / "alpha.pdf").write_bytes(b"%PDF-1 a")
    assert batch_ingest.batch_ingest(str(root), "raw/pdfs", [".pdf"], dry_run=True) == 0
    assert "would convert" in capsys.readouterr().out
    assert sources(root) == [] and not (root / "raw" / "converted").exists()


def test_known_hashes_include_the_source_pdf_hash(scaffolded_vault):
    stub = scaffolded_vault / "wiki" / "sources" / "x.md"
    stub.write_text('---\nraw_sha256: "aaa"\nsource_pdf_sha256: "bbb"\n---\n', encoding="utf-8")
    assert batch_ingest.collect_known_hashes(scaffolded_vault) == {"aaa", "bbb"}
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run --with pytest python -m pytest tests/test_batch_ingest.py`

Expected: FAIL — PDFs are not converted, `no_convert` is an unexpected keyword, hashes are not merged

- [ ] **Step 3: Index the source PDF hash too**

In `llm-wiki/scripts/batch_ingest.py`, replace:

```python
                if line.strip().startswith("raw_sha256:"):
                    sha = line.split(":", 1)[1].strip().strip("\"'")
                    if sha:
                        known.add(sha)
```

with:

```python
                key = line.strip().split(":", 1)[0]
                if key in ("raw_sha256", "source_pdf_sha256"):
                    sha = line.split(":", 1)[1].strip().strip("\"'")
                    if sha:
                        known.add(sha)
```

- [ ] **Step 4: Remove the old pdf-ingest hook (and its fragile stem matching)**

In `llm-wiki/scripts/batch_ingest.py`, replace:

```python
def convert_pdf(pdf_path: Path, wiki_root: Path, out_dir: Path) -> Path | None:
    """Convert a PDF to markdown using pdf_to_md.py. Returns the output path."""
    converter = wiki_root / ".claude" / "skills" / "pdf-ingest" / "scripts" / "pdf_to_md.py"
    if not converter.exists():
        print(f"  ⚠️  pdf_to_md.py not found at {converter}. Enable pdf-ingest skill.")
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [sys.executable, str(converter), str(pdf_path), "--out", str(out_dir)],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"  ✗ PDF conversion failed: {pdf_path.name}")
        print(f"    {result.stderr.strip()}")
        return None
    # Find the output file
    stem = pdf_path.stem.lower()
    for f in out_dir.glob("*.md"):
        if stem in f.stem.lower():
            return f
    return None
```

with:

```python

```

- [ ] **Step 5: Add the no_convert parameter**

In `llm-wiki/scripts/batch_ingest.py`, replace:

```python
def batch_ingest(root: str, scan_dir: str = "raw", extensions: list[str] | None = None,
                 pdf_mode: bool = False, dry_run: bool = False,
                 force: bool = False) -> int:
```

with:

```python
def batch_ingest(root: str, scan_dir: str = "raw", extensions: list[str] | None = None,
                 pdf_mode: bool = False, dry_run: bool = False,
                 force: bool = False, no_convert: bool = False) -> int:
    # pdf_mode is the deprecated --pdf flag: PDFs are now always converted (by fast_ingest.py,
    # via convert_source.py) unless no_convert is set.
```

- [ ] **Step 6: Never scan raw/converted/ as separate sources**

In `llm-wiki/scripts/batch_ingest.py`, replace:

```python
    candidates: list[Path] = []
    for ext in extensions:
        candidates.extend(sorted(scan_path.rglob(f"*{ext}")))
```

with:

```python
    candidates: list[Path] = []
    converted_root = root_path / "raw" / "converted"
    for ext in extensions:
        # raw/converted/ holds script-written copies of PDFs; they are ingested via their PDF
        candidates.extend(
            f for f in sorted(scan_path.rglob(f"*{ext}"))
            if converted_root not in f.parents
        )
```

- [ ] **Step 7: Let fast_ingest convert PDFs; handle --no-convert and dry runs**

In `llm-wiki/scripts/batch_ingest.py`, replace:

```python
        # PDF conversion mode
        ingest_target = raw_file
        if pdf_mode and raw_file.suffix.lower() == ".pdf":
            if dry_run:
                print(f"  [DRY] would convert PDF: {raw_file.relative_to(root_path)}")
                new_count += 1
                continue
            out_dir = root_path / "raw" / "articles"
            converted = convert_pdf(raw_file, root_path, out_dir)
            if converted is None:
                error_count += 1
                continue
            ingest_target = converted
            print(f"  ✓ converted PDF → {converted.relative_to(root_path)}")
```

with:

```python
        # PDFs are converted to line-citable markdown inside fast_ingest.py (convert_source.py)
        ingest_target = raw_file
        if raw_file.suffix.lower() == ".pdf":
            if no_convert:
                skip_count += 1
                print(f"  ↷ PDF skipped (--no-convert): {raw_file.relative_to(root_path)}")
                continue
            if dry_run:
                print(f"  [DRY] would convert PDF and ingest: {raw_file.relative_to(root_path)}")
                new_count += 1
                continue
```

- [ ] **Step 8: CLI: deprecate --pdf, add --no-convert**

In `llm-wiki/scripts/batch_ingest.py`, replace:

```python
    parser.add_argument("--pdf", action="store_true",
                        help="Auto-convert PDF files before ingest")
```

with:

```python
    parser.add_argument("--pdf", action="store_true",
                        help="Deprecated no-op: PDFs are always converted unless --no-convert")
    parser.add_argument("--no-convert", action="store_true",
                        help="Skip PDF files instead of converting them")
```

- [ ] **Step 9: CLI: pass no_convert through**

In `llm-wiki/scripts/batch_ingest.py`, replace:

```python
        pdf_mode=args.pdf,
        dry_run=args.dry_run,
        force=args.force,
    ))
```

with:

```python
        pdf_mode=args.pdf,
        dry_run=args.dry_run,
        force=args.force,
        no_convert=args.no_convert,
    ))
```

- [ ] **Step 10: Run the tests and verify they pass**

Run: `uv run --with pytest python -m pytest tests/test_batch_ingest.py`

Expected: PASS

- [ ] **Step 11: Run the full suite**

Run: `uv run --with pytest python -m pytest `

Expected: PASS — all earlier tests still pass

- [ ] **Step 12: Commit**

```bash
git add llm-wiki/scripts/batch_ingest.py tests/test_batch_ingest.py
git commit -m "feat(batch_ingest): convert PDFs via fast_ingest, dedupe by PDF hash, skip raw/converted"
```


### Task 12: Lint: stale converted copies, lossy and low-confidence notes

**Files:**
- Modify: `llm-wiki/scripts/lint_wiki.py`
- Test: `tests/test_lint_conversion.py`

**Interfaces:**
- Consumes: stub frontmatter fields from Task 10; `parse_frontmatter`, `sha256_file`, `log_issue` already in lint_wiki.py
- Produces: `lint_wiki.check_conversion_state(wiki_dir: Path, root: Path) -> None` called from `lint()` right after `check_sha256`; WARNING (check `sha256`) for a replaced or missing source PDF; INFO (check `conversion`) for lossy conversions and `confidence: low` page maps

- [ ] **Step 1: Write the failing tests**

Create `tests/test_lint_conversion.py`:

```python
"""Lint: stale converted copies and lossy / low-confidence conversions."""
import json

import lint_wiki


def _vault(tmp_path, *, stub_extra="", pdf_bytes=b"%PDF-1", stored_sha=None, pages=None):
    import hashlib
    root = tmp_path
    (root / "wiki" / "sources").mkdir(parents=True)
    (root / "raw" / "pdfs").mkdir(parents=True)
    (root / "raw" / "converted" / "paper").mkdir(parents=True)
    pdf = root / "raw" / "pdfs" / "paper.pdf"
    pdf.write_bytes(pdf_bytes)
    md = root / "raw" / "converted" / "paper" / "paper.md"
    md.write_text("text\n", encoding="utf-8")
    if pages is not None:
        (root / "raw" / "converted" / "paper" / "paper.pages.json").write_text(json.dumps(pages), encoding="utf-8")
    sha = stored_sha or hashlib.sha256(b"%PDF-1").hexdigest()
    (root / "wiki" / "sources" / "paper.md").write_text(
        "---\ntype: source-summary\n"
        'raw_file: "raw/converted/paper/paper.md"\n'
        'source_pdf: "raw/pdfs/paper.pdf"\n'
        f'source_pdf_sha256: "{sha}"\n'
        f"{stub_extra}---\n\n# Paper\n",
        encoding="utf-8",
    )
    return root


def _run(root):
    lint_wiki.errors.clear()
    lint_wiki.warnings.clear()
    lint_wiki.suggestions.clear()
    lint_wiki.check_conversion_state(root / "wiki", root)
    return list(lint_wiki.warnings), list(lint_wiki.suggestions)


def test_healthy_conversion_is_silent(tmp_path):
    root = _vault(tmp_path, pages={"confidence": "high", "pages": []})
    assert _run(root) == ([], [])


def test_replaced_pdf_is_reported_as_stale(tmp_path):
    root = _vault(tmp_path, pdf_bytes=b"%PDF-2 new version")
    warnings, _ = _run(root)
    assert len(warnings) == 1 and "stale" in warnings[0] and "convert_source.py" in warnings[0]


def test_missing_pdf_is_a_warning(tmp_path):
    root = _vault(tmp_path)
    (root / "raw" / "pdfs" / "paper.pdf").unlink()
    warnings, _ = _run(root)
    assert len(warnings) == 1 and "not found" in warnings[0]


def test_lossy_conversion_is_noted(tmp_path):
    root = _vault(tmp_path, stub_extra="source_lossy: true\n")
    _, notes = _run(root)
    assert len(notes) == 1 and "lossy" in notes[0]


def test_low_confidence_page_map_is_noted(tmp_path):
    root = _vault(tmp_path, pages={"confidence": "low", "pages": []})
    _, notes = _run(root)
    assert len(notes) == 1 and "page translation" in notes[0]


def test_stub_without_source_pdf_is_ignored(tmp_path):
    root = _vault(tmp_path)
    (root / "wiki" / "sources" / "other.md").write_text('---\nraw_file: "raw/a.md"\n---\n', encoding="utf-8")
    assert _run(root) == ([], [])
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run --with pytest python -m pytest tests/test_lint_conversion.py`

Expected: FAIL — `AttributeError: module 'lint_wiki' has no attribute 'check_conversion_state'`

- [ ] **Step 3: Import json**

In `llm-wiki/scripts/lint_wiki.py`, replace:

```python
import hashlib
import datetime
```

with:

```python
import hashlib
import json
import datetime
```

- [ ] **Step 4: Add the check before the stale-pages section**

In `llm-wiki/scripts/lint_wiki.py`, replace:

```python
# ── Check 6: Stale Pages ─────────────────────────────────────────────────────
```

with:

```python
# ── Check 5b: Converted PDFs ──────────────────────────────────────────────────

def check_conversion_state(wiki_dir: Path, root: Path) -> None:
    """Source stubs that were ingested from a PDF (``source_pdf`` in frontmatter):
    warn when the PDF changed or vanished since conversion, and note lossy or
    low-confidence conversions."""
    src_dir = wiki_dir / "sources"
    if not src_dir.exists():
        return
    for f in sorted(src_dir.glob("*.md")):
        fm, _ = parse_frontmatter(f)
        pdf_rel = fm.get("source_pdf", "")
        if not pdf_rel:
            continue
        rel = f.relative_to(root)
        pdf_path = root / pdf_rel
        if not pdf_path.exists():
            log_issue("WARNING", "sha256", f"{rel} — source_pdf '{pdf_rel}' not found")
        elif fm.get("source_pdf_sha256") and sha256_file(pdf_path) != fm["source_pdf_sha256"]:
            log_issue("WARNING", "sha256",
                      f"{rel} — converted copy is stale: {pdf_rel} changed since conversion; "
                      f"re-run: python3 scripts/convert_source.py {root} {pdf_rel} --force")
        if fm.get("source_lossy", "").lower() == "true":
            log_issue("INFO", "conversion", f"{rel} — cited raw is a lossy conversion (pdftotext)")
        raw_file = fm.get("raw_file", "")
        pages_json = (root / raw_file).with_suffix(".pages.json") if raw_file else None
        if pages_json and pages_json.exists():
            try:
                confidence = json.loads(pages_json.read_text(encoding="utf-8")).get("confidence")
            except (OSError, ValueError):
                confidence = None
            if confidence == "low":
                log_issue("INFO", "conversion", f"{rel} — page translation unreliable for {raw_file}")


# ── Check 6: Stale Pages ─────────────────────────────────────────────────────
```

- [ ] **Step 5: Run it from lint()**

In `llm-wiki/scripts/lint_wiki.py`, replace:

```python
    modified_sources = check_sha256(wiki_path, root_path)
```

with:

```python
    modified_sources = check_sha256(wiki_path, root_path)
    check_conversion_state(wiki_path, root_path)
```

- [ ] **Step 6: Run the tests and verify they pass**

Run: `uv run --with pytest python -m pytest tests/test_lint_conversion.py`

Expected: PASS

- [ ] **Step 7: Run the full suite**

Run: `uv run --with pytest python -m pytest `

Expected: PASS — all earlier tests still pass

- [ ] **Step 8: Commit**

```bash
git add llm-wiki/scripts/lint_wiki.py tests/test_lint_conversion.py
git commit -m "feat(lint): flag stale PDF conversions and lossy or low-confidence page maps"
```


### Task 13: Scaffold the config section, slow-test marker and documentation

**Files:**
- Modify: `llm-wiki/scripts/scaffold.py`
- Modify: `pytest.ini`
- Create: `tests/test_scaffold_conversion.py`
- Create: `tests/test_convert_real.py`
- Modify: `llm-wiki/SKILL.md`
- Modify: `llm-wiki/references/ops/op-ingest.md`
- Modify: `llm-wiki/references/tooling-tips.md`
- Modify: `tests/README.md`

**Interfaces:**
- Consumes: `convert_source.load_config` (Task 4)
- Produces: every new vault's `CLAUDE.md` has `## Document conversion` with the defaults; the `slow` pytest marker (excluded by default); docs describing PDF conversion, the `raw/converted/` ownership rule and the new flags

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scaffold_conversion.py`:

```python
"""Scaffolded vaults ship the conversion config and the new scripts."""
import convert_source


def test_scaffolded_claude_md_has_a_default_conversion_section(scaffolded_vault):
    text = (scaffolded_vault / "CLAUDE.md").read_text(encoding="utf-8")
    assert "## Document conversion" in text
    cfg = convert_source.load_config(scaffolded_vault)
    assert (cfg.converter, cfg.tier, cfg.timeout, cfg.command) == ("auto", "basic", 900, "")


def test_conversion_section_documents_privacy_and_disabling(scaffolded_vault):
    text = (scaffolded_vault / "CLAUDE.md").read_text(encoding="utf-8")
    section = text.split("## Document conversion", 1)[1].split("\n## ", 1)[0]
    assert "converter: none" in section and "never uploaded" in section.lower()


def test_bundled_skill_contains_the_new_modules(scaffolded_vault):
    scripts = scaffolded_vault / ".claude" / "skills" / "llm-wiki" / "scripts"
    for name in ("convert_source.py", "textnorm.py", "vault_config.py"):
        assert (scripts / name).exists(), name
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run --with pytest python -m pytest tests/test_scaffold_conversion.py`

Expected: FAIL — the scaffolded CLAUDE.md has no `## Document conversion` section

- [ ] **Step 3: Add the section to the CLAUDE.md template**

In `llm-wiki/scripts/scaffold.py`, replace (inside the f-string, so braces are doubled):

```python
### Raw file policy
- Small text sources → copy into `raw/<subfolder>/`.
- Large binaries → create `raw/refs/<slug>.md` pointer file.

## Current articles
```

with:

```python
### Raw file policy
- Small text sources → copy into `raw/<subfolder>/`.
- Large binaries → create `raw/refs/<slug>.md` pointer file.

## Document conversion

PDFs in `raw/pdfs/` are converted once, at ingest, into line-citable markdown under
`raw/converted/<slug>/` (script-written, never edit by hand). Cite the converted markdown,
e.g. `(raw/converted/<slug>/<slug>.md, L14-22)`; `<slug>.pages.json` translates lines back to PDF pages.

converter: auto        # auto | mineru | pdftotext | command | none
tier: basic            # MinerU tier: flash | basic | standard | advanced (standard/advanced use the GPU)
timeout: 900           # seconds per file
command: ""            # converter: command -> "mytool {{input}} {{outdir}}"; must write {{outdir}}/<slug>.md

`auto` tries your `command`, then local MinerU (`mineru-kit`), then `pdftotext` (lossy, text-layer
PDFs only). Set `converter: none` to turn conversion off. Documents are never uploaded: the remote
MinerU service is not used.

## Current articles
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `uv run --with pytest python -m pytest tests/test_scaffold_conversion.py`

Expected: PASS

- [ ] **Step 5: Register the slow marker and exclude it by default**

In `pytest.ini`, replace:

```ini
addopts = -q
```

with:

```ini
addopts = -q -m "not slow"
markers =
    slow: needs external tools or large inputs; run with `-m slow`
```

- [ ] **Step 6: Add the opt-in real MinerU test**

Create `tests/test_convert_real.py`:

```python
"""Real MinerU conversion. Opt in with: LLM_WIKI_TEST_PDF=/path/to/any.pdf pytest -m slow"""
import json
import os
import shutil

import pytest

import convert_source

pytestmark = pytest.mark.slow

PDF = os.environ.get("LLM_WIKI_TEST_PDF", "")


@pytest.mark.skipif(not PDF or not os.path.isfile(PDF), reason="set LLM_WIKI_TEST_PDF to a PDF")
@pytest.mark.skipif(convert_source.find_mineru_kit() is None, reason="mineru-kit not installed")
def test_real_mineru_conversion(tmp_path):
    root = tmp_path / "vault"
    (root / "raw" / "pdfs").mkdir(parents=True)
    pdf = root / "raw" / "pdfs" / "sample.pdf"
    shutil.copy(PDF, pdf)
    cfg = convert_source.Config(converter="mineru", tier="basic", timeout=1800)
    res = convert_source.convert(pdf, root, config=cfg)
    meta = json.loads((res.directory / "sample.convert.json").read_text())
    pages = json.loads((res.directory / "sample.pages.json").read_text())
    assert res.md_path.stat().st_size > 0
    assert meta["converter"] == "mineru-kit" and meta["lossy"] is False and meta["tier"] == "basic"
    assert pages["pages"] and pages["pages"][0]["line_start"] == 1
    assert "data:image" not in res.md_path.read_text()          # images are files, not base64
```

- [ ] **Step 7: Run the slow tests (skipped by default)**

Run: `uv run --with pytest python -m pytest -m slow`

Expected: PASS — 1 skipped unless `LLM_WIKI_TEST_PDF` points at a PDF and mineru-kit is installed

- [ ] **Step 8: SKILL.md: directory layout**

In `llm-wiki/SKILL.md`, replace:

```markdown
│   ├── pdfs/              ← PDF source files
```

with:

```markdown
│   ├── pdfs/              ← PDF source files
│   ├── converted/         ← Script-written markdown of PDFs (line-citable; never edit)
```

- [ ] **Step 9: SKILL.md: ownership table**

In `llm-wiki/SKILL.md`, replace:

```markdown
| `raw/`                       | Human  | IMMUTABLE. LLM reads only, never modifies.                                    |
```

with:

```markdown
| `raw/`                       | Human  | IMMUTABLE. LLM reads only, never modifies.                                    |
| `raw/converted/`             | Script | Written once per PDF by `convert_source.py`. The LLM never edits it. Cite this markdown, not the PDF. |
```

- [ ] **Step 10: SKILL.md: ingest**

In `llm-wiki/SKILL.md`, replace:

```markdown
first. For a large source, scaffold before you summarize (§ Divide-and-conquer).
```

with:

```markdown
first. For a large source, scaffold before you summarize (§ Divide-and-conquer).

**PDFs:** `fast_ingest.py` first converts a PDF into line-citable markdown under
`raw/converted/<slug>/` (local MinerU by default, your own tool or `pdftotext` otherwise; see
the vault `CLAUDE.md` § Document conversion) and ingests that markdown, so citations stay
`(raw/converted/<slug>/<slug>.md, Lxx)`. `<slug>.pages.json` maps lines back to PDF pages.
A source stub from a PDF carries `source_pdf` and `source_pdf_sha256`.
```

- [ ] **Step 11: SKILL.md: batch_ingest note**

In `llm-wiki/SKILL.md`, replace:

```markdown
> The script also exposes a `--pdf` flag (auto-convert via a PDF skill before
> ingest) — **off by default**. Use it only if this vault's `CLAUDE.md` calls for a
> preprocessing step.
```

with:

```markdown
> PDFs (`--ext .pdf`) are converted automatically before ingest (see `ingest`). Pass
> `--no-convert` to skip them. The old `--pdf` flag is a deprecated no-op. Files under
> `raw/converted/` are never scanned as separate sources.
```

- [ ] **Step 12: op-ingest.md**

In `llm-wiki/references/ops/op-ingest.md`, replace:

```markdown
stub); it does not transform the source. If the vault's `CLAUDE.md` requires a
```

with:

```markdown
stub); it does not transform the source, except that a **PDF is converted first** into
line-citable markdown under `raw/converted/<slug>/` (`convert_source.py`, configured in the vault
`CLAUDE.md` § Document conversion) and the stub points at that markdown. If the vault's `CLAUDE.md` requires a
```

- [ ] **Step 13: tooling-tips.md**

In `llm-wiki/references/tooling-tips.md`, replace:

```markdown
# Ingest all PDFs with auto-conversion
python3 scripts/batch_ingest.py <wiki-root> --dir raw/pdfs --ext .pdf --pdf
```

with:

```markdown
# Ingest all PDFs (converted to markdown first; --no-convert skips them)
python3 scripts/batch_ingest.py <wiki-root> --dir raw/pdfs --ext .pdf

# Convert one PDF without ingesting it (add --force to reconvert; line numbers change)
python3 scripts/convert_source.py <wiki-root> raw/pdfs/paper.pdf
```

- [ ] **Step 14: tests/README.md**

In `tests/README.md`, replace:

```markdown
| `test_scaffold_and_hot.py` | `scaffold.py`, `lint_wiki.py` |
```

with:

```markdown
| `test_textnorm.py` | `textnorm.py` | case-preserving normalizer, fold/loose views, hyphen variants, offset map |
| `test_vault_config.py` | `vault_config.py` | CLAUDE.md sections, key/values, private zones |
| `test_convert_source.py` | `convert_source.py` | config, slugs, pdftotext / command / MinerU converters, page map, `convert()`, CLI |
| `test_fast_ingest_pdf.py`, `test_batch_ingest.py` | `fast_ingest.py`, `batch_ingest.py` | PDF conversion at ingest, dedupe by PDF hash |
| `test_lint_claims.py`, `test_lint_conversion.py` | `lint_wiki.py` | check 13 normalization, stale / lossy conversions |
| `test_convert_real.py` | `convert_source.py` | real MinerU run, opt in with `-m slow` and `LLM_WIKI_TEST_PDF` |
| `test_scaffold_and_hot.py` | `scaffold.py`, `lint_wiki.py` |
```

- [ ] **Step 15: Run the full suite**

Run: `uv run --with pytest python -m pytest `

Expected: PASS — the original 31 tests plus every new test

- [ ] **Step 16: Commit**

```bash
git add llm-wiki/scripts/scaffold.py pytest.ini tests/test_scaffold_conversion.py tests/test_convert_real.py llm-wiki/SKILL.md llm-wiki/references/ops/op-ingest.md llm-wiki/references/tooling-tips.md tests/README.md
git commit -m "feat: scaffold Document conversion config, slow marker, docs for PDF conversion"
```


