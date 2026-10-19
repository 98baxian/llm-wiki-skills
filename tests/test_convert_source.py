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


"""Page map: align MinerU blocks with markdown lines."""
import convert_source

MD = "\n".join([
    "# Evidence Aggregator for rare disease",                              # 1
    "",                                                                   # 2
    "Retrieving and synthesizing technical information is hard work.",    # 3
    "",                                                                   # 4
    "## Introduction",                                                    # 5
    "",                                                                   # 6
    "Rare diseases affect many people worldwide and diagnosis is slow.",  # 7
    "",                                                                   # 8
    "![](images/page_2_image_4.jpg)",                                     # 9
    "",                                                                   # 10
    "We evaluated the tool with a set of solved cases from the clinic.",  # 11
    "",                                                                   # 12
    "| System | Score |",                                                 # 13
    "| --- | --- |",                                                      # 14
    "| Ours | 91 |",                                                      # 15
    "",                                                                   # 16
    "Retrieving and synthesizing technical information is hard work.",    # 17 (repeated text)
]) + "\n"


def blk(kind, text):
    return {"type": kind, "content": text}


PAGES = [
    {"page_idx": 0, "blocks": [
        blk("header", "bioRxiv preprint doi: 10.1101/2025.03.10"),
        blk("doc_title", "Evidence Aggregator for rare disease"),
        blk("text", "Retrieving and synthesizing technical information is hard work."),
        blk("text", "- PREPRINT - Page 1 of 22"),
        blk("page_number", "1"),
    ]},
    {"page_idx": 1, "blocks": [
        blk("paragraph_title", "Introduction"),
        blk("text", "Rare diseases affect many people worldwide and diagnosis is slow."),
        blk("page_footnote", "1 https://example.org/tool"),
    ]},
    {"page_idx": 2, "blocks": [
        blk("text", "We evaluated the tool with a set of solved cases from the clinic."),
        blk("table", "<table><tr><td>System</td><td>Score</td></tr></table>"),
        blk("text", "Retrieving and synthesizing technical information is hard work."),
    ]},
]


class TestAlign:
    def test_ranges_form_a_partition_with_forward_search(self):
        ranges, rate, confidence = convert_source.align_pages(MD, PAGES)
        assert [(r["page"], r["line_start"], r["line_end"], r["how"]) for r in ranges] == [
            (1, 1, 4, "aligned"), (2, 5, 10, "aligned"), (3, 11, 17, "aligned"),
        ]
        assert round(rate, 3) == round(6 / 7, 3) and confidence == "high"

    def test_repeated_text_maps_to_the_later_occurrence(self):
        ranges, _, _ = convert_source.align_pages(MD, PAGES)
        assert ranges[2]["line_end"] == 17          # not pulled back to line 3

    def test_page_idx_is_absolute_across_windows(self):
        shifted = [dict(p, page_idx=p["page_idx"] + 4) for p in PAGES]
        ranges, _, _ = convert_source.align_pages(MD, shifted)
        assert [r["page"] for r in ranges] == [5, 6, 7]

    def test_page_without_matches_is_interpolated(self):
        pages = PAGES + [{"page_idx": 3, "blocks": [blk("image", "fig")]}]
        ranges, _, _ = convert_source.align_pages(MD, pages)
        last = ranges[-1]
        assert last["how"] == "interpolated" and last["page"] == 4
        assert 1 <= last["line_start"] <= last["line_end"] <= 17

    def test_nothing_matches_gives_low_confidence(self):
        pages = [{"page_idx": 0, "blocks": [blk("text", "completely unrelated sentence here")]}]
        ranges, rate, confidence = convert_source.align_pages(MD, pages)
        assert rate == 0.0 and confidence == "low" and ranges[0]["how"] == "interpolated"

    def test_escapes_and_case_do_not_break_matching(self):
        md = "Growth of 5\\*3 PERCENT was reported in the second quarter.\n"
        pages = [{"page_idx": 0, "blocks": [blk("text", "growth of 5*3 percent was reported in the second quarter.")]}]
        _, rate, _ = convert_source.align_pages(md, pages)
        assert rate == 1.0


class TestDroppedLines:
    def test_collects_margin_blocks_once_per_page(self):
        pages = [
            {"page_idx": 0, "blocks": [blk("header", "Running  header"), blk("header", "Running header"),
                                       blk("text", "body"), blk("footer", "p. 1")]},
            {"page_idx": 1, "blocks": [blk("page_footnote", "1 https://example.org"), blk("page_number", "2")]},
        ]
        assert convert_source.dropped_lines(pages) == [
            "p1\theader\tRunning header", "p1\tfooter\tp. 1",
            "p2\tpage_footnote\t1 https://example.org", "p2\tpage_number\t2",
        ]

    def test_flat_text_handles_nested_content(self):
        assert convert_source.flat_text([{"type": "text", "content": "a"}, {"content": ["b", "c"]}]) == "a b c"


