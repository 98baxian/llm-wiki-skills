# verify_quotes Implementation Plan

> **Verification note (2026-10-19):** every task below was executed in order on a scratch clone with Plan A already applied, tests first (red), then code (green), then the commit - ending at 293 passing tests plus 3 opt-in slow tests (the 1 MB performance guard was run and passes). The finished script ran on a copy of a real vault in about 2 s.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic checker that verifies every quoted passage in wiki pages against the raw file it cites, reports verdicts as JSON, can flag failures with FIX comments, and plugs into lint behind --verify-quotes.

**Architecture:** A shared citation grammar (citations.py) and the normalizer from Plan A (textnorm.py) feed verify_quotes.py: extraction and binding, a matching ladder (literal, case-folded, loose, script-folded, fuzzy with a trigram prefilter), verdict assembly with severities, an incremental cache, a report and CLI. cascade_flag.py shares the citation grammar and a FIX-comment inserter. lint_wiki.py imports verify_wiki.

**Tech Stack:** Python 3.11+ standard library only (difflib; zhconv optional); pytest via `uv run --with pytest`.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-10-19-verify-quotes-design.md` (read it first); it builds on `2026-10-19-document-conversion-design.md`.
- Prerequisite: Plan A (document conversion) is merged, at least its Tasks 1 (`textnorm.py`) and 3 (`vault_config.py`); the converted-file artifacts (`convert.json`, `pages.json`, `dropped.txt`) are optional inputs.
- Scripts are plain modules in `llm-wiki/scripts/`, imported by name; tests live in `tests/` (conftest puts the scripts directory on `sys.path`).
- Run tests with `uv run --with pytest python -m pytest`. All earlier tests must stay green.
- verify_quotes never modifies `wiki/` or `raw/` unless `--flag` is given, and then only inserts FIX comments for error-severity `not_found` results.
- verify_quotes is never called from `fast_ingest.py`.
- Constants (documented, not CLI flags): SLACK=10, THRESHOLD=85, NEAR_MISS=60, minimum quote length 3 Latin words / 12 CJK characters (2 words / 6 characters with a digit).
- Docs and code comments in English; no secrets in the repo. Commit after every task; do not push.

---

### Task 1: citations.py, the shared citation grammar

**Files:**
- Create: `llm-wiki/scripts/citations.py`
- Test: `tests/test_citations.py`

**Interfaces:**
- Consumes: nothing
- Produces: `Citation(raw: str, locs: tuple, start: int, end: int)` (frozen dataclass); `parse_locs(text) -> tuple` of `("L", a, b)` / `("p", a, b)` / `("unknown",)`; `find_citations(line) -> list[Citation]`; `malformed(line) -> list[str]`; `mask_citations(line) -> str`; `CITE_RE`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_citations.py`:

```python
"""Tests for citations.py: the raw-citation grammar."""
import pytest

import citations


def one(line):
    found = citations.find_citations(line)
    assert len(found) == 1, found
    return found[0]


@pytest.mark.parametrize("line, raw, locs", [
    ("x (raw/articles/gpt3.md, L8)", "raw/articles/gpt3.md", (("L", 8, 8),)),
    ("x (raw/x.md, L14-22)", "raw/x.md", (("L", 14, 22),)),
    ("x (raw/x.md, L11-L13)", "raw/x.md", (("L", 11, 13),)),
    ("x (raw/x.md, L110-113)", "raw/x.md", (("L", 110, 113),)),
    ("x (raw/x.md, L39/L63)", "raw/x.md", (("L", 39, 39), ("L", 63, 63))),
    ("x (raw/x.md, L61-62, L91)", "raw/x.md", (("L", 61, 62), ("L", 91, 91))),
    ("x (raw/x.md, L10-L36; L81-L93)", "raw/x.md", (("L", 10, 36), ("L", 81, 93))),
    ("x (raw/x.md, p.12-13)", "raw/x.md", (("p", 12, 13),)),
    ("x (raw/x.md, P.3)", "raw/x.md", (("p", 3, 3),)),
    ("x (raw/x.md, p. 7)", "raw/x.md", (("p", 7, 7),)),
    ("x (raw/x.md, location unknown)", "raw/x.md", (("unknown",),)),
    ("x (raw/x.md,   L3 )", "raw/x.md", (("L", 3, 3),)),
    ("x (raw/x.md, L3–10)", "raw/x.md", (("L", 3, 10),)),
    ("x (raw/x.md，L3)", "raw/x.md", (("L", 3, 3),)),
    ("x (raw/a,b.md, L8)", "raw/a,b.md", (("L", 8, 8),)),
    ("x (raw/a(1).md, L8)", "raw/a(1).md", (("L", 8, 8),)),
    ("x (raw/my file.md, L3)", "raw/my file.md", (("L", 3, 3),)),
    ("x (raw/clippings/张三：全球气候会议纪要？.md, L9)", "raw/clippings/张三：全球气候会议纪要？.md", (("L", 9, 9),)),
    ('x (raw/stocks/早报 ACME公司"预告"发布！.md, L9)', 'raw/stocks/早报 ACME公司"预告"发布！.md', (("L", 9, 9),)),
    ("x (raw/a.b/c.d.md, L2)", "raw/a.b/c.d.md", (("L", 2, 2),)),
])
def test_forms(line, raw, locs):
    c = one(line)
    assert c.raw == raw and c.locs == locs


def test_reversed_range_is_ordered():
    assert one("(raw/x.md, L22-14)").locs == (("L", 14, 22),)


def test_several_sources_in_one_parenthesis():
    found = citations.find_citations("claim (raw/a.md, L9; raw/b.md, L13).")
    assert [(c.raw, c.locs) for c in found] == [("raw/a.md", (("L", 9, 9),)), ("raw/b.md", (("L", 13, 13),))]


def test_two_parentheses_and_spans():
    line = "one (raw/a.md, L1) two (raw/b.md, p.2)"
    a, b = citations.find_citations(line)
    assert line[a.start:a.end] == "raw/a.md, L1" and line[b.start:b.end] == "raw/b.md, p.2"
    assert a.end < b.start


def test_no_citation_in_plain_text():
    assert citations.find_citations("see the raw folder, L3 is nothing") == []


class TestMalformed:
    def test_well_formed_is_not_malformed(self):
        assert citations.malformed("x (raw/a.md, L1; raw/b.md, L2)") == []

    def test_missing_extension_and_location(self):
        assert citations.malformed("x (raw/clippings/unfinished") == ["(raw/clippings/unfinished"]
        assert citations.malformed("x (raw/a.md, line 5)") == ["(raw/a.md, line 5)"]

    def test_only_the_broken_one_is_reported(self):
        line = "x (raw/a.md, L1) and (raw/b"
        assert citations.malformed(line) == ["(raw/b"]


def test_mask_blanks_citations_but_keeps_length():
    line = 'He said "the cell divides" (raw/a "x" y.md, L3) ok'
    masked = citations.mask_citations(line)
    assert len(masked) == len(line) and '"x"' not in masked and masked.startswith('He said "the cell divides" (')
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run --with pytest python -m pytest tests/test_citations.py`

Expected: FAIL — `ModuleNotFoundError: No module named 'citations'`

- [ ] **Step 3: Implement**

Create `llm-wiki/scripts/citations.py`:

```python
"""Raw-citation grammar shared by verify_quotes.py and cascade_flag.py.

A citation is ``raw/<path>.<ext>`` followed by one or more locations::

    (raw/articles/x.md, L14-22)           (raw/x.md, L11-L13)       (raw/x.md, L39/L63)
    (raw/x.md, L61-62, L91)               (raw/x.md, p.12-13)       (raw/x.md, location unknown)
    (raw/a.md, L9; raw/b.md, L13)         several sources in one parenthesis

Tolerated: ``P.3``, an en dash for ranges, a full-width comma, extra spaces, commas and
parentheses inside the file name, CJK and spaces in the path.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_DASH = r"\s*[-–—]\s*"
_LOC = (
    r"(?:[Ll]\d+(?:" + _DASH + r"[Ll]?\d+)?"
    r"|[Pp]\.\s*\d+(?:" + _DASH + r"\d+)?"
    r"|location unknown)"
)
_SEP = r"\s*[,，;；/]\s*"
# the path may hold anything except a newline or the start of another citation
_PATH = r"raw/(?:(?!\(raw/)[^\n])*?\.[A-Za-z0-9]{1,8}"
CITE_RE = re.compile(rf"({_PATH})\s*[,，]\s*({_LOC}(?:{_SEP}{_LOC})*)")
_LOC_PIECE = re.compile(
    r"[Ll](\d+)(?:\s*[-–—]\s*[Ll]?(\d+))?|[Pp]\.\s*(\d+)(?:\s*[-–—]\s*(\d+))?|location unknown"
)


@dataclass(frozen=True)
class Citation:
    raw: str                 # "raw/articles/x.md"
    locs: tuple              # (("L", 14, 22), ("p", 12, 12), ("unknown",))
    start: int               # span of "<path>, <locations>" inside the line
    end: int


def parse_locs(text: str) -> tuple:
    out = []
    for m in _LOC_PIECE.finditer(text):
        if m.group(1):
            a, b = int(m.group(1)), int(m.group(2) or m.group(1))
            out.append(("L", min(a, b), max(a, b)))
        elif m.group(3):
            a, b = int(m.group(3)), int(m.group(4) or m.group(3))
            out.append(("p", min(a, b), max(a, b)))
        else:
            out.append(("unknown",))
    return tuple(out)


def find_citations(line: str) -> list[Citation]:
    return [
        Citation(m.group(1).strip(), parse_locs(m.group(2)), m.start(), m.end())
        for m in CITE_RE.finditer(line)
    ]


def malformed(line: str) -> list[str]:
    """``(raw/`` fragments that do not start a well-formed citation."""
    starts = {c.start for c in find_citations(line)}
    return [line[i:i + 80].rstrip() for i in (m.start() for m in re.finditer(r"\(raw/", line))
            if i + 1 not in starts]


def mask_citations(line: str) -> str:
    """Replace every citation with spaces (same length) so file names cannot be read as quotes."""
    out = line
    for c in find_citations(line):
        out = out[:c.start] + " " * (c.end - c.start) + out[c.end:]
    return out
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `uv run --with pytest python -m pytest tests/test_citations.py`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add llm-wiki/scripts/citations.py tests/test_citations.py
git commit -m "feat(citations): tolerant raw-citation grammar shared by verify_quotes and cascade_flag"
```


### Task 2: Quote extraction and binding

**Files:**
- Create: `llm-wiki/scripts/verify_quotes.py`
- Test: `tests/test_verify_quotes.py`

**Interfaces:**
- Consumes: `citations.find_citations`, `citations.malformed`, `citations.mask_citations` (Task 1)
- Produces: constants `SLACK THRESHOLD NEAR_MISS SHORT_WORDS SHORT_CJK`; `is_cjk(ch)`; `long_enough(q) -> bool`; `fragments_of(quote) -> list[str]`; `Claim(page, line_no, quote, fragments, raw, locs, cite_line, shared)`; `ExtractResult(claims, uncited, too_short, malformed)`; `body_lines(text)`; `extract_claims(text: str, page: str) -> ExtractResult`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_verify_quotes.py`:

```python
"""Tests for quote extraction and binding (verify_quotes.extract_claims)."""
import verify_quotes as vq


def claims(text):
    return vq.extract_claims(text, "wiki/concepts/x.md")


def quotes(text):
    return [c.quote for c in claims(text).claims]


class TestQuoteStyles:
    def test_straight_curly_and_corner_quotes(self):
        text = (
            'The paper says "the cell divides rapidly" (raw/a.md, L1)\n'
            "又说“细胞分裂的速度非常之快而且很稳定”（见下） (raw/a.md, L2)\n"
            "还有「这是一个足够长的中文引文用于测试」 (raw/a.md, L3)\n"
            "以及『另一个足够长的中文引文用于测试』 (raw/a.md, L4)\n"
        )
        assert quotes(text) == [
            "the cell divides rapidly", "细胞分裂的速度非常之快而且很稳定",
            "这是一个足够长的中文引文用于测试", "另一个足够长的中文引文用于测试",
        ]

    def test_single_quotes_and_apostrophes_are_ignored(self):
        assert quotes("It's what 'they' said, don't you think (raw/a.md, L1)\n") == []

    def test_quote_inside_a_file_name_is_not_a_quote(self):
        text = '- 事实 (raw/stocks/早报 ACME公司"预告"发布！.md, L9)\n'
        res = claims(text)
        assert res.claims == [] and res.too_short == 0

    def test_quote_must_sit_on_one_line(self):
        assert quotes('He wrote "the cell divides\nrapidly in culture" (raw/a.md, L1)\n') == []


class TestMinimumLength:
    def test_three_latin_words_pass_two_do_not(self):
        assert quotes('A "three word quote" (raw/a.md, L1) and "two words" (raw/a.md, L2)\n') == ["three word quote"]

    def test_digit_rule_lets_the_repos_own_example_through(self):
        text = 'The model has "175 billion parameters" (raw/articles/gpt3.md, L8)\n'
        assert quotes(text) == ["175 billion parameters"]
        assert quotes('It is "up 3%" (raw/a.md, L1)\n') == ["up 3%"]

    def test_cjk_needs_twelve_characters_or_six_with_a_digit(self):
        assert quotes("他说“今天天气很好”吧 (raw/a.md, L1)\n") == []
        assert quotes("他说“增长了百分之三十五点二”吧 (raw/a.md, L1)\n") == []
        assert quotes("他说“增长了35.2%的速度”吧 (raw/a.md, L1)\n") == ["增长了35.2%的速度"]

    def test_short_quotes_are_counted(self):
        assert claims('So-called "AI" (raw/a.md, L1)\n').too_short == 1


class TestIgnoredRegions:
    def test_frontmatter_code_fences_inline_code_and_comments(self):
        text = (
            '---\nclaims_requiring_verification:\n  - \'"two percent discount" (raw/a.md, L1)\'\n---\n'
            '```\nprint("this is not a quote at all")\n```\n'
            'Use `say("inline code is not a quote")` here (raw/a.md, L9)\n'
            '<!-- FIX: "an old fix comment text" (raw/a.md, L9) -->\n'
            'Real: "the actual quoted passage" (raw/a.md, L10)\n'
        )
        assert quotes(text) == ["the actual quoted passage"]

    def test_wikilink_alias_text_is_not_a_quote(self):
        assert quotes('See [[concepts/x|"a long alias that looks quoted"]] (raw/a.md, L1)\n') == []


class TestBinding:
    def test_binds_to_first_citation_on_its_right(self):
        text = 'First "quote number one here" (raw/a.md, L1) then "quote number two here" (raw/b.md, L2)\n'
        got = [(c.quote, c.raw, c.locs) for c in claims(text).claims]
        assert got == [("quote number one here", "raw/a.md", (("L", 1, 1),)),
                       ("quote number two here", "raw/b.md", (("L", 2, 2),))]

    def test_citation_to_the_left_does_not_count(self):
        res = claims('(raw/a.md, L1) then "a quote without its own citation"\n')
        assert res.claims == [] and res.uncited == 1

    def test_binds_to_a_later_line_of_the_same_paragraph(self):
        text = 'She wrote "a quote that continues" and\nmore words follow (raw/a.md, L5)\n'
        c = claims(text).claims[0]
        assert (c.line_no, c.cite_line, c.raw) == (1, 2, "raw/a.md")

    def test_list_items_are_separate_units(self):
        text = '- item with "an uncited quote here"\n- another item (raw/a.md, L5)\n'
        res = claims(text)
        assert res.claims == [] and res.uncited == 1

    def test_blank_line_ends_the_paragraph(self):
        res = claims('Has "an uncited quote here"\n\nLater (raw/a.md, L5)\n')
        assert res.claims == [] and res.uncited == 1

    def test_table_rows_never_bind_across_rows(self):
        text = '| a "quote in row one here" | x |\n| b | (raw/a.md, L5) |\n'
        res = claims(text)
        assert res.claims == [] and res.uncited == 1

    def test_several_quotes_share_one_citation_and_know_it(self):
        text = 'As in "quote A is first" and "quote B is second" (raw/c.md, L9)\n'
        cs = claims(text).claims
        assert [c.shared for c in cs] == [1, 1] and {c.raw for c in cs} == {"raw/c.md"}

    def test_several_locations_are_kept(self):
        c = claims('X "a quote with two places" (raw/a.md, L1-2, L7)\n').claims[0]
        assert c.locs == (("L", 1, 2), ("L", 7, 7))


class TestBlockquotes:
    def test_run_of_quote_lines_is_one_quote_at_its_first_line(self):
        text = "> The cell divides\n> rapidly in culture\n> (raw/a.md, L4-5)\n\nafter\n"
        res = claims(text)
        assert [(c.line_no, c.quote) for c in res.claims] == [(1, "The cell divides rapidly in culture")]

    def test_citation_on_the_line_after_the_block(self):
        text = "> The cell divides rapidly in culture\n(raw/a.md, L4)\n"
        assert [c.raw for c in claims(text).claims] == ["raw/a.md"]

    def test_inner_quotes_of_a_blockquote_are_not_extracted_twice(self):
        text = '> He said "the cell divides rapidly" today (raw/a.md, L4)\n'
        assert len(claims(text).claims) == 1


