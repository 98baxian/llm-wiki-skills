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


import sys

import pytest

import scaffold as scaffold_mod


def _make(tmp_path, **kw):
    root = tmp_path / "vault"
    scaffold_mod.scaffold(str(root), "Test Wiki", domains=["AI"], lang="none",
                          primary_lang="English", private_zones=[], **kw)
    return root


def test_mineru_yes_writes_mineru(tmp_path):
    root = _make(tmp_path, mineru=True)
    assert convert_source.load_config(root).converter == "mineru"


def test_mineru_no_writes_pdftotext(tmp_path):
    root = _make(tmp_path, mineru=False)
    assert convert_source.load_config(root).converter == "pdftotext"


def test_no_answer_keeps_auto(tmp_path):
    root = _make(tmp_path)
    assert convert_source.load_config(root).converter == "auto"


def test_yes_without_mineru_installed_warns_but_still_writes_mineru(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(convert_source, "find_mineru_kit", lambda: None)
    root = _make(tmp_path, mineru=True)
    assert "mineru-kit" in capsys.readouterr().out
    assert convert_source.load_config(root).converter == "mineru"


def test_yes_with_mineru_installed_does_not_warn(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(convert_source, "find_mineru_kit", lambda: "/usr/bin/mineru-kit")
    _make(tmp_path, mineru=True)
    assert "was not found" not in capsys.readouterr().out


def _run_main(monkeypatch, tmp_path, extra):
    root = tmp_path / "vault"
    argv = ["scaffold.py", str(root), "Test Wiki", "--domains", "AI",
            "--primary-lang", "English", "--lang", "no", "--private-zones", ""] + extra
    monkeypatch.setattr(sys, "argv", argv)
    scaffold_mod.main()
    return root


def test_cli_flag_no(tmp_path, monkeypatch):
    root = _run_main(monkeypatch, tmp_path, ["--mineru", "no"])
    assert convert_source.load_config(root).converter == "pdftotext"


def test_cli_flag_yes(tmp_path, monkeypatch):
    monkeypatch.setattr(convert_source, "find_mineru_kit", lambda: "/usr/bin/mineru-kit")
    root = _run_main(monkeypatch, tmp_path, ["--mineru", "yes"])
    assert convert_source.load_config(root).converter == "mineru"


@pytest.mark.parametrize("answer,expected", [("", "mineru"), ("y", "mineru"), ("No", "pdftotext"), ("n", "pdftotext")])
def test_interactive_prompt(tmp_path, monkeypatch, answer, expected):
    monkeypatch.setattr(convert_source, "find_mineru_kit", lambda: "/usr/bin/mineru-kit")
    monkeypatch.setattr("builtins.input", lambda *a: answer)
    root = _run_main(monkeypatch, tmp_path, [])
    assert convert_source.load_config(root).converter == expected