"""MinerU converter: page windows, kill-and-halve retry, zip reading, schema guard."""
import io
import json
import os
import stat
import sys
import zipfile

import pytest

import convert_source

FAKE_KIT = '''#!__PYTHON__
import json, os, signal, sys, time, zipfile
argv = sys.argv[1:]
out = argv[argv.index("-o") + 1]
span = argv[argv.index("-p") + 1]
first, _, last = span.partition("-")
first = int(first)
last = int(last or first)
with open(os.environ["FAKE_KIT_LOG"], "a") as log:
    log.write(json.dumps({"argv": argv, "cuda": os.environ.get("CUDA_VISIBLE_DEVICES")}) + "\\n")
if os.environ.get("FAKE_KIT_SLEEP"):
    time.sleep(float(os.environ["FAKE_KIT_SLEEP"]))
limit = os.environ.get("FAKE_KIT_KILL_OVER")
if limit and last - first + 1 > int(limit):
    os.kill(os.getpid(), signal.SIGKILL)
md, pages = [], []
for n in range(first, last + 1):
    text = f"Page {n} paragraph with enough words to align."
    md.append(text)
    pages.append({"page_idx": n - 1, "blocks": [
        {"type": "header", "content": "running header"},
        {"type": "text", "content": text},
    ]})
middle = {"schema": "docvortex.middle", "schema_version": os.environ.get("FAKE_KIT_SCHEMA", "2.0"),
          "metadata": {"producer": {"name": "mineru", "version": "9.9.9"}}, "pages": pages}
with zipfile.ZipFile(os.path.join(out, "doc.zip"), "w") as z:
    z.writestr("markdown.md", "\\n\\n".join(md) + "\\n")
    z.writestr("middle_json.json", json.dumps(middle))
    z.writestr("images/img_%d.jpg" % first, b"JPG")
'''


