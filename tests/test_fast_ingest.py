"""Tests for the deterministic bookkeeping in fast_ingest.py."""
import hashlib

import fast_ingest


class TestGenerateSlug:
    def test_basic_lowercase_and_hyphenate(self):
        assert fast_ingest.generate_slug("Attention Is All You Need.md") == "attention-is-all-you-need"

    def test_strips_leading_date_prefix(self):
        assert fast_ingest.generate_slug("2026-04-28-my-note.md") == "my-note"

    def test_collapses_and_trims_hyphens(self):
        assert fast_ingest.generate_slug("  Foo --- Bar!! .md") == "foo-bar"

    def test_title_overrides_filename(self):
        assert fast_ingest.generate_slug("whatever.md", title="Real Title") == "real-title"

    def test_empty_becomes_untitled(self):
        assert fast_ingest.generate_slug("!!!.md") == "untitled"

    def test_unicode_dropped_to_hyphens(self):
        # Non-ascii is not alphanumeric in [a-z0-9]; slug stays english-only.
        assert fast_ingest.generate_slug("第一性原理.md") == "untitled"


def test_compute_sha256_matches_hashlib(tmp_path):
    f = tmp_path / "src.md"
    f.write_bytes(b"hello wiki\n")
    expected = hashlib.sha256(b"hello wiki\n").hexdigest()
    assert fast_ingest.compute_sha256(str(f)) == expected


class TestDetectSourceType:
    def test_personal_path(self, tmp_path):
        root = tmp_path
        personal = root / "raw" / "personal" / "diary.md"
        personal.parent.mkdir(parents=True)
        personal.write_text("# Diary\nmy thoughts\n")
        assert fast_ingest.detect_source_type(str(personal), str(root)) == "personal-writing"

    def test_frontmatter_type_personal(self, tmp_path):
        root = tmp_path
        f = root / "raw" / "articles" / "essay.md"
        f.parent.mkdir(parents=True)
        f.write_text("---\ntype: personal-writing\n---\n# Essay\n")
        assert fast_ingest.detect_source_type(str(f), str(root)) == "personal-writing"

    def test_default_external(self, tmp_path):
        root = tmp_path
        f = root / "raw" / "articles" / "paper.md"
        f.parent.mkdir(parents=True)
        f.write_text("# Paper\ncontent\n")
        assert fast_ingest.detect_source_type(str(f), str(root)) == "source-summary"


class TestExtractTitle:
    def test_h1_heading(self, tmp_path):
        f = tmp_path / "a.md"
        f.write_text("# The Title\n\nbody\n")
        assert fast_ingest.extract_title_from_md(str(f)) == "The Title"

    def test_frontmatter_title_wins(self, tmp_path):
        f = tmp_path / "b.md"
        f.write_text('---\ntitle: "FM Title"\n---\n# H1 Title\n')
        assert fast_ingest.extract_title_from_md(str(f)) == "FM Title"


def test_fast_ingest_creates_personal_stub(tmp_path, capsys):
    """The /save flow relies on raw/personal/ producing a personal-writing stub."""
    root = tmp_path / "vault"
    (root / "raw" / "personal").mkdir(parents=True)
    (root / "wiki" / "sources").mkdir(parents=True)
    note = root / "raw" / "personal" / "2026-07-06-idea.md"
    note.write_text("---\ntitle: My Idea\ntype: personal-writing\n---\n# My Idea\nsome reasoning\n")

    fast_ingest.fast_ingest(str(root), "raw/personal/2026-07-06-idea.md")

    # Slug comes from the note's title ("My Idea"), not the dated filename.
    stub = root / "wiki" / "sources" / "my-idea.md"
    assert stub.exists(), "expected a stub at wiki/sources/my-idea.md"
    text = stub.read_text()
    assert "type: personal-writing" in text
    assert "raw/personal/2026-07-06-idea.md" in text
    assert "raw_sha256:" in text