class TestEllipsis:
    def test_fragments_split_on_every_ellipsis_style(self):
        for ell in ("[...]", "[…]", "...", "…", "……", "⋯"):
            text = f'He said "the first long fragment {ell} and the second long fragment" (raw/a.md, L1)\n'
            c = claims(text).claims[0]
            assert c.fragments == ["the first long fragment", "and the second long fragment"], ell

    def test_short_fragments_are_dropped_but_one_must_remain(self):
        c = claims('He said "the first long fragment ... ok" (raw/a.md, L1)\n').claims[0]
        assert c.fragments == ["the first long fragment"]
        assert claims('He said "ok ... no" (raw/a.md, L1)\n').claims == []


def test_malformed_citations_are_reported_with_page_and_line():
    res = claims('line one\nBroken (raw/clippings/unfinished and "a quote that is long enough"\n')
    assert res.malformed == [("wiki/concepts/x.md", 2, "(raw/clippings/unfinished and \"a quote that is long enough\"")]
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run --with pytest python -m pytest tests/test_verify_quotes.py`

Expected: FAIL — `ModuleNotFoundError: No module named 'verify_quotes'`

- [ ] **Step 3: Implement**

Create `llm-wiki/scripts/verify_quotes.py`:

```python
#!/usr/bin/env python3
"""
verify_quotes.py - check that quoted passages in wiki pages really occur in the raw
file they cite, at the cited place.

Usage:
    python3 verify_quotes.py <wiki-root> [--page PATH]... [--threshold N] [--report PATH]
                             [--flag [--dry-run]] [--strict] [--no-cache]

Reports only; wiki pages are edited solely with --flag (one FIX comment per quote that is
not found). Exit 0: no error-severity quote. Exit 1: at least one. Exit 2: usage/environment.
"""
from __future__ import annotations

import argparse
import bisect
import difflib
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import citations
import textnorm
import vault_config

SLACK = 10                 # lines of tolerance around a cited line range
THRESHOLD = 85             # fuzzy similarity that counts as the same passage
NEAR_MISS = 60             # similarity that suggests a paraphrase in quotation marks
SHORT_WORDS = 5            # a quote of this many words or fewer is "short" (case matters)
SHORT_CJK = 12

QUOTE_RES = [
    re.compile(r'"([^"\n]+)"'),
    re.compile("“([^”\n]+)”"),
    re.compile("「([^」\n]+)」"),
    re.compile("『([^』\n]+)』"),
]
ELLIPSIS_RE = re.compile(r"\[\s*(?:\.{3}|…)\s*\]|\.{3,}|…+|⋯+")
LIST_START = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s")


def is_cjk(ch: str) -> bool:
    return "一" <= ch <= "鿿"


def long_enough(q: str) -> bool:
    """Minimum length: Latin 3 words, CJK 12 characters; with a digit 2 words / 6 characters."""
    cjk = sum(1 for c in q if is_cjk(c))
    has_digit = any(c.isdigit() for c in q)
    if cjk >= 0.3 * max(len(q), 1):
        return cjk >= SHORT_CJK or (cjk >= 6 and has_digit)
    words = len(q.split())
    return words >= 3 or (words >= 2 and has_digit)


def fragments_of(quote: str) -> list[str]:
    parts = [p.strip() for p in ELLIPSIS_RE.split(quote)] if ELLIPSIS_RE.search(quote) else [quote.strip()]
    return [p for p in parts if long_enough(p)]


@dataclass
class Claim:
    page: str
    line_no: int
    quote: str
    fragments: list[str]
    raw: str
    locs: tuple
    cite_line: int
    shared: int = 0          # how many other quotes are bound to the same citation


@dataclass
class ExtractResult:
    claims: list[Claim] = field(default_factory=list)
    uncited: int = 0
    too_short: int = 0
    malformed: list[tuple[str, int, str]] = field(default_factory=list)


def body_lines(text: str):
    """Yield ``(line_no, line)`` for lines outside frontmatter, fenced code and HTML comments."""
    lines = text.split("\n")
    start = 0
    if lines and lines[0].strip() == "---":
        for k in range(1, len(lines)):
            if lines[k].strip() == "---":
                start = k + 1
                break
    in_code = in_comment = False
    for i in range(start, len(lines)):
        line = lines[i]
        s = line.strip()
        if s.startswith("```") or s.startswith("~~~"):
            in_code = not in_code
            continue
        if in_code:
            continue
        if in_comment:
            if "-->" in line:
                in_comment = False
            continue
        if s.startswith("<!--") and "-->" not in line:
            in_comment = True
            continue
        yield i + 1, line


def _blank(m: re.Match) -> str:
    return " " * len(m.group(0))


def prepare(line: str) -> str:
    """Line with citations, comments, inline code, links and wikilinks blanked (same length)."""
    out = citations.mask_citations(line)
    out = re.sub(r"<!--.*?-->", _blank, out)
    out = re.sub(r"`[^`\n]*`", _blank, out)
    out = re.sub(r"\[\[[^\]\n]*\]\]", _blank, out)
    return re.sub(r"\]\([^)\n]*\)", _blank, out)


def _inline_quotes(prepared: str):
    found = []
    for rx in QUOTE_RES:
        for m in rx.finditer(prepared):
            found.append((m.start(), m.end(), m.group(1).strip()))
    found.sort()
    out, last_end = [], -1
    for start, end, q in found:
        if start >= last_end:
            out.append((start, end, q))
            last_end = end
    return out


def extract_claims(text: str, page: str) -> ExtractResult:
    """Find quoted passages and bind each to a raw citation.

    A quote binds to the first citation to its right on the same line; failing that, to the
    first citation on a later line of the same unit. A unit is a paragraph, a list item, a
    table row or a blockquote group (which may also use the line right after it). A quote
    with no citation is counted as ``uncited``, never as a failure.
    """
    res = ExtractResult()
    rows = list(body_lines(text))
    n = len(rows)
    cites = [citations.find_citations(line) for _, line in rows]
    prepared = [prepare(line) for _, line in rows]
    for (no, line) in rows:
        for frag in citations.malformed(line):
            res.malformed.append((page, no, frag))

    unit = [0] * n
    kind = ["text"] * n
    current = 0
    for k, (_, line) in enumerate(rows):
        s = line.strip()
        if not s:
            current += 1
            kind[k] = "blank"
        elif s.startswith("#") or LIST_START.match(line) or s.startswith("|"):
            current += 1
            kind[k] = "table" if s.startswith("|") else "block"
        elif s.startswith(">"):
            if k == 0 or kind[k - 1] != "quote":
                current += 1
            kind[k] = "quote"
        unit[k] = current

    bound: list[Claim] = []

    def bind(k: int, after: int) -> tuple[int, citations.Citation] | None:
        for c in cites[k]:
            if c.start >= after:
                return k, c
        j = k + 1
        while j < n and unit[j] == unit[k] and kind[j] != "blank":
            if cites[j]:
                return j, cites[j][0]
            j += 1
        return None

    k = 0
    while k < n:
        if kind[k] == "quote":
            j = k
            while j + 1 < n and kind[j + 1] == "quote":
                j += 1
            body = " ".join(prepared[i].strip().lstrip(">").strip() for i in range(k, j + 1))
            body = " ".join(re.sub(r"[(（]\s*[)）]", " ", body).split())   # parentheses left empty by the masked citation
            frags = fragments_of(body) if body else []
            if not frags:
                res.too_short += 1
            else:
                hit = None
                for i in range(k, j + 1):
                    if cites[i]:
                        hit = (i, cites[i][0])
                        break
                if hit is None and j + 1 < n and cites[j + 1] and kind[j + 1] != "blank":
                    hit = (j + 1, cites[j + 1][0])
                if hit is None:
                    res.uncited += 1
                else:
                    bound.append(Claim(page, rows[k][0], body, frags, hit[1].raw, hit[1].locs, rows[hit[0]][0]))
            k = j + 1
            continue
        for start, end, q in _inline_quotes(prepared[k]):
            frags = fragments_of(q)
            if not frags:
                res.too_short += 1
                continue
            hit = bind(k, end)
            if hit is None:
                res.uncited += 1
            else:
                bound.append(Claim(page, rows[k][0], q, frags, hit[1].raw, hit[1].locs, rows[hit[0]][0]))
        k += 1

    groups = Counter((c.cite_line, c.raw, c.locs) for c in bound)
    for c in bound:
        c.shared = groups[(c.cite_line, c.raw, c.locs)] - 1
    res.claims = bound
    return res
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `uv run --with pytest python -m pytest tests/test_verify_quotes.py`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add llm-wiki/scripts/verify_quotes.py tests/test_verify_quotes.py
git commit -m "feat(verify_quotes): extract quotes and bind them to raw citations"
```


### Task 3: Matching primitives: Doc views, ordered fragments, fuzzy search

**Files:**
- Modify: `llm-wiki/scripts/verify_quotes.py`
- Modify: `tests/test_verify_quotes.py`

**Interfaces:**
- Consumes: `textnorm.normalize_with_map`, `textnorm.normalize`, `textnorm.fold_case`, `textnorm.loose`, `textnorm.HYPHEN_BREAK` (Plan A Task 1); `is_cjk` (Task 2)
- Produces: constants `MIN_FUZZY_CHARS SCAN_LIMIT MAX_POSTINGS TOP_CANDIDATES RUNGS`; `Doc(text)` with `n_lines`, `view(mode, join_hyphen)`, `line_of(offset)`, `offsets_for_lines(first, last)`, `find(needle, mode, line_range, after, join_hyphen) -> (start, end) | None`, `lines_of_span(mode, span, join_hyphen)`, `trigram_index()`, `cjk_share`; `needle_for(mode, fragment) -> str`; `match_fragments(doc, fragments, mode, ranges) -> (first_line, last_line) | None` (modes `literal`/`fold`/`loose`; line ranges are 1-based inclusive); `partial_match(query, window) -> (score, (first, last) | None)`; `fuzzy_best(doc, fragment, line_range) -> (score, (first_line, last_line) | None, position)` (position -1 when nothing matched)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_verify_quotes.py`:

```python
"""Matching primitives: Doc views, ordered fragment search, fuzzy search."""
import random

import verify_quotes as vq


def doc(*lines):
    return vq.Doc("\n".join(lines) + "\n")


class TestDoc:
    def test_line_bookkeeping(self):
        d = doc("alpha", "beta", "gamma")
        assert d.n_lines == 3
        assert [d.line_of(o) for o in (0, 4, 5, 6, 10, 11)] == [1, 1, 1, 2, 2, 3]   # a newline belongs to the line it ends
        assert vq.Doc("").n_lines == 0 and vq.Doc("no newline").n_lines == 1

    def test_offsets_for_lines_are_clamped(self):
        d = doc("alpha", "beta", "gamma")
        assert d.offsets_for_lines(2, 2) == (6, 11)
        assert d.offsets_for_lines(-5, 99) == (0, len(d.text))

    def test_views_map_back_to_lines(self):
        d = doc("first line", "the  cell   divides", "last")
        span = d.find("the cell divides", "literal", (1, 3))
        assert d.lines_of_span("literal", span) == (2, 2)


class TestMatchFragments:
    D = doc("intro text here", "The Cell divides rapidly in culture", "unrelated middle", "tail with 5*3 percent growth")

    def test_literal_fold_and_loose_rungs(self):
        assert vq.match_fragments(self.D, ["The Cell divides rapidly"], "literal", [(1, 4)]) == (2, 2)
        assert vq.match_fragments(self.D, ["the cell divides rapidly"], "literal", [(1, 4)]) is None
        assert vq.match_fragments(self.D, ["the cell divides rapidly"], "fold", [(1, 4)]) == (2, 2)
        assert vq.match_fragments(self.D, ["thecell, divides-rapidly"], "loose", [(1, 4)]) == (2, 2)

    def test_markdown_escape_in_raw_matches_plain_quote(self):
        d = doc("growth", "tail with 5\\*3 percent growth")
        assert vq.match_fragments(d, ["5*3 percent growth"], "literal", [(1, 2)]) == (2, 2)

    def test_range_restricts_the_search(self):
        assert vq.match_fragments(self.D, ["unrelated middle"], "literal", [(1, 2)]) is None
        assert vq.match_fragments(self.D, ["unrelated middle"], "literal", [(3, 3)]) == (3, 3)

    def test_any_of_several_ranges(self):
        assert vq.match_fragments(self.D, ["unrelated middle"], "literal", [(1, 1), (3, 3)]) == (3, 3)

    def test_fragments_must_appear_in_order(self):
        d = doc("alpha beta gamma delta epsilon")
        assert vq.match_fragments(d, ["alpha beta", "delta epsilon"], "literal", [(1, 1)]) == (1, 1)
        assert vq.match_fragments(d, ["delta epsilon", "alpha beta"], "literal", [(1, 1)]) is None

    def test_both_hyphen_forms_are_tried(self):
        d = doc("a well-", "known fact and inter-", "national law")
        assert vq.match_fragments(d, ["well-known fact"], "literal", [(1, 3)]) == (1, 2)
        assert vq.match_fragments(d, ["international law"], "literal", [(1, 3)]) == (2, 3)

    def test_empty_needle_never_matches(self):
        assert vq.match_fragments(self.D, ["!!!"], "loose", [(1, 4)]) is None


class TestFuzzy:
    TEXT = [
        "Completely unrelated opening sentence about astronomy and galaxies far away.",
        "The quick brown fox jumps over the lazy dog near the river bank today.",
        "Another filler sentence that has nothing to do with the target at all.",
    ]

    def test_one_word_changed_scores_high_and_reports_its_line(self):
        d = doc(*self.TEXT)
        score, lines, pos = vq.fuzzy_best(d, "the quick brown fox jumps over the lazy cat near the river bank", (1, 3))
        assert score >= vq.THRESHOLD and lines == (2, 2) and pos > 0

    def test_unrelated_sentence_scores_below_near_miss(self):
        d = doc(*self.TEXT)
        score, _, _ = vq.fuzzy_best(d, "photosynthesis converts sunlight into chemical energy inside chloroplasts", (1, 3))
        assert score < vq.NEAR_MISS

    def test_cjk_is_measured_in_characters(self):
        d = doc("无关的内容放在这里", "图书馆可以延长开放时间，但是人手必须先补上才行", "其他内容")
        score, lines, _ = vq.fuzzy_best(d, "图书馆可以延长开放时间，但人手必须先补上", (1, 3))
        assert score >= vq.THRESHOLD and lines == (2, 2)

    def test_search_is_limited_to_the_line_range(self):
        d = doc(*self.TEXT)
        score, _, _ = vq.fuzzy_best(d, "the quick brown fox jumps over the lazy cat near the river bank", (1, 1))
        assert score < vq.NEAR_MISS

    def test_tiny_queries_are_not_scored(self):
        assert vq.fuzzy_best(doc("abc def"), "ab", (1, 1)) == (0, None, -1)

    def test_large_ranges_use_the_trigram_index(self):
        rng = random.Random(7)
        words = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet"]
        lines = [" ".join(rng.choice(words) for _ in range(40)) for _ in range(400)]
        lines[250] = "the quick brown fox jumps over the lazy dog near the river bank today"
        d = doc(*lines)
        assert len(d.text) > vq.SCAN_LIMIT * 4
        score, found, _ = vq.fuzzy_best(d, "the quick brown fox jumps over the lazy cat near the river bank", (1, 400))
        assert score >= vq.THRESHOLD and found == (251, 251)
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run --with pytest python -m pytest tests/test_verify_quotes.py`

Expected: FAIL — `AttributeError: module 'verify_quotes' has no attribute 'Doc'`

- [ ] **Step 3: Implement**

Append to `llm-wiki/scripts/verify_quotes.py`:

