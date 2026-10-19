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


"""verify_claim: windows, the matching ladder, verdicts and severities."""
import pytest

import verify_quotes as vq


def claim(quote, locs=(("L", 1, 1),), fragments=None, shared=0):
    c = vq.Claim("wiki/x.md", 5, quote, fragments or vq.fragments_of(quote), "raw/a.md", locs, 5)
    c.shared = shared
    return c


def ctx(*lines, **kw):
    return vq.Context(doc=doc(*lines), **kw)


class TestWindows:
    def test_line_ranges_get_slack_and_clamp_at_one(self):
        c = claim("x", locs=(("L", 3, 4), ("L", 30, 30)))
        assert vq.windows_for(c, ctx("a")) == [(1, 4 + vq.SLACK), (30 - vq.SLACK, 30 + vq.SLACK)]

    def test_unknown_or_page_without_map_means_whole_file(self):
        assert vq.windows_for(claim("x", locs=(("unknown",),)), ctx("a")) is None
        assert vq.windows_for(claim("x", locs=(("p", 2, 2),)), ctx("a")) is None
        assert vq.windows_for(claim("x", locs=(("L", 1, 1), ("unknown",))), ctx("a")) is None

    def test_page_with_map_uses_the_page_line_range(self):
        pages = {"confidence": "high", "pages": [
            {"page": 1, "line_start": 1, "line_end": 40}, {"page": 2, "line_start": 41, "line_end": 80}]}
        c = claim("x", locs=(("p", 2, 2),))
        assert vq.windows_for(c, ctx("a", pages=pages)) == [(41 - vq.SLACK, 80 + vq.SLACK)]
        assert vq.windows_for(claim("x", locs=(("p", 9, 9),)), ctx("a", pages=pages)) is None


class TestLadder:
    D = ("intro", "The Cell divides rapidly in culture", "filler", "tail text")

    def test_literal_match_in_the_cited_lines(self):
        r = vq.verify_claim(claim("The Cell divides rapidly", locs=(("L", 2, 2),)), ctx(*self.D))
        assert (r.verdict, r.severity, r.score, r.matched_lines, r.note) == ("exact", "none", 100, [2, 2], "")

    def test_slack_covers_small_line_drift(self):
        lines = ["x"] * 7 + ["the quoted passage is right here"]
        r = vq.verify_claim(claim("the quoted passage is right here", locs=(("L", 1, 1),)), ctx(*lines))
        assert r.verdict == "exact" and r.matched_lines == [8, 8]

    def test_outside_the_window_is_wrong_location_with_lines(self):
        lines = ["x"] * 30 + ["the quoted passage is right here"]
        r = vq.verify_claim(claim("the quoted passage is right here", locs=(("L", 1, 1),)), ctx(*lines))
        assert (r.verdict, r.severity, r.matched_lines) == ("wrong_location", "warning", [31, 31])

    def test_long_quote_differing_only_in_case_is_exact(self):
        r = vq.verify_claim(claim("the cell divides rapidly in culture", locs=(("L", 2, 2),)), ctx(*self.D))
        assert (r.verdict, r.note) == ("exact", "case differs")

    def test_short_quote_differing_only_in_case_is_fuzzy(self):
        r = vq.verify_claim(claim("The Act applies", locs=(("L", 1, 1),)), ctx("the act applies here"))
        assert (r.verdict, r.score, r.note, r.severity) == ("fuzzy", 95, "case differs", "warning")

    def test_loose_rung_handles_asr_spacing_and_punctuation(self):
        c = claim("明年继续扩建社区图书馆", fragments=["明年继续扩建社区图书馆"])
        r = vq.verify_claim(c, ctx("开头", "他说 明年 继续 扩建 社区 图书馆 。", "结尾"))
        assert (r.verdict, r.note, r.matched_lines) == ("exact", "loose", [2, 2])

    def test_whole_file_is_the_cited_place_for_location_unknown(self):
        r = vq.verify_claim(claim("The Cell divides rapidly", locs=(("unknown",),)), ctx(*self.D))
        assert r.verdict == "exact"

    def test_ellipsis_fragments_must_be_in_order(self):
        d = ctx("alpha beta gamma delta epsilon")
        ok = claim("alpha beta ... delta epsilon", fragments=["alpha beta", "delta epsilon"])
        bad = claim("delta epsilon ... alpha beta", fragments=["delta epsilon", "alpha beta"])
        assert vq.verify_claim(ok, d).verdict == "exact"
        assert vq.verify_claim(bad, d).verdict == "not_found"

    def test_several_locations_any_may_hold_the_quote(self):
        lines = ["x"] * 60 + ["the quoted passage is right here"]
        c = claim("the quoted passage is right here", locs=(("L", 1, 1), ("L", 61, 61)))
        assert vq.verify_claim(c, ctx(*lines)).verdict == "exact"


