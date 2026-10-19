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
    dropped: Doc | None = None            # header/footer/footnote text the converted markdown omits


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
    if ctx.dropped is not None:
        margin = [(1, max(ctx.dropped.n_lines, 1))]
        if any(match_fragments(ctx.dropped, frags, rung, margin) for rung in RUNGS):
            return Result("exact", "none", 100, [], "page margin")
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

    def dropped(self, rel: str) -> tuple[Doc | None, str]:
        """The ``<slug>.dropped.txt`` next to a converted markdown file, and its sha256."""
        path = self.root / rel
        f = path.with_name(path.stem + ".dropped.txt")
        if "converted" not in path.parts or not f.is_file():
            return None, ""
        data = f.read_bytes()
        return Doc(data.decode("utf-8", errors="replace")), sha256_bytes(data)

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
            dropped_doc, dropped_sha = store.dropped(claim.raw)
            if reason == "raw file missing":
                missing[claim.raw] += 1
            key = claim_key(claim)
            seen.add(key)
            fingerprint = {
                "raw_sha256": sha256_bytes(doc.text.encode("utf-8")) if doc else reason,
                "pages_sha256": pages_sha, "convert_sha256": convert_sha, "dropped_sha256": dropped_sha,
                "threshold": threshold, "near_miss": near_miss, "strict": strict, "algo": algo,
            }
            cached = cache.get(key, fingerprint, corpus)
            if cached is not None:
                result = Result(**cached)
                counts["from_cache"] += 1
            else:
                ctx = Context(doc, reason, convert, pages_meta,
                              others=lambda rel=claim.raw: store.others(rel), dropped=dropped_doc,
                              threshold=threshold, near_miss=near_miss, strict=strict)
                result = verify_claim(claim, ctx)
                cache.put(key, fingerprint, corpus, result)
            tally(counts, result)
            results.append({
                "id": claim_id(claim), "page": page, "line": claim.line_no, "quote": claim.quote, "raw": claim.raw,
                "loc": format_locs(claim.locs), "pdf_page": lines_to_pages(pages_meta, result.matched_lines),
                "verdict": result.verdict,
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
    parser.add_argument("wiki_root", help="Path to the wiki root directory")
    parser.add_argument("--page", action="append", default=[], help="Only check this wiki file (repeatable)")
    parser.add_argument("--threshold", type=int, default=THRESHOLD, help=f"Fuzzy similarity that counts as a match (default {THRESHOLD})")
    parser.add_argument("--report", default=None, help="Report path (default outputs/verify-quotes-YYYY-MM-DD.json)")
    parser.add_argument("--strict", action="store_true", help="Keep downgraded not_found and near_miss results as errors")
    parser.add_argument("--no-cache", action="store_true", help="Do not read or write .cache/verify_quotes.json")
    parser.add_argument("--flag", action="store_true",
                        help="Insert a <!-- FIX: --> comment under every quote that is not found (error severity only)")
    parser.add_argument("--dry-run", action="store_true", help="With --flag: print the edits, change nothing")
    args = parser.parse_args(argv)
    if args.dry_run and not args.flag:
        parser.error("--dry-run only makes sense with --flag")
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
    if args.flag:
        flag_report(root, report, args.dry_run)
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
