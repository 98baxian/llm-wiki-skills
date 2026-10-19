"""Tests for textnorm.py: the shared normalizer."""
import textnorm


class TestNormalize:
    def test_keeps_case(self):
        assert textnorm.normalize("The Cell DIVIDES") == "The Cell DIVIDES"

    def test_collapses_whitespace_and_strips(self):
        assert textnorm.normalize("  a \n\t b   c  ") == "a b c"

    def test_nfkc_folds_full_width_and_ligatures(self):
        assert textnorm.normalize("ＡＢＣ１２３，ok") == "ABC123,ok"
        assert textnorm.normalize("ﬁne") == "fine"

    def test_curly_quotes_and_dashes(self):
        assert textnorm.normalize("“quoted” it’s a–b—c") == "\"quoted\" it's a-b-c"

    def test_removes_markdown_backslash_escapes(self):
        assert textnorm.normalize(r"5\*3 percent and \~300 million") == "5*3 percent and ~300 million"

    def test_strips_zero_width_characters(self):
        assert textnorm.normalize("a​b﻿c") == "abc"

    def test_joins_hyphenated_line_break(self):
        assert textnorm.normalize("inter-\nnational rules") == "international rules"

    def test_hyphen_kept_variant(self):
        assert textnorm.normalize("well-\nknown", join_hyphen=False) == "well-known"

    def test_does_not_join_when_hyphen_is_a_list_marker(self):
        assert textnorm.normalize("items -\nnext") == "items - next"

    def test_idempotent(self):
        once = textnorm.normalize("“A”  b-\nc \\* d")
        assert textnorm.normalize(once) == once

    def test_does_not_loosen_exact_matching(self):
        # check 13 relies on case-preserving normalization
        assert "US" not in textnorm.normalize("tell us now")
        assert "ACT" not in textnorm.normalize("act of 1990")


class TestFoldCaseAndLoose:
    def test_fold_case(self):
        assert textnorm.fold_case("The Cell  DIVIDES") == "the cell divides"

    def test_loose_drops_spaces_punctuation_and_markers(self):
        assert textnorm.loose("明年 继续 扩建，**社区图书馆**！") == "明年继续扩建社区图书馆"
        assert textnorm.loose("A-b, C_d.") == "abcd"


class TestVariantsAndContains:
    def test_variants_single_when_no_hyphen_break(self):
        assert textnorm.variants("plain text") == ["plain text"]

    def test_variants_both_forms(self):
        assert textnorm.variants("well-\nknown fact") == ["wellknown fact", "well-known fact"]

    def test_contains_matches_either_hyphen_form(self):
        raw = "a well-\nknown fact and inter-\nnational law"
        assert textnorm.contains(raw, "well-known fact")
        assert textnorm.contains(raw, "international law")
        assert not textnorm.contains(raw, "Well-known fact")


class TestNormalizeWithMap:
    def test_offsets_point_back_to_source_characters(self):
        text = "He said \\*hi\\*  there"
        norm, offs = textnorm.normalize_with_map(text)
        assert norm == "He said *hi* there"
        assert len(norm) == len(offs)
        for ch, off in zip(norm, offs):
            if ch != " ":
                assert text[off] == ch

    def test_collapsed_whitespace_maps_to_first_space(self):
        norm, offs = textnorm.normalize_with_map("a   b")
        assert norm == "a b"
        assert offs == [0, 1, 4]

    def test_leading_and_trailing_space_dropped_with_offsets(self):
        norm, offs = textnorm.normalize_with_map("  ab  ")
        assert norm == "ab"
        assert offs == [2, 3]

    def test_fold_and_loose_modes_keep_lengths_consistent(self):
        for kwargs in ({"fold": True}, {"loose_mode": True}):
            norm, offs = textnorm.normalize_with_map("Ａ b-\nc ＤＥ", **kwargs)
            assert len(norm) == len(offs)

    def test_hyphen_join_offsets_skip_the_break(self):
        text = "inter-\nnational"
        norm, offs = textnorm.normalize_with_map(text)
        assert norm == "international"
        assert text[offs[5]] == "n"  # first char after the removed "-\n"
        assert offs[5] == 7


class TestToSimplified:
    def test_identity_without_zhconv_or_converts_with_it(self):
        out = textnorm.to_simplified("財經")
        assert out in ("財經", "财经")
