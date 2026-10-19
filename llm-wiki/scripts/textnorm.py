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
