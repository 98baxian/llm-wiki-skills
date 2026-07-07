"""Tests for scaffold.py structure and the hot-cache wiring."""
import subprocess
import sys
from pathlib import Path

import lint_wiki

LINT = Path(__file__).resolve().parent.parent / "llm-wiki" / "scripts" / "lint_wiki.py"


def test_scaffold_creates_core_files(scaffolded_vault):
    root = scaffolded_vault
    for rel in ("CLAUDE.md", "wiki/index.md", "wiki/hot.md", "wiki/overview.md",
                "wiki/QUESTIONS.md"):
        assert (root / rel).exists(), f"missing {rel}"


def test_scaffold_creates_expected_dirs(scaffolded_vault):
    root = scaffolded_vault
    for rel in ("raw/personal", "raw/pdfs", "wiki/sources", "wiki/concepts",
                "wiki/entities", "wiki/comparisons", "wiki/synthesis",
                "wiki/templates", "log", "audit/resolved", "outputs/queries"):
        assert (root / rel).is_dir(), f"missing dir {rel}"


def test_hot_md_frontmatter(scaffolded_vault):
    hot = (scaffolded_vault / "wiki" / "hot.md").read_text()
    assert "type: system-hot" in hot
    assert "graph-excluded: true" in hot
    # It is a cache with the expected section skeleton.
    assert "## Last updated" in hot
    assert "## Active threads" in hot


def test_claude_md_lists_nine_ops_including_save(scaffolded_vault):
    claude = (scaffolded_vault / "CLAUDE.md").read_text()
    assert "nine operations" in claude
    assert "save" in claude


def test_hot_md_is_a_system_file_for_lint():
    """Regression guard: lint must skip hot.md so it is never an orphan/stale page."""
    assert "hot.md" in lint_wiki.SYSTEM_FILES


def test_lint_wiki_files_excludes_hot_md(scaffolded_vault):
    wiki = scaffolded_vault / "wiki"
    names = {f.name for f in lint_wiki.wiki_files(wiki)}
    assert "hot.md" not in names
    assert "index.md" not in names  # sanity: other system files excluded too


def test_lint_run_does_not_flag_hot_md(scaffolded_vault):
    """End-to-end: a full lint of a fresh vault must not report hot.md as an issue."""
    result = subprocess.run(
        [sys.executable, str(LINT), str(scaffolded_vault)],
        capture_output=True, text=True,
    )
    # lint returns non-zero when issues exist; either way it must not name hot.md.
    combined = result.stdout + result.stderr
    assert "hot.md" not in combined, f"lint unexpectedly flagged hot.md:\n{combined}"
