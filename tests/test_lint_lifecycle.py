"""Lint: stale_after (check 6), superseded_by (check 17), Contradictions entries (check 18),
plus the superseded-page handling in build_index.py."""
import datetime

import build_index
import lint_wiki

TODAY = datetime.date.today()
PAST = (TODAY - datetime.timedelta(days=10)).isoformat()
FUTURE = (TODAY + datetime.timedelta(days=10)).isoformat()
RECENT = TODAY.isoformat()


def _page(root, rel, fm="", body="Body text.\n", page_type="concept"):
    path = root / "wiki" / f"{rel}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ntype: {page_type}\ndate: {RECENT}\n{fm}---\n\n# T\n\n{body}",
                    encoding="utf-8")
    return path


def _reset():
    lint_wiki.errors.clear()
    lint_wiki.warnings.clear()
    lint_wiki.suggestions.clear()


def _issues():
    return list(lint_wiki.errors), list(lint_wiki.warnings), list(lint_wiki.suggestions)


def _stale(root):
    _reset()
    lint_wiki.check_stale(root / "wiki", root, lint_wiki.load_staleness_thresholds(root))
    return _issues()


def _supersession(root):
    _reset()
    lint_wiki.check_supersession(root / "wiki", root)
    return _issues()


def _contradictions(root):
    _reset()
    open_pages = lint_wiki.check_contradictions(root / "wiki", root)
    return open_pages, _issues()


# ── stale_after ───────────────────────────────────────────────────────────────

def test_stale_after_in_the_past_warns_on_any_page_type(tmp_path):
    _page(tmp_path, "entities/nvidia", f"stale_after: {PAST}\n", page_type="entity")
    _, warnings, _ = _stale(tmp_path)
    assert len(warnings) == 1
    assert "wiki/entities/nvidia.md" in warnings[0] and f"past stale_after {PAST}" in warnings[0]


def test_stale_after_in_the_future_or_today_is_silent(tmp_path):
    _page(tmp_path, "sources/a", f"stale_after: {FUTURE}\n", page_type="source-summary")
    _page(tmp_path, "sources/b", f"stale_after: {RECENT}\n", page_type="source-summary")
    assert _stale(tmp_path) == ([], [], [])


def test_stale_after_tolerates_quotes_and_trailing_comment(tmp_path):
    _page(tmp_path, "synthesis/s", f'stale_after: "{PAST}"   # prices change\n',
          page_type="synthesis")
    _, warnings, _ = _stale(tmp_path)
    assert len(warnings) == 1 and PAST in warnings[0]


def test_invalid_stale_after_is_a_frontmatter_warning_not_a_crash(tmp_path):
    _page(tmp_path, "concepts/x", f"stale_after: next spring\nlast_reviewed: {RECENT}\n")
    assert _stale(tmp_path) == ([], [], [])
    _reset()
    lint_wiki.check_frontmatter(tmp_path / "wiki", tmp_path)
    _, warnings, _ = _issues()
    assert any("stale_after 'next spring' is not a YYYY-MM-DD date" in w for w in warnings)


def test_review_age_staleness_is_unchanged(tmp_path):
    old = (TODAY - datetime.timedelta(days=400)).isoformat()
    _page(tmp_path, "concepts/old", f"last_reviewed: {old}\ndomain_volatility: low\n")
    _, warnings, _ = _stale(tmp_path)
    assert len(warnings) == 1 and "400d since review (limit for 'low': 365d)" in warnings[0]


def test_superseded_pages_are_never_stale(tmp_path):
    old = (TODAY - datetime.timedelta(days=400)).isoformat()
    _page(tmp_path, "concepts/new", f"last_reviewed: {RECENT}\n")
    _page(tmp_path, "concepts/old",
          f'last_reviewed: {old}\nstale_after: {PAST}\nsuperseded_by: "[[concepts/new]]"\n')
    assert _stale(tmp_path) == ([], [], [])


def test_stale_only_lists_stale_after_pages(tmp_path, capsys):
    _page(tmp_path, "entities/e", f"stale_after: {PAST}\n", page_type="entity")
    assert lint_wiki.stale_only(str(tmp_path)) == 1
    out = capsys.readouterr().out
    assert "1 stale page(s)" in out and "10d overdue  [stale_after" in out
    assert "wiki/entities/e.md" in out


def test_stale_only_without_concepts_dir_is_clean(tmp_path, capsys):
    _page(tmp_path, "entities/e", f"stale_after: {FUTURE}\n", page_type="entity")
    assert lint_wiki.stale_only(str(tmp_path)) == 0
    assert "No stale pages found" in capsys.readouterr().out


# ── superseded_by ─────────────────────────────────────────────────────────────

