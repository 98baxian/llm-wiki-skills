"""Tests for fix_links.py — deterministic wikilink repair."""
import subprocess
import sys
from pathlib import Path

import fix_links

FIX_LINKS = Path(__file__).resolve().parent.parent / "llm-wiki" / "scripts" / "fix_links.py"


def test_norm_key():
    assert fix_links.norm("My Paper 2024") == "my-paper-2024"
    assert fix_links.norm("first---principles") == "first-principles"


def test_link_resolves_bare_stem():
    by_cat_stem = {"concepts/alpha", "sources/beta"}
    assert fix_links.link_resolves("alpha", by_cat_stem)          # bare stem in some category
    assert fix_links.link_resolves("concepts/alpha", by_cat_stem)  # exact
    assert not fix_links.link_resolves("concepts/missing", by_cat_stem)


def test_wrong_category_link_is_repaired(scaffolded_vault):
    wiki = scaffolded_vault / "wiki"
    # Real page lives in concepts/
    (wiki / "concepts" / "first-principles.md").write_text(
        "---\ntype: concept\ntitle: First Principles\n---\n# First Principles\n"
    )
    # A source page links to the WRONG category (concept/ vs concepts/)
    src = wiki / "sources" / "test-source.md"
    src.write_text(
        "---\ntype: source-summary\ntitle: Test Source\n---\n"
        "# Test Source\n\nSee [[concept/first-principles]] for the idea.\n"
    )

    result = subprocess.run(
        [sys.executable, str(FIX_LINKS), str(scaffolded_vault), "--apply"],
        capture_output=True, text=True,
    )
    assert result.returncode in (0, 1), result.stderr

    fixed = src.read_text()
    assert "[[concepts/first-principles]]" in fixed
    assert "[[concept/first-principles]]" not in fixed


def test_stub_created_for_missing_target(scaffolded_vault):
    wiki = scaffolded_vault / "wiki"
    src = wiki / "sources" / "orphan-ref.md"
    src.write_text(
        "---\ntype: source-summary\ntitle: Orphan Ref\n---\n"
        "# Orphan Ref\n\nMentions [[concepts/nonexistent-thing]].\n"
    )

    result = subprocess.run(
        [sys.executable, str(FIX_LINKS), str(scaffolded_vault), "--apply", "--stub"],
        capture_output=True, text=True,
    )
    assert result.returncode in (0, 1), result.stderr

    stub = wiki / "concepts" / "nonexistent-thing.md"
    assert stub.exists(), "expected a stub page to be created for the missing target"
    assert "stub: true" in stub.read_text()