class TestFuzzyBranches:
    """The thresholds are exercised with a stubbed scorer so the bands are exact."""

    def stub(self, monkeypatch, cited, whole=None):
        calls = []

        def fake(doc, fragment, line_range):
            calls.append(line_range)
            return (cited, (2, 2), 10) if len(calls) == 1 or whole is None else (whole, (9, 9), 90)

        monkeypatch.setattr(vq, "fuzzy_best", fake)

    def run(self):
        return vq.verify_claim(claim("zzz qqq www vvv", locs=(("L", 2, 2),)), ctx("a", "b", "c"))

    def test_high_score_in_window_is_fuzzy(self, monkeypatch):
        self.stub(monkeypatch, 90)
        r = self.run()
        assert (r.verdict, r.score, r.matched_lines, r.severity) == ("fuzzy", 90, [2, 2], "warning")

    def test_high_score_only_elsewhere_is_wrong_location(self, monkeypatch):
        self.stub(monkeypatch, 40, whole=88)
        r = self.run()
        assert (r.verdict, r.score, r.matched_lines) == ("wrong_location", 88, [9, 9])

    def test_middle_band_is_near_miss_and_a_warning(self, monkeypatch):
        self.stub(monkeypatch, 72, whole=65)
        r = self.run()
        assert (r.verdict, r.score, r.severity) == ("near_miss", 72, "warning")

    def test_low_band_is_not_found_and_an_error(self, monkeypatch):
        self.stub(monkeypatch, 40, whole=30)
        r = self.run()
        assert (r.verdict, r.severity) == ("not_found", "error")

    def test_threshold_and_near_miss_are_configurable(self, monkeypatch):
        self.stub(monkeypatch, 70)
        r = vq.verify_claim(claim("zzz qqq www vvv", locs=(("L", 2, 2),)),
                            ctx("a", "b", "c", threshold=65, near_miss=50))
        assert r.verdict == "fuzzy"


class TestNotFound:
    def test_plain_not_found_is_an_error(self):
        r = vq.verify_claim(claim("completely invented sentence about nothing"), ctx("alpha", "beta"))
        assert (r.verdict, r.severity) == ("not_found", "error")

    def test_shared_citation_is_mentioned(self):
        r = vq.verify_claim(claim("completely invented sentence about nothing", shared=2), ctx("alpha", "beta"))
        assert "shares citation with 2 other quotes" in r.note

    def test_cross_language_only_after_a_failed_match(self):
        english = ctx("The cell divides rapidly in culture and the cell grows.")
        r = vq.verify_claim(claim("细胞在培养中分裂得非常迅速而且稳定", locs=(("unknown",),)), english)
        assert (r.verdict, r.severity, r.kind) == ("unverifiable", "suggestion", "cross_language")
        chinese = ctx("细胞在培养中分裂得非常迅速而且稳定，并且持续生长很长时间。")
        r2 = vq.verify_claim(claim("the cell divides rapidly in culture", locs=(("unknown",),)), chinese)
        assert r2.kind == "cross_language"

    def test_a_chinese_quote_that_matches_is_never_called_cross_language(self):
        english_with_chinese = ctx("English filler text. 细胞在培养中分裂得非常迅速而且稳定 more English.")
        r = vq.verify_claim(claim("细胞在培养中分裂得非常迅速而且稳定", locs=(("unknown",),)), english_with_chinese)
        assert r.verdict == "exact"

    def test_mixed_script_quotes_stay_not_found(self):
        english = ctx("Only English text about other things here.")
        few_cjk = vq.verify_claim(claim("GDP 增长 in Q2 report", locs=(("unknown",),)), english)
        assert few_cjk.verdict == "not_found"                       # fewer than 6 CJK letters: could be a term
        mixed_raw = ctx("English text with 一些中文 mixed in, 约占百分之三十 of the letters, and more中文内容在这里。")
        chinese = vq.verify_claim(claim("细胞在培养中分裂得非常迅速而且稳定", locs=(("unknown",),)), mixed_raw)
        assert chinese.verdict == "not_found"                       # the raw has real Chinese text, so it may be there

    def test_suggested_raw_when_the_quote_lives_in_another_file(self):
        other = doc("something", "the quoted passage is right here", "more")
        r = vq.verify_claim(claim("the quoted passage is right here"),
                            ctx("alpha", "beta", others=lambda: iter([("raw/b.md", other)])))
        assert (r.verdict, r.severity, r.suggested_raw) == ("not_found", "error", "raw/b.md")
        assert "found verbatim in raw/b.md" in r.note

    def test_unreadable_raw_is_unverifiable_infrastructure(self):
        r = vq.verify_claim(claim("any quote that is long enough"), vq.Context(doc=None, unreadable="raw file missing"))
        assert (r.verdict, r.severity, r.kind, r.note) == ("unverifiable", "suggestion", "infrastructure", "raw file missing")


