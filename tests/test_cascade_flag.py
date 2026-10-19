"""Shared FIX-comment inserter; behaviour of cascade_flag.py must not regress."""
import re

import cascade_flag


def legacy_flag(text, modified):
    """Verbatim copy of the pre-refactor algorithm, kept to prove the outputs stay identical."""
    citation_line_re = re.compile(r"\((raw/[^,)]+),")
    lines = text.splitlines(keepends=True)
    new_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        match = citation_line_re.search(line)
        if match and match.group(1).strip() in modified:
            next_line = lines[i + 1] if i + 1 < len(lines) else ""
            if cascade_flag.FIX_TAG not in next_line and cascade_flag.FIX_TAG not in line:
                new_lines.append(line)
                eol = "\n" if not line.endswith("\n") else ""
                new_lines.append(cascade_flag.FIX_TAG + eol + "\n")
                i += 1
                continue
        new_lines.append(line)
        i += 1
    return "".join(new_lines)


PAGE = (
    "---\ntype: concept\n---\n\n"
    "- Claim one (raw/articles/a.md, L3)\n"
    "- Claim two (raw/articles/b.md, L9)\n"
    "- Claim three (raw/articles/a.md, L11-L13)\n"
    "\nplain line\n"
)


def run_new(tmp_path, text, modified, dry_run=False):
    f = tmp_path / "page.md"
    f.write_text(text, encoding="utf-8")
    added = cascade_flag.flag_citations_in_file(f, modified, dry_run=dry_run)
    return added, f.read_text(encoding="utf-8")


def test_output_is_byte_identical_to_the_old_algorithm(tmp_path):
    added, new_text = run_new(tmp_path, PAGE, {"raw/articles/a.md"})
    assert added == 2 and new_text == legacy_flag(PAGE, {"raw/articles/a.md"})


def test_second_run_adds_nothing(tmp_path):
    _, once = run_new(tmp_path, PAGE, {"raw/articles/a.md"})
    added, twice = run_new(tmp_path, once, {"raw/articles/a.md"})
    assert added == 0 and twice == once


def test_dry_run_changes_nothing(tmp_path):
    added, text = run_new(tmp_path, PAGE, {"raw/articles/a.md"}, dry_run=True)
    assert added == 2 and text == PAGE


def test_citations_the_old_regex_missed_are_now_flagged(tmp_path):
    page = "Two sources (raw/articles/a.md, L1; raw/articles/b.md, L2)\n"
    added, text = run_new(tmp_path, page, {"raw/articles/b.md"})
    assert added == 1 and text.endswith(cascade_flag.FIX_TAG + "\n")


def test_last_line_without_newline_no_longer_glues_the_tag(tmp_path):
    page = "Claim (raw/articles/a.md, L3)"
    _, text = run_new(tmp_path, page, {"raw/articles/a.md"})
    assert text == "Claim (raw/articles/a.md, L3)\n" + cascade_flag.FIX_TAG + "\n"


class TestInsertFixComments:
    def test_several_edits_land_on_the_right_lines(self, tmp_path):
        f = tmp_path / "p.md"
        f.write_text("a\nb\nc\nd\n", encoding="utf-8")
        n = cascade_flag.insert_fix_comments(f, [(0, "<!-- FIX: one [m1] -->", "[m1]"), (2, "<!-- FIX: two [m2] -->", "[m2]")])
        assert n == 2
        assert f.read_text() == "a\n<!-- FIX: one [m1] -->\nb\nc\n<!-- FIX: two [m2] -->\nd\n"

    def test_marker_in_following_fix_comments_prevents_duplicates(self, tmp_path):
        f = tmp_path / "p.md"
        f.write_text("a\n<!-- FIX: other -->\n<!-- FIX: one [m1] -->\nb\n", encoding="utf-8")
        assert cascade_flag.insert_fix_comments(f, [(0, "<!-- FIX: one [m1] -->", "[m1]")]) == 0

    def test_unrelated_following_line_does_not_count(self, tmp_path):
        f = tmp_path / "p.md"
        f.write_text("a\nb [m1]\n", encoding="utf-8")
        assert cascade_flag.insert_fix_comments(f, [(0, "<!-- FIX: one [m1] -->", "[m1]")]) == 1

    def test_out_of_range_index_is_ignored(self, tmp_path):
        f = tmp_path / "p.md"
        f.write_text("a\n", encoding="utf-8")
        assert cascade_flag.insert_fix_comments(f, [(9, "<!-- FIX: x -->", "[x]")]) == 0
