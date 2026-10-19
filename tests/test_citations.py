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
