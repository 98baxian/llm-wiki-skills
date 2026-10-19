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