```python
MIN_FUZZY_CHARS = 6
SCAN_LIMIT = 4000          # ranges up to this many normalized characters are scanned directly
MAX_POSTINGS = 3000        # trigrams more frequent than this carry no signal
TOP_CANDIDATES = 5
RUNGS = ("literal", "fold", "loose")


class Doc:
    """A raw text plus lazily built normalized views that map back to line numbers."""

    def __init__(self, text: str):
        self.text = text
        self.line_starts = [0] + [m.end() for m in re.finditer("\n", text)]
        self.n_lines = len(self.line_starts) - (1 if text.endswith("\n") or not text else 0)
        self._views: dict = {}
        self._trigrams = None
        self._cjk_share = None

    def view(self, mode: str, join_hyphen: bool = True) -> tuple[str, list[int]]:
        key = (mode, join_hyphen)
        if key not in self._views:
            self._views[key] = textnorm.normalize_with_map(
                self.text, fold=(mode == "fold"), loose_mode=(mode == "loose"), join_hyphen=join_hyphen)
        return self._views[key]

    def has_hyphen_break(self) -> bool:
        return bool(textnorm.HYPHEN_BREAK.search(self.text))

    def line_of(self, offset: int) -> int:
        return bisect.bisect_right(self.line_starts, offset)

    def offsets_for_lines(self, first: int, last: int) -> tuple[int, int]:
        first = max(1, first)
        last = min(last, max(self.n_lines, 1))
        lo = self.line_starts[first - 1] if first - 1 < len(self.line_starts) else len(self.text)
        hi = self.line_starts[last] if last < len(self.line_starts) else len(self.text)
        return lo, hi

    def find(self, needle: str, mode: str, line_range: tuple[int, int], after: int = 0,
             join_hyphen: bool = True) -> tuple[int, int] | None:
        """Span ``(start, end)`` in the normalized view, searching only ``line_range`` from ``after``."""
        text, offs = self.view(mode, join_hyphen)
        lo, hi = self.offsets_for_lines(*line_range)
        lo_n = bisect.bisect_left(offs, lo)
        hi_n = bisect.bisect_left(offs, hi)
        j = text.find(needle, max(lo_n, after), hi_n)
        return None if j < 0 else (j, j + len(needle))

    def lines_of_span(self, mode: str, span: tuple[int, int], join_hyphen: bool = True) -> tuple[int, int]:
        _, offs = self.view(mode, join_hyphen)
        return self.line_of(offs[span[0]]), self.line_of(offs[span[1] - 1])

    def trigram_index(self) -> dict:
        if self._trigrams is None:
            text, _ = self.view("loose")
            index: dict = defaultdict(list)
            for i in range(len(text) - 2):
                index[text[i:i + 3]].append(i)
            self._trigrams = index
        return self._trigrams

    @property
    def cjk_share(self) -> float:
        if self._cjk_share is None:
            letters = [c for c in self.text if c.isalpha()]
            self._cjk_share = sum(1 for c in letters if is_cjk(c)) / len(letters) if letters else 0.0
        return self._cjk_share


def needle_for(mode: str, fragment: str) -> str:
    if mode == "literal":
        return textnorm.normalize(fragment)
    if mode == "fold":
        return textnorm.fold_case(fragment)
    return textnorm.loose(fragment)


def match_fragments(doc: Doc, fragments: list[str], mode: str, ranges: list[tuple[int, int]]) -> tuple[int, int] | None:
    """All fragments, in order, inside one of ``ranges`` (line ranges). Returns the matched lines."""
    needles = [needle_for(mode, f) for f in fragments]
    if not needles or not all(needles):
        return None
    joins = (True, False) if mode != "loose" and doc.has_hyphen_break() else (True,)
    for rng in ranges:
        for join in joins:
            cursor, first, last, ok = 0, None, None, True
            for needle in needles:
                span = doc.find(needle, mode, rng, after=cursor, join_hyphen=join)
                if span is None:
                    ok = False
                    break
                cursor = span[1]
                a, b = doc.lines_of_span(mode, span, join)
                first = a if first is None else first
                last = b
            if ok:
                return first, last
    return None


def partial_match(query: str, window: str) -> tuple[int, tuple[int, int] | None]:
    """Percentage of ``query`` characters found, in order, inside ``window``, and where.

    The span runs from the first to the last solid matching block (3+ characters), so
    stray one-letter coincidences do not stretch it.
    """
    sm = difflib.SequenceMatcher(None, query, window, autojunk=False)
    blocks = [b for b in sm.get_matching_blocks() if b.size]
    score = int(100 * sum(b.size for b in blocks) / len(query))
    solid = [b for b in blocks if b.size >= 3] or blocks
    if not solid:
        return score, None
    return score, (min(b.b for b in solid), max(b.b + b.size for b in solid) - 1)


def fuzzy_best(doc: Doc, fragment: str, line_range: tuple[int, int]) -> tuple[int, tuple[int, int] | None, int]:
    """Best fuzzy score of ``fragment`` inside ``line_range``: ``(score, (first_line, last_line), position)``.

    ``position`` is the index of the match in the loose view (-1 when nothing matched); callers use
    it to check that ellipsis fragments appear in order.

    Small ranges are scanned; large ones first rank windows by character-trigram overlap
    (an inverted index built once per raw file) and only the top candidates are scored.
    """
    query = textnorm.loose(fragment)
    n = len(query)
    if n < MIN_FUZZY_CHARS:
        return 0, None, -1
    text, offs = doc.view("loose")
    lo, hi = doc.offsets_for_lines(*line_range)
    lo_n = bisect.bisect_left(offs, lo)
    hi_n = bisect.bisect_left(offs, hi)
    if hi_n - lo_n <= SCAN_LIMIT:
        starts = list(range(lo_n, max(lo_n, hi_n - n) + 1, max(1, n // 4)))
    else:
        index = doc.trigram_index()
        step = max(1, n // 2)
        votes: Counter = Counter()
        for gram in {query[i:i + 3] for i in range(n - 2)}:
            positions = index.get(gram)
            if not positions or len(positions) > MAX_POSTINGS:
                continue
            for p in positions[bisect.bisect_left(positions, lo_n):bisect.bisect_right(positions, hi_n)]:
                votes[p // step] += 1
        starts = sorted({max(lo_n, b * step - n // 4) for b, _ in votes.most_common(TOP_CANDIDATES)})
    best, best_span = 0, None
    width = int(n * 1.3) + 1
    for s in starts:
        score, rel = partial_match(query, text[s:min(s + width, hi_n)])
        if score > best and rel is not None:
            best, best_span = score, (s + rel[0], s + rel[1])
    if best_span is None:
        return 0, None, -1
    return best, (doc.line_of(offs[best_span[0]]), doc.line_of(offs[best_span[1]])), best_span[0]
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `uv run --with pytest python -m pytest tests/test_verify_quotes.py`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add llm-wiki/scripts/verify_quotes.py tests/test_verify_quotes.py
git commit -m "feat(verify_quotes): Doc views, ordered fragment matching and trigram-prefiltered fuzzy search"
```


### Task 4: verify_claim: ladder, scopes, verdicts and severities

**Files:**
- Modify: `llm-wiki/scripts/verify_quotes.py`
- Modify: `tests/test_verify_quotes.py`

**Interfaces:**
- Consumes: `Claim`, `Doc`, `match_fragments`, `fuzzy_best`, `RUNGS`, `is_cjk`, constants `SLACK THRESHOLD NEAR_MISS SHORT_WORDS SHORT_CJK` (Tasks 2-3)
- Produces: `Result(verdict, severity, score=0, matched_lines=[], note='', suggested_raw=None, kind='')`; `Context(doc, unreadable='', convert=None, pages=None, others=None, threshold=THRESHOLD, near_miss=NEAR_MISS, strict=False)`; `is_short(quote)`; `page_lines(pages_meta, first, last)`; `windows_for(claim, ctx) -> list[(first, last)] | None`; `scripts_cannot_overlap(quote, raw_cjk_share)`; `verify_claim(claim, ctx) -> Result`. Verdicts `exact fuzzy wrong_location near_miss not_found unverifiable`; severities `none suggestion warning error`; `kind` is `infrastructure`, `cross_language` (unverifiable) or `downgraded` (not_found)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_verify_quotes.py`:

```python
"""verify_claim: windows, the matching ladder, verdicts and severities."""
import pytest

import verify_quotes as vq


def claim(quote, locs=(("L", 1, 1),), fragments=None, shared=0):
    c = vq.Claim("wiki/x.md", 5, quote, fragments or vq.fragments_of(quote), "raw/a.md", locs, 5)
    c.shared = shared
    return c


def ctx(*lines, **kw):
    return vq.Context(doc=doc(*lines), **kw)


class TestWindows:
    def test_line_ranges_get_slack_and_clamp_at_one(self):
        c = claim("x", locs=(("L", 3, 4), ("L", 30, 30)))
        assert vq.windows_for(c, ctx("a")) == [(1, 4 + vq.SLACK), (30 - vq.SLACK, 30 + vq.SLACK)]

    def test_unknown_or_page_without_map_means_whole_file(self):
        assert vq.windows_for(claim("x", locs=(("unknown",),)), ctx("a")) is None
        assert vq.windows_for(claim("x", locs=(("p", 2, 2),)), ctx("a")) is None
        assert vq.windows_for(claim("x", locs=(("L", 1, 1), ("unknown",))), ctx("a")) is None

    def test_page_with_map_uses_the_page_line_range(self):
        pages = {"confidence": "high", "pages": [
            {"page": 1, "line_start": 1, "line_end": 40}, {"page": 2, "line_start": 41, "line_end": 80}]}
        c = claim("x", locs=(("p", 2, 2),))
        assert vq.windows_for(c, ctx("a", pages=pages)) == [(41 - vq.SLACK, 80 + vq.SLACK)]
        assert vq.windows_for(claim("x", locs=(("p", 9, 9),)), ctx("a", pages=pages)) is None


class TestLadder:
    D = ("intro", "The Cell divides rapidly in culture", "filler", "tail text")

    def test_literal_match_in_the_cited_lines(self):
        r = vq.verify_claim(claim("The Cell divides rapidly", locs=(("L", 2, 2),)), ctx(*self.D))
        assert (r.verdict, r.severity, r.score, r.matched_lines, r.note) == ("exact", "none", 100, [2, 2], "")

    def test_slack_covers_small_line_drift(self):
        lines = ["x"] * 7 + ["the quoted passage is right here"]
        r = vq.verify_claim(claim("the quoted passage is right here", locs=(("L", 1, 1),)), ctx(*lines))
        assert r.verdict == "exact" and r.matched_lines == [8, 8]

    def test_outside_the_window_is_wrong_location_with_lines(self):
        lines = ["x"] * 30 + ["the quoted passage is right here"]
        r = vq.verify_claim(claim("the quoted passage is right here", locs=(("L", 1, 1),)), ctx(*lines))
        assert (r.verdict, r.severity, r.matched_lines) == ("wrong_location", "warning", [31, 31])

    def test_long_quote_differing_only_in_case_is_exact(self):
        r = vq.verify_claim(claim("the cell divides rapidly in culture", locs=(("L", 2, 2),)), ctx(*self.D))
        assert (r.verdict, r.note) == ("exact", "case differs")

    def test_short_quote_differing_only_in_case_is_fuzzy(self):
        r = vq.verify_claim(claim("The Act applies", locs=(("L", 1, 1),)), ctx("the act applies here"))
        assert (r.verdict, r.score, r.note, r.severity) == ("fuzzy", 95, "case differs", "warning")

    def test_loose_rung_handles_asr_spacing_and_punctuation(self):
        c = claim("明年继续扩建社区图书馆", fragments=["明年继续扩建社区图书馆"])
        r = vq.verify_claim(c, ctx("开头", "他说 明年 继续 扩建 社区 图书馆 。", "结尾"))
        assert (r.verdict, r.note, r.matched_lines) == ("exact", "loose", [2, 2])

    def test_whole_file_is_the_cited_place_for_location_unknown(self):
        r = vq.verify_claim(claim("The Cell divides rapidly", locs=(("unknown",),)), ctx(*self.D))
        assert r.verdict == "exact"

    def test_ellipsis_fragments_must_be_in_order(self):
        d = ctx("alpha beta gamma delta epsilon")
        ok = claim("alpha beta ... delta epsilon", fragments=["alpha beta", "delta epsilon"])
        bad = claim("delta epsilon ... alpha beta", fragments=["delta epsilon", "alpha beta"])
        assert vq.verify_claim(ok, d).verdict == "exact"
        assert vq.verify_claim(bad, d).verdict == "not_found"

    def test_several_locations_any_may_hold_the_quote(self):
        lines = ["x"] * 60 + ["the quoted passage is right here"]
        c = claim("the quoted passage is right here", locs=(("L", 1, 1), ("L", 61, 61)))
        assert vq.verify_claim(c, ctx(*lines)).verdict == "exact"


class TestFuzzyBranches:
    """The thresholds are exercised with a stubbed scorer so the bands are exact."""

    def stub(self, monkeypatch, cited, whole=None):
        calls = []

        def fake(doc, fragment, line_range):
            calls.append(line_range)
            return (cited, (2, 2), 10) if len(calls) == 1 or whole is None else (whole, (9, 9), 90)

        monkeypatch.setattr(vq, "fuzzy_best", fake)

    def run(self):
        return vq.verify_claim(claim("zzz qqq www vvv", locs=(("L", 2, 2),)), ctx("a", "b", "c"))

    def test_high_score_in_window_is_fuzzy(self, monkeypatch):
        self.stub(monkeypatch, 90)
        r = self.run()
        assert (r.verdict, r.score, r.matched_lines, r.severity) == ("fuzzy", 90, [2, 2], "warning")

    def test_high_score_only_elsewhere_is_wrong_location(self, monkeypatch):
        self.stub(monkeypatch, 40, whole=88)
        r = self.run()
        assert (r.verdict, r.score, r.matched_lines) == ("wrong_location", 88, [9, 9])

    def test_middle_band_is_near_miss_and_a_warning(self, monkeypatch):
        self.stub(monkeypatch, 72, whole=65)
        r = self.run()
        assert (r.verdict, r.score, r.severity) == ("near_miss", 72, "warning")

    def test_low_band_is_not_found_and_an_error(self, monkeypatch):
        self.stub(monkeypatch, 40, whole=30)
        r = self.run()
        assert (r.verdict, r.severity) == ("not_found", "error")

    def test_threshold_and_near_miss_are_configurable(self, monkeypatch):
        self.stub(monkeypatch, 70)
        r = vq.verify_claim(claim("zzz qqq www vvv", locs=(("L", 2, 2),)),
                            ctx("a", "b", "c", threshold=65, near_miss=50))
        assert r.verdict == "fuzzy"


class TestNotFound:
    def test_plain_not_found_is_an_error(self):
        r = vq.verify_claim(claim("completely invented sentence about nothing"), ctx("alpha", "beta"))
        assert (r.verdict, r.severity) == ("not_found", "error")

    def test_shared_citation_is_mentioned(self):
        r = vq.verify_claim(claim("completely invented sentence about nothing", shared=2), ctx("alpha", "beta"))
        assert "shares citation with 2 other quotes" in r.note

    def test_cross_language_only_after_a_failed_match(self):
        english = ctx("The cell divides rapidly in culture and the cell grows.")
        r = vq.verify_claim(claim("细胞在培养中分裂得非常迅速而且稳定", locs=(("unknown",),)), english)
        assert (r.verdict, r.severity, r.kind) == ("unverifiable", "suggestion", "cross_language")
        chinese = ctx("细胞在培养中分裂得非常迅速而且稳定，并且持续生长很长时间。")
        r2 = vq.verify_claim(claim("the cell divides rapidly in culture", locs=(("unknown",),)), chinese)
        assert r2.kind == "cross_language"

    def test_a_chinese_quote_that_matches_is_never_called_cross_language(self):
        english_with_chinese = ctx("English filler text. 细胞在培养中分裂得非常迅速而且稳定 more English.")
        r = vq.verify_claim(claim("细胞在培养中分裂得非常迅速而且稳定", locs=(("unknown",),)), english_with_chinese)
        assert r.verdict == "exact"

    def test_mixed_script_quotes_stay_not_found(self):
        english = ctx("Only English text about other things here.")
        few_cjk = vq.verify_claim(claim("GDP 增长 in Q2 report", locs=(("unknown",),)), english)
        assert few_cjk.verdict == "not_found"                       # fewer than 6 CJK letters: could be a term
        mixed_raw = ctx("English text with 一些中文 mixed in, 约占百分之三十 of the letters, and more中文内容在这里。")
        chinese = vq.verify_claim(claim("细胞在培养中分裂得非常迅速而且稳定", locs=(("unknown",),)), mixed_raw)
        assert chinese.verdict == "not_found"                       # the raw has real Chinese text, so it may be there

    def test_suggested_raw_when_the_quote_lives_in_another_file(self):
        other = doc("something", "the quoted passage is right here", "more")
        r = vq.verify_claim(claim("the quoted passage is right here"),
                            ctx("alpha", "beta", others=lambda: iter([("raw/b.md", other)])))
        assert (r.verdict, r.severity, r.suggested_raw) == ("not_found", "error", "raw/b.md")
        assert "found verbatim in raw/b.md" in r.note

    def test_unreadable_raw_is_unverifiable_infrastructure(self):
        r = vq.verify_claim(claim("any quote that is long enough"), vq.Context(doc=None, unreadable="raw file missing"))
        assert (r.verdict, r.severity, r.kind, r.note) == ("unverifiable", "suggestion", "infrastructure", "raw file missing")


class TestSeverityDowngrades:
    def not_found(self, **kw):
        return vq.verify_claim(claim("completely invented sentence about nothing", **kw.pop("claim_kw", {})),
                               ctx("alpha", "beta", **kw))

    def test_lossy_conversion_downgrades_to_warning(self):
        r = self.not_found(convert={"lossy": True})
        assert (r.severity, r.kind) == ("warning", "downgraded") and "lossy" in r.note

    def test_low_confidence_page_map_downgrades_only_page_citations(self):
        pages = {"confidence": "low", "pages": []}
        by_line = self.not_found(pages=pages)
        by_page = self.not_found(pages=pages, claim_kw={"locs": (("p", 1, 1),)})
        assert by_line.severity == "error" and by_page.severity == "warning"

    def test_strict_keeps_errors(self):
        r = self.not_found(convert={"lossy": True}, strict=True)
        assert r.severity == "error"

    def test_strict_promotes_near_miss(self, monkeypatch):
        monkeypatch.setattr(vq, "fuzzy_best", lambda d, f, r: (72, (1, 1), 0))
        c = claim("zzz qqq www vvv", locs=(("L", 1, 1),))
        assert vq.verify_claim(c, ctx("a", strict=True)).severity == "error"
        assert vq.verify_claim(c, ctx("a")).severity == "warning"
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run --with pytest python -m pytest tests/test_verify_quotes.py`

Expected: FAIL — `AttributeError: module 'verify_quotes' has no attribute 'Context'`

- [ ] **Step 3: Implement**

Append to `llm-wiki/scripts/verify_quotes.py`:

```python
@dataclass
class Result:
    verdict: str            # exact | fuzzy | wrong_location | near_miss | not_found | unverifiable
    severity: str           # none | suggestion | warning | error
    score: int = 0
    matched_lines: list[int] = field(default_factory=list)      # [first, last] or []
    note: str = ""
    suggested_raw: str | None = None
    kind: str = ""          # unverifiable: infrastructure | cross_language; not_found: downgraded