class TestSeverityDowngrades:
    def not_found(self, **kw):
        return vq.verify_claim(claim("completely invented sentence about nothing", **kw.pop("claim_kw", {})),
                               ctx("alpha", "beta", **kw))

    def test_lossy_conversion_downgrades_to_warning(self):
        r = self.not_found(convert={"lossy": True})
        assert (r.severity, r.kind) == ("warning", "downgraded") and "lossy" in r.note

    def test_low_confidence_page_map_downgrades_only_page_citations(self):
        pages = {"confidence": "low", "pages": []}
        by_line = self.not_found(pages=pages)
        by_page = self.not_found(pages=pages, claim_kw={"locs": (("p", 1, 1),)})
        assert by_line.severity == "error" and by_page.severity == "warning"

    def test_strict_keeps_errors(self):
        r = self.not_found(convert={"lossy": True}, strict=True)
        assert r.severity == "error"

    def test_strict_promotes_near_miss(self, monkeypatch):
        monkeypatch.setattr(vq, "fuzzy_best", lambda d, f, r: (72, (1, 1), 0))
        c = claim("zzz qqq www vvv", locs=(("L", 1, 1),))
        assert vq.verify_claim(c, ctx("a", strict=True)).severity == "error"
        assert vq.verify_claim(c, ctx("a")).severity == "warning"


"""Incremental cache: keyed by quote + place, valid only for the same evidence."""
import json
from dataclasses import asdict

import verify_quotes as vq


def cache_claim(quote="the quoted passage is right here", raw="raw/a.md", locs=(("L", 1, 1),)):
    return vq.Claim("wiki/x.md", 5, quote, vq.fragments_of(quote), raw, locs, 5)


FP = {"raw_sha256": "r1", "pages_sha256": "", "convert_sha256": "", "dropped_sha256": "",
      "threshold": 85, "near_miss": 60, "strict": False, "algo": "a1"}


class TestKey:
    def test_stable_and_sensitive_to_quote_place_and_raw(self):
        base = vq.claim_key(cache_claim())
        assert base == vq.claim_key(cache_claim())
        assert base == vq.claim_key(cache_claim("the  quoted passage\nis right here"))      # whitespace is normalized
        assert base != vq.claim_key(cache_claim("The quoted passage is right here"))         # case is not
        assert base != vq.claim_key(cache_claim(raw="raw/b.md"))
        assert base != vq.claim_key(cache_claim(locs=(("L", 2, 2),)))
        assert base != vq.claim_key(cache_claim("a different quoted passage entirely"))

    def test_short_id_is_eight_hex_digits(self):
        assert len(vq.claim_id(cache_claim())) == 8 and int(vq.claim_id(cache_claim()), 16) >= 0


