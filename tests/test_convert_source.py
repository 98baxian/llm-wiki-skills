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


"""Custom command converter: the user's own tool, run without a shell."""
import json
import shlex
import sys

import pytest

import convert_source

SCRIPT = '''
import json, os, pathlib, sys, time
pdf, outdir, slug = sys.argv[1:4]
if os.environ.get("FAKE_CMD_SLEEP"):
    time.sleep(float(os.environ["FAKE_CMD_SLEEP"]))
if os.environ.get("FAKE_CMD_FAIL"):
    sys.stderr.write("boom from tool")
    sys.exit(1)
out = pathlib.Path(outdir)
if not os.environ.get("FAKE_CMD_NO_MD"):
    (out / (slug + ".md")).write_text("Converted line one\\nConverted line two\\n", encoding="utf-8")
if os.environ.get("FAKE_CMD_PAGES"):
    (out / (slug + ".pages.json")).write_text(json.dumps({
        "match_rate": 1.0, "confidence": "high",
        "pages": [{"page": 1, "line_start": 1, "line_end": 2, "how": "exact"}]}), encoding="utf-8")
    (out / "images").mkdir()
    (out / "images" / "fig.png").write_bytes(b"PNG")
with open(os.environ.get("FAKE_CMD_LOG", os.devnull), "a") as log:
    log.write(pdf + "\\n")
'''


@pytest.fixture
def tool(tmp_path):
    script = tmp_path / "tool.py"
    script.write_text(SCRIPT, encoding="utf-8")
    template = f"{shlex.quote(sys.executable)} {shlex.quote(str(script))} {{input}} {{outdir}} {{slug}}"
    return template


def test_writes_markdown_only(tool, tmp_path):
    pdf = tmp_path / "My Paper (final).pdf"        # spaces and parentheses must survive intact
    pdf.write_bytes(b"%PDF")
    out = convert_source.convert_command(tool, pdf, "my-paper")
    assert out.markdown == "Converted line one\nConverted line two\n"
    assert (out.converter, out.version, out.lossy, out.page_map) == ("command", "custom", False, "none")
    assert out.pages is None and out.images == {}


def test_pages_json_and_images_are_picked_up(tool, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CMD_PAGES", "1")
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF")
    out = convert_source.convert_command(tool, pdf, "x")
    assert out.page_map == "supplied" and out.confidence == "high"
    assert out.pages[0]["page"] == 1
    assert out.images == {"images/fig.png": b"PNG"}


def test_missing_markdown_is_a_failure(tool, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CMD_NO_MD", "1")
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF")
    with pytest.raises(convert_source.ConvertError) as exc:
        convert_source.convert_command(tool, pdf, "x")
    assert exc.value.code == convert_source.EXIT_FAILED and "x.md" in str(exc.value)


def test_nonzero_exit_reports_stderr(tool, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CMD_FAIL", "1")
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF")
    with pytest.raises(convert_source.ConvertError) as exc:
        convert_source.convert_command(tool, pdf, "x")
    assert exc.value.code == convert_source.EXIT_FAILED and "boom from tool" in str(exc.value)


def test_timeout(tool, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CMD_SLEEP", "5")
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF")
    with pytest.raises(convert_source.ConvertError) as exc:
        convert_source.convert_command(tool, pdf, "x", timeout=1)
    assert exc.value.code == convert_source.EXIT_FAILED and "timed out" in str(exc.value)


def test_unknown_program_and_empty_template_exit_2(tmp_path):
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF")
    with pytest.raises(convert_source.ConvertError) as exc:
        convert_source.convert_command("no-such-program-xyz {input} {outdir}", pdf, "x")
    assert exc.value.code == convert_source.EXIT_USAGE
    with pytest.raises(convert_source.ConvertError) as exc:
        convert_source.convert_command("  ", pdf, "x")
    assert exc.value.code == convert_source.EXIT_USAGE