@dataclass
class Context:
    """Everything verify_claim needs to know about the cited raw file."""
    doc: Doc | None
    unreadable: str = ""
    convert: dict | None = None            # the raw's convert.json
    pages: dict | None = None              # the raw's pages.json
    others: object = None                  # callable returning (rel_path, Doc) pairs, or None
    threshold: int = THRESHOLD
    near_miss: int = NEAR_MISS
    strict: bool = False


def is_short(quote: str) -> bool:
    cjk = sum(1 for c in quote if is_cjk(c))
    if cjk >= 0.3 * max(len(quote), 1):
        return cjk < SHORT_CJK
    return len(quote.split()) <= SHORT_WORDS


def page_lines(pages_meta: dict, first_page: int, last_page: int) -> tuple[int, int] | None:
    rows = {p["page"]: p for p in pages_meta.get("pages", [])}
    if first_page not in rows or last_page not in rows:
        return None
    return rows[first_page]["line_start"], rows[last_page]["line_end"]


def windows_for(claim: Claim, ctx: Context) -> list[tuple[int, int]] | None:
    """Line ranges to search first, with SLACK. None means the whole file is the cited place."""
    ranges = []
    for loc in claim.locs:
        if loc[0] == "L":
            first, last = loc[1], loc[2]
        elif loc[0] == "p" and ctx.pages and ctx.pages.get("pages"):
            span = page_lines(ctx.pages, loc[1], loc[2])
            if span is None:
                return None
            first, last = span
        else:
            return None
        ranges.append((max(1, first - SLACK), last + SLACK))
    return ranges or None


def scripts_cannot_overlap(quote: str, raw_cjk_share: float) -> bool:
    quote_cjk = sum(1 for c in quote if is_cjk(c))
    if quote_cjk >= 6 and raw_cjk_share < 0.05:
        return True
    return quote_cjk == 0 and len(quote.split()) >= 3 and raw_cjk_share > 0.6


def _fuzzy_over(doc: Doc, fragments: list[str], ranges: list[tuple[int, int]]) -> tuple[int, list[int]]:
    best_per_fragment = []
    for frag in fragments:
        best: tuple[int, tuple[int, int] | None, int] = (0, None, -1)
        for rng in ranges:
            cand = fuzzy_best(doc, frag, rng)
            if cand[0] > best[0]:
                best = cand
        best_per_fragment.append(best)
    positions = [b[2] for b in best_per_fragment]
    if len(positions) > 1 and any(a >= b for a, b in zip(positions, positions[1:])):
        return 0, []                         # ellipsis fragments must appear in order
    scores = [b[0] for b in best_per_fragment]
    spans = [b[1] for b in best_per_fragment]
    found = [s for s in spans if s]
    lines = [min(s[0] for s in found), max(s[1] for s in found)] if found else []
    return (min(scores) if scores else 0), lines


def _apply_severity(result: Result, claim: Claim, ctx: Context) -> Result:
    if result.verdict == "not_found" and not ctx.strict:
        lossy = bool(ctx.convert and ctx.convert.get("lossy"))
        low_pages = bool(ctx.pages and ctx.pages.get("confidence") == "low"
                         and any(loc[0] == "p" for loc in claim.locs))
        if lossy or low_pages:
            result.severity = "warning"
            result.kind = "downgraded"
            reason = "raw is a lossy conversion" if lossy else "page map has low confidence"
            result.note = f"{result.note}; {reason}" if result.note else reason
    elif result.verdict == "near_miss" and ctx.strict:
        result.severity = "error"
    return result


def _not_found(claim: Claim, ctx: Context, best: int) -> Result:
    doc = ctx.doc
    if scripts_cannot_overlap(claim.quote, doc.cjk_share):
        return Result("unverifiable", "suggestion", best,
                      note="cross-language quote; cite verbatim or paraphrase", kind="cross_language")
    result = Result("not_found", "error", best)
    notes = []
    if claim.shared:
        notes.append(f"shares citation with {claim.shared} other quotes")
    if ctx.others:
        for rel, other in ctx.others():
            whole = [(1, max(other.n_lines, 1))]
            if any(match_fragments(other, claim.fragments, rung, whole) for rung in ("literal", "loose")):
                result.suggested_raw = rel
                notes.append(f"found verbatim in {rel}")
                break
    result.note = "; ".join(notes)
    return result


def verify_claim(claim: Claim, ctx: Context) -> Result:
    """Verdict for one quote. Scopes are tried cited-lines first, then the whole file; inside
    a scope the ladder is literal, case-folded, loose; only then fuzzy similarity."""
    if ctx.doc is None:
        return Result("unverifiable", "suggestion", note=ctx.unreadable or "raw file unavailable",
                      kind="infrastructure")
    doc, frags = ctx.doc, claim.fragments
    windows = windows_for(claim, ctx)
    whole = [(1, max(doc.n_lines, 1))]
    short = is_short(claim.quote)
    scopes = ([("cited", windows)] if windows else []) + [("file", whole)]
    for scope, ranges in scopes:
        in_cited_place = scope == "cited" or windows is None
        for rung in RUNGS:
            lines = match_fragments(doc, frags, rung, ranges)
            if lines is None:
                continue
            where = list(lines)
            if not in_cited_place:
                return Result("wrong_location", "warning", 100, where,
                              f"found outside the cited lines ({rung})")
            if rung == "literal":
                return Result("exact", "none", 100, where)
            if rung == "fold":
                if short:
                    return Result("fuzzy", "warning", 95, where, "case differs")
                return Result("exact", "none", 100, where, "case differs")
            return Result("exact", "none", 100, where, "loose")
    cited_score, cited_lines = _fuzzy_over(doc, frags, windows or whole)
    if cited_score >= ctx.threshold:
        return Result("fuzzy", "warning", cited_score, cited_lines, "wording differs slightly")
    file_score, file_lines = (cited_score, cited_lines) if not windows else _fuzzy_over(doc, frags, whole)
    if windows and file_score >= ctx.threshold:
        return Result("wrong_location", "warning", file_score, file_lines, "similar text outside the cited lines")
    best, best_lines = (cited_score, cited_lines) if cited_score >= file_score else (file_score, file_lines)
    if best >= ctx.near_miss:
        result = Result("near_miss", "warning", best, best_lines,
                        "wording differs; a paraphrase in quotation marks?")
    else:
        result = _not_found(claim, ctx, best)
    return _apply_severity(result, claim, ctx)
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `uv run --with pytest python -m pytest tests/test_verify_quotes.py`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add llm-wiki/scripts/verify_quotes.py tests/test_verify_quotes.py
git commit -m "feat(verify_quotes): verdict ladder with windows, near-miss, cross-language and severities"
```


### Task 5: Incremental cache

**Files:**
- Modify: `llm-wiki/scripts/verify_quotes.py`
- Modify: `tests/test_verify_quotes.py`

**Interfaces:**
- Consumes: `Claim`, `Result` (Tasks 2, 4); `textnorm.normalize` (Plan A)
- Produces: `RAW_SUFFIXES`; `format_locs(locs) -> str` (e.g. `L14-22, L40`, `p.2`, `location unknown`); `claim_key(claim) -> str` (sha256); `claim_id(claim) -> str` (first 8 hex); `algo_hash() -> str` (16 hex, sha256 of verify_quotes.py + textnorm.py + citations.py); `corpus_token(root) -> str`; `Cache(path | None)` with `entries`, `get(key, fingerprint, corpus) -> dict | None`, `put(key, fingerprint, corpus, result)`, `prune(keep)`, `save()`; `Cache(None)` is a no-op

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_verify_quotes.py`:

```python
"""Incremental cache: keyed by quote + place, valid only for the same evidence."""
import json
from dataclasses import asdict

import verify_quotes as vq


def cache_claim(quote="the quoted passage is right here", raw="raw/a.md", locs=(("L", 1, 1),)):
    return vq.Claim("wiki/x.md", 5, quote, vq.fragments_of(quote), raw, locs, 5)


FP = {"raw_sha256": "r1", "pages_sha256": "", "convert_sha256": "", "dropped_sha256": "",
      "threshold": 85, "near_miss": 60, "strict": False, "algo": "a1"}


class TestKey:
    def test_stable_and_sensitive_to_quote_place_and_raw(self):
        base = vq.claim_key(cache_claim())
        assert base == vq.claim_key(cache_claim())
        assert base == vq.claim_key(cache_claim("the  quoted passage\nis right here"))      # whitespace is normalized
        assert base != vq.claim_key(cache_claim("The quoted passage is right here"))         # case is not
        assert base != vq.claim_key(cache_claim(raw="raw/b.md"))
        assert base != vq.claim_key(cache_claim(locs=(("L", 2, 2),)))
        assert base != vq.claim_key(cache_claim("a different quoted passage entirely"))

    def test_short_id_is_eight_hex_digits(self):
        assert len(vq.claim_id(cache_claim())) == 8 and int(vq.claim_id(cache_claim()), 16) >= 0


class TestCache:
    def result(self, verdict="exact"):
        return vq.Result(verdict, "none" if verdict == "exact" else "error", 100, [3, 3], "n")

    def test_hit_and_miss_on_fingerprint(self, tmp_path):
        cache = vq.Cache(tmp_path / ".cache" / "vq.json")
        cache.put("k", FP, "corpus1", self.result())
        assert vq.Result(**cache.get("k", FP, "corpus1")) == self.result()
        assert cache.get("k", dict(FP, raw_sha256="r2"), "corpus1") is None
        assert cache.get("k", dict(FP, strict=True), "corpus1") is None
        assert cache.get("other", FP, "corpus1") is None

    def test_corpus_only_matters_for_not_found(self, tmp_path):
        cache = vq.Cache(tmp_path / "vq.json")
        cache.put("ok", FP, "c1", self.result("exact"))
        cache.put("nf", FP, "c1", self.result("not_found"))
        assert cache.get("ok", FP, "c2") is not None            # an exact verdict does not depend on other files
        assert cache.get("nf", FP, "c2") is None                # suggested_raw may have changed
        assert cache.get("nf", FP, "c1") is not None

    def test_save_load_roundtrip_and_atomic_file(self, tmp_path):
        path = tmp_path / ".cache" / "vq.json"
        cache = vq.Cache(path)
        cache.put("k", FP, "c1", self.result())
        cache.save()
        assert path.exists() and not list(path.parent.glob("*.tmp"))
        again = vq.Cache(path)
        assert again.get("k", FP, "c1") is not None

    def test_corrupt_or_foreign_file_is_ignored(self, tmp_path):
        path = tmp_path / "vq.json"
        path.write_text("{ not json", encoding="utf-8")
        assert vq.Cache(path).entries == {}
        path.write_text(json.dumps({"schema": "something-else", "entries": {"k": {}}}), encoding="utf-8")
        assert vq.Cache(path).entries == {}

    def test_prune_keeps_only_seen_keys(self, tmp_path):
        cache = vq.Cache(tmp_path / "vq.json")
        cache.put("a", FP, "c", self.result())
        cache.put("b", FP, "c", self.result())
        cache.prune({"a"})
        assert set(cache.entries) == {"a"}

    def test_disabled_cache_never_hits_or_writes(self, tmp_path):
        cache = vq.Cache(None)
        cache.put("k", FP, "c", self.result())
        assert cache.get("k", FP, "c") is None
        cache.save()                                          # no path: nothing happens, no error


def test_algo_hash_is_stable_and_short():
    assert vq.algo_hash() == vq.algo_hash() and len(vq.algo_hash()) == 16


def test_corpus_token_changes_when_raw_files_change(tmp_path):
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / "a.md").write_text("one", encoding="utf-8")
    first = vq.corpus_token(tmp_path)
    assert first == vq.corpus_token(tmp_path)
    (tmp_path / "raw" / "b.txt").write_text("two", encoding="utf-8")
    assert vq.corpus_token(tmp_path) != first
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run --with pytest python -m pytest tests/test_verify_quotes.py`

Expected: FAIL — `AttributeError: module 'verify_quotes' has no attribute 'claim_key'`

- [ ] **Step 3: Implement**

Append to `llm-wiki/scripts/verify_quotes.py`:

```python
CACHE_SCHEMA = "llm-wiki.verify-quotes-cache/1"
RAW_SUFFIXES = {".md", ".txt"}


def format_locs(locs: tuple) -> str:
    parts = []
    for loc in locs:
        if loc[0] == "L":
            parts.append(f"L{loc[1]}" if loc[1] == loc[2] else f"L{loc[1]}-{loc[2]}")
        elif loc[0] == "p":
            parts.append(f"p.{loc[1]}" if loc[1] == loc[2] else f"p.{loc[1]}-{loc[2]}")
        else:
            parts.append("location unknown")
    return ", ".join(parts)


def claim_key(claim: Claim) -> str:
    """Identity of a quote at a place: normalized quote, cited file and locations."""
    material = "|".join([textnorm.normalize(claim.quote), claim.raw, format_locs(claim.locs)])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def claim_id(claim: Claim) -> str:
    return claim_key(claim)[:8]


def algo_hash() -> str:
    """Fingerprint of the code that decides verdicts: a cache entry made by other code is stale."""
    here = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for name in ("verify_quotes.py", "textnorm.py", "citations.py"):
        h.update((here / name).read_bytes())
    return h.hexdigest()[:16]


def corpus_token(root: Path) -> str:
    """Changes whenever any raw .md/.txt file is added, removed or modified."""
    h = hashlib.sha256()
    raw = Path(root) / "raw"
    if raw.is_dir():
        for f in sorted(raw.rglob("*")):
            if f.is_file() and f.suffix.lower() in RAW_SUFFIXES:
                st = f.stat()
                h.update(f"{f.relative_to(root).as_posix()}|{st.st_size}|{st.st_mtime_ns}\n".encode())
    return h.hexdigest()[:16]


class Cache:
    """Verdicts keyed by quote + place. An entry is reused only for the same evidence:
    raw content, page map, conversion record, threshold, strictness and verdict code.
    A ``not_found`` entry also depends on the other raw files (they may hold the quote)."""

    def __init__(self, path: Path | None):
        self.path = Path(path) if path else None
        self.entries: dict = {}
        if self.path and self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if data.get("schema") == CACHE_SCHEMA and isinstance(data.get("entries"), dict):
                    self.entries = data["entries"]
            except (OSError, ValueError, AttributeError):
                self.entries = {}

    def get(self, key: str, fingerprint: dict, corpus: str) -> dict | None:
        if self.path is None:
            return None
        entry = self.entries.get(key)
        if not entry or entry.get("fp") != fingerprint:
            return None
        if entry.get("corpus") is not None and entry["corpus"] != corpus:
            return None
        return entry["result"]

    def put(self, key: str, fingerprint: dict, corpus: str, result: Result) -> None:
        if self.path is None:
            return
        self.entries[key] = {
            "fp": fingerprint,
            "corpus": corpus if result.verdict == "not_found" else None,
            "result": asdict(result),
        }

    def prune(self, keep: set) -> None:
        self.entries = {k: v for k, v in self.entries.items() if k in keep}

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"schema": CACHE_SCHEMA, "entries": self.entries}, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `uv run --with pytest python -m pytest tests/test_verify_quotes.py`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add llm-wiki/scripts/verify_quotes.py tests/test_verify_quotes.py
git commit -m "feat(verify_quotes): evidence-keyed verdict cache"
```


### Task 6: verify_wiki, the JSON report and the CLI

**Files:**
- Modify: `llm-wiki/scripts/verify_quotes.py`
- Modify: `tests/test_verify_quotes.py`

**Interfaces:**
- Consumes: everything from Tasks 1-5; `vault_config.in_private_zone` (Plan A Task 3)
- Produces: `UsageError`; `RawStore(root)` with `load(rel) -> (Doc | None, reason)`, `sidecars(rel) -> (convert, pages, convert_sha, pages_sha)`, `others(exclude)`; `wiki_pages(root, pages)`; `verify_wiki(root, pages=None, *, threshold, near_miss, strict, use_cache) -> dict` (report schema `llm-wiki.verify-quotes/1`: `generated_at`, `pages_checked`, `counts`, `errors`, `missing_raw_files`, `malformed`, `results[]` with `page line quote raw loc pdf_page verdict severity score matched_lines note suggested_raw`); `tally(counts, result)`; `print_summary(report, path, root)`; `main(argv) -> int` with exit codes 0 (no error), 1 (an error-severity result), 2 (usage); options `--page`, `--threshold`, `--report`, `--strict`, `--no-cache`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_verify_quotes.py`:

```python
"""verify_wiki, the JSON report, and the CLI."""
import hashlib
import json

import pytest

import verify_quotes as vq


def make_vault(tmp_path, pages=None, raw=None, claude="# Wiki\n"):
    (tmp_path / "wiki").mkdir(exist_ok=True)
    (tmp_path / "CLAUDE.md").write_text(claude, encoding="utf-8")
    for rel, text in (pages or {}).items():
        f = tmp_path / "wiki" / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
    for rel, text in (raw or {}).items():
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
    return tmp_path