class TestCache:
    def result(self, verdict="exact"):
        return vq.Result(verdict, "none" if verdict == "exact" else "error", 100, [3, 3], "n")

    def test_hit_and_miss_on_fingerprint(self, tmp_path):
        cache = vq.Cache(tmp_path / ".cache" / "vq.json")
        cache.put("k", FP, "corpus1", self.result())
        assert vq.Result(**cache.get("k", FP, "corpus1")) == self.result()
        assert cache.get("k", dict(FP, raw_sha256="r2"), "corpus1") is None
        assert cache.get("k", dict(FP, strict=True), "corpus1") is None
        assert cache.get("other", FP, "corpus1") is None

    def test_corpus_only_matters_for_not_found(self, tmp_path):
        cache = vq.Cache(tmp_path / "vq.json")
        cache.put("ok", FP, "c1", self.result("exact"))
        cache.put("nf", FP, "c1", self.result("not_found"))
        assert cache.get("ok", FP, "c2") is not None            # an exact verdict does not depend on other files
        assert cache.get("nf", FP, "c2") is None                # suggested_raw may have changed
        assert cache.get("nf", FP, "c1") is not None

    def test_save_load_roundtrip_and_atomic_file(self, tmp_path):
        path = tmp_path / ".cache" / "vq.json"
        cache = vq.Cache(path)
        cache.put("k", FP, "c1", self.result())
        cache.save()
        assert path.exists() and not list(path.parent.glob("*.tmp"))
        again = vq.Cache(path)
        assert again.get("k", FP, "c1") is not None

    def test_corrupt_or_foreign_file_is_ignored(self, tmp_path):
        path = tmp_path / "vq.json"
        path.write_text("{ not json", encoding="utf-8")
        assert vq.Cache(path).entries == {}
        path.write_text(json.dumps({"schema": "something-else", "entries": {"k": {}}}), encoding="utf-8")
        assert vq.Cache(path).entries == {}

    def test_prune_keeps_only_seen_keys(self, tmp_path):
        cache = vq.Cache(tmp_path / "vq.json")
        cache.put("a", FP, "c", self.result())
        cache.put("b", FP, "c", self.result())
        cache.prune({"a"})
        assert set(cache.entries) == {"a"}

    def test_disabled_cache_never_hits_or_writes(self, tmp_path):
        cache = vq.Cache(None)
        cache.put("k", FP, "c", self.result())
        assert cache.get("k", FP, "c") is None
        cache.save()                                          # no path: nothing happens, no error


def test_algo_hash_is_stable_and_short():
    assert vq.algo_hash() == vq.algo_hash() and len(vq.algo_hash()) == 16


def test_corpus_token_changes_when_raw_files_change(tmp_path):
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / "a.md").write_text("one", encoding="utf-8")
    first = vq.corpus_token(tmp_path)
    assert first == vq.corpus_token(tmp_path)
    (tmp_path / "raw" / "b.txt").write_text("two", encoding="utf-8")
    assert vq.corpus_token(tmp_path) != first


"""verify_wiki, the JSON report, and the CLI."""
import hashlib
import json

import pytest

import verify_quotes as vq


def make_vault(tmp_path, pages=None, raw=None, claude="# Wiki\n"):
    (tmp_path / "wiki").mkdir(exist_ok=True)
    (tmp_path / "CLAUDE.md").write_text(claude, encoding="utf-8")
    for rel, text in (pages or {}).items():
        f = tmp_path / "wiki" / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
    for rel, text in (raw or {}).items():
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
    return tmp_path


def tree_hash(root):
    h = hashlib.sha256()
    for f in sorted(p for p in root.rglob("*") if p.is_file() and ".cache" not in p.parts and "outputs" not in p.parts):
        h.update(f.relative_to(root).as_posix().encode())
        h.update(f.read_bytes())
    return h.hexdigest()


RAW = "line one\nthe quoted passage is right here\nline three\n"
PAGE_OK = 'A claim: "the quoted passage is right here" (raw/articles/a.md, L2)\n'
PAGE_BAD = 'A claim: "an entirely invented sentence about nothing" (raw/articles/a.md, L2)\n'


