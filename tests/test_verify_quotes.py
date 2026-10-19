"""Tests for quote extraction and binding (verify_quotes.extract_claims)."""
import verify_quotes as vq


def claims(text):
    return vq.extract_claims(text, "wiki/concepts/x.md")


def quotes(text):
    return [c.quote for c in claims(text).claims]


class TestQuoteStyles:
    def test_straight_curly_and_corner_quotes(self):
        text = (
            'The paper says "the cell divides rapidly" (raw/a.md, L1)\n'
            "又说“细胞分裂的速度非常之快而且很稳定”（见下） (raw/a.md, L2)\n"
            "还有「这是一个足够长的中文引文用于测试」 (raw/a.md, L3)\n"
            "以及『另一个足够长的中文引文用于测试』 (raw/a.md, L4)\n"
        )
        assert quotes(text) == [
            "the cell divides rapidly", "细胞分裂的速度非常之快而且很稳定",
            "这是一个足够长的中文引文用于测试", "另一个足够长的中文引文用于测试",
        ]

    def test_single_quotes_and_apostrophes_are_ignored(self):
        assert quotes("It's what 'they' said, don't you think (raw/a.md, L1)\n") == []

    def test_quote_inside_a_file_name_is_not_a_quote(self):
        text = '- 事实 (raw/stocks/早报 ACME公司"预告"发布！.md, L9)\n'
        res = claims(text)
        assert res.claims == [] and res.too_short == 0

    def test_quote_must_sit_on_one_line(self):
        assert quotes('He wrote "the cell divides\nrapidly in culture" (raw/a.md, L1)\n') == []


class TestMinimumLength:
    def test_three_latin_words_pass_two_do_not(self):
        assert quotes('A "three word quote" (raw/a.md, L1) and "two words" (raw/a.md, L2)\n') == ["three word quote"]

    def test_digit_rule_lets_the_repos_own_example_through(self):
        text = 'The model has "175 billion parameters" (raw/articles/gpt3.md, L8)\n'
        assert quotes(text) == ["175 billion parameters"]
        assert quotes('It is "up 3%" (raw/a.md, L1)\n') == ["up 3%"]

    def test_cjk_needs_twelve_characters_or_six_with_a_digit(self):
        assert quotes("他说“今天天气很好”吧 (raw/a.md, L1)\n") == []
        assert quotes("他说“增长了百分之三十五点二”吧 (raw/a.md, L1)\n") == []
        assert quotes("他说“增长了35.2%的速度”吧 (raw/a.md, L1)\n") == ["增长了35.2%的速度"]

    def test_short_quotes_are_counted(self):
        assert claims('So-called "AI" (raw/a.md, L1)\n').too_short == 1


class TestIgnoredRegions:
    def test_frontmatter_code_fences_inline_code_and_comments(self):
        text = (
            '---\nclaims_requiring_verification:\n  - \'"two percent discount" (raw/a.md, L1)\'\n---\n'
            '```\nprint("this is not a quote at all")\n```\n'
            'Use `say("inline code is not a quote")` here (raw/a.md, L9)\n'
            '<!-- FIX: "an old fix comment text" (raw/a.md, L9) -->\n'
            'Real: "the actual quoted passage" (raw/a.md, L10)\n'
        )
        assert quotes(text) == ["the actual quoted passage"]

    def test_wikilink_alias_text_is_not_a_quote(self):
        assert quotes('See [[concepts/x|"a long alias that looks quoted"]] (raw/a.md, L1)\n') == []


