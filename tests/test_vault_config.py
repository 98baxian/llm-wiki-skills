"""Tests for vault_config.py: the tiny CLAUDE.md readers."""
import vault_config

CLAUDE = """# Test Knowledge Base

## Scope

What this wiki covers:
- AI

## Private zones

Paths containing sensitive data.

- `sensitive_data/`
- `clinic/notes`

## Document conversion

converter: auto        # auto | mineru
tier: basic
command: "mytool {input} {outdir}"

### Sub heading stays inside the section
timeout: 120

## Notes for the LLM
- Language: English
"""


def _vault(tmp_path, text=CLAUDE):
    (tmp_path / "CLAUDE.md").write_text(text, encoding="utf-8")
    return tmp_path


def test_section_lines_stops_at_next_h2(tmp_path):
    lines = vault_config.section_lines(_vault(tmp_path), "Document conversion")
    assert any(l.startswith("converter:") for l in lines)
    assert not any("Language" in l for l in lines)


def test_section_lines_missing_file_or_heading(tmp_path):
    assert vault_config.section_lines(tmp_path, "Document conversion") == []
    assert vault_config.section_lines(_vault(tmp_path), "Nope") == []


def test_key_values_strips_comments_and_quotes(tmp_path):
    kv = vault_config.key_values(vault_config.section_lines(_vault(tmp_path), "Document conversion"))
    assert kv["converter"] == "auto"
    assert kv["tier"] == "basic"
    assert kv["command"] == "mytool {input} {outdir}"
    assert kv["timeout"] == "120"


def test_private_zones_parsed_from_bullets(tmp_path):
    assert vault_config.private_zones(_vault(tmp_path)) == ["sensitive_data", "clinic/notes"]


def test_in_private_zone(tmp_path):
    root = _vault(tmp_path)
    (root / "sensitive_data").mkdir()
    (root / "raw").mkdir()
    assert vault_config.in_private_zone(root, root / "sensitive_data" / "a.pdf")
    assert vault_config.in_private_zone(root, root / "clinic" / "notes" / "deep" / "b.pdf")
    assert not vault_config.in_private_zone(root, root / "raw" / "c.pdf")
    assert not vault_config.in_private_zone(root, root / "sensitive_data_not" / "d.pdf")
