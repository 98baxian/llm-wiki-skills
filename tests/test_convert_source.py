"""Tests for convert_source.py: config, slugs, hashing."""
import pytest

import convert_source


def _vault(tmp_path, body=None):
    text = "# Wiki\n\n## Scope\n\n- AI\n"
    if body is not None:
        text += "\n## Document conversion\n\n" + body + "\n"
    (tmp_path / "CLAUDE.md").write_text(text, encoding="utf-8")
    return tmp_path


class TestConfig:
    def test_defaults_without_section(self, tmp_path):
        cfg = convert_source.load_config(_vault(tmp_path))
        assert (cfg.converter, cfg.tier, cfg.timeout, cfg.command) == ("auto", "basic", 900, "")

    def test_reads_values(self, tmp_path):
        body = 'converter: command\ntier: flash\ntimeout: 60\ncommand: "mytool {input} {outdir}"'
        cfg = convert_source.load_config(_vault(tmp_path, body))
        assert (cfg.converter, cfg.tier, cfg.timeout) == ("command", "flash", 60)
        assert cfg.command == "mytool {input} {outdir}"

    def test_missing_claude_md_gives_defaults(self, tmp_path):
        assert convert_source.load_config(tmp_path).converter == "auto"

    @pytest.mark.parametrize("body", ["converter: nope", "tier: huge", "timeout: soon", "timeout: 0"])
    def test_invalid_values_exit_2(self, tmp_path, body):
        with pytest.raises(convert_source.ConvertError) as exc:
            convert_source.load_config(_vault(tmp_path, body))
        assert exc.value.code == convert_source.EXIT_USAGE


class TestSlug:
    def test_basic(self):
        assert convert_source.sanitize_slug("My Paper (v2): Final!.pdf") == "my-paper-v2-final"

    def test_cjk_is_kept(self):
        assert convert_source.sanitize_slug("第一性原理 笔记.pdf") == "第一性原理-笔记"

    def test_citation_breaking_characters_removed(self):
        slug = convert_source.sanitize_slug("a,b (1); c) d.pdf")
        assert not any(ch in slug for ch in ",();")

    def test_empty_becomes_untitled(self):
        assert convert_source.sanitize_slug("!!!.pdf") == "untitled"


def test_sha256_file(tmp_path):
    f = tmp_path / "x.bin"
    f.write_bytes(b"hello")
    assert convert_source.sha256_file(f) == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"


"""pdftotext converter: markdown written page by page, exact page map."""
import shutil

import pytest

from pdf_helpers import make_pdf

import convert_source

needs_poppler = pytest.mark.skipif(shutil.which("pdftotext") is None, reason="pdftotext not installed")


@needs_poppler
class TestPdftotext:
    def _pdf(self, tmp_path, pages):
        path = tmp_path / "paper.pdf"
        path.write_bytes(make_pdf(pages))
        return path

    def test_pages_split_on_form_feed(self, tmp_path):
        pdf = self._pdf(tmp_path, ["Alpha line one\nAlpha line two", "Beta page text here"])
        pages = convert_source.pdftotext_pages(pdf)
        assert len(pages) == 2
        assert "Alpha line two" in pages[0] and "Beta page text" in pages[1]

    def test_markdown_and_exact_page_map(self, tmp_path):
        pdf = self._pdf(tmp_path, ["Alpha line one\nAlpha line two", "Beta page text here"])
        out = convert_source.convert_pdftotext(pdf)
        lines = out.markdown.rstrip("\n").split("\n")
        assert out.converter == "pdftotext" and out.lossy is True
        assert out.page_map == "exact" and out.confidence == "high" and out.pages_total == 2
        p1, p2 = out.pages
        assert (p1["page"], p1["how"]) == (1, "exact") and p2["page"] == 2
        assert "Alpha line one" in lines[p1["line_start"] - 1]
        assert "Beta page text" in lines[p2["line_start"] - 1]
        assert p1["line_end"] + 1 == p2["line_start"]          # contiguous partition
        assert p2["line_end"] == len(lines)

    def test_scanned_pdf_exits_4(self, tmp_path):
        pdf = self._pdf(tmp_path, [" "])
        with pytest.raises(convert_source.ConvertError) as exc:
            convert_source.convert_pdftotext(pdf)
        assert exc.value.code == convert_source.EXIT_SCANNED


def test_missing_pdftotext_exits_2(tmp_path, monkeypatch):
    monkeypatch.setattr(convert_source.shutil, "which", lambda name: None)
    with pytest.raises(convert_source.ConvertError) as exc:
        convert_source.pdftotext_pages(tmp_path / "x.pdf")
    assert exc.value.code == convert_source.EXIT_USAGE