class TestVerifyWiki:
    def test_exact_quote_gives_a_clean_report(self, tmp_path):
        root = make_vault(tmp_path, {"concepts/x.md": PAGE_OK}, {"raw/articles/a.md": RAW})
        report = vq.verify_wiki(root, use_cache=False)
        assert report["schema"] == "llm-wiki.verify-quotes/1" and report["pages_checked"] == 1
        assert report["counts"]["exact"] == 1 and report["errors"] == 0
        r = report["results"][0]
        assert (r["page"], r["line"], r["raw"], r["loc"], r["verdict"], r["severity"]) == (
            "wiki/concepts/x.md", 1, "raw/articles/a.md", "L2", "exact", "none")
        assert r["matched_lines"] == [2, 2] and r["quote"] == "the quoted passage is right here"
        assert len(r["id"]) == 8

    def test_fabricated_quote_is_an_error(self, tmp_path):
        root = make_vault(tmp_path, {"concepts/x.md": PAGE_BAD}, {"raw/articles/a.md": RAW})
        report = vq.verify_wiki(root, use_cache=False)
        assert report["counts"]["not_found_error"] == 1 and report["errors"] == 1

    def test_only_content_folders_are_scanned_and_conflict_copies_skipped(self, tmp_path):
        root = make_vault(tmp_path, {
            "concepts/x.md": PAGE_OK,
            "concepts/x.sync-conflict-20260101-ABC.md": PAGE_BAD,
            "templates/t.md": PAGE_BAD,
            "index.md": PAGE_BAD,
        }, {"raw/articles/a.md": RAW})
        report = vq.verify_wiki(root, use_cache=False)
        assert report["pages_checked"] == 1 and report["errors"] == 0

    def test_pages_argument_restricts_the_run(self, tmp_path):
        root = make_vault(tmp_path, {"concepts/x.md": PAGE_OK, "concepts/y.md": PAGE_BAD}, {"raw/articles/a.md": RAW})
        report = vq.verify_wiki(root, pages=["wiki/concepts/x.md"], use_cache=False)
        assert report["pages_checked"] == 1 and report["errors"] == 0
        with pytest.raises(vq.UsageError):
            vq.verify_wiki(root, pages=["wiki/concepts/missing.md"], use_cache=False)

    def test_missing_pdf_and_private_raw_are_unverifiable(self, tmp_path):
        page = (
            'Gone: "a quote whose file is gone" (raw/articles/gone.md, L1)\n'
            'Pdf: "a quote cited to a pdf file" (raw/pdfs/p.pdf, L1)\n'
            'Secret: "a quote inside the private zone" (raw/secret/s.md, L1)\n'
        )
        claude = "# Wiki\n\n## Private zones\n\n- `raw/secret/`\n"
        root = make_vault(tmp_path, {"concepts/x.md": page}, {"raw/secret/s.md": "a quote inside the private zone\n"}, claude)
        (root / "raw" / "pdfs").mkdir()
        (root / "raw" / "pdfs" / "p.pdf").write_bytes(b"%PDF")
        report = vq.verify_wiki(root, use_cache=False)
        assert report["counts"]["unverifiable_infrastructure"] == 3 and report["errors"] == 0
        notes = " | ".join(r["note"] for r in report["results"])
        assert "missing" in notes and "PDF" in notes and "private zone" in notes
        assert report["missing_raw_files"] == [{"raw": "raw/articles/gone.md", "citations": 1}]

    def test_uncited_and_malformed_are_counted(self, tmp_path):
        page = 'No cite: "a quote with no citation at all"\n\nBroken (raw/articles/oops\n'
        root = make_vault(tmp_path, {"concepts/x.md": page}, {})
        report = vq.verify_wiki(root, use_cache=False)
        assert report["counts"]["uncited"] == 1 and report["counts"]["malformed_citations"] == 1
        assert report["malformed"][0]["fragment"].startswith("(raw/articles/oops")

    def test_wrong_location_and_suggested_raw(self, tmp_path):
        raw = {"raw/articles/a.md": "x\n" * 30 + "the quoted passage is right here\n",
               "raw/articles/b.md": "an entirely invented sentence about nothing\n"}
        page = PAGE_OK + PAGE_BAD
        root = make_vault(tmp_path, {"concepts/x.md": page}, raw)
        report = vq.verify_wiki(root, use_cache=False)
        by_quote = {r["quote"]: r for r in report["results"]}
        assert by_quote["the quoted passage is right here"]["verdict"] == "wrong_location"
        bad = by_quote["an entirely invented sentence about nothing"]
        assert bad["verdict"] == "not_found" and bad["suggested_raw"] == "raw/articles/b.md"

    def test_lossy_conversion_downgrades_and_strict_restores(self, tmp_path):
        raw = {"raw/converted/doc/doc.md": "something else entirely\n",
               "raw/converted/doc/doc.convert.json": json.dumps({"lossy": True})}
        page = 'Q: "an entirely invented sentence about nothing" (raw/converted/doc/doc.md, L1)\n'
        root = make_vault(tmp_path, {"concepts/x.md": page}, raw)
        soft = vq.verify_wiki(root, use_cache=False)
        assert soft["counts"]["not_found_warning"] == 1 and soft["errors"] == 0
        strict = vq.verify_wiki(root, use_cache=False, strict=True)
        assert strict["counts"]["not_found_error"] == 1 and strict["errors"] == 1

    def test_page_citation_uses_pages_json(self, tmp_path):
        pages = {"confidence": "high", "pages": [
            {"page": 1, "line_start": 1, "line_end": 40, "how": "aligned"},
            {"page": 2, "line_start": 41, "line_end": 80, "how": "aligned"}]}
        raw = {"raw/converted/doc/doc.md": "x\n" * 60 + "the quoted passage is right here\n",
               "raw/converted/doc/doc.pages.json": json.dumps(pages)}
        page = 'Q: "the quoted passage is right here" (raw/converted/doc/doc.md, p.2)\n'
        root = make_vault(tmp_path, {"concepts/x.md": page}, raw)
        report = vq.verify_wiki(root, use_cache=False)
        assert report["counts"]["exact"] == 1