class TestBinding:
    def test_binds_to_first_citation_on_its_right(self):
        text = 'First "quote number one here" (raw/a.md, L1) then "quote number two here" (raw/b.md, L2)\n'
        got = [(c.quote, c.raw, c.locs) for c in claims(text).claims]
        assert got == [("quote number one here", "raw/a.md", (("L", 1, 1),)),
                       ("quote number two here", "raw/b.md", (("L", 2, 2),))]

    def test_citation_to_the_left_does_not_count(self):
        res = claims('(raw/a.md, L1) then "a quote without its own citation"\n')
        assert res.claims == [] and res.uncited == 1

    def test_binds_to_a_later_line_of_the_same_paragraph(self):
        text = 'She wrote "a quote that continues" and\nmore words follow (raw/a.md, L5)\n'
        c = claims(text).claims[0]
        assert (c.line_no, c.cite_line, c.raw) == (1, 2, "raw/a.md")

    def test_list_items_are_separate_units(self):
        text = '- item with "an uncited quote here"\n- another item (raw/a.md, L5)\n'
        res = claims(text)
        assert res.claims == [] and res.uncited == 1

    def test_blank_line_ends_the_paragraph(self):
        res = claims('Has "an uncited quote here"\n\nLater (raw/a.md, L5)\n')
        assert res.claims == [] and res.uncited == 1

    def test_table_rows_never_bind_across_rows(self):
        text = '| a "quote in row one here" | x |\n| b | (raw/a.md, L5) |\n'
        res = claims(text)
        assert res.claims == [] and res.uncited == 1

    def test_several_quotes_share_one_citation_and_know_it(self):
        text = 'As in "quote A is first" and "quote B is second" (raw/c.md, L9)\n'
        cs = claims(text).claims
        assert [c.shared for c in cs] == [1, 1] and {c.raw for c in cs} == {"raw/c.md"}

    def test_several_locations_are_kept(self):
        c = claims('X "a quote with two places" (raw/a.md, L1-2, L7)\n').claims[0]
        assert c.locs == (("L", 1, 2), ("L", 7, 7))


class TestBlockquotes:
    def test_run_of_quote_lines_is_one_quote_at_its_first_line(self):
        text = "> The cell divides\n> rapidly in culture\n> (raw/a.md, L4-5)\n\nafter\n"
        res = claims(text)
        assert [(c.line_no, c.quote) for c in res.claims] == [(1, "The cell divides rapidly in culture")]

    def test_citation_on_the_line_after_the_block(self):
        text = "> The cell divides rapidly in culture\n(raw/a.md, L4)\n"
        assert [c.raw for c in claims(text).claims] == ["raw/a.md"]

    def test_inner_quotes_of_a_blockquote_are_not_extracted_twice(self):
        text = '> He said "the cell divides rapidly" today (raw/a.md, L4)\n'
        assert len(claims(text).claims) == 1


class TestEllipsis:
    def test_fragments_split_on_every_ellipsis_style(self):
        for ell in ("[...]", "[…]", "...", "…", "……", "⋯"):
            text = f'He said "the first long fragment {ell} and the second long fragment" (raw/a.md, L1)\n'
            c = claims(text).claims[0]
            assert c.fragments == ["the first long fragment", "and the second long fragment"], ell

    def test_short_fragments_are_dropped_but_one_must_remain(self):
        c = claims('He said "the first long fragment ... ok" (raw/a.md, L1)\n').claims[0]
        assert c.fragments == ["the first long fragment"]
        assert claims('He said "ok ... no" (raw/a.md, L1)\n').claims == []


def test_malformed_citations_are_reported_with_page_and_line():
    res = claims('line one\nBroken (raw/clippings/unfinished and "a quote that is long enough"\n')
    assert res.malformed == [("wiki/concepts/x.md", 2, "(raw/clippings/unfinished and \"a quote that is long enough\"")]


"""Matching primitives: Doc views, ordered fragment search, fuzzy search."""
import random

import verify_quotes as vq


def doc(*lines):
    return vq.Doc("\n".join(lines) + "\n")


class TestDoc:
    def test_line_bookkeeping(self):
        d = doc("alpha", "beta", "gamma")
        assert d.n_lines == 3
        assert [d.line_of(o) for o in (0, 4, 5, 6, 10, 11)] == [1, 1, 1, 2, 2, 3]   # a newline belongs to the line it ends
        assert vq.Doc("").n_lines == 0 and vq.Doc("no newline").n_lines == 1

    def test_offsets_for_lines_are_clamped(self):
        d = doc("alpha", "beta", "gamma")
        assert d.offsets_for_lines(2, 2) == (6, 11)
        assert d.offsets_for_lines(-5, 99) == (0, len(d.text))

    def test_views_map_back_to_lines(self):
        d = doc("first line", "the  cell   divides", "last")
        span = d.find("the cell divides", "literal", (1, 3))
        assert d.lines_of_span("literal", span) == (2, 2)