def test_superseded_by_existing_page_is_silent_in_every_reference_form(tmp_path):
    _page(tmp_path, "concepts/new")
    _page(tmp_path, "concepts/a", 'superseded_by: "[[concepts/new]]"\n')
    _page(tmp_path, "concepts/b", 'superseded_by: "[[concepts/new|New name]]"\n')
    _page(tmp_path, "concepts/c", "superseded_by: wiki/concepts/new.md\n")
    _page(tmp_path, "concepts/d", "superseded_by: new\n")
    _page(tmp_path, "concepts/e", 'superseded_by: ""   # empty with a comment\n')
    assert _supersession(tmp_path) == ([], [], [])


def test_superseded_by_missing_page_is_an_error(tmp_path):
    _page(tmp_path, "concepts/a", 'superseded_by: "[[concepts/ghost]]"\n')
    errors, _, _ = _supersession(tmp_path)
    assert len(errors) == 1 and "does not exist" in errors[0] and "concepts/a.md" in errors[0]


def test_superseded_by_itself_is_an_error(tmp_path):
    _page(tmp_path, "concepts/a", 'superseded_by: "[[concepts/a]]"\n')
    errors, _, _ = _supersession(tmp_path)
    assert len(errors) == 1 and "points to itself" in errors[0]


def test_superseded_by_ambiguous_bare_stem_warns(tmp_path):
    _page(tmp_path, "concepts/x")
    _page(tmp_path, "entities/x", page_type="entity")
    _page(tmp_path, "concepts/a", "superseded_by: x\n")
    errors, warnings, _ = _supersession(tmp_path)
    assert errors == [] and len(warnings) == 1 and "category/slug" in warnings[0]


def test_superseded_by_chain_points_to_the_final_page(tmp_path):
    _page(tmp_path, "concepts/a", 'superseded_by: "[[concepts/b]]"\n')
    _page(tmp_path, "concepts/b", 'superseded_by: "[[concepts/c]]"\n')
    _page(tmp_path, "concepts/c")
    errors, warnings, _ = _supersession(tmp_path)
    assert errors == []
    assert warnings == [w for w in warnings if "concepts/a.md" in w] and len(warnings) == 1
    assert "point to [[concepts/c]]" in warnings[0]


def test_superseded_by_cycle_is_one_error(tmp_path):
    _page(tmp_path, "concepts/a", 'superseded_by: "[[concepts/b]]"\n')
    _page(tmp_path, "concepts/b", 'superseded_by: "[[concepts/a]]"\n')
    _page(tmp_path, "concepts/z", 'superseded_by: "[[concepts/a]]"\n')   # leads into the cycle
    errors, _, _ = _supersession(tmp_path)
    assert len(errors) == 1 and "cycle" in errors[0] and "no current page" in errors[0]


# ── Contradictions ────────────────────────────────────────────────────────────

def test_contradiction_citing_both_sides_is_silent(tmp_path):
    _page(tmp_path, "concepts/c", body=(
        "## Contradictions\n\n"
        "- Paper A reports 12% (raw/papers/a.md, L10); paper B reports 30% (raw/papers/b.md, L4).\n"
    ))
    open_pages, issues = _contradictions(tmp_path)
    assert issues == ([], [], [])
    assert open_pages == [("wiki/concepts/c.md", 1)]


def test_contradiction_citing_one_side_warns(tmp_path):
    _page(tmp_path, "concepts/c", body=(
        "## Contradictions\n\n"
        "- Paper A reports 12% (raw/papers/a.md, L10) but others disagree.\n"
    ))
    _, (errors, warnings, _) = _contradictions(tmp_path)
    assert errors == [] and len(warnings) == 1 and "cites 1 side(s)" in warnings[0]


def test_same_source_cited_twice_counts_once(tmp_path):
    _page(tmp_path, "sources/a", 'raw_file: "raw/papers/a.md"\n', page_type="source-summary")
    _page(tmp_path, "concepts/c", body=(
        "## Contradictions\n\n"
        "- [[sources/a]] says X (raw/papers/a.md, L1) and Y (raw/papers/a.md, L9).\n"
    ))
    _, (_, warnings, _) = _contradictions(tmp_path)
    assert len(warnings) == 1 and "cites 1 side(s)" in warnings[0]


def test_source_page_counts_its_own_raw_file_as_one_side(tmp_path):
    _page(tmp_path, "sources/a", 'raw_file: "raw/papers/a.md"\n', page_type="source-summary",
          body="## Contradictions\n\n- Contradicts the 2019 survey (raw/papers/survey.md, L33).\n")
    _, issues = _contradictions(tmp_path)
    assert issues == ([], [], [])