class TestCacheIntegration:
    def test_second_run_is_served_from_the_cache_until_the_raw_changes(self, tmp_path):
        root = make_vault(tmp_path, {"concepts/x.md": PAGE_OK}, {"raw/articles/a.md": RAW})
        first = vq.verify_wiki(root)
        assert first["counts"]["from_cache"] == 0 and (root / ".cache" / "verify_quotes.json").exists()
        second = vq.verify_wiki(root)
        assert second["counts"]["from_cache"] == 1 and second["counts"]["exact"] == 1
        (root / "raw" / "articles" / "a.md").write_text("changed completely\n", encoding="utf-8")
        third = vq.verify_wiki(root)
        assert third["counts"]["from_cache"] == 0 and third["errors"] == 1

    def test_threshold_change_invalidates(self, tmp_path):
        root = make_vault(tmp_path, {"concepts/x.md": PAGE_OK}, {"raw/articles/a.md": RAW})
        vq.verify_wiki(root, threshold=85)
        assert vq.verify_wiki(root, threshold=90)["counts"]["from_cache"] == 0

    def test_no_cache_option_writes_nothing(self, tmp_path):
        root = make_vault(tmp_path, {"concepts/x.md": PAGE_OK}, {"raw/articles/a.md": RAW})
        vq.verify_wiki(root, use_cache=False)
        assert not (root / ".cache").exists()


class TestCli:
    def test_clean_run_exits_0_and_writes_the_report_without_touching_the_vault(self, tmp_path, capsys):
        root = make_vault(tmp_path, {"concepts/x.md": PAGE_OK}, {"raw/articles/a.md": RAW})
        before = tree_hash(root)
        assert vq.main([str(root)]) == 0
        out = capsys.readouterr().out
        assert "exact 1" in out and "Report:" in out
        reports = list((root / "outputs").glob("verify-quotes-*.json"))
        assert len(reports) == 1 and json.loads(reports[0].read_text())["counts"]["exact"] == 1
        assert tree_hash(root) == before

    def test_fabricated_quote_exits_1_and_is_listed(self, tmp_path, capsys):
        root = make_vault(tmp_path, {"concepts/x.md": PAGE_BAD}, {"raw/articles/a.md": RAW})
        before = tree_hash(root)
        assert vq.main([str(root), "--no-cache"]) == 1
        assert "wiki/concepts/x.md:1" in capsys.readouterr().out
        assert tree_hash(root) == before

    def test_warnings_only_exit_0(self, tmp_path):
        raw = {"raw/articles/a.md": "x\n" * 30 + "the quoted passage is right here\n"}
        root = make_vault(tmp_path, {"concepts/x.md": PAGE_OK}, raw)
        assert vq.main([str(root), "--no-cache"]) == 0

    def test_usage_errors_exit_2(self, tmp_path, capsys):
        assert vq.main([str(tmp_path / "nope")]) == 2
        root = make_vault(tmp_path, {"concepts/x.md": PAGE_OK}, {"raw/articles/a.md": RAW})
        assert vq.main([str(root), "--page", "wiki/concepts/missing.md"]) == 2
        with pytest.raises(SystemExit) as exc:
            vq.main([str(root), "--threshold", "abc"])
        assert exc.value.code == 2

    def test_report_path_page_and_threshold_options(self, tmp_path):
        root = make_vault(tmp_path, {"concepts/x.md": PAGE_OK, "concepts/y.md": PAGE_BAD}, {"raw/articles/a.md": RAW})
        target = tmp_path / "custom" / "r.json"
        assert vq.main([str(root), "--page", "wiki/concepts/x.md", "--report", str(target), "--no-cache"]) == 0
        assert json.loads(target.read_text())["pages_checked"] == 1


"""verify_quotes --flag: one FIX comment per error-severity not_found, idempotent."""
import audit_review


def flag_vault(tmp_path, page, raw=None):
    return make_vault(tmp_path, {"concepts/x.md": page}, raw or {"raw/articles/a.md": RAW})


def page_text(root):
    return (root / "wiki" / "concepts" / "x.md").read_text(encoding="utf-8")


