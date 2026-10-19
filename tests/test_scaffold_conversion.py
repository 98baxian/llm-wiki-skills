"""Scaffolded vaults ship the conversion config and the new scripts."""
import convert_source


def test_scaffolded_claude_md_has_a_default_conversion_section(scaffolded_vault):
    text = (scaffolded_vault / "CLAUDE.md").read_text(encoding="utf-8")
    assert "## Document conversion" in text
    cfg = convert_source.load_config(scaffolded_vault)
    assert (cfg.converter, cfg.tier, cfg.timeout, cfg.command) == ("auto", "basic", 900, "")


def test_conversion_section_documents_privacy_and_disabling(scaffolded_vault):
    text = (scaffolded_vault / "CLAUDE.md").read_text(encoding="utf-8")
    section = text.split("## Document conversion", 1)[1].split("\n## ", 1)[0]
    assert "converter: none" in section and "never uploaded" in section.lower()


def test_bundled_skill_contains_the_new_modules(scaffolded_vault):
    scripts = scaffolded_vault / ".claude" / "skills" / "llm-wiki" / "scripts"
    for name in ("convert_source.py", "textnorm.py", "vault_config.py"):
        assert (scripts / name).exists(), name
