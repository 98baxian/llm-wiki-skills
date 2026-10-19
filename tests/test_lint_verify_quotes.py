"""lint --verify-quotes maps verdicts to lint levels; the default run is unchanged."""
import sys

import pytest

import lint_wiki

RAW = "line one\nthe quoted passage is right here\nline three\n"


def add_page(root, text, raw=None):
    (root / "wiki" / "concepts" / "qv.md").write_text(text, encoding="utf-8")
    for rel, body in (raw or {"raw/articles/a.md": RAW}).items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body, encoding="utf-8")


def issues(root, **kw):
    for bucket in (lint_wiki.errors, lint_wiki.warnings, lint_wiki.suggestions):
        bucket.clear()
    lint_wiki.check_quote_verification(root, **kw)
    pick = lambda items: [i for i in items if "(quote-verification)" in i]
    return pick(lint_wiki.errors), pick(lint_wiki.warnings), pick(lint_wiki.suggestions)


def test_not_found_is_an_error_with_page_line_and_quote(scaffolded_vault):
    add_page(scaffolded_vault, 'X: "an entirely invented sentence about nothing" (raw/articles/a.md, L2)\n')
    errors, warnings, notes = issues(scaffolded_vault)
    assert len(errors) == 1 and "wiki/concepts/qv.md:1" in errors[0] and "invented sentence" in errors[0]
    assert warnings == [] and notes == []


def test_wrong_location_is_a_warning_and_unverifiable_a_suggestion(scaffolded_vault):
    raw = {"raw/articles/a.md": "x\n" * 30 + "the quoted passage is right here\n"}
    page = ('A: "the quoted passage is right here" (raw/articles/a.md, L1)\n'
            'B: "a quote whose file is gone entirely" (raw/articles/gone.md, L1)\n')
    add_page(scaffolded_vault, page, raw)
    errors, warnings, notes = issues(scaffolded_vault)
    assert errors == [] and any("wrong_location" in w for w in warnings) and any("unverifiable" in n for n in notes)


def test_many_unverifiable_citations_raise_a_warning_but_cross_language_does_not(scaffolded_vault):
    gone = "".join(f'Q{i}: "missing file quote number {i} here" (raw/articles/gone{i}.md, L1)\n' for i in range(3))
    add_page(scaffolded_vault, 'ok: "the quoted passage is right here" (raw/articles/a.md, L2)\n' + gone)
    _, warnings, _ = issues(scaffolded_vault)
    assert any("could not be checked" in w and "%" in w for w in warnings)
    chinese = "".join(f"Q{i}: “细胞在培养中分裂得非常迅速而且稳定{i}” (raw/articles/a.md, L2)\n" for i in range(5))
    add_page(scaffolded_vault, 'ok: "the quoted passage is right here" (raw/articles/a.md, L2)\n' + chinese)
    _, warnings, _ = issues(scaffolded_vault)
    assert not any("could not be checked" in w for w in warnings)


def test_default_lint_does_not_run_the_check(scaffolded_vault):
    add_page(scaffolded_vault, 'X: "an entirely invented sentence about nothing" (raw/articles/a.md, L2)\n')
    for bucket in (lint_wiki.errors, lint_wiki.warnings, lint_wiki.suggestions):
        bucket.clear()
    lint_wiki.lint(str(scaffolded_vault))
    assert not any("quote-verification" in i for i in lint_wiki.errors + lint_wiki.warnings + lint_wiki.suggestions)


def test_flag_runs_it_through_lint_and_main(scaffolded_vault, monkeypatch):
    add_page(scaffolded_vault, 'X: "an entirely invented sentence about nothing" (raw/articles/a.md, L2)\n')
    for bucket in (lint_wiki.errors, lint_wiki.warnings, lint_wiki.suggestions):
        bucket.clear()
    monkeypatch.setattr(sys, "argv", ["lint_wiki.py", str(scaffolded_vault), "--verify-quotes"])
    with pytest.raises(SystemExit) as exc:
        lint_wiki.main()
    assert exc.value.code == 1
    assert any("quote-verification" in e for e in lint_wiki.errors)