class TestMatchFragments:
    D = doc("intro text here", "The Cell divides rapidly in culture", "unrelated middle", "tail with 5*3 percent growth")

    def test_literal_fold_and_loose_rungs(self):
        assert vq.match_fragments(self.D, ["The Cell divides rapidly"], "literal", [(1, 4)]) == (2, 2)
        assert vq.match_fragments(self.D, ["the cell divides rapidly"], "literal", [(1, 4)]) is None
        assert vq.match_fragments(self.D, ["the cell divides rapidly"], "fold", [(1, 4)]) == (2, 2)
        assert vq.match_fragments(self.D, ["thecell, divides-rapidly"], "loose", [(1, 4)]) == (2, 2)

    def test_markdown_escape_in_raw_matches_plain_quote(self):
        d = doc("growth", "tail with 5\\*3 percent growth")
        assert vq.match_fragments(d, ["5*3 percent growth"], "literal", [(1, 2)]) == (2, 2)

    def test_range_restricts_the_search(self):
        assert vq.match_fragments(self.D, ["unrelated middle"], "literal", [(1, 2)]) is None
        assert vq.match_fragments(self.D, ["unrelated middle"], "literal", [(3, 3)]) == (3, 3)

    def test_any_of_several_ranges(self):
        assert vq.match_fragments(self.D, ["unrelated middle"], "literal", [(1, 1), (3, 3)]) == (3, 3)

    def test_fragments_must_appear_in_order(self):
        d = doc("alpha beta gamma delta epsilon")
        assert vq.match_fragments(d, ["alpha beta", "delta epsilon"], "literal", [(1, 1)]) == (1, 1)
        assert vq.match_fragments(d, ["delta epsilon", "alpha beta"], "literal", [(1, 1)]) is None

    def test_both_hyphen_forms_are_tried(self):
        d = doc("a well-", "known fact and inter-", "national law")
        assert vq.match_fragments(d, ["well-known fact"], "literal", [(1, 3)]) == (1, 2)
        assert vq.match_fragments(d, ["international law"], "literal", [(1, 3)]) == (2, 3)

    def test_empty_needle_never_matches(self):
        assert vq.match_fragments(self.D, ["!!!"], "loose", [(1, 4)]) is None


class TestFuzzy:
    TEXT = [
        "Completely unrelated opening sentence about astronomy and galaxies far away.",
        "The quick brown fox jumps over the lazy dog near the river bank today.",
        "Another filler sentence that has nothing to do with the target at all.",
    ]

    def test_one_word_changed_scores_high_and_reports_its_line(self):
        d = doc(*self.TEXT)
        score, lines, pos = vq.fuzzy_best(d, "the quick brown fox jumps over the lazy cat near the river bank", (1, 3))
        assert score >= vq.THRESHOLD and lines == (2, 2) and pos > 0

    def test_unrelated_sentence_scores_below_near_miss(self):
        d = doc(*self.TEXT)
        score, _, _ = vq.fuzzy_best(d, "photosynthesis converts sunlight into chemical energy inside chloroplasts", (1, 3))
        assert score < vq.NEAR_MISS

    def test_cjk_is_measured_in_characters(self):
        d = doc("无关的内容放在这里", "图书馆可以延长开放时间，但是人手必须先补上才行", "其他内容")
        score, lines, _ = vq.fuzzy_best(d, "图书馆可以延长开放时间，但人手必须先补上", (1, 3))
        assert score >= vq.THRESHOLD and lines == (2, 2)

    def test_search_is_limited_to_the_line_range(self):
        d = doc(*self.TEXT)
        score, _, _ = vq.fuzzy_best(d, "the quick brown fox jumps over the lazy cat near the river bank", (1, 1))
        assert score < vq.NEAR_MISS

    def test_tiny_queries_are_not_scored(self):
        assert vq.fuzzy_best(doc("abc def"), "ab", (1, 1)) == (0, None, -1)

    def test_large_ranges_use_the_trigram_index(self):
        rng = random.Random(7)
        words = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet"]
        lines = [" ".join(rng.choice(words) for _ in range(40)) for _ in range(400)]
        lines[250] = "the quick brown fox jumps over the lazy dog near the river bank today"
        d = doc(*lines)
        assert len(d.text) > vq.SCAN_LIMIT * 4
        score, found, _ = vq.fuzzy_best(d, "the quick brown fox jumps over the lazy cat near the river bank", (1, 400))
        assert score >= vq.THRESHOLD and found == (251, 251)
