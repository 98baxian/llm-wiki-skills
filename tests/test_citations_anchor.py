"""Anchor citations: ``raw/x.md#id`` is found beside line citations, and ids map to lines."""
import citations


def test_anchor_citation_is_found():
    line = "A claim (raw/cases/x.md#ev-pvs1) holds."
    [c] = citations.find_citations(line)
    assert c.raw == "raw/cases/x.md" and c.locs == (("anchor", "ev-pvs1"),)
    assert line[c.start:c.end] == "raw/cases/x.md#ev-pvs1"


def test_line_and_anchor_citations_on_one_line():
    found = citations.find_citations("(raw/a.md, L3-4) and (raw/b.md#k1)")
    assert [c.locs for c in found] == [(("L", 3, 4),), (("anchor", "k1"),)]


def test_line_citation_is_unchanged():
    [c] = citations.find_citations("(raw/a.md, L14-22)")
    assert c.locs == (("L", 14, 22),)


def test_anchor_citation_is_not_malformed():
    assert citations.malformed("(raw/a.md#k1)") == []


def test_anchor_lines_maps_block_ids():
    text = "intro\nPVS1 fires ^ev-pvs1\n^only-id\nno id here\ndup ^d\nagain ^d\n"
    assert citations.anchor_lines(text) == {"ev-pvs1": [2], "only-id": [3], "d": [5, 6]}


def test_caret_inside_a_line_is_not_an_anchor():
    assert citations.anchor_lines("x^2 is the square\n") == {}
