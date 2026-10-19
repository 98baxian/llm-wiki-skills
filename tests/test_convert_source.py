"""Tests for convert_source.py: config, slugs, hashing."""
import pytest

import convert_source


def _vault(tmp_path, body=None):
    text = "# Wiki\n\n## Scope\n\n- AI\n"
    if body is not None:
        text += "\n## Document conversion\n\n" + body + "\n"
    (tmp_path / "CLAUDE.md").write_text(text, encoding="utf-8")
    return tmp_path


class TestConfig:
    def test_defaults_without_section(self, tmp_path):
        cfg = convert_source.load_config(_vault(tmp_path))
        assert (cfg.converter, cfg.tier, cfg.timeout, cfg.command) == ("auto", "basic", 900, "")

    def test_reads_values(self, tmp_path):
        body = 'converter: command\ntier: flash\ntimeout: 60\ncommand: "mytool {input} {outdir}"'
        cfg = convert_source.load_config(_vault(tmp_path, body))
        assert (cfg.converter, cfg.tier, cfg.timeout) == ("command", "flash", 60)
        assert cfg.command == "mytool {input} {outdir}"

    def test_missing_claude_md_gives_defaults(self, tmp_path):
        assert convert_source.load_config(tmp_path).converter == "auto"

    @pytest.mark.parametrize("body", ["converter: nope", "tier: huge", "timeout: soon", "timeout: 0"])
    def test_invalid_values_exit_2(self, tmp_path, body):
        with pytest.raises(convert_source.ConvertError) as exc:
            convert_source.load_config(_vault(tmp_path, body))
        assert exc.value.code == convert_source.EXIT_USAGE


class TestSlug:
    def test_basic(self):
        assert convert_source.sanitize_slug("My Paper (v2): Final!.pdf") == "my-paper-v2-final"

    def test_cjk_is_kept(self):
        assert convert_source.sanitize_slug("第一性原理 笔记.pdf") == "第一性原理-笔记"

    def test_citation_breaking_characters_removed(self):
        slug = convert_source.sanitize_slug("a,b (1); c) d.pdf")
        assert not any(ch in slug for ch in ",();")

    def test_empty_becomes_untitled(self):
        assert convert_source.sanitize_slug("!!!.pdf") == "untitled"


def test_sha256_file(tmp_path):
    f = tmp_path / "x.bin"
    f.write_bytes(b"hello")
    assert convert_source.sha256_file(f) == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