def tree_hash(root):
    h = hashlib.sha256()
    for f in sorted(p for p in root.rglob("*") if p.is_file() and ".cache" not in p.parts and "outputs" not in p.parts):
        h.update(f.relative_to(root).as_posix().encode())
        h.update(f.read_bytes())
    return h.hexdigest()


RAW = "line one\nthe quoted passage is right here\nline three\n"
PAGE_OK = 'A claim: "the quoted passage is right here" (raw/articles/a.md, L2)\n'
PAGE_BAD = 'A claim: "an entirely invented sentence about nothing" (raw/articles/a.md, L2)\n'


class TestVerifyWiki:
    def test_exact_quote_gives_a_clean_report(self, tmp_path):
        root = make_vault(tmp_path, {"concepts/x.md": PAGE_OK}, {"raw/articles/a.md": RAW})
        report = vq.verify_wiki(root, use_cache=False)
        assert report["schema"] == "llm-wiki.verify-quotes/1" and report["pages_checked"] == 1
        assert report["counts"]["exact"] == 1 and report["errors"] == 0
        r = report["results"][0]
        assert (r["page"], r["line"], r["raw"], r["loc"], r["verdict"], r["severity"]) == (
            "wiki/concepts/x.md", 1, "raw/articles/a.md", "L2", "exact", "none")
        assert r["matched_lines"] == [2, 2] and r["quote"] == "the quoted passage is right here"
        assert len(r["id"]) == 8

    def test_fabricated_quote_is_an_error(self, tmp_path):
        root = make_vault(tmp_path, {"concepts/x.md": PAGE_BAD}, {"raw/articles/a.md": RAW})
        report = vq.verify_wiki(root, use_cache=False)
        assert report["counts"]["not_found_error"] == 1 and report["errors"] == 1

    def test_only_content_folders_are_scanned_and_conflict_copies_skipped(self, tmp_path):
        root = make_vault(tmp_path, {
            "concepts/x.md": PAGE_OK,
            "concepts/x.sync-conflict-20260101-ABC.md": PAGE_BAD,
            "templates/t.md": PAGE_BAD,
            "index.md": PAGE_BAD,
        }, {"raw/articles/a.md": RAW})
        report = vq.verify_wiki(root, use_cache=False)
        assert report["pages_checked"] == 1 and report["errors"] == 0

    def test_pages_argument_restricts_the_run(self, tmp_path):
        root = make_vault(tmp_path, {"concepts/x.md": PAGE_OK, "concepts/y.md": PAGE_BAD}, {"raw/articles/a.md": RAW})
        report = vq.verify_wiki(root, pages=["wiki/concepts/x.md"], use_cache=False)
        assert report["pages_checked"] == 1 and report["errors"] == 0
        with pytest.raises(vq.UsageError):
            vq.verify_wiki(root, pages=["wiki/concepts/missing.md"], use_cache=False)

    def test_missing_pdf_and_private_raw_are_unverifiable(self, tmp_path):
        page = (
            'Gone: "a quote whose file is gone" (raw/articles/gone.md, L1)\n'
            'Pdf: "a quote cited to a pdf file" (raw/pdfs/p.pdf, L1)\n'
            'Secret: "a quote inside the private zone" (raw/secret/s.md, L1)\n'
        )
        claude = "# Wiki\n\n## Private zones\n\n- `raw/secret/`\n"
        root = make_vault(tmp_path, {"concepts/x.md": page}, {"raw/secret/s.md": "a quote inside the private zone\n"}, claude)
        (root / "raw" / "pdfs").mkdir()
        (root / "raw" / "pdfs" / "p.pdf").write_bytes(b"%PDF")
        report = vq.verify_wiki(root, use_cache=False)
        assert report["counts"]["unverifiable_infrastructure"] == 3 and report["errors"] == 0
        notes = " | ".join(r["note"] for r in report["results"])
        assert "missing" in notes and "PDF" in notes and "private zone" in notes
        assert report["missing_raw_files"] == [{"raw": "raw/articles/gone.md", "citations": 1}]

    def test_uncited_and_malformed_are_counted(self, tmp_path):
        page = 'No cite: "a quote with no citation at all"\n\nBroken (raw/articles/oops\n'
        root = make_vault(tmp_path, {"concepts/x.md": page}, {})
        report = vq.verify_wiki(root, use_cache=False)
        assert report["counts"]["uncited"] == 1 and report["counts"]["malformed_citations"] == 1
        assert report["malformed"][0]["fragment"].startswith("(raw/articles/oops")

    def test_wrong_location_and_suggested_raw(self, tmp_path):
        raw = {"raw/articles/a.md": "x\n" * 30 + "the quoted passage is right here\n",
               "raw/articles/b.md": "an entirely invented sentence about nothing\n"}
        page = PAGE_OK + PAGE_BAD
        root = make_vault(tmp_path, {"concepts/x.md": page}, raw)
        report = vq.verify_wiki(root, use_cache=False)
        by_quote = {r["quote"]: r for r in report["results"]}
        assert by_quote["the quoted passage is right here"]["verdict"] == "wrong_location"
        bad = by_quote["an entirely invented sentence about nothing"]
        assert bad["verdict"] == "not_found" and bad["suggested_raw"] == "raw/articles/b.md"

    def test_lossy_conversion_downgrades_and_strict_restores(self, tmp_path):
        raw = {"raw/converted/doc/doc.md": "something else entirely\n",
               "raw/converted/doc/doc.convert.json": json.dumps({"lossy": True})}
        page = 'Q: "an entirely invented sentence about nothing" (raw/converted/doc/doc.md, L1)\n'
        root = make_vault(tmp_path, {"concepts/x.md": page}, raw)
        soft = vq.verify_wiki(root, use_cache=False)
        assert soft["counts"]["not_found_warning"] == 1 and soft["errors"] == 0
        strict = vq.verify_wiki(root, use_cache=False, strict=True)
        assert strict["counts"]["not_found_error"] == 1 and strict["errors"] == 1

    def test_page_citation_uses_pages_json(self, tmp_path):
        pages = {"confidence": "high", "pages": [
            {"page": 1, "line_start": 1, "line_end": 40, "how": "aligned"},
            {"page": 2, "line_start": 41, "line_end": 80, "how": "aligned"}]}
        raw = {"raw/converted/doc/doc.md": "x\n" * 60 + "the quoted passage is right here\n",
               "raw/converted/doc/doc.pages.json": json.dumps(pages)}
        page = 'Q: "the quoted passage is right here" (raw/converted/doc/doc.md, p.2)\n'
        root = make_vault(tmp_path, {"concepts/x.md": page}, raw)
        report = vq.verify_wiki(root, use_cache=False)
        assert report["counts"]["exact"] == 1


class TestCacheIntegration:
    def test_second_run_is_served_from_the_cache_until_the_raw_changes(self, tmp_path):
        root = make_vault(tmp_path, {"concepts/x.md": PAGE_OK}, {"raw/articles/a.md": RAW})
        first = vq.verify_wiki(root)
        assert first["counts"]["from_cache"] == 0 and (root / ".cache" / "verify_quotes.json").exists()
        second = vq.verify_wiki(root)
        assert second["counts"]["from_cache"] == 1 and second["counts"]["exact"] == 1
        (root / "raw" / "articles" / "a.md").write_text("changed completely\n", encoding="utf-8")
        third = vq.verify_wiki(root)
        assert third["counts"]["from_cache"] == 0 and third["errors"] == 1

    def test_threshold_change_invalidates(self, tmp_path):
        root = make_vault(tmp_path, {"concepts/x.md": PAGE_OK}, {"raw/articles/a.md": RAW})
        vq.verify_wiki(root, threshold=85)
        assert vq.verify_wiki(root, threshold=90)["counts"]["from_cache"] == 0

    def test_no_cache_option_writes_nothing(self, tmp_path):
        root = make_vault(tmp_path, {"concepts/x.md": PAGE_OK}, {"raw/articles/a.md": RAW})
        vq.verify_wiki(root, use_cache=False)
        assert not (root / ".cache").exists()


class TestCli:
    def test_clean_run_exits_0_and_writes_the_report_without_touching_the_vault(self, tmp_path, capsys):
        root = make_vault(tmp_path, {"concepts/x.md": PAGE_OK}, {"raw/articles/a.md": RAW})
        before = tree_hash(root)
        assert vq.main([str(root)]) == 0
        out = capsys.readouterr().out
        assert "exact 1" in out and "Report:" in out
        reports = list((root / "outputs").glob("verify-quotes-*.json"))
        assert len(reports) == 1 and json.loads(reports[0].read_text())["counts"]["exact"] == 1
        assert tree_hash(root) == before

    def test_fabricated_quote_exits_1_and_is_listed(self, tmp_path, capsys):
        root = make_vault(tmp_path, {"concepts/x.md": PAGE_BAD}, {"raw/articles/a.md": RAW})
        before = tree_hash(root)
        assert vq.main([str(root), "--no-cache"]) == 1
        assert "wiki/concepts/x.md:1" in capsys.readouterr().out
        assert tree_hash(root) == before

    def test_warnings_only_exit_0(self, tmp_path):
        raw = {"raw/articles/a.md": "x\n" * 30 + "the quoted passage is right here\n"}
        root = make_vault(tmp_path, {"concepts/x.md": PAGE_OK}, raw)
        assert vq.main([str(root), "--no-cache"]) == 0

    def test_usage_errors_exit_2(self, tmp_path, capsys):
        assert vq.main([str(tmp_path / "nope")]) == 2
        root = make_vault(tmp_path, {"concepts/x.md": PAGE_OK}, {"raw/articles/a.md": RAW})
        assert vq.main([str(root), "--page", "wiki/concepts/missing.md"]) == 2
        with pytest.raises(SystemExit) as exc:
            vq.main([str(root), "--threshold", "abc"])
        assert exc.value.code == 2

    def test_report_path_page_and_threshold_options(self, tmp_path):
        root = make_vault(tmp_path, {"concepts/x.md": PAGE_OK, "concepts/y.md": PAGE_BAD}, {"raw/articles/a.md": RAW})
        target = tmp_path / "custom" / "r.json"
        assert vq.main([str(root), "--page", "wiki/concepts/x.md", "--report", str(target), "--no-cache"]) == 0
        assert json.loads(target.read_text())["pages_checked"] == 1
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run --with pytest python -m pytest tests/test_verify_quotes.py`

Expected: FAIL — `AttributeError: module 'verify_quotes' has no attribute 'verify_wiki'`

- [ ] **Step 3: Implement**

Append to `llm-wiki/scripts/verify_quotes.py`:

```python
WIKI_DIRS = ("concepts", "sources", "entities", "comparisons", "synthesis")
OTHER_RAW_MAX_FILES = 200
OTHER_RAW_MAX_BYTES = 20_000_000
REPORT_SCHEMA = "llm-wiki.verify-quotes/1"


class UsageError(Exception):
    """Bad arguments or an unusable vault (exit code 2)."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class RawStore:
    """Loads cited raw files once; refuses private zones, PDFs and anything outside the vault."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self._docs: dict[str, tuple[Doc | None, str]] = {}
        self._meta: dict[str, tuple[dict | None, dict | None, str, str]] = {}

    def load(self, rel: str) -> tuple[Doc | None, str]:
        if rel in self._docs:
            return self._docs[rel]
        path = (self.root / rel).resolve()
        suffix = path.suffix.lower()
        if self.root not in path.parents:
            result: tuple[Doc | None, str] = (None, "path is outside the vault")
        elif vault_config.in_private_zone(self.root, path):
            result = (None, "raw file is inside a private zone (not read)")
        elif suffix == ".pdf":
            result = (None, "cited file is a PDF; cite the converted markdown under raw/converted/")
        elif suffix not in RAW_SUFFIXES:
            result = (None, f"unsupported raw file type {suffix or '(none)'}")
        elif not path.is_file():
            result = (None, "raw file missing")
        else:
            result = (Doc(path.read_text(encoding="utf-8", errors="replace")), "")
        self._docs[rel] = result
        return result

    def sidecars(self, rel: str) -> tuple[dict | None, dict | None, str, str]:
        """``(convert.json, pages.json, convert_sha, pages_sha)`` next to a converted markdown file."""
        if rel in self._meta:
            return self._meta[rel]
        path = (self.root / rel)
        convert = pages = None
        convert_sha = pages_sha = ""
        if "converted" in path.parts:
            for suffix, slot in ((".convert.json", "convert"), (".pages.json", "pages")):
                f = path.with_name(path.stem + suffix)
                if f.is_file():
                    data = f.read_bytes()
                    try:
                        parsed = json.loads(data)
                    except ValueError:
                        parsed = None
                    if slot == "convert":
                        convert, convert_sha = parsed, sha256_bytes(data)
                    else:
                        pages, pages_sha = parsed, sha256_bytes(data)
        self._meta[rel] = (convert, pages, convert_sha, pages_sha)
        return self._meta[rel]

    def others(self, exclude: str):
        """Other raw documents, for finding a quote cited to the wrong file (capped)."""
        files = size = 0
        for f in sorted((self.root / "raw").rglob("*")):
            if not f.is_file() or f.suffix.lower() not in RAW_SUFFIXES:
                continue
            rel = f.relative_to(self.root).as_posix()
            if rel == exclude:
                continue
            size += f.stat().st_size
            files += 1
            if files > OTHER_RAW_MAX_FILES or size > OTHER_RAW_MAX_BYTES:
                return
            doc, _ = self.load(rel)
            if doc is not None:
                yield rel, doc


def wiki_pages(root: Path, pages: list[str] | None) -> list[Path]:
    wiki = root / "wiki"
    if pages:
        chosen = []
        for p in pages:
            path = Path(p) if Path(p).is_absolute() else root / p
            if not path.is_file():
                raise UsageError(f"page not found: {p}")
            chosen.append(path.resolve())
        return sorted(set(chosen))
    found = []
    for d in WIKI_DIRS:
        base = wiki / d
        if base.is_dir():
            found.extend(f for f in base.rglob("*.md") if "sync-conflict" not in f.name)
    return sorted(found)


def verify_wiki(root, pages: list[str] | None = None, *, threshold: int = THRESHOLD,
                near_miss: int = NEAR_MISS, strict: bool = False, use_cache: bool = True) -> dict:
    """Check every quote in the wiki pages and return the report as a dict."""
    root = Path(root).resolve()
    if not (root / "wiki").is_dir():
        raise UsageError(f"wiki/ directory not found at {root / 'wiki'}")
    store = RawStore(root)
    cache = Cache(root / ".cache" / "verify_quotes.json" if use_cache else None)
    algo, corpus = algo_hash(), corpus_token(root)
    files = wiki_pages(root, pages)
    counts: Counter = Counter()
    results, malformed, seen = [], [], set()
    missing: Counter = Counter()
    for f in files:
        page = f.relative_to(root).as_posix()
        extracted = extract_claims(f.read_text(encoding="utf-8", errors="replace"), page)
        counts["uncited"] += extracted.uncited
        counts["too_short"] += extracted.too_short
        for p, line_no, fragment in extracted.malformed:
            malformed.append({"page": p, "line": line_no, "fragment": fragment})
        for claim in extracted.claims:
            doc, reason = store.load(claim.raw)
            convert, pages_meta, convert_sha, pages_sha = store.sidecars(claim.raw)
            if reason == "raw file missing":
                missing[claim.raw] += 1
            key = claim_key(claim)
            seen.add(key)
            fingerprint = {
                "raw_sha256": sha256_bytes(doc.text.encode("utf-8")) if doc else reason,
                "pages_sha256": pages_sha, "convert_sha256": convert_sha, "dropped_sha256": "",
                "threshold": threshold, "near_miss": near_miss, "strict": strict, "algo": algo,
            }
            cached = cache.get(key, fingerprint, corpus)
            if cached is not None:
                result = Result(**cached)
                counts["from_cache"] += 1
            else:
                ctx = Context(doc, reason, convert, pages_meta,
                              others=lambda rel=claim.raw: store.others(rel),
                              threshold=threshold, near_miss=near_miss, strict=strict)
                result = verify_claim(claim, ctx)
                cache.put(key, fingerprint, corpus, result)
            tally(counts, result)
            results.append({
                "id": claim_id(claim), "page": page, "line": claim.line_no, "quote": claim.quote, "raw": claim.raw,
                "loc": format_locs(claim.locs), "pdf_page": None, "verdict": result.verdict,
                "severity": result.severity, "score": result.score,
                "matched_lines": result.matched_lines, "note": result.note,
                "suggested_raw": result.suggested_raw,
            })
    counts["malformed_citations"] = len(malformed)
    if pages is None:
        cache.prune(seen)
    cache.save()
    names = ("exact", "fuzzy", "wrong_location", "near_miss", "not_found_error", "not_found_warning",
             "unverifiable_infrastructure", "unverifiable_cross_language", "uncited",
             "malformed_citations", "from_cache", "too_short")
    return {
        "schema": REPORT_SCHEMA,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pages_checked": len(files),
        "counts": {n: counts.get(n, 0) for n in names},
        "errors": sum(1 for r in results if r["severity"] == "error"),
        "missing_raw_files": [{"raw": r, "citations": n} for r, n in missing.most_common()],
        "malformed": malformed,
        "results": results,
    }


def tally(counts: Counter, result: Result) -> None:
    if result.verdict == "not_found":
        counts["not_found_error" if result.severity == "error" else "not_found_warning"] += 1
    elif result.verdict == "unverifiable":
        counts["unverifiable_cross_language" if result.kind == "cross_language" else "unverifiable_infrastructure"] += 1
    else:
        counts[result.verdict] += 1


