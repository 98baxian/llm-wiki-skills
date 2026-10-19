"""Lint: stale converted copies and lossy / low-confidence conversions."""
import json

import lint_wiki


def _vault(tmp_path, *, stub_extra="", pdf_bytes=b"%PDF-1", stored_sha=None, pages=None):
    import hashlib
    root = tmp_path
    (root / "wiki" / "sources").mkdir(parents=True)
    (root / "raw" / "pdfs").mkdir(parents=True)
    (root / "raw" / "converted" / "paper").mkdir(parents=True)
    pdf = root / "raw" / "pdfs" / "paper.pdf"
    pdf.write_bytes(pdf_bytes)
    md = root / "raw" / "converted" / "paper" / "paper.md"
    md.write_text("text\n", encoding="utf-8")
    if pages is not None:
        (root / "raw" / "converted" / "paper" / "paper.pages.json").write_text(json.dumps(pages), encoding="utf-8")
    sha = stored_sha or hashlib.sha256(b"%PDF-1").hexdigest()
    (root / "wiki" / "sources" / "paper.md").write_text(
        "---\ntype: source-summary\n"
        'raw_file: "raw/converted/paper/paper.md"\n'
        'source_pdf: "raw/pdfs/paper.pdf"\n'
        f'source_pdf_sha256: "{sha}"\n'
        f"{stub_extra}---\n\n# Paper\n",
        encoding="utf-8",
    )
    return root


def _run(root):
    lint_wiki.errors.clear()
    lint_wiki.warnings.clear()
    lint_wiki.suggestions.clear()
    lint_wiki.check_conversion_state(root / "wiki", root)
    return list(lint_wiki.warnings), list(lint_wiki.suggestions)


def test_healthy_conversion_is_silent(tmp_path):
    root = _vault(tmp_path, pages={"confidence": "high", "pages": []})
    assert _run(root) == ([], [])


def test_replaced_pdf_is_reported_as_stale(tmp_path):
    root = _vault(tmp_path, pdf_bytes=b"%PDF-2 new version")
    warnings, _ = _run(root)
    assert len(warnings) == 1 and "stale" in warnings[0] and "convert_source.py" in warnings[0]


def test_missing_pdf_is_a_warning(tmp_path):
    root = _vault(tmp_path)
    (root / "raw" / "pdfs" / "paper.pdf").unlink()
    warnings, _ = _run(root)
    assert len(warnings) == 1 and "not found" in warnings[0]


def test_lossy_conversion_is_noted(tmp_path):
    root = _vault(tmp_path, stub_extra="source_lossy: true\n")
    _, notes = _run(root)
    assert len(notes) == 1 and "lossy" in notes[0]


def test_low_confidence_page_map_is_noted(tmp_path):
    root = _vault(tmp_path, pages={"confidence": "low", "pages": []})
    _, notes = _run(root)
    assert len(notes) == 1 and "page translation" in notes[0]


def test_stub_without_source_pdf_is_ignored(tmp_path):
    root = _vault(tmp_path)
    (root / "wiki" / "sources" / "other.md").write_text('---\nraw_file: "raw/a.md"\n---\n', encoding="utf-8")
    assert _run(root) == ([], [])
