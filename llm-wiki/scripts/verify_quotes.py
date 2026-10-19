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