def print_summary(report: dict, report_path: Path | None, root: Path) -> None:
    c = report["counts"]
    checked = sum(c[n] for n in ("exact", "fuzzy", "wrong_location", "near_miss", "not_found_error",
                                 "not_found_warning", "unverifiable_infrastructure", "unverifiable_cross_language"))
    print(f"verify_quotes: {report['pages_checked']} pages, {checked} cited quotes")
    print(f"  exact {c['exact']}  fuzzy {c['fuzzy']}  wrong_location {c['wrong_location']}  near_miss {c['near_miss']}  "
          f"not_found {c['not_found_error']} (+{c['not_found_warning']} downgraded)")
    print(f"  unverifiable {c['unverifiable_infrastructure']} infrastructure, {c['unverifiable_cross_language']} cross-language"
          f"  uncited {c['uncited']}  malformed citations {c['malformed_citations']}  from cache {c['from_cache']}")
    if report["missing_raw_files"]:
        total = sum(m["citations"] for m in report["missing_raw_files"])
        print(f"  missing raw files: {len(report['missing_raw_files'])} ({total} citations)")
    for r in [r for r in report["results"] if r["severity"] == "error"][:10]:
        print(f"  ERROR {r['page']}:{r['line']} quote not found in {r['raw']} ({r['loc']}): \"{r['quote'][:60]}\"")
    if report["errors"] > 10:
        print(f"  ... and {report['errors'] - 10} more errors; see the report")
    if report_path:
        print(f"Report: {report_path.relative_to(root) if root in report_path.parents else report_path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify quoted passages in wiki pages against the raw files they cite.")
    parser.add_argument("wiki_root", help="Path to the wiki root directory")
    parser.add_argument("--page", action="append", default=[], help="Only check this wiki file (repeatable)")
    parser.add_argument("--threshold", type=int, default=THRESHOLD, help=f"Fuzzy similarity that counts as a match (default {THRESHOLD})")
    parser.add_argument("--report", default=None, help="Report path (default outputs/verify-quotes-YYYY-MM-DD.json)")
    parser.add_argument("--strict", action="store_true", help="Keep downgraded not_found and near_miss results as errors")
    parser.add_argument("--no-cache", action="store_true", help="Do not read or write .cache/verify_quotes.json")
    args = parser.parse_args(argv)
    root = Path(args.wiki_root).resolve()
    try:
        report = verify_wiki(root, args.page or None, threshold=args.threshold,
                             strict=args.strict, use_cache=not args.no_cache)
    except UsageError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    path = Path(args.report) if args.report else root / "outputs" / f"verify-quotes-{datetime.now().strftime('%Y-%m-%d')}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print_summary(report, path, root)
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `uv run --with pytest python -m pytest tests/test_verify_quotes.py`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add llm-wiki/scripts/verify_quotes.py tests/test_verify_quotes.py
git commit -m "feat(verify_quotes): verify_wiki, JSON report and CLI"
```


### Task 7: cascade_flag refactor and verify_quotes --flag

**Files:**
- Modify: `llm-wiki/scripts/cascade_flag.py`
- Modify: `llm-wiki/scripts/verify_quotes.py`
- Create: `tests/test_cascade_flag.py`
- Modify: `tests/test_verify_quotes.py`

**Interfaces:**
- Consumes: `citations.find_citations` (Task 1); `verify_wiki` report entries with `id`, `page`, `line`, `raw`, `loc`, `suggested_raw`, `verdict`, `severity` (Task 6); `audit_review.scan_fix_comments` (existing)
- Produces: `cascade_flag.insert_fix_comments(wiki_file, edits: list[(line_index, tag, marker)], dry_run=False) -> int` (bottom-up, idempotent by marker); `cascade_flag.FIX_PREFIX`; `cascade_flag.flag_citations_in_file` now built on it and on `citations.find_citations` (the `CITATION_LINE_RE` constant is removed); `verify_quotes.flag_report(root, report, dry_run) -> int`; CLI `--flag` and `--dry-run` (usage error without `--flag`); comment form `<!-- FIX: quote not found [vq:<id>] in <raw> <loc>[; found in <suggested_raw>] -->`

- [ ] **Step 1: Write the regression and inserter tests**

Create `tests/test_cascade_flag.py`:

```python
"""Shared FIX-comment inserter; behaviour of cascade_flag.py must not regress."""
import re

import cascade_flag


def legacy_flag(text, modified):
    """Verbatim copy of the pre-refactor algorithm, kept to prove the outputs stay identical."""
    citation_line_re = re.compile(r"\((raw/[^,)]+),")
    lines = text.splitlines(keepends=True)
    new_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        match = citation_line_re.search(line)
        if match and match.group(1).strip() in modified:
            next_line = lines[i + 1] if i + 1 < len(lines) else ""
            if cascade_flag.FIX_TAG not in next_line and cascade_flag.FIX_TAG not in line:
                new_lines.append(line)
                eol = "\n" if not line.endswith("\n") else ""
                new_lines.append(cascade_flag.FIX_TAG + eol + "\n")
                i += 1
                continue
        new_lines.append(line)
        i += 1
    return "".join(new_lines)


PAGE = (
    "---\ntype: concept\n---\n\n"
    "- Claim one (raw/articles/a.md, L3)\n"
    "- Claim two (raw/articles/b.md, L9)\n"
    "- Claim three (raw/articles/a.md, L11-L13)\n"
    "\nplain line\n"
)


def run_new(tmp_path, text, modified, dry_run=False):
    f = tmp_path / "page.md"
    f.write_text(text, encoding="utf-8")
    added = cascade_flag.flag_citations_in_file(f, modified, dry_run=dry_run)
    return added, f.read_text(encoding="utf-8")


def test_output_is_byte_identical_to_the_old_algorithm(tmp_path):
    added, new_text = run_new(tmp_path, PAGE, {"raw/articles/a.md"})
    assert added == 2 and new_text == legacy_flag(PAGE, {"raw/articles/a.md"})


def test_second_run_adds_nothing(tmp_path):
    _, once = run_new(tmp_path, PAGE, {"raw/articles/a.md"})
    added, twice = run_new(tmp_path, once, {"raw/articles/a.md"})
    assert added == 0 and twice == once


def test_dry_run_changes_nothing(tmp_path):
    added, text = run_new(tmp_path, PAGE, {"raw/articles/a.md"}, dry_run=True)
    assert added == 2 and text == PAGE


def test_citations_the_old_regex_missed_are_now_flagged(tmp_path):
    page = "Two sources (raw/articles/a.md, L1; raw/articles/b.md, L2)\n"
    added, text = run_new(tmp_path, page, {"raw/articles/b.md"})
    assert added == 1 and text.endswith(cascade_flag.FIX_TAG + "\n")


def test_last_line_without_newline_no_longer_glues_the_tag(tmp_path):
    page = "Claim (raw/articles/a.md, L3)"
    _, text = run_new(tmp_path, page, {"raw/articles/a.md"})
    assert text == "Claim (raw/articles/a.md, L3)\n" + cascade_flag.FIX_TAG + "\n"


class TestInsertFixComments:
    def test_several_edits_land_on_the_right_lines(self, tmp_path):
        f = tmp_path / "p.md"
        f.write_text("a\nb\nc\nd\n", encoding="utf-8")
        n = cascade_flag.insert_fix_comments(f, [(0, "<!-- FIX: one [m1] -->", "[m1]"), (2, "<!-- FIX: two [m2] -->", "[m2]")])
        assert n == 2
        assert f.read_text() == "a\n<!-- FIX: one [m1] -->\nb\nc\n<!-- FIX: two [m2] -->\nd\n"

    def test_marker_in_following_fix_comments_prevents_duplicates(self, tmp_path):
        f = tmp_path / "p.md"
        f.write_text("a\n<!-- FIX: other -->\n<!-- FIX: one [m1] -->\nb\n", encoding="utf-8")
        assert cascade_flag.insert_fix_comments(f, [(0, "<!-- FIX: one [m1] -->", "[m1]")]) == 0

    def test_unrelated_following_line_does_not_count(self, tmp_path):
        f = tmp_path / "p.md"
        f.write_text("a\nb [m1]\n", encoding="utf-8")
        assert cascade_flag.insert_fix_comments(f, [(0, "<!-- FIX: one [m1] -->", "[m1]")]) == 1

    def test_out_of_range_index_is_ignored(self, tmp_path):
        f = tmp_path / "p.md"
        f.write_text("a\n", encoding="utf-8")
        assert cascade_flag.insert_fix_comments(f, [(9, "<!-- FIX: x -->", "[x]")]) == 0
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run --with pytest python -m pytest tests/test_cascade_flag.py`

Expected: FAIL — `AttributeError: module 'cascade_flag' has no attribute 'insert_fix_comments'` (the byte-identical test already passes against the old code and must keep passing)

- [ ] **Step 3: Import the shared grammar; replace the old regex constant**

In `llm-wiki/scripts/cascade_flag.py`, replace:

```python
from pathlib import Path


CITATION_LINE_RE = re.compile(r"\((raw/[^,)]+),")
FIX_TAG = "<!-- FIX: raw source changed (sha256 mismatch) — re-verify this claim -->"
```

with:

```python
from pathlib import Path

import citations

FIX_PREFIX = "<!-- FIX:"
FIX_TAG = "<!-- FIX: raw source changed (sha256 mismatch) — re-verify this claim -->"
```

- [ ] **Step 4: Add insert_fix_comments and rebuild flag_citations_in_file on it**

In `llm-wiki/scripts/cascade_flag.py`, replace:

```python
def flag_citations_in_file(wiki_file: Path, modified_sources: set[str],
                            dry_run: bool = False) -> int:
    """Add FIX comments to lines that cite a modified source. Returns count added."""
    try:
        original = wiki_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0

    lines = original.splitlines(keepends=True)
    new_lines: list[str] = []
    added = 0

    i = 0
    while i < len(lines):
        line = lines[i]
        # Check if this line cites a raw file that has changed
        match = CITATION_LINE_RE.search(line)
        if match:
            cited_raw = match.group(1).strip()  # e.g. "raw/articles/bert-paper.md"
            if cited_raw in modified_sources:
                # Check that we haven't already added a FIX comment on the next line
                next_line = lines[i + 1] if i + 1 < len(lines) else ""
                if FIX_TAG not in next_line and FIX_TAG not in line:
                    new_lines.append(line)
                    eol = "\n" if not line.endswith("\n") else ""
                    new_lines.append(FIX_TAG + eol + "\n")
                    added += 1
                    i += 1
                    continue
        new_lines.append(line)
        i += 1

    if added > 0 and not dry_run:
        wiki_file.write_text("".join(new_lines), encoding="utf-8")

    return added
```

with:

```python
def insert_fix_comments(wiki_file: Path, edits: list[tuple[int, str, str]],
                        dry_run: bool = False) -> int:
    """Insert FIX comments. ``edits`` holds ``(line_index, tag, marker)``: ``tag`` goes on the line
    after ``line_index`` (0-based), unless ``marker`` already appears on that line or in the FIX
    comments directly below it, so running twice adds nothing. Edits are applied from the last line
    to the first, so earlier indices stay valid. Returns the number of comments added."""
    try:
        original = wiki_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    lines = re.split(r"(?<=\n)", original)          # keeps each "\n"; indices match text.split("\n")
    added = 0
    for idx, tag, marker in sorted(edits, key=lambda e: -e[0]):
        if idx >= len(lines) or marker in lines[idx]:
            continue
        j = idx + 1
        duplicate = False
        while j < len(lines) and lines[j].lstrip().startswith(FIX_PREFIX):
            if marker in lines[j]:
                duplicate = True
                break
            j += 1
        if duplicate:
            continue
        if not lines[idx].endswith("\n"):
            lines[idx] += "\n"
        lines.insert(idx + 1, tag + "\n")
        added += 1
    if added and not dry_run:
        wiki_file.write_text("".join(lines), encoding="utf-8")
    return added


def flag_citations_in_file(wiki_file: Path, modified_sources: set[str],
                            dry_run: bool = False) -> int:
    """Add a FIX comment under each line that cites a modified source. Returns count added."""
    try:
        original = wiki_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    edits = []
    for i, line in enumerate(original.split("\n")):
        if any(c.raw in modified_sources for c in citations.find_citations(line)):
            edits.append((i, FIX_TAG, FIX_TAG))
    return insert_fix_comments(wiki_file, edits, dry_run)
```

- [ ] **Step 5: Run the tests and verify they pass**

Run: `uv run --with pytest python -m pytest tests/test_cascade_flag.py`

Expected: PASS

- [ ] **Step 6: Write the --flag tests**

Append to `tests/test_verify_quotes.py`:

```python
"""verify_quotes --flag: one FIX comment per error-severity not_found, idempotent."""
import audit_review


def flag_vault(tmp_path, page, raw=None):
    return make_vault(tmp_path, {"concepts/x.md": page}, raw or {"raw/articles/a.md": RAW})


def page_text(root):
    return (root / "wiki" / "concepts" / "x.md").read_text(encoding="utf-8")


class TestFlag:
    def test_inserts_one_comment_under_the_quote_line_and_is_idempotent(self, tmp_path, capsys):
        root = flag_vault(tmp_path, "intro\n" + PAGE_BAD + "outro\n")
        assert vq.main([str(root), "--flag", "--no-cache"]) == 1
        lines = page_text(root).split("\n")
        assert lines[1].startswith("A claim:") and lines[2].startswith("<!-- FIX: quote not found [vq:")
        assert "in raw/articles/a.md L2 -->" in lines[2] and lines[3] == "outro"
        before = page_text(root)
        assert vq.main([str(root), "--flag", "--no-cache"]) == 1
        assert page_text(root) == before
        assert "FIX comments added: 0" in capsys.readouterr().out

    def test_rewording_around_the_quote_does_not_duplicate(self, tmp_path):
        root = flag_vault(tmp_path, PAGE_BAD)
        vq.main([str(root), "--flag", "--no-cache"])
        text = page_text(root).replace("A claim:", "A different claim:")
        (root / "wiki" / "concepts" / "x.md").write_text(text, encoding="utf-8")
        vq.main([str(root), "--flag", "--no-cache"])
        assert page_text(root).count("<!-- FIX: quote not found") == 1

    def test_several_errors_in_one_file_each_get_their_own_comment(self, tmp_path):
        page = (
            'First: "an entirely invented sentence about nothing" (raw/articles/a.md, L2)\n'
            "middle\n"
            'Second: "another fabricated statement of no origin" (raw/articles/a.md, L2)\n'
        )
        root = flag_vault(tmp_path, page)
        vq.main([str(root), "--flag", "--no-cache"])
        lines = page_text(root).split("\n")
        assert lines[0].startswith("First:") and lines[1].startswith("<!-- FIX")
        assert lines[2] == "middle"
        assert lines[3].startswith("Second:") and lines[4].startswith("<!-- FIX")

    def test_only_error_not_found_is_flagged(self, tmp_path):
        raw = {"raw/articles/a.md": "x\n" * 30 + "the quoted passage is right here\n",      # wrong_location
               "raw/converted/d/d.md": "something else\n",
               "raw/converted/d/d.convert.json": '{"lossy": true}'}                         # downgraded not_found
        page = (PAGE_OK +
                'Lossy: "an entirely invented sentence about nothing" (raw/converted/d/d.md, L1)\n')
        root = flag_vault(tmp_path, page, raw)
        assert vq.main([str(root), "--flag", "--no-cache"]) == 0
        assert "FIX" not in page_text(root)

    def test_suggested_file_is_part_of_the_comment(self, tmp_path):
        raw = {"raw/articles/a.md": RAW, "raw/articles/b.md": "an entirely invented sentence about nothing\n"}
        root = flag_vault(tmp_path, PAGE_BAD, raw)
        vq.main([str(root), "--flag", "--no-cache"])
        assert "found in raw/articles/b.md" in page_text(root)

    def test_dry_run_prints_and_changes_nothing(self, tmp_path, capsys):
        root = flag_vault(tmp_path, PAGE_BAD)
        before = page_text(root)
        assert vq.main([str(root), "--flag", "--dry-run", "--no-cache"]) == 1
        assert page_text(root) == before and "would add" in capsys.readouterr().out

    def test_dry_run_alone_is_a_usage_error(self, tmp_path):
        import pytest
        root = flag_vault(tmp_path, PAGE_OK)
        with pytest.raises(SystemExit) as exc:
            vq.main([str(root), "--dry-run"])
        assert exc.value.code == 2

    def test_audit_review_picks_the_comment_up(self, tmp_path):
        root = flag_vault(tmp_path, PAGE_BAD)
        vq.main([str(root), "--flag", "--no-cache"])
        entries = audit_review.scan_fix_comments(root)
        assert len(entries) == 1 and "quote not found" in str(entries[0])
