"""Lint check 13 must tolerate Markdown escapes and hyphen breaks, but stay case-sensitive."""
import lint_wiki


def _vault(tmp_path, raw_text, claim):
    (tmp_path / "wiki" / "sources").mkdir(parents=True)
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / "doc.md").write_text(raw_text, encoding="utf-8")
    (tmp_path / "wiki" / "sources" / "doc.md").write_text(
        "---\ntype: source-summary\nclaims_requiring_verification:\n"
        f"  - '\"{claim}\" (raw/doc.md, L1)'\n---\n\n# Doc\n",
        encoding="utf-8",
    )
    return tmp_path


def _errors(root):
    lint_wiki.errors.clear()
    lint_wiki.warnings.clear()
    lint_wiki.suggestions.clear()
    lint_wiki.check_claims_verification(root / "wiki", root)
    return list(lint_wiki.errors)


def test_markdown_escape_in_raw_is_tolerated(tmp_path):
    root = _vault(tmp_path, "growth of 5\\*3 percent\n", "5*3 percent")
    assert _errors(root) == []


def test_hyphen_break_in_raw_is_tolerated(tmp_path):
    root = _vault(tmp_path, "a well-\nknown fact\n", "well-known fact")
    assert _errors(root) == []


def test_whitespace_differences_are_tolerated(tmp_path):
    root = _vault(tmp_path, "the   cell\ndivides\n", "the cell divides")
    assert _errors(root) == []


def test_still_case_sensitive(tmp_path):
    root = _vault(tmp_path, "please tell us now\n", "US")
    assert len(_errors(root)) == 1


def test_absent_claim_still_fails(tmp_path):
    root = _vault(tmp_path, "something else entirely\n", "2% discount")
    assert len(_errors(root)) == 1