def test_nested_bullets_and_continuations_belong_to_one_entry(tmp_path):
    _page(tmp_path, "concepts/c", body=(
        "## Contradictions\n\n"
        "- Claim: the effect size\n"
        "  - Source A: large (raw/a.md, L2)\n"
        "\n"
        "  - Source B: none (raw/b.md, L7)\n"
    ))
    open_pages, issues = _contradictions(tmp_path)
    assert issues == ([], [], []) and open_pages == [("wiki/concepts/c.md", 1)]


def test_table_rows_are_entries_and_header_is_skipped(tmp_path):
    _page(tmp_path, "concepts/c", body=(
        "## Contradictions\n\n"
        "| Claim | Side A | Side B |\n"
        "|-------|--------|--------|\n"
        "| Rate | 12% (raw/a.md, L1) | 30% (raw/b.md, L2) |\n"
        "| Date | 2019 (raw/a.md, L5) | — |\n"
    ))
    open_pages, (_, warnings, _) = _contradictions(tmp_path)
    assert open_pages == [("wiki/concepts/c.md", 2)]
    assert len(warnings) == 1 and "Date" in warnings[0]


def test_empty_placeholder_and_comment_sections_are_not_contradictions(tmp_path):
    _page(tmp_path, "concepts/a", body="## Contradictions\n\n## Sources\n\n- [[sources/x]]\n")
    _page(tmp_path, "concepts/b", body="## Contradictions\n\nNone.\n")
    _page(tmp_path, "concepts/c", body="## Contradictions\n\n<!-- - A vs B (raw/a.md, L1) -->\n")
    _page(tmp_path, "concepts/d", body="## Contradictions\n\n*(none yet)*\n")
    open_pages, issues = _contradictions(tmp_path)
    assert open_pages == [] and issues == ([], [], [])


def test_section_ends_at_next_heading_of_same_level(tmp_path):
    _page(tmp_path, "concepts/c", body=(
        "## Contradictions\n\n"
        "- A (raw/a.md, L1) vs B (raw/b.md, L2)\n\n"
        "## My Notes\n\n"
        "- A loose note with no citation at all.\n"
    ))
    open_pages, issues = _contradictions(tmp_path)
    assert open_pages == [("wiki/concepts/c.md", 1)] and issues == ([], [], [])


def test_empty_research_questions_with_open_contradictions_suggests(tmp_path):
    (tmp_path / "CLAUDE.md").write_text(
        "# KB\n\n## Open research questions\n- <What do you want to understand better?>\n\n"
        "## Research gaps\n- [ ] x\n", encoding="utf-8")
    _page(tmp_path, "concepts/c", body="## Contradictions\n\n- A (raw/a.md, L1) vs B (raw/b.md, L2)\n")
    _, (_, _, suggestions) = _contradictions(tmp_path)
    assert len(suggestions) == 1 and "Open research questions is empty" in suggestions[0]

    (tmp_path / "CLAUDE.md").write_text(
        "# KB\n\n## Open research questions\n- Is the rate 12% or 30%?\n", encoding="utf-8")
    _, (_, _, suggestions) = _contradictions(tmp_path)
    assert suggestions == []


def test_full_lint_report_lists_open_contradictions(tmp_path):
    (tmp_path / "wiki" / "index.md").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "wiki" / "index.md").write_text("---\ntype: system-index\n---\n[[c]]\n",
                                                encoding="utf-8")
    _page(tmp_path, "concepts/c", body="## Contradictions\n\n- A (raw/a.md, L1) vs B (raw/b.md, L2)\n")
    _reset()
    lint_wiki.lint(str(tmp_path), structural=True)
    report = (tmp_path / "outputs" / f"lint-{TODAY.isoformat()}.md").read_text(encoding="utf-8")
    assert "## Open Contradictions (1 page)" in report
    assert "- wiki/concepts/c.md — 1 entry" in report


# ── build_index ───────────────────────────────────────────────────────────────

def test_build_index_marks_superseded_and_drops_them_from_highlights(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("## Scope\n\nWhat this wiki covers:\n- AI\n",
                                        encoding="utf-8")
    _page(tmp_path, "concepts/new", 'title: "New"\ndomain: AI\nsource_count: 1\n')
    _page(tmp_path, "concepts/old",
          'title: "Old"\ndomain: AI\nsource_count: 9\nsuperseded_by: "[[concepts/new]]"\n')
    assert build_index.build_index(str(tmp_path)) == 0
    catalog = (tmp_path / "wiki" / "index" / "ai.md").read_text(encoding="utf-8")
    master = (tmp_path / "wiki" / "index.md").read_text(encoding="utf-8")
    assert "[[concepts/old\\|Old]] *(superseded → [[concepts/new]])*" in catalog
    assert "[[concepts/new|New]]" in master and "concepts/old" not in master