```

- [ ] **Step 7: Run the tests and verify they fail**

Run: `uv run --with pytest python -m pytest tests/test_verify_quotes.py`

Expected: FAIL — `unrecognized arguments: --flag`

- [ ] **Step 8: Add flag_report above main()**

In `llm-wiki/scripts/verify_quotes.py`, replace:

```python
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify quoted passages in wiki pages against the raw files they cite.")
```

with:

```python
def flag_report(root: Path, report: dict, dry_run: bool) -> int:
    """Insert one FIX comment under each quote whose result is an error-severity not_found."""
    import cascade_flag

    edits: dict[str, list] = defaultdict(list)
    for r in report["results"]:
        if r["verdict"] != "not_found" or r["severity"] != "error":
            continue
        marker = f"[vq:{r['id']}]"
        tag = f"<!-- FIX: quote not found {marker} in {r['raw']} {r['loc']}"
        if r["suggested_raw"]:
            tag += f"; found in {r['suggested_raw']}"
        edits[r["page"]].append((r["line"] - 1, tag + " -->", marker))
    total = 0
    for page, page_edits in sorted(edits.items()):
        added = cascade_flag.insert_fix_comments(root / page, page_edits, dry_run=dry_run)
        total += added
        if added:
            print(f"  {'would add' if dry_run else 'added'} {added} FIX comment(s): {page}")
    print(f"FIX comments {'that would be ' if dry_run else ''}added: {total}")
    return total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify quoted passages in wiki pages against the raw files they cite.")
```

- [ ] **Step 9: Add --flag and --dry-run**

In `llm-wiki/scripts/verify_quotes.py`, replace:

```python
    parser.add_argument("--no-cache", action="store_true", help="Do not read or write .cache/verify_quotes.json")
    args = parser.parse_args(argv)
```

with:

```python
    parser.add_argument("--no-cache", action="store_true", help="Do not read or write .cache/verify_quotes.json")
    parser.add_argument("--flag", action="store_true",
                        help="Insert a <!-- FIX: --> comment under every quote that is not found (error severity only)")
    parser.add_argument("--dry-run", action="store_true", help="With --flag: print the edits, change nothing")
    args = parser.parse_args(argv)
    if args.dry_run and not args.flag:
        parser.error("--dry-run only makes sense with --flag")
```

- [ ] **Step 10: Call flag_report after the summary**

In `llm-wiki/scripts/verify_quotes.py`, replace:

```python
    print_summary(report, path, root)
    return 1 if report["errors"] else 0
```

with:

```python
    print_summary(report, path, root)
    if args.flag:
        flag_report(root, report, args.dry_run)
    return 1 if report["errors"] else 0
```

- [ ] **Step 11: Run the tests and verify they pass**

Run: `uv run --with pytest python -m pytest tests/test_verify_quotes.py`

Expected: PASS

- [ ] **Step 12: Run the full suite**

Run: `uv run --with pytest python -m pytest `

Expected: PASS — all earlier tests still pass

- [ ] **Step 13: Commit**

```bash
git add llm-wiki/scripts/cascade_flag.py llm-wiki/scripts/verify_quotes.py tests/test_cascade_flag.py tests/test_verify_quotes.py
git commit -m "feat: shared FIX inserter in cascade_flag; verify_quotes --flag"
```


### Task 8: lint --verify-quotes

**Files:**
- Modify: `llm-wiki/scripts/lint_wiki.py`
- Test: `tests/test_lint_verify_quotes.py`

**Interfaces:**
- Consumes: `verify_quotes.verify_wiki(root) -> dict` (Task 6); `log_issue` in lint_wiki.py
- Produces: `lint_wiki.check_quote_verification(root: Path) -> None` (check 16: ERROR for error-severity results, WARNING for warnings, INFO for suggestions, plus a WARNING when more than 10% of cited quotes are infrastructure-unverifiable); `lint(root, structural=False, verify_quotes=False)`; CLI flag `--verify-quotes`; off by default

- [ ] **Step 1: Write the failing tests**

Create `tests/test_lint_verify_quotes.py`:

```python
"""lint --verify-quotes maps verdicts to lint levels; the default run is unchanged."""
import sys

import pytest

import lint_wiki

RAW = "line one\nthe quoted passage is right here\nline three\n"


def add_page(root, text, raw=None):
    (root / "wiki" / "concepts" / "qv.md").write_text(text, encoding="utf-8")
    for rel, body in (raw or {"raw/articles/a.md": RAW}).items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body, encoding="utf-8")


def issues(root, **kw):
    for bucket in (lint_wiki.errors, lint_wiki.warnings, lint_wiki.suggestions):
        bucket.clear()
    lint_wiki.check_quote_verification(root, **kw)
    pick = lambda items: [i for i in items if "(quote-verification)" in i]
    return pick(lint_wiki.errors), pick(lint_wiki.warnings), pick(lint_wiki.suggestions)


def test_not_found_is_an_error_with_page_line_and_quote(scaffolded_vault):
    add_page(scaffolded_vault, 'X: "an entirely invented sentence about nothing" (raw/articles/a.md, L2)\n')
    errors, warnings, notes = issues(scaffolded_vault)
    assert len(errors) == 1 and "wiki/concepts/qv.md:1" in errors[0] and "invented sentence" in errors[0]
    assert warnings == [] and notes == []


def test_wrong_location_is_a_warning_and_unverifiable_a_suggestion(scaffolded_vault):
    raw = {"raw/articles/a.md": "x\n" * 30 + "the quoted passage is right here\n"}
    page = ('A: "the quoted passage is right here" (raw/articles/a.md, L1)\n'
            'B: "a quote whose file is gone entirely" (raw/articles/gone.md, L1)\n')
    add_page(scaffolded_vault, page, raw)
    errors, warnings, notes = issues(scaffolded_vault)
    assert errors == [] and any("wrong_location" in w for w in warnings) and any("unverifiable" in n for n in notes)


def test_many_unverifiable_citations_raise_a_warning_but_cross_language_does_not(scaffolded_vault):
    gone = "".join(f'Q{i}: "missing file quote number {i} here" (raw/articles/gone{i}.md, L1)\n' for i in range(3))
    add_page(scaffolded_vault, 'ok: "the quoted passage is right here" (raw/articles/a.md, L2)\n' + gone)
    _, warnings, _ = issues(scaffolded_vault)
    assert any("could not be checked" in w and "%" in w for w in warnings)
    chinese = "".join(f"Q{i}: “细胞在培养中分裂得非常迅速而且稳定{i}” (raw/articles/a.md, L2)\n" for i in range(5))
    add_page(scaffolded_vault, 'ok: "the quoted passage is right here" (raw/articles/a.md, L2)\n' + chinese)
    _, warnings, _ = issues(scaffolded_vault)
    assert not any("could not be checked" in w for w in warnings)


def test_default_lint_does_not_run_the_check(scaffolded_vault):
    add_page(scaffolded_vault, 'X: "an entirely invented sentence about nothing" (raw/articles/a.md, L2)\n')
    for bucket in (lint_wiki.errors, lint_wiki.warnings, lint_wiki.suggestions):
        bucket.clear()
    lint_wiki.lint(str(scaffolded_vault))
    assert not any("quote-verification" in i for i in lint_wiki.errors + lint_wiki.warnings + lint_wiki.suggestions)


def test_flag_runs_it_through_lint_and_main(scaffolded_vault, monkeypatch):
    add_page(scaffolded_vault, 'X: "an entirely invented sentence about nothing" (raw/articles/a.md, L2)\n')
    for bucket in (lint_wiki.errors, lint_wiki.warnings, lint_wiki.suggestions):
        bucket.clear()
    monkeypatch.setattr(sys, "argv", ["lint_wiki.py", str(scaffolded_vault), "--verify-quotes"])
    with pytest.raises(SystemExit) as exc:
        lint_wiki.main()
    assert exc.value.code == 1
    assert any("quote-verification" in e for e in lint_wiki.errors)
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run --with pytest python -m pytest tests/test_lint_verify_quotes.py`

Expected: FAIL — `AttributeError: module 'lint_wiki' has no attribute 'check_quote_verification'`

- [ ] **Step 3: Add the check above the stale-only mode section**

In `llm-wiki/scripts/lint_wiki.py`, replace:

```python
# ── Stale-Only Mode ───────────────────────────────────────────────────────────
```

with:

```python
# ── Check 16: Quote Verification (opt-in: --verify-quotes) ───────────────────

UNVERIFIABLE_SHARE_WARNING = 0.10


def check_quote_verification(root: Path) -> None:
    """Quoted passages must occur in the raw file they cite (see verify_quotes.py).
    ERROR = not found; WARNING = fuzzy / wrong place / near miss / downgraded; INFO = unverifiable."""
    import verify_quotes

    report = verify_quotes.verify_wiki(root)
    level = {"error": "ERROR", "warning": "WARNING", "suggestion": "INFO"}
    for r in report["results"]:
        if r["severity"] in level:
            note = f" — {r['note']}" if r["note"] else ""
            log_issue(level[r["severity"]], "quote-verification",
                      f"{r['page']}:{r['line']} {r['verdict']} in {r['raw']} ({r['loc']}): "
                      f"\"{r['quote'][:60]}\"{note}")
    c = report["counts"]
    cited = sum(c[k] for k in ("exact", "fuzzy", "wrong_location", "near_miss", "not_found_error",
                               "not_found_warning", "unverifiable_infrastructure", "unverifiable_cross_language"))
    if cited and c["unverifiable_infrastructure"] / cited > UNVERIFIABLE_SHARE_WARNING:
        log_issue("WARNING", "quote-verification",
                  f"{c['unverifiable_infrastructure']} of {cited} cited quotes "
                  f"({100 * c['unverifiable_infrastructure'] // cited}%) could not be checked "
                  "(missing, PDF or unreadable raw files)")


# ── Stale-Only Mode ───────────────────────────────────────────────────────────
```

- [ ] **Step 4: Add the parameter to lint()**

In `llm-wiki/scripts/lint_wiki.py`, replace:

```python
def lint(root: str, structural: bool = False) -> int:
```

with:

```python
def lint(root: str, structural: bool = False, verify_quotes: bool = False) -> int:
```

- [ ] **Step 5: Run it when requested**

In `llm-wiki/scripts/lint_wiki.py`, replace:

```python
    check_duplicate_list_entries(wiki_path, root_path)
```

with:

```python
    check_duplicate_list_entries(wiki_path, root_path)
    if verify_quotes:
        check_quote_verification(root_path)
```

- [ ] **Step 6: Add the CLI flag**

In `llm-wiki/scripts/lint_wiki.py`, replace:

```python
    parser.add_argument("--stale-only", action="store_true",
                        help="Only report stale pages (fast; used by session-start)")
```

with:

```python
    parser.add_argument("--stale-only", action="store_true",
                        help="Only report stale pages (fast; used by session-start)")
    parser.add_argument("--verify-quotes", action="store_true",
                        help="Also check that quoted passages occur in the raw files they cite")
```

- [ ] **Step 7: Pass it through main()**

In `llm-wiki/scripts/lint_wiki.py`, replace:

```python
        sys.exit(lint(args.wiki_root, structural=args.structural))
```

with:

```python
        sys.exit(lint(args.wiki_root, structural=args.structural, verify_quotes=args.verify_quotes))
```

- [ ] **Step 8: Run the tests and verify they pass**

Run: `uv run --with pytest python -m pytest tests/test_lint_verify_quotes.py`

Expected: PASS

- [ ] **Step 9: Run the full suite**

Run: `uv run --with pytest python -m pytest `

Expected: PASS — all earlier tests still pass

- [ ] **Step 10: Confirm verify_quotes is not wired into fast_ingest.py**

- [ ] **Step 11: Commit**

```bash
git add llm-wiki/scripts/lint_wiki.py tests/test_lint_verify_quotes.py
git commit -m "feat(lint): --verify-quotes runs verify_quotes as check 16"
```


### Task 9: Page margins (dropped.txt) and PDF page numbers in the report

**Files:**
- Modify: `llm-wiki/scripts/verify_quotes.py`
- Modify: `tests/test_verify_quotes.py`

**Interfaces:**
- Consumes: `<slug>.dropped.txt` and `<slug>.pages.json` written by Plan A's `convert_source.py`; `RawStore`, `Context`, `verify_claim`, `verify_wiki` (Tasks 4 and 6)
- Produces: `lines_to_pages(pages_meta, matched_lines) -> str | None` (`"2"` or `"2-3"`); `RawStore.dropped(rel) -> (Doc | None, sha256)`; `Context.dropped`; quotes found only in the margin text are `exact` with note `page margin`; report entries carry `pdf_page`; `dropped_sha256` joins the cache fingerprint. This task can be dropped without touching the rest.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_verify_quotes.py`:

```python
"""Margin text (dropped.txt) as a last search target, and PDF page numbers in the report."""
import json


class TestDroppedAndPdfPages:
    def vault(self, tmp_path, page, md="body text only\nsecond line\n", pages=None, dropped=None):
        raw = {"raw/converted/doc/doc.md": md}
        if pages is not None:
            raw["raw/converted/doc/doc.pages.json"] = json.dumps(pages)
        if dropped is not None:
            raw["raw/converted/doc/doc.dropped.txt"] = dropped
        return make_vault(tmp_path, {"concepts/x.md": page}, raw)

    def test_margin_text_is_found_in_dropped_txt(self, tmp_path):
        page = 'DOI: "doi: https://doi.org/10.1101/2025.03.10.642480" (raw/converted/doc/doc.md, L1)\n'
        dropped = "p1\theader\tbioRxiv preprint doi: https://doi.org/10.1101/2025.03.10.642480; this version posted\n"
        root = self.vault(tmp_path, page, dropped=dropped)
        report = vq.verify_wiki(root, use_cache=False)
        r = report["results"][0]
        assert (r["verdict"], r["severity"], r["note"]) == ("exact", "none", "page margin")

    def test_without_dropped_txt_the_same_quote_is_not_found(self, tmp_path):
        page = 'DOI: "doi: https://doi.org/10.1101/2025.03.10.642480" (raw/converted/doc/doc.md, L1)\n'
        report = vq.verify_wiki(self.vault(tmp_path, page), use_cache=False)
        assert report["counts"]["not_found_error"] == 1

    def test_changing_dropped_txt_invalidates_the_cache(self, tmp_path):
        page = 'DOI: "doi: https://doi.org/10.1101/2025.03.10.642480" (raw/converted/doc/doc.md, L1)\n'
        root = self.vault(tmp_path, page)
        assert vq.verify_wiki(root)["errors"] == 1
        (root / "raw" / "converted" / "doc" / "doc.dropped.txt").write_text("p1\theader\tdoi: https://doi.org/10.1101/2025.03.10.642480\n")
        again = vq.verify_wiki(root)
        assert again["errors"] == 0 and again["counts"]["from_cache"] == 0

    def test_report_translates_lines_to_pdf_pages(self, tmp_path):
        pages = {"confidence": "high", "pages": [
            {"page": 1, "line_start": 1, "line_end": 1, "how": "aligned"},
            {"page": 2, "line_start": 2, "line_end": 3, "how": "aligned"}]}
        page = 'Q: "second line of the body" (raw/converted/doc/doc.md, L2)\n'
        root = self.vault(tmp_path, page, md="first line\nsecond line of the body\n", pages=pages)
        r = vq.verify_wiki(root, use_cache=False)["results"][0]
        assert r["verdict"] == "exact" and r["pdf_page"] == "2"

    def test_lines_to_pages_ranges_and_missing_map(self):
        pages = {"pages": [{"page": 1, "line_start": 1, "line_end": 10}, {"page": 2, "line_start": 11, "line_end": 20},
                           {"page": 3, "line_start": 21, "line_end": 30}]}
        assert vq.lines_to_pages(pages, [5, 5]) == "1"
        assert vq.lines_to_pages(pages, [9, 12]) == "1-2"
        assert vq.lines_to_pages(pages, []) is None
        assert vq.lines_to_pages(None, [1, 2]) is None
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run --with pytest python -m pytest tests/test_verify_quotes.py`

Expected: FAIL — `AttributeError: ... no attribute 'lines_to_pages'` and the margin quote is reported as not_found

- [ ] **Step 3: Add lines_to_pages**

In `llm-wiki/scripts/verify_quotes.py`, replace:

```python
def wiki_pages(root: Path, pages: list[str] | None) -> list[Path]:
```

with:

```python
def lines_to_pages(pages_meta: dict | None, matched: list[int]) -> str | None:
    """PDF pages (``"2"`` or ``"2-3"``) covering the matched markdown lines, from pages.json."""
    if not pages_meta or not matched:
        return None
    first, last = matched[0], matched[-1]
    hit = [p["page"] for p in pages_meta.get("pages", [])
           if p["line_start"] <= last and p["line_end"] >= first]
    if not hit:
        return None
    return str(min(hit)) if min(hit) == max(hit) else f"{min(hit)}-{max(hit)}"


def wiki_pages(root: Path, pages: list[str] | None) -> list[Path]:
```

- [ ] **Step 4: RawStore.dropped**

In `llm-wiki/scripts/verify_quotes.py`, replace:

```python
    def others(self, exclude: str):
```

with:

```python
    def dropped(self, rel: str) -> tuple[Doc | None, str]:
        """The ``<slug>.dropped.txt`` next to a converted markdown file, and its sha256."""
        path = self.root / rel
        f = path.with_name(path.stem + ".dropped.txt")
        if "converted" not in path.parts or not f.is_file():
            return None, ""
        data = f.read_bytes()
        return Doc(data.decode("utf-8", errors="replace")), sha256_bytes(data)

    def others(self, exclude: str):
```

- [ ] **Step 5: Context.dropped**

In `llm-wiki/scripts/verify_quotes.py`, replace:

```python
    strict: bool = False


def is_short(quote: str) -> bool:
```

with:

