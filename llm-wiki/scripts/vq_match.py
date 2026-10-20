"""vq_match - match a quote's fragments against a raw text and decide the verdict.

Part of verify_quotes (see verify_quotes.py). Holds the normalized-view ``Doc``, the literal /
case-folded / loose ladder, the fuzzy fallback and ``verify_claim``.
"""
from __future__ import annotations

import bisect
import difflib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import textnorm
from vq_extract import SHORT_CJK, SHORT_WORDS, Claim, is_cjk, long_enough

SLACK = 10                 # lines of tolerance around a cited line range
THRESHOLD = 85             # fuzzy similarity that counts as the same passage
NEAR_MISS = 60             # similarity that suggests a paraphrase in quotation marks

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
            if mode == "nospace":      # case-folded with every whitespace character removed, punctuation kept
                text, offs = textnorm.normalize_with_map(self.text, fold=True, join_hyphen=join_hyphen)
                keep = [k for k, c in enumerate(text) if not c.isspace()]
                self._views[key] = ("".join(text[k] for k in keep), [offs[k] for k in keep])
            else:
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

    def simplified(self) -> "Doc":
        """This text with Traditional characters folded to Simplified (``self`` when nothing changes)."""
        if not hasattr(self, "_simplified"):
            folded = textnorm.to_simplified(self.text)
            self._simplified = self if folded == self.text else Doc(folded)
        return self._simplified

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
    if mode == "nospace":
        return "".join(textnorm.fold_case(fragment).split())
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


def fuzzy_best(doc: Doc, fragment: str, line_range: tuple[int, int],
               after: int = 0) -> tuple[int, tuple[int, int] | None, int, int]:
    """Best fuzzy score of ``fragment`` inside ``line_range``:
    ``(score, (first_line, last_line), start, end)``.

    ``start`` and ``end`` (exclusive) delimit the match in the loose view (-1 when nothing matched).
    ``after`` is the minimum loose-view start position, so ellipsis fragments can be searched in
    order: each one after the previous fragment's ``end``.

    Small ranges are scanned; large ones first rank windows by character-trigram overlap
    (an inverted index built once per raw file) and only the top candidates are scored.
    """
    query = textnorm.loose(fragment)
    n = len(query)
    if n < MIN_FUZZY_CHARS:
        return 0, None, -1, -1
    text, offs = doc.view("loose")
    lo, hi = doc.offsets_for_lines(*line_range)
    lo_n = bisect.bisect_left(offs, lo)
    hi_n = bisect.bisect_left(offs, hi)
    lo_n = max(lo_n, after)
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
        return 0, None, -1, -1
    return best, (doc.line_of(offs[best_span[0]]), doc.line_of(offs[best_span[1]])), best_span[0], best_span[1] + 1


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
    """Line ranges to search first, with SLACK. None means the whole file is the cited place.

    An empty list means the citation names pages that an existing page map does not contain:
    there is no cited place, so the whole file only counts as somewhere else.
    """
    ranges = []
    missing = False
    for loc in claim.locs:
        if loc[0] == "L":
            first, last = loc[1], loc[2]
        elif loc[0] == "p" and ctx.pages and ctx.pages.get("pages"):
            span = page_lines(ctx.pages, loc[1], loc[2])
            if span is None:
                missing = True
                continue
            first, last = span
        else:
            return None
        ranges.append((max(1, first - SLACK), last + SLACK))
    if ranges:
        return ranges
    return [] if missing else None


def scripts_cannot_overlap(quote: str, raw_cjk_share: float) -> bool:
    quote_cjk = sum(1 for c in quote if is_cjk(c))
    if quote_cjk >= 6 and raw_cjk_share < 0.05:
        return True
    return quote_cjk == 0 and len(quote.split()) >= 3 and raw_cjk_share > 0.6


