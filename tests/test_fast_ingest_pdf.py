"""fast_ingest converts a PDF first and ingests the converted markdown."""
import re
import shlex
import shutil
import sys

import pytest

from pdf_helpers import make_pdf

import fast_ingest

TOOL = '''
import pathlib, sys
pdf, outdir, slug = sys.argv[1:4]
(pathlib.Path(outdir) / (slug + ".md")).write_text(
    "# Paper One\\n\\nConverted line one\\nConverted line two\\n", encoding="utf-8")
'''


def set_conversion(root, tmp_path, converter="command"):
    """Replace the vault's Document conversion section with one that uses a fake tool."""
    tool = tmp_path / "tool.py"
    tool.write_text(TOOL, encoding="utf-8")
    claude = root / "CLAUDE.md"
    text = re.sub(r"\n## Document conversion\n.*?(?=\n## |\Z)", "", claude.read_text(encoding="utf-8"), flags=re.S)
    text += (
        "\n## Document conversion\n\n"
        f"converter: {converter}\n"
        f"command: {shlex.quote(sys.executable)} {shlex.quote(str(tool))} {{input}} {{outdir}} {{slug}}\n"
    )
    claude.write_text(text, encoding="utf-8")


def stub_text(root):
    stubs = list((root / "wiki" / "sources").glob("*.md"))
    assert len(stubs) == 1
    return stubs[0].read_text(encoding="utf-8")


def test_pdf_is_converted_and_stub_points_at_the_markdown(scaffolded_vault, tmp_path):
    root = scaffolded_vault
    set_conversion(root, tmp_path)
    pdf = root / "raw" / "pdfs" / "paper-one.pdf"
    pdf.write_bytes(b"%PDF-1 fake")
    fast_ingest.fast_ingest(str(root), "raw/pdfs/paper-one.pdf")
    text = stub_text(root)
    md = root / "raw" / "converted" / "paper-one" / "paper-one.md"
    assert md.exists()
    assert 'raw_file: "raw/converted/paper-one/paper-one.md"' in text
    assert f'raw_sha256: "{fast_ingest.compute_sha256(str(md))}"' in text
    assert 'source_pdf: "raw/pdfs/paper-one.pdf"' in text
    assert f'source_pdf_sha256: "{fast_ingest.compute_sha256(str(pdf))}"' in text
    assert "source_lossy" not in text


@pytest.mark.skipif(shutil.which("pdftotext") is None, reason="pdftotext not installed")
def test_lossy_conversion_is_recorded_in_the_stub(scaffolded_vault, tmp_path):
    root = scaffolded_vault
    set_conversion(root, tmp_path, converter="pdftotext")
    (root / "raw" / "pdfs" / "scan.pdf").write_bytes(make_pdf(["Alpha line one\nAlpha line two", "Beta page text here"]))
    fast_ingest.fast_ingest(str(root), "raw/pdfs/scan.pdf")
    assert "source_lossy: true" in stub_text(root)


def test_converter_none_ingests_the_pdf_as_before(scaffolded_vault, tmp_path):
    root = scaffolded_vault
    set_conversion(root, tmp_path, converter="none")
    (root / "raw" / "pdfs" / "paper-one.pdf").write_bytes(b"%PDF-1 fake")
    fast_ingest.fast_ingest(str(root), "raw/pdfs/paper-one.pdf")
    text = stub_text(root)
    assert 'raw_file: "raw/pdfs/paper-one.pdf"' in text and "source_pdf" not in text
    assert not (root / "raw" / "converted").exists()


def test_private_zone_pdf_is_refused(scaffolded_vault, tmp_path):
    root = scaffolded_vault
    set_conversion(root, tmp_path)
    with open(root / "CLAUDE.md", "a", encoding="utf-8") as f:
        f.write("\n## Private zones\n\n- `sensitive_data/`\n")
    (root / "sensitive_data").mkdir()
    (root / "sensitive_data" / "r.pdf").write_bytes(b"%PDF-1")
    with pytest.raises(SystemExit) as exc:
        fast_ingest.fast_ingest(str(root), "sensitive_data/r.pdf")
    assert exc.value.code == 5
    assert not list((root / "wiki" / "sources").glob("*.md"))