class TestFlag:
    def test_inserts_one_comment_under_the_quote_line_and_is_idempotent(self, tmp_path, capsys):
        root = flag_vault(tmp_path, "intro\n" + PAGE_BAD + "outro\n")
        assert vq.main([str(root), "--flag", "--no-cache"]) == 1
        lines = page_text(root).split("\n")
        assert lines[1].startswith("A claim:") and lines[2].startswith("<!-- FIX: quote not found [vq:")
        assert "in raw/articles/a.md L2 -->" in lines[2] and lines[3] == "outro"
        before = page_text(root)
        assert vq.main([str(root), "--flag", "--no-cache"]) == 1
        assert page_text(root) == before
        assert "FIX comments added: 0" in capsys.readouterr().out

    def test_rewording_around_the_quote_does_not_duplicate(self, tmp_path):
        root = flag_vault(tmp_path, PAGE_BAD)
        vq.main([str(root), "--flag", "--no-cache"])
        text = page_text(root).replace("A claim:", "A different claim:")
        (root / "wiki" / "concepts" / "x.md").write_text(text, encoding="utf-8")
        vq.main([str(root), "--flag", "--no-cache"])
        assert page_text(root).count("<!-- FIX: quote not found") == 1

    def test_several_errors_in_one_file_each_get_their_own_comment(self, tmp_path):
        page = (
            'First: "an entirely invented sentence about nothing" (raw/articles/a.md, L2)\n'
            "middle\n"
            'Second: "another fabricated statement of no origin" (raw/articles/a.md, L2)\n'
        )
        root = flag_vault(tmp_path, page)
        vq.main([str(root), "--flag", "--no-cache"])
        lines = page_text(root).split("\n")
        assert lines[0].startswith("First:") and lines[1].startswith("<!-- FIX")
        assert lines[2] == "middle"
        assert lines[3].startswith("Second:") and lines[4].startswith("<!-- FIX")

    def test_only_error_not_found_is_flagged(self, tmp_path):
        raw = {"raw/articles/a.md": "x\n" * 30 + "the quoted passage is right here\n",      # wrong_location
               "raw/converted/d/d.md": "something else\n",
               "raw/converted/d/d.convert.json": '{"lossy": true}'}                         # downgraded not_found
        page = (PAGE_OK +
                'Lossy: "an entirely invented sentence about nothing" (raw/converted/d/d.md, L1)\n')
        root = flag_vault(tmp_path, page, raw)
        assert vq.main([str(root), "--flag", "--no-cache"]) == 0
        assert "FIX" not in page_text(root)

    def test_suggested_file_is_part_of_the_comment(self, tmp_path):
        raw = {"raw/articles/a.md": RAW, "raw/articles/b.md": "an entirely invented sentence about nothing\n"}
        root = flag_vault(tmp_path, PAGE_BAD, raw)
        vq.main([str(root), "--flag", "--no-cache"])
        assert "found in raw/articles/b.md" in page_text(root)

    def test_dry_run_prints_and_changes_nothing(self, tmp_path, capsys):
        root = flag_vault(tmp_path, PAGE_BAD)
        before = page_text(root)
        assert vq.main([str(root), "--flag", "--dry-run", "--no-cache"]) == 1
        assert page_text(root) == before and "would add" in capsys.readouterr().out

    def test_dry_run_alone_is_a_usage_error(self, tmp_path):
        import pytest
        root = flag_vault(tmp_path, PAGE_OK)
        with pytest.raises(SystemExit) as exc:
            vq.main([str(root), "--dry-run"])
        assert exc.value.code == 2

    def test_audit_review_picks_the_comment_up(self, tmp_path):
        root = flag_vault(tmp_path, PAGE_BAD)
        vq.main([str(root), "--flag", "--no-cache"])
        entries = audit_review.scan_fix_comments(root)
        assert len(entries) == 1 and "quote not found" in str(entries[0])


"""Margin text (dropped.txt) as a last search target, and PDF page numbers in the report."""
import json


