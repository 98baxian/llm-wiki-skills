"""scaffold.py skill placement: copy (default) vs --link-skill symlink, and never writing
through an existing symlink or deleting an existing copy."""
import shutil
import sys
from pathlib import Path

import pytest

import scaffold as scaffold_mod

SKILL_SRC = Path(scaffold_mod.__file__).resolve().parent.parent


def _make(root, **kw):
    scaffold_mod.scaffold(str(root), "Test Wiki", domains=["AI"], lang="none",
                          primary_lang="English", private_zones=[], mineru=False, **kw)
    return root / ".claude" / "skills" / "llm-wiki"


@pytest.fixture
def install(tmp_path):
    """A separate 'shared install' of the skill, like ~/.local/share/llm-wiki-skills/llm-wiki."""
    dst = tmp_path / "install" / "llm-wiki"
    shutil.copytree(SKILL_SRC, dst, ignore=shutil.ignore_patterns("__pycache__"))
    return dst


def _snapshot(d):
    return {p.relative_to(d): p.read_bytes() for p in d.rglob("*") if p.is_file()}


def test_default_still_copies(tmp_path):
    skill = _make(tmp_path / "v")
    assert skill.is_dir() and not skill.is_symlink()
    assert (skill / "SKILL.md").is_file()


def test_link_skill_to_path_creates_symlink(tmp_path, install, capsys):
    skill = _make(tmp_path / "v", link_skill=str(install))
    assert skill.is_symlink() and skill.resolve() == install.resolve()
    assert (skill / "scripts" / "lint_wiki.py").is_file()
    out = capsys.readouterr().out
    assert "Linked .claude/skills/llm-wiki" in out and "linked into this vault" in out


def test_link_skill_without_path_links_to_own_skill(tmp_path):
    skill = _make(tmp_path / "v", link_skill="")
    assert skill.is_symlink() and skill.resolve() == SKILL_SRC


def test_invalid_link_target_fails_before_writing_anything(tmp_path):
    root = tmp_path / "v"
    with pytest.raises(ValueError, match="no SKILL.md"):
        _make(root, link_skill=str(tmp_path / "nowhere"))
    assert not root.exists()


def test_rescaffold_copy_mode_onto_linked_vault_does_not_crash_or_write_through(tmp_path, install, capsys):
    # The bug: copytree onto a symlinked skill dir copied files onto themselves
    # ("are the same file"), or into whatever install the link points at.
    root = tmp_path / "v"
    _make(root, link_skill=str(install))
    install_before = _snapshot(install)
    capsys.readouterr()
    skill = _make(root)                                   # default copy mode
    assert skill.is_symlink() and skill.resolve() == install.resolve()
    assert _snapshot(install) == install_before
    assert "not copying" in capsys.readouterr().out


def test_copy_mode_onto_link_to_own_skill_is_a_noop(tmp_path):
    root = tmp_path / "v"
    _make(root, link_skill="")
    skill = _make(root)
    assert skill.is_symlink() and skill.resolve() == SKILL_SRC


def test_link_mode_never_replaces_a_real_copy(tmp_path, install, capsys):
    root = tmp_path / "v"
    skill = _make(root)                                   # real bundled copy
    marker = skill / "LOCAL-EDIT.md"
    marker.write_text("keep me", encoding="utf-8")
    capsys.readouterr()
    _make(root, link_skill=str(install))
    assert not skill.is_symlink() and marker.read_text(encoding="utf-8") == "keep me"
    out = capsys.readouterr().out
    assert "not replacing it with a link" in out and "NOT placed" in out


def test_link_mode_repoints_an_existing_symlink(tmp_path, install, capsys):
    root = tmp_path / "v"
    _make(root, link_skill="")                            # → repo skill
    skill = _make(root, link_skill=str(install))          # → shared install
    assert skill.is_symlink() and skill.resolve() == install.resolve()
    assert "Repointed" in capsys.readouterr().out
    assert (SKILL_SRC / "SKILL.md").is_file()             # old target untouched


def test_link_mode_repairs_a_broken_symlink(tmp_path, install):
    root = tmp_path / "v"
    skill = root / ".claude" / "skills" / "llm-wiki"
    skill.parent.mkdir(parents=True)
    skill.symlink_to(tmp_path / "gone", target_is_directory=True)
    _make(root, link_skill=str(install))
    assert skill.resolve() == install.resolve()


def test_cli_link_skill_flag(tmp_path, install, monkeypatch):
    root = tmp_path / "v"
    monkeypatch.setattr(sys, "argv", [
        "scaffold.py", str(root), "Test Wiki", "--domains", "AI", "--primary-lang", "English",
        "--lang", "no", "--private-zones", "", "--mineru", "no", "--link-skill", str(install)])
    scaffold_mod.main()
    skill = root / ".claude" / "skills" / "llm-wiki"
    assert skill.is_symlink() and skill.resolve() == install.resolve()


def test_cli_bad_link_target_exits_2_without_writing(tmp_path, monkeypatch, capsys):
    root = tmp_path / "v"
    monkeypatch.setattr(sys, "argv", [
        "scaffold.py", str(root), "Test Wiki", "--domains", "AI", "--primary-lang", "English",
        "--lang", "no", "--private-zones", "", "--mineru", "no", "--link-skill", str(tmp_path)])
    with pytest.raises(SystemExit) as exc:
        scaffold_mod.main()
    assert exc.value.code == 2 and "no SKILL.md" in capsys.readouterr().err
    assert not root.exists()