```python
    strict: bool = False
    dropped: Doc | None = None            # header/footer/footnote text the converted markdown omits


def is_short(quote: str) -> bool:
```

- [ ] **Step 6: Search the margin text before the fuzzy step**

In `llm-wiki/scripts/verify_quotes.py`, replace:

```python
    cited_score, cited_lines = _fuzzy_over(doc, frags, windows or whole)
```

with:

```python
    if ctx.dropped is not None:
        margin = [(1, max(ctx.dropped.n_lines, 1))]
        if any(match_fragments(ctx.dropped, frags, rung, margin) for rung in RUNGS):
            return Result("exact", "none", 100, [], "page margin")
    cited_score, cited_lines = _fuzzy_over(doc, frags, windows or whole)
```

- [ ] **Step 7: Load it in verify_wiki**

In `llm-wiki/scripts/verify_quotes.py`, replace:

```python
            convert, pages_meta, convert_sha, pages_sha = store.sidecars(claim.raw)
```

with:

```python
            convert, pages_meta, convert_sha, pages_sha = store.sidecars(claim.raw)
            dropped_doc, dropped_sha = store.dropped(claim.raw)
```

- [ ] **Step 8: Put its hash in the cache fingerprint**

In `llm-wiki/scripts/verify_quotes.py`, replace:

```python
"pages_sha256": pages_sha, "convert_sha256": convert_sha, "dropped_sha256": "",
```

with:

```python
"pages_sha256": pages_sha, "convert_sha256": convert_sha, "dropped_sha256": dropped_sha,
```

- [ ] **Step 9: Pass it to the context**

In `llm-wiki/scripts/verify_quotes.py`, replace:

```python
                ctx = Context(doc, reason, convert, pages_meta,
                              others=lambda rel=claim.raw: store.others(rel),
```

with:

```python
                ctx = Context(doc, reason, convert, pages_meta,
                              others=lambda rel=claim.raw: store.others(rel), dropped=dropped_doc,
```

- [ ] **Step 10: Fill pdf_page in the report**

In `llm-wiki/scripts/verify_quotes.py`, replace:

```python
"loc": format_locs(claim.locs), "pdf_page": None, "verdict": result.verdict,
```

with:

```python
"loc": format_locs(claim.locs), "pdf_page": lines_to_pages(pages_meta, result.matched_lines),
                "verdict": result.verdict,
```

- [ ] **Step 11: Run the tests and verify they pass**

Run: `uv run --with pytest python -m pytest tests/test_verify_quotes.py`

Expected: PASS

- [ ] **Step 12: Commit**

```bash
git add llm-wiki/scripts/verify_quotes.py tests/test_verify_quotes.py
git commit -m "feat(verify_quotes): search page-margin text and report PDF pages"
```


### Task 10: Optional Traditional-to-Simplified rung

**Files:**
- Modify: `llm-wiki/scripts/verify_quotes.py`
- Modify: `tests/test_verify_quotes.py`

**Interfaces:**
- Consumes: `textnorm.to_simplified` (identity unless `zhconv` is installed); `Doc`, `match_fragments`, `RUNGS`, `verify_claim` (Tasks 3-4)
- Produces: `Doc.simplified() -> Doc` (cached; `self` when nothing changes); a `script-folded` rung inside each scope of `verify_claim` (`exact` in the cited place, `wrong_location` outside). Inert without zhconv. This task can be dropped without touching the rest.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_verify_quotes.py`:

```python
"""Optional script-folding rung: Traditional raw text, Simplified quote."""


class TestScriptFold:
    TABLE = str.maketrans({"歡": "欢", "視": "视", "環": "环", "財": "财", "經": "经", "資": "资", "務": "务"})

    def fake_simplified(self, monkeypatch):
        monkeypatch.setattr(vq.textnorm, "to_simplified", lambda s: s.translate(self.TABLE))

    def test_without_zhconv_the_traditional_text_does_not_match(self, monkeypatch):
        monkeypatch.setattr(vq.textnorm, "to_simplified", lambda s: s)
        d = doc("開頭", "歡迎收看本台環球財經 今天", "結尾")
        c = claim("欢迎收看本台环球财经", locs=(("L", 2, 2),), fragments=["欢迎收看本台环球财经"])
        assert vq.verify_claim(c, vq.Context(doc=d)).verdict != "exact"

    def test_with_the_fold_it_matches_and_reports_lines(self, monkeypatch):
        self.fake_simplified(monkeypatch)
        d = doc("開頭", "歡迎收看本台環球財經 今天", "結尾")
        c = claim("欢迎收看本台环球财经", locs=(("L", 2, 2),), fragments=["欢迎收看本台环球财经"])
        r = vq.verify_claim(c, vq.Context(doc=d))
        assert (r.verdict, r.note, r.matched_lines) == ("exact", "script-folded", [2, 2])

    def test_a_fold_hit_outside_the_cited_lines_is_wrong_location(self, monkeypatch):
        self.fake_simplified(monkeypatch)
        d = doc(*(["開頭"] * 30 + ["歡迎收看本台環球財經 今天"]))
        c = claim("欢迎收看本台环球财经", locs=(("L", 1, 1),), fragments=["欢迎收看本台环球财经"])
        assert vq.verify_claim(c, vq.Context(doc=d)).verdict == "wrong_location"

    def test_simplified_doc_is_cached_and_identity_returns_self(self, monkeypatch):
        d = doc("abc")
        monkeypatch.setattr(vq.textnorm, "to_simplified", lambda s: s)
        assert d.simplified() is d
        self.fake_simplified(monkeypatch)
        d2 = doc("歡迎")
        assert d2.simplified() is d2.simplified() and d2.simplified().text == "欢迎\n"
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run --with pytest python -m pytest tests/test_verify_quotes.py`

Expected: FAIL — `AttributeError: 'Doc' object has no attribute 'simplified'`

- [ ] **Step 3: Doc.simplified**

In `llm-wiki/scripts/verify_quotes.py`, replace:

```python
    @property
    def cjk_share(self) -> float:
```

with:

```python
    def simplified(self) -> "Doc":
        """This text with Traditional characters folded to Simplified (``self`` when nothing changes)."""
        if not hasattr(self, "_simplified"):
            folded = textnorm.to_simplified(self.text)
            self._simplified = self if folded == self.text else Doc(folded)
        return self._simplified

    @property
    def cjk_share(self) -> float:
```

- [ ] **Step 4: Try the folded text after the ladder in each scope**

In `llm-wiki/scripts/verify_quotes.py`, replace:

```python
            return Result("exact", "none", 100, where, "loose")
```

with:

```python
            return Result("exact", "none", 100, where, "loose")
        folded = doc.simplified()
        if folded is not doc:
            lines = None
            for rung in RUNGS:
                lines = match_fragments(folded, [textnorm.to_simplified(f) for f in frags], rung, ranges)
                if lines:
                    break
            if lines:
                if not in_cited_place:
                    return Result("wrong_location", "warning", 100, list(lines), "found outside the cited lines (script-folded)")
                return Result("exact", "none", 100, list(lines), "script-folded")
```

- [ ] **Step 5: Run the tests and verify they pass**

Run: `uv run --with pytest python -m pytest tests/test_verify_quotes.py`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add llm-wiki/scripts/verify_quotes.py tests/test_verify_quotes.py
git commit -m "feat(verify_quotes): optional Traditional-to-Simplified rung"
```


### Task 11: Documentation, performance guard and real-vault check

**Files:**
- Create: `llm-wiki/references/ops/op-verify-quotes.md`
- Create: `tests/test_verify_quotes_slow.py`
- Modify: `llm-wiki/SKILL.md`
- Modify: `tests/README.md`

**Interfaces:**
- Consumes: the finished verify_quotes.py and lint flag (Tasks 1-10)
- Produces: `op-verify-quotes.md` (purpose, quote and citation rules, verdict table, flags, exit codes, flagging, known limits); SKILL.md pointers and the one-line-quotation rule for page-writing agents; opt-in slow tests (1 MB performance guard; `VQ_REAL_VAULT=<path>` runs a pass over a copy of a real vault)

- [ ] **Step 1: Write the operation reference**

Create `llm-wiki/references/ops/op-verify-quotes.md`:

```markdown
# op-verify-quotes - check quoted passages against the raw files they cite

`verify_quotes.py` is a deterministic checker. For every quotation in a wiki page that carries a
raw citation, it decides whether the passage really occurs in the cited raw file, at the cited
place. It reports; it edits wiki pages only with `--flag`. It does not judge paraphrase, meaning
or numbers outside quotation marks.

```bash
python3 .claude/skills/llm-wiki/scripts/verify_quotes.py <wiki-root>             # report + summary
python3 .claude/skills/llm-wiki/scripts/verify_quotes.py <wiki-root> --page wiki/concepts/x.md
python3 .claude/skills/llm-wiki/scripts/verify_quotes.py <wiki-root> --flag --dry-run
python3 .claude/skills/llm-wiki/scripts/lint_wiki.py <wiki-root> --verify-quotes   # as lint check 16
```

Options: `--page PATH` (repeatable), `--threshold N` (fuzzy similarity, default 85), `--report PATH`
(default `outputs/verify-quotes-YYYY-MM-DD.json`), `--strict`, `--no-cache`, `--flag [--dry-run]`.
Exit codes: 0 no error-severity quote, 1 at least one, 2 usage error.
Add `.cache/` to the vault's `.gitignore`; it holds the incremental verdict cache.

## What counts as a quote

Text in `"..."`, curly quotes, `「...」` or `『...』`, and a run of `>` blockquote lines, on one line
(a quotation that wraps over several lines of the page is not extracted - do not hard-wrap quotes).
Minimum length: 3 Latin words or 12 CJK characters; with a digit, 2 words or 6 characters. A quote
binds to the first citation to its right on the same line, otherwise the first citation later in the
same paragraph, list item or blockquote group (never across table rows). A quote with no citation is
counted as `uncited`, never as a failure. `[...]`, `...`, `…`, `……` and `⋯` split a quote into fragments that must
all occur, in order.

## Citation forms understood

`(raw/x.md, L14-22)`, `L11-L13`, `L39/L63`, `L61-62, L91`, `L10-L36; L81-L93`, `p.12`, `P.3-4`,
`location unknown`, several sources in one parenthesis, an en dash or full-width comma, extra spaces,
commas or parentheses inside the file name. A `(raw/` fragment that does not parse is reported as a
malformed citation. For a PDF cite the converted markdown (`raw/converted/<slug>/<slug>.md`).

## Verdicts

| Verdict | Meaning | Severity |
|---|---|---|
| `exact` | found in the cited lines (literal, case-folded for long quotes, loose, script-folded, or page margin) | none |
| `fuzzy` | similar (>= threshold) in the cited lines, or a short quote that differs only in case | warning |
| `wrong_location` | found, but outside the cited lines (lines reported) | warning |
| `near_miss` | similarity 60-85: probably a paraphrase in quotation marks | warning |
| `not_found` | nowhere in the file; `suggested_raw` names another raw file that has it | **error** |
| `unverifiable` | raw missing, a PDF, a private zone, or a cross-language quote | suggestion |

`not_found` is downgraded to a warning when the raw file is a lossy conversion (`lossy: true`) or
the page map has low confidence and the citation is a page number; `--strict` keeps it an error and
also makes `near_miss` an error. A quote that cannot occur in the raw file because the scripts do
not overlap (6+ CJK letters against an English raw file, or the reverse) is `unverifiable` instead
of `not_found`, but only after every match attempt failed.

## Flagging

`--flag` inserts `<!-- FIX: quote not found [vq:<id>] in <raw> <loc> -->` under each quote that is
an error-severity `not_found` (with `; found in <file>` when it exists elsewhere). Running it twice
adds nothing. `audit` picks the comments up like any other inline FIX.

## Known limits

Quotes crossing a page break are matched only if the converter kept the text contiguous;
reconverting a PDF changes line numbers (lint check 5 and `cascade_flag.py` flag the affected
citations); table cells and figure captions depend on how the converter rendered them; a close
paraphrase reads as `near_miss`, and the 60 / 85 band is a judgement call to tune per vault.
```

- [ ] **Step 2: SKILL.md: lint flags**

In `llm-wiki/SKILL.md`, replace:

```markdown
Flags: `--stale-only` (fast; used at session start), `--structural` (skip the
uncited-claims check on large wikis). After running, show the error / warning / suggestion
```

with:

```markdown
Flags: `--stale-only` (fast; used at session start), `--structural` (skip the
uncited-claims check on large wikis), `--verify-quotes` (also check that quoted passages occur
in the raw files they cite; see `references/ops/op-verify-quotes.md`). After running, show the error / warning / suggestion
```

- [ ] **Step 3: SKILL.md: Rule 2, one-line quotations**

In `llm-wiki/SKILL.md`, replace:

```markdown
- RIGHT: The model has "175 billion parameters" (raw/articles/gpt3.md, L8).
```

with:

```markdown
- RIGHT: The model has "175 billion parameters" (raw/articles/gpt3.md, L8).
Keep each quotation on one line, with its citation after it: `verify_quotes.py` checks quoted
passages against the cited raw lines and an unfinished or wrapped quotation is not checked.
```

- [ ] **Step 4: Add the opt-in slow tests**

Create `tests/test_verify_quotes_slow.py`:

```python
"""Opt-in checks: performance on 1 MB of text, and a pass over a real vault.

    pytest -m slow                                         # performance guard
    VQ_REAL_VAULT=/path/to/vault pytest -m slow            # also read a COPY of that vault
"""
import os
import random
import shutil
import time

import pytest

import verify_quotes as vq

pytestmark = pytest.mark.slow


def test_fifty_misses_on_one_megabyte_stay_under_ten_seconds(tmp_path):
    rng = random.Random(11)
    vocab = [f"word{i}" for i in range(4000)]
    paragraphs = [" ".join(rng.choice(vocab) for _ in range(120)) for _ in range(1200)]       # ~1 MB
    raw = tmp_path / "raw" / "articles"
    raw.mkdir(parents=True)
    (raw / "big.md").write_text("\n".join(paragraphs) + "\n", encoding="utf-8")
    (tmp_path / "wiki" / "concepts").mkdir(parents=True)
    (tmp_path / "CLAUDE.md").write_text("# Wiki\n", encoding="utf-8")
    quotes = [" ".join(rng.choice(vocab) + "x" for _ in range(12)) for _ in range(50)]          # none of these occur
    page = "".join(f'Q{i}: "{q}" (raw/articles/big.md, L5)\n' for i, q in enumerate(quotes))
    (tmp_path / "wiki" / "concepts" / "x.md").write_text(page, encoding="utf-8")
    start = time.time()
    report = vq.verify_wiki(tmp_path, use_cache=False)
    elapsed = time.time() - start
    assert report["counts"]["not_found_error"] + report["counts"]["near_miss"] == 50
    assert elapsed < 10, f"{elapsed:.1f}s"


@pytest.mark.skipif(not os.environ.get("VQ_REAL_VAULT"), reason="set VQ_REAL_VAULT to a vault path")
def test_real_vault_copy_runs_and_second_run_is_cached(tmp_path):
    copy = tmp_path / "vault"
    shutil.copytree(os.environ["VQ_REAL_VAULT"], copy, ignore=shutil.ignore_patterns(".git", ".obsidian", "node_modules"))
    first = vq.verify_wiki(copy)
    c = first["counts"]
    assert first["pages_checked"] > 0 and sum(c[k] for k in ("exact", "fuzzy", "wrong_location", "near_miss", "not_found_error")) > 0
    second = vq.verify_wiki(copy)
    assert second["counts"]["from_cache"] > 0 and second["errors"] == first["errors"]
```

- [ ] **Step 5: Run the slow tests**

Run: `uv run --with pytest python -m pytest -m slow`

Expected: PASS — the performance guard runs and passes; the real-vault test is skipped unless `VQ_REAL_VAULT` is set

- [ ] **Step 6: tests/README.md**

In `tests/README.md`, replace:

```markdown
| `test_convert_real.py` | `convert_source.py` | real MinerU run, opt in with `-m slow` and `LLM_WIKI_TEST_PDF` |
```

with:

```markdown
| `test_convert_real.py` | `convert_source.py` | real MinerU run, opt in with `-m slow` and `LLM_WIKI_TEST_PDF` |
| `test_citations.py` | `citations.py` | the raw-citation grammar: ranges, lists, `p.N`, tolerant separators, malformed fragments |
| `test_verify_quotes.py` | `verify_quotes.py` | quote extraction and binding, matching ladder, verdicts and severities, cache, report, CLI, `--flag` |
| `test_cascade_flag.py` | `cascade_flag.py` | shared FIX inserter; output identical to the pre-refactor algorithm |
| `test_lint_verify_quotes.py` | `lint_wiki.py` | `--verify-quotes` level mapping; default run unchanged |
| `test_verify_quotes_slow.py` | `verify_quotes.py` | 1 MB performance guard; opt-in pass over a vault copy (`VQ_REAL_VAULT`), `-m slow` |
```

- [ ] **Step 7: Run the full suite**

Run: `uv run --with pytest python -m pytest `

Expected: PASS — the original 31 tests plus every new test

- [ ] **Step 8: Commit**

```bash
git add llm-wiki/references/ops/op-verify-quotes.md llm-wiki/SKILL.md tests/README.md tests/test_verify_quotes_slow.py
git commit -m "docs(verify_quotes): operation reference, SKILL.md pointers, slow tests"
```