@pytest.fixture
def kit(tmp_path, monkeypatch):
    path = tmp_path / "fake-kit"
    path.write_text(FAKE_KIT.replace("__PYTHON__", sys.executable), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    log = tmp_path / "kit.log"
    monkeypatch.setenv("FAKE_KIT_LOG", str(log))
    monkeypatch.setenv("MINERU_KIT_BIN", str(path))
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    return path, log


def calls(log):
    return [json.loads(line) for line in log.read_text().splitlines()]


def test_plan_windows():
    assert convert_source.plan_windows(20, 8) == [(1, 8), (9, 16), (17, 20)]
    assert convert_source.plan_windows(3, 8) == [(1, 3)]


def test_find_mineru_kit_prefers_env(kit):
    assert convert_source.find_mineru_kit() == str(kit[0])


def test_find_mineru_kit_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("MINERU_KIT_BIN", raising=False)
    monkeypatch.setattr(convert_source.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(convert_source.shutil, "which", lambda name: None)
    assert convert_source.find_mineru_kit() is None


def test_read_kit_zip(tmp_path):
    buf = tmp_path / "x.zip"
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("markdown.md", "# T\n")
        z.writestr("middle_json.json", json.dumps({"pages": []}))
        z.writestr("images/a.jpg", b"A")
        z.writestr("model_output.json", "{}")
    res = convert_source.read_kit_zip(buf)
    assert res.markdown == "# T\n" and res.middle == {"pages": []} and res.images == {"images/a.jpg": b"A"}


def test_window_command_has_no_remote_flags_and_hides_gpu_on_cpu_tiers(kit, tmp_path):
    pdf = tmp_path / "d.pdf"
    pdf.write_bytes(b"%PDF")
    convert_source.run_kit_window(str(kit[0]), pdf, 1, 2, "basic", 60)
    convert_source.run_kit_window(str(kit[0]), pdf, 3, 3, "standard", 60)
    first, second = calls(kit[1])
    assert first["argv"][:2] == ["parse", str(pdf)]
    assert first["argv"][first["argv"].index("-f") + 1] == "zip"
    assert first["argv"][first["argv"].index("-p") + 1] == "1-2"
    assert second["argv"][second["argv"].index("-p") + 1] == "3"
    for call in (first, second):
        assert not any(a.startswith("--remote") or a == "--api-key" for a in call["argv"])
    assert first["cuda"] == ""          # basic tier: GPU hidden
    assert second["cuda"] is None       # standard tier: left alone


def test_killed_window_raises_windowkilled(kit, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_KIT_KILL_OVER", "1")
    pdf = tmp_path / "d.pdf"
    pdf.write_bytes(b"%PDF")
    with pytest.raises(convert_source.WindowKilled):
        convert_source.run_kit_window(str(kit[0]), pdf, 1, 2, "basic", 60)


def test_timeout_is_a_failure(kit, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_KIT_SLEEP", "5")
    pdf = tmp_path / "d.pdf"
    pdf.write_bytes(b"%PDF")
    with pytest.raises(convert_source.ConvertError) as exc:
        convert_source.run_kit_window(str(kit[0]), pdf, 1, 1, "basic", 1)
    assert exc.value.code == convert_source.EXIT_FAILED and "timed out" in str(exc.value)


class TestHalving:
    def test_halves_until_windows_fit(self):
        seen = []

        def runner(first, last):
            seen.append((first, last))
            if last - first + 1 > 2:
                raise convert_source.WindowKilled()
            return (first, last)

        assert convert_source.run_with_halving(runner, 1, 8) == [(1, 2), (3, 4), (5, 6), (7, 8)]
        assert seen[0] == (1, 8)

    def test_single_page_that_dies_fails_with_exit_3(self):
        def runner(first, last):
            raise convert_source.WindowKilled()

        with pytest.raises(convert_source.ConvertError) as exc:
            convert_source.run_with_halving(runner, 4, 4)
        assert exc.value.code == convert_source.EXIT_FAILED and "page 4" in str(exc.value)


class TestConvertMineru:
    def _pdf(self, tmp_path):
        pdf = tmp_path / "d.pdf"
        pdf.write_bytes(b"%PDF")
        return pdf

    def test_end_to_end_with_halving_and_merge(self, kit, tmp_path, monkeypatch):
        monkeypatch.setenv("FAKE_KIT_KILL_OVER", "2")
        out = convert_source.convert_mineru(self._pdf(tmp_path), "basic", 60, total_pages=5, window=4)
        assert [c["argv"][c["argv"].index("-p") + 1] for c in calls(kit[1])] == ["1-4", "1-2", "3-4", "5"]
        assert out.markdown.count("paragraph with enough words") == 5
        assert out.markdown.index("Page 1 ") < out.markdown.index("Page 5 ")
        assert out.converter == "mineru-kit" and out.version == "9.9.9" and out.lossy is False
        assert out.page_map == "aligned" and out.confidence == "high" and out.pages_total == 5
        assert [p["page"] for p in out.pages] == [1, 2, 3, 4, 5]
        assert set(out.images) == {"images/img_1.jpg", "images/img_3.jpg", "images/img_5.jpg"}
        assert out.dropped_lines[0] == "p1\theader\trunning header"

    def test_unknown_schema_skips_page_map_but_succeeds(self, kit, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("FAKE_KIT_SCHEMA", "3.0")
        out = convert_source.convert_mineru(self._pdf(tmp_path), "basic", 60, total_pages=2)
        assert out.pages is None and out.page_map == "none" and out.confidence == "low"
        assert "schema" in capsys.readouterr().err

    def test_missing_kit_exit_2(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MINERU_KIT_BIN", raising=False)
        monkeypatch.setattr(convert_source, "find_mineru_kit", lambda: None)
        with pytest.raises(convert_source.ConvertError) as exc:
            convert_source.convert_mineru(self._pdf(tmp_path), "basic", 60, total_pages=1)
        assert exc.value.code == convert_source.EXIT_USAGE


"""convert(): resolution chain, idempotence, collisions, private zones, atomic output, CLI."""
import json
import shlex
import shutil
import sys

import pytest

from pdf_helpers import make_pdf

import convert_source

TOOL = '''
import os, pathlib, sys
pdf, outdir, slug = sys.argv[1:4]
with open(os.environ["FAKE_CMD_LOG"], "a") as log:
    log.write(pdf + "\\n")
if os.environ.get("FAKE_CMD_FAIL"):
    (pathlib.Path(outdir) / "partial.txt").write_text("x")
    sys.exit(1)
(pathlib.Path(outdir) / (slug + ".md")).write_text("Converted line one\\nConverted line two\\n", encoding="utf-8")
'''


@pytest.fixture
def vault(tmp_path, monkeypatch):
    root = tmp_path / "vault"
    (root / "raw" / "pdfs").mkdir(parents=True)
    tool = tmp_path / "tool.py"
    tool.write_text(TOOL, encoding="utf-8")
    log = tmp_path / "cmd.log"
    monkeypatch.setenv("FAKE_CMD_LOG", str(log))
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(tool))} {{input}} {{outdir}} {{slug}}"
    (root / "CLAUDE.md").write_text(
        "# Wiki\n\n## Document conversion\n\nconverter: command\n"
        f"command: {command}\n\n## Private zones\n\n- `sensitive_data/`\n",
        encoding="utf-8",
    )
    (root / "sensitive_data").mkdir()
    return root


def add_pdf(root, name="Paper One.pdf", payload=b"%PDF-1"):
    path = root / "raw" / "pdfs" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def runs(root):
    log = root.parent / "cmd.log"
    return len(log.read_text().splitlines()) if log.exists() else 0


def test_creates_script_owned_artifacts(vault):
    res = convert_source.convert(add_pdf(vault), vault)
    assert res.slug == "paper-one" and not res.skipped
    assert res.md_path == vault / "raw" / "converted" / "paper-one" / "paper-one.md"
    meta = json.loads((res.directory / "paper-one.convert.json").read_text())
    assert meta["schema"] == "llm-wiki.convert/1" and meta["converter"] == "command"
    assert meta["source_pdf"] == "raw/pdfs/Paper One.pdf"
    assert meta["source_pdf_sha256"] == convert_source.sha256_file(vault / "raw" / "pdfs" / "Paper One.pdf")
    assert meta["md_sha256"] == convert_source.sha256_file(res.md_path)
    assert meta["lossy"] is False and meta["page_map"] == "none"


def test_second_call_is_skipped_without_running_the_tool(vault):
    pdf = add_pdf(vault)
    convert_source.convert(pdf, vault)
    again = convert_source.convert(pdf, vault)
    assert again.skipped and runs(vault) == 1


def test_changed_pdf_needs_force(vault):
    pdf = add_pdf(vault)
    first = convert_source.convert(pdf, vault)
    before = first.md_path.read_bytes()
    pdf.write_bytes(b"%PDF-2 changed")
    with pytest.raises(convert_source.ConvertError) as exc:
        convert_source.convert(pdf, vault)
    assert exc.value.code == convert_source.EXIT_EXISTS and first.md_path.read_bytes() == before
    forced = convert_source.convert(pdf, vault, force=True)
    meta = json.loads((forced.directory / "paper-one.convert.json").read_text())
    assert not forced.skipped and meta["source_pdf_sha256"] == convert_source.sha256_file(pdf)
    assert runs(vault) == 2


def test_two_pdfs_with_the_same_name_do_not_collide(vault):
    a = add_pdf(vault, "a/paper.pdf", b"%PDF-A")
    b = add_pdf(vault, "b/paper.pdf", b"%PDF-B")
    ra = convert_source.convert(a, vault)
    rb = convert_source.convert(b, vault)
    assert ra.slug == "paper" and rb.slug.startswith("paper-") and rb.slug != ra.slug
    assert rb.md_path.exists() and ra.md_path.exists()


def test_private_zone_input_is_refused_and_nothing_is_written(vault):
    secret = vault / "sensitive_data" / "report.pdf"
    secret.write_bytes(b"%PDF")
    with pytest.raises(convert_source.ConvertError) as exc:
        convert_source.convert(secret, vault)
    assert exc.value.code == convert_source.EXIT_PRIVATE
    assert not (vault / "raw" / "converted").exists() and runs(vault) == 0


def test_failed_conversion_leaves_no_partial_output(vault, monkeypatch):
    monkeypatch.setenv("FAKE_CMD_FAIL", "1")
    with pytest.raises(convert_source.ConvertError) as exc:
        convert_source.convert(add_pdf(vault), vault)
    assert exc.value.code == convert_source.EXIT_FAILED
    base = vault / "raw" / "converted"
    assert not base.exists() or list(base.iterdir()) == []


def test_converter_none_is_refused(vault):
    cfg = convert_source.Config(converter="none")
    with pytest.raises(convert_source.ConvertError) as exc:
        convert_source.convert(add_pdf(vault), vault, config=cfg)
    assert exc.value.code == convert_source.EXIT_USAGE


class TestAutoChain:
    def test_command_wins_over_other_converters(self, vault, monkeypatch):
        monkeypatch.setattr(convert_source, "find_mineru_kit", lambda: "/x/mineru-kit")
        cfg = convert_source.load_config(vault)
        cfg.converter = "auto"
        assert convert_source.candidate_converters(cfg)[0] == "command"

    def test_order_without_command(self, monkeypatch):
        monkeypatch.setattr(convert_source, "find_mineru_kit", lambda: "/x/mineru-kit")
        monkeypatch.setattr(convert_source.shutil, "which", lambda name: "/usr/bin/pdftotext")
        assert convert_source.candidate_converters(convert_source.Config()) == ["mineru", "pdftotext"]

    def test_nothing_available_exit_2_with_hint(self, monkeypatch):
        monkeypatch.setattr(convert_source, "find_mineru_kit", lambda: None)
        monkeypatch.setattr(convert_source.shutil, "which", lambda name: None)
        with pytest.raises(convert_source.ConvertError) as exc:
            convert_source.candidate_converters(convert_source.Config())
        assert exc.value.code == convert_source.EXIT_USAGE and "mineru-kit" in str(exc.value)


def test_mineru_path_writes_pages_dropped_and_images(vault):
    def runner(first, last):
        pages = [{"page_idx": n - 1, "blocks": [
            {"type": "header", "content": "running header"},
            {"type": "text", "content": f"Page {n} paragraph with enough words to align."},
        ]} for n in range(first, last + 1)]
        md = "\n\n".join(f"Page {n} paragraph with enough words to align." for n in range(first, last + 1))
        middle = {"schema": "docvortex.middle", "schema_version": "2.0",
                  "metadata": {"producer": {"version": "4.0.12"}}, "pages": pages}
        return convert_source.WindowResult(md + "\n", middle, {f"images/p{first}.jpg": b"JPG"})

    cfg = convert_source.Config(converter="mineru")
    res = convert_source.convert(add_pdf(vault), vault, config=cfg, runner=runner, total_pages=3)
    d = res.directory
    pages = json.loads((d / "paper-one.pages.json").read_text())
    assert pages["schema"] == "llm-wiki.pages/1" and pages["confidence"] == "high"
    assert [p["page"] for p in pages["pages"]] == [1, 2, 3]
    assert "p1\theader\trunning header" in (d / "paper-one.dropped.txt").read_text()
    assert (d / "images" / "p1.jpg").read_bytes() == b"JPG"
    meta = json.loads((d / "paper-one.convert.json").read_text())
    assert meta["converter"] == "mineru-kit" and meta["version"] == "4.0.12" and meta["page_map"] == "aligned"
    assert meta["pages_total"] == 3 and meta["dropped"] == ["header", "footer", "page_number", "page_footnote"]


def test_image_names_cannot_escape_the_output_directory(vault):
    def runner(first, last):
        middle = {"schema": "docvortex.middle", "schema_version": "2.0", "pages": []}
        return convert_source.WindowResult("text\n", middle, {"images/../../evil.txt": b"x"})

    cfg = convert_source.Config(converter="mineru")
    res = convert_source.convert(add_pdf(vault), vault, config=cfg, runner=runner, total_pages=1)
    assert not (vault / "raw" / "evil.txt").exists() and not (res.directory.parent / "evil.txt").exists()


@pytest.mark.skipif(shutil.which("pdftotext") is None, reason="pdftotext not installed")
def test_pdftotext_end_to_end_marks_lossy(vault):
    pdf = add_pdf(vault, "scan.pdf", make_pdf(["Alpha line one\nAlpha line two", "Beta page text here"]))
    cfg = convert_source.Config(converter="pdftotext")
    res = convert_source.convert(pdf, vault, config=cfg)
    meta = json.loads((res.directory / "scan.convert.json").read_text())
    assert meta["lossy"] is True and meta["converter"] == "pdftotext" and meta["page_map"] == "exact"
    assert res.lossy is True


class TestCli:
    def test_ok_prints_path_and_returns_0(self, vault, capsys):
        pdf = add_pdf(vault)
        assert convert_source.main([str(vault), str(pdf)]) == 0
        assert "raw/converted/paper-one/paper-one.md" in capsys.readouterr().out

    def test_error_code_is_returned(self, vault, capsys):
        secret = vault / "sensitive_data" / "r.pdf"
        secret.write_bytes(b"%PDF")
        assert convert_source.main([str(vault), str(secret)]) == convert_source.EXIT_PRIVATE
        assert "private zone" in capsys.readouterr().err

    def test_missing_pdf_is_usage_error(self, vault):
        assert convert_source.main([str(vault), str(vault / "nope.pdf")]) == convert_source.EXIT_USAGE