class TestDroppedAndPdfPages:
    def vault(self, tmp_path, page, md="body text only\nsecond line\n", pages=None, dropped=None):
        raw = {"raw/converted/doc/doc.md": md}
        if pages is not None:
            raw["raw/converted/doc/doc.pages.json"] = json.dumps(pages)
        if dropped is not None:
            raw["raw/converted/doc/doc.dropped.txt"] = dropped
        return make_vault(tmp_path, {"concepts/x.md": page}, raw)

    def test_margin_text_is_found_in_dropped_txt(self, tmp_path):
        page = 'DOI: "doi: https://doi.org/10.1101/2025.03.10.642480" (raw/converted/doc/doc.md, L1)\n'
        dropped = "p1\theader\tbioRxiv preprint doi: https://doi.org/10.1101/2025.03.10.642480; this version posted\n"
        root = self.vault(tmp_path, page, dropped=dropped)
        report = vq.verify_wiki(root, use_cache=False)
        r = report["results"][0]
        assert (r["verdict"], r["severity"], r["note"]) == ("exact", "none", "page margin")

    def test_without_dropped_txt_the_same_quote_is_not_found(self, tmp_path):
        page = 'DOI: "doi: https://doi.org/10.1101/2025.03.10.642480" (raw/converted/doc/doc.md, L1)\n'
        report = vq.verify_wiki(self.vault(tmp_path, page), use_cache=False)
        assert report["counts"]["not_found_error"] == 1

    def test_changing_dropped_txt_invalidates_the_cache(self, tmp_path):
        page = 'DOI: "doi: https://doi.org/10.1101/2025.03.10.642480" (raw/converted/doc/doc.md, L1)\n'
        root = self.vault(tmp_path, page)
        assert vq.verify_wiki(root)["errors"] == 1
        (root / "raw" / "converted" / "doc" / "doc.dropped.txt").write_text("p1\theader\tdoi: https://doi.org/10.1101/2025.03.10.642480\n")
        again = vq.verify_wiki(root)
        assert again["errors"] == 0 and again["counts"]["from_cache"] == 0

    def test_report_translates_lines_to_pdf_pages(self, tmp_path):
        pages = {"confidence": "high", "pages": [
            {"page": 1, "line_start": 1, "line_end": 1, "how": "aligned"},
            {"page": 2, "line_start": 2, "line_end": 3, "how": "aligned"}]}
        page = 'Q: "second line of the body" (raw/converted/doc/doc.md, L2)\n'
        root = self.vault(tmp_path, page, md="first line\nsecond line of the body\n", pages=pages)
        r = vq.verify_wiki(root, use_cache=False)["results"][0]
        assert r["verdict"] == "exact" and r["pdf_page"] == "2"

    def test_lines_to_pages_ranges_and_missing_map(self):
        pages = {"pages": [{"page": 1, "line_start": 1, "line_end": 10}, {"page": 2, "line_start": 11, "line_end": 20},
                           {"page": 3, "line_start": 21, "line_end": 30}]}
        assert vq.lines_to_pages(pages, [5, 5]) == "1"
        assert vq.lines_to_pages(pages, [9, 12]) == "1-2"
        assert vq.lines_to_pages(pages, []) is None
        assert vq.lines_to_pages(None, [1, 2]) is None


"""Optional script-folding rung: Traditional raw text, Simplified quote."""


class TestScriptFold:
    TABLE = str.maketrans({"歡": "欢", "視": "视", "環": "环", "財": "财", "經": "经", "資": "资", "務": "务"})

    def fake_simplified(self, monkeypatch):
        monkeypatch.setattr(vq.textnorm, "to_simplified", lambda s: s.translate(self.TABLE))

    def test_without_zhconv_the_traditional_text_does_not_match(self, monkeypatch):
        monkeypatch.setattr(vq.textnorm, "to_simplified", lambda s: s)
        d = doc("開頭", "歡迎收看本台環球財經 今天", "結尾")
        c = claim("欢迎收看本台环球财经", locs=(("L", 2, 2),), fragments=["欢迎收看本台环球财经"])
        assert vq.verify_claim(c, vq.Context(doc=d)).verdict != "exact"

    def test_with_the_fold_it_matches_and_reports_lines(self, monkeypatch):
        self.fake_simplified(monkeypatch)
        d = doc("開頭", "歡迎收看本台環球財經 今天", "結尾")
        c = claim("欢迎收看本台环球财经", locs=(("L", 2, 2),), fragments=["欢迎收看本台环球财经"])
        r = vq.verify_claim(c, vq.Context(doc=d))
        assert (r.verdict, r.note, r.matched_lines) == ("exact", "script-folded", [2, 2])

    def test_a_fold_hit_outside_the_cited_lines_is_wrong_location(self, monkeypatch):
        self.fake_simplified(monkeypatch)
        d = doc(*(["開頭"] * 30 + ["歡迎收看本台環球財經 今天"]))
        c = claim("欢迎收看本台环球财经", locs=(("L", 1, 1),), fragments=["欢迎收看本台环球财经"])
        assert vq.verify_claim(c, vq.Context(doc=d)).verdict == "wrong_location"

    def test_simplified_doc_is_cached_and_identity_returns_self(self, monkeypatch):
        d = doc("abc")
        monkeypatch.setattr(vq.textnorm, "to_simplified", lambda s: s)
        assert d.simplified() is d
        self.fake_simplified(monkeypatch)
        d2 = doc("歡迎")
        assert d2.simplified() is d2.simplified() and d2.simplified().text == "欢迎\n"
