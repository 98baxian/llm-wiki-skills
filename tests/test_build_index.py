"""Tests for build_index.py — frontmatter parsing, domain routing, index generation."""
from pathlib import Path

import build_index


def test_parse_frontmatter(tmp_path):
    f = tmp_path / "p.md"
    f.write_text('---\ntype: concept\ntitle: "First Principles"\nconfidence: high\n---\n# body\n')
    fm = build_index.parse_frontmatter(f)
    assert fm["type"] == "concept"
    assert fm["title"] == "First Principles"
    assert fm["confidence"] == "high"


def test_parse_frontmatter_no_frontmatter(tmp_path):
    f = tmp_path / "p.md"
    f.write_text("# just a heading\n")
    assert build_index.parse_frontmatter(f) == {}


def test_load_domains_from_claude_md(tmp_path):
    (tmp_path / "CLAUDE.md").write_text(
        "# KB\n\n## Scope\n\nWhat this wiki covers:\n- AI\n- Finance\n\n"
        "What this wiki deliberately excludes:\n- Cooking\n"
    )
    assert build_index.load_domains(tmp_path) == ["AI", "Finance"]


def test_load_domains_skips_placeholder(tmp_path):
    (tmp_path / "CLAUDE.md").write_text(
        "## Scope\n\nWhat this wiki covers:\n- <define out-of-scope areas>\n"
    )
    # placeholder bullet skipped → falls back to General
    assert build_index.load_domains(tmp_path) == ["General"]


def test_slug_to_domain_explicit_frontmatter():
    domains = ["AI", "Finance"]
    assert build_index.slug_to_domain("x", domains, {"domain": "Finance"}) == "Finance"


def test_slug_to_domain_defaults_to_first():
    domains = ["AI", "Finance"]
    assert build_index.slug_to_domain("random-slug", domains, {}) == "AI"


def test_build_index_end_to_end(scaffolded_vault):
    """Add a concept page, build the index, assert it is catalogued and hot.md is untouched."""
    wiki = scaffolded_vault / "wiki"
    concept = wiki / "concepts" / "first-principles.md"
    concept.write_text(
        "---\ntype: concept\ntitle: First Principles\ndomain: AI\n"
        "confidence: medium\nsource_count: 2\n---\n# First Principles\n\nbody\n"
    )
    hot_before = (wiki / "hot.md").read_text()

    rc = build_index.build_index(str(scaffolded_vault))
    assert rc == 0

    master = (wiki / "index.md").read_text()
    assert "first-principles" in master
    ai_catalog = (wiki / "index" / "ai.md").read_text()
    assert "First Principles" in ai_catalog

    # build_index must never rewrite the hot cache
    assert (wiki / "hot.md").read_text() == hot_before
