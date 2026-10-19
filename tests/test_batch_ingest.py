"""batch_ingest: PDFs go through fast_ingest (which converts), dedupe by either hash."""
import re
import shlex
import sys

import batch_ingest

TOOL = '''
import pathlib, sys
pdf, outdir, slug = sys.argv[1:4]
(pathlib.Path(outdir) / (slug + ".md")).write_text("# " + slug + "\\n\\nConverted text for " + slug + "\\n", encoding="utf-8")
'''


def set_conversion(root, tmp_path):
    tool = tmp_path / "tool.py"
    tool.write_text(TOOL, encoding="utf-8")
    claude = root / "CLAUDE.md"
    text = re.sub(r"\n## Document conversion\n.*?(?=\n## |\Z)", "", claude.read_text(encoding="utf-8"), flags=re.S)
    text += (
        "\n## Document conversion\n\nconverter: command\n"
        f"command: {shlex.quote(sys.executable)} {shlex.quote(str(tool))} {{input}} {{outdir}} {{slug}}\n"
    )
    claude.write_text(text, encoding="utf-8")


def sources(root):
    return sorted(p.name for p in (root / "wiki" / "sources").glob("*.md"))


def test_pdfs_are_converted_and_ingested_once(scaffolded_vault, tmp_path, capsys):
    root = scaffolded_vault
    set_conversion(root, tmp_path)
    for name in ("alpha.pdf", "beta.pdf"):
        (root / "raw" / "pdfs" / name).write_bytes(b"%PDF-1 " + name.encode())
    assert batch_ingest.batch_ingest(str(root), "raw/pdfs", [".pdf"]) == 0
    assert len(sources(root)) == 2
    assert (root / "raw" / "converted" / "alpha" / "alpha.md").exists()
    capsys.readouterr()
    assert batch_ingest.batch_ingest(str(root), "raw/pdfs", [".pdf"]) == 0
    assert "already ingested" in capsys.readouterr().out
    assert len(sources(root)) == 2


def test_converted_files_are_not_scanned_as_separate_sources(scaffolded_vault, tmp_path):
    root = scaffolded_vault
    set_conversion(root, tmp_path)
    (root / "raw" / "pdfs" / "alpha.pdf").write_bytes(b"%PDF-1 a")
    assert batch_ingest.batch_ingest(str(root), "raw", [".pdf"]) == 0
    assert batch_ingest.batch_ingest(str(root), "raw", [".md"]) == 0       # would pick up raw/converted/*.md
    assert len(sources(root)) == 1


def test_no_convert_skips_pdfs(scaffolded_vault, tmp_path):
    root = scaffolded_vault
    set_conversion(root, tmp_path)
    (root / "raw" / "pdfs" / "alpha.pdf").write_bytes(b"%PDF-1 a")
    assert batch_ingest.batch_ingest(str(root), "raw/pdfs", [".pdf"], no_convert=True) == 0
    assert sources(root) == [] and not (root / "raw" / "converted").exists()


def test_dry_run_creates_nothing(scaffolded_vault, tmp_path, capsys):
    root = scaffolded_vault
    set_conversion(root, tmp_path)
    (root / "raw" / "pdfs" / "alpha.pdf").write_bytes(b"%PDF-1 a")
    assert batch_ingest.batch_ingest(str(root), "raw/pdfs", [".pdf"], dry_run=True) == 0
    assert "would convert" in capsys.readouterr().out
    assert sources(root) == [] and not (root / "raw" / "converted").exists()


def test_known_hashes_include_the_source_pdf_hash(scaffolded_vault):
    stub = scaffolded_vault / "wiki" / "sources" / "x.md"
    stub.write_text('---\nraw_sha256: "aaa"\nsource_pdf_sha256: "bbb"\n---\n', encoding="utf-8")
    assert batch_ingest.collect_known_hashes(scaffolded_vault) == {"aaa", "bbb"}