def _fuzzy_over(doc: Doc, fragments: list[str], ranges: list[tuple[int, int]],
                threshold: int = THRESHOLD, near_miss: int = NEAR_MISS) -> tuple[int, list[int]]:
    """Fuzzy score of an ellipsis quote, fragments taken in order (each after the previous match).

    Long fragments are scored by similarity. A short fragment is not scored: it is either found
    (literal, case-folded or loose view, which covers spaced ASR text) after the previous fragment,
    or missing. A missing short fragment keeps the quote below ``threshold``, so it can never end up
    ``fuzzy``; it stays a ``near_miss`` only when the long fragments themselves scored in that band.
    """
    best_per_fragment = []
    short_missing = False
    after = 0
    for frag in fragments:
        best: tuple[int, tuple[int, int] | None, int, int] = (0, None, -1, -1)
        if not long_enough(frag):
            needle = textnorm.loose(frag)
            for rng in ranges:
                span = doc.find(needle, "loose", rng, after=after) if needle else None
                if span is not None and (best[2] < 0 or span[0] < best[2]):
                    a, b = doc.lines_of_span("loose", span)
                    best = (100, (a, b), span[0], span[1])
            if best[2] < 0:
                short_missing = True
                continue              # a missing short fragment does not move the cursor
            best_per_fragment.append((None,) + best[1:])
            after = best[3]
            continue
        for rng in ranges:
            # ellipsis fragments must appear in order: each one is searched after the previous match ends
            cand = fuzzy_best(doc, frag, rng, after=after) if after else fuzzy_best(doc, frag, rng)
            if cand[0] > best[0]:
                best = cand
        best_per_fragment.append(best)
        if best[3] >= 0:
            after = best[3]
    scores = [b[0] for b in best_per_fragment if b[0] is not None]
    spans = [b[1] for b in best_per_fragment]
    found = [s for s in spans if s]
    lines = [min(s[0] for s in found), max(s[1] for s in found)] if found else []
    score = min(scores) if scores else 0
    if short_missing and score >= threshold:
        score = near_miss - 1
    return score, lines


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
    no_page = windows == []      # cited page absent from the page map: nothing counts as the cited place
    where_note = "page not in page map; found elsewhere in the file" if no_page else "found outside the cited lines"
    for scope, ranges in scopes:
        in_cited_place = scope == "cited" or windows is None
        for rung in RUNGS:
            lines = match_fragments(doc, frags, rung, ranges)
            if lines is None:
                continue
            where = list(lines)
            if not in_cited_place:
                return Result("wrong_location", "warning", 100, where,
                              f"{where_note} ({rung})")
            if rung == "literal":
                return Result("exact", "none", 100, where)
            if rung == "fold":
                if short:
                    return Result("fuzzy", "warning", 95, where, "case differs")
                return Result("exact", "none", 100, where, "case differs")
            if short and match_fragments(doc, frags, "nospace", ranges) is None:
                return Result("fuzzy", "warning", 95, where, "loose match on short quote")
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
                    return Result("wrong_location", "warning", 100, list(lines), f"{where_note} (script-folded)")
                return Result("exact", "none", 100, list(lines), "script-folded")
    if ctx.dropped is not None:
        margin = [(1, max(ctx.dropped.n_lines, 1))]
        if any(match_fragments(ctx.dropped, frags, rung, margin) for rung in RUNGS):
            return Result("exact", "none", 100, [], "page margin")
    if no_page:
        cited_score, cited_lines = 0, []
    else:
        cited_score, cited_lines = _fuzzy_over(doc, frags, windows or whole, ctx.threshold, ctx.near_miss)
    if cited_score >= ctx.threshold:
        return Result("fuzzy", "warning", cited_score, cited_lines, "wording differs slightly")
    file_score, file_lines = (cited_score, cited_lines) if windows is None else _fuzzy_over(doc, frags, whole, ctx.threshold, ctx.near_miss)
    if windows is not None and file_score >= ctx.threshold:
        note = "page not in page map; similar text elsewhere in the file" if no_page else "similar text outside the cited lines"
        return Result("wrong_location", "warning", file_score, file_lines, note)
    best, best_lines = (cited_score, cited_lines) if cited_score >= file_score else (file_score, file_lines)
    if best >= ctx.near_miss:
        result = Result("near_miss", "warning", best, best_lines,
                        "wording differs; a paraphrase in quotation marks?")
    else:
        result = _not_found(claim, ctx, best)
    return _apply_severity(result, claim, ctx)
