# Scaffold MinerU Question Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ask at init/scaffold time whether to enable MinerU for PDF conversion, and write `converter: mineru` (Yes) or `converter: pdftotext` (No) into the new vault's `CLAUDE.md`.

**Architecture:** `scaffold.py` gains a `mineru: bool | None` parameter, a `--mineru yes|no` flag and an interactive prompt. The conversion block in the generated `CLAUDE.md` takes its `converter:` value from the answer (`None` keeps `auto`). The wizard reference and `SKILL.md` are updated so the Claude-driven interview asks the question and passes the flag.

**Tech Stack:** Python 3, pytest. Spec: `docs/superpowers/specs/2026-10-21-scaffold-mineru-question-design.md`.

## Global Constraints

- Yes writes `converter: mineru`; No writes `converter: pdftotext`; no answer (direct `scaffold()` call) keeps `converter: auto`.
- Yes with MinerU not found: print a warning with the install hint and still write `mineru`.
- Reuse `convert_source.find_mineru_kit()`; do not duplicate the lookup.
- Do not install MinerU, do not migrate existing vaults, do not change what `auto` does.
- Docs and code comments are in English.
- Run tests from the repo root: `pytest` (`tests/README.md`).

---

### Task 1: scaffold.py question, flag and config line

**Files:**
- Modify: `llm-wiki/scripts/scaffold.py` (`scaffold()` signature ~line 98, conversion block ~lines 202-216, `main()` ~lines 686-733)
- Test: `tests/test_scaffold_conversion.py`

**Interfaces:**
- Produces: `scaffold(root, title, domains, lang, primary_lang="English", private_zones=None, mineru: bool | None = None)`; `_converter_for(mineru: bool | None) -> str`; `_warn_if_mineru_missing(mineru: bool | None) -> None`; `_parse_yes_no(text: str) -> bool`; CLI flag `--mineru {yes,no}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scaffold_conversion.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scaffold_conversion.py -v`
Expected: the new tests FAIL (`unexpected keyword argument 'mineru'` / unrecognized `--mineru`); the three original tests PASS.

- [ ] **Step 3: Implement**

In `llm-wiki/scripts/scaffold.py`, add helpers above `scaffold()`:

```python
def _converter_for(mineru: bool | None) -> str:
    """converter: value for the new vault. None keeps the auto fallback chain."""
    if mineru is None:
        return "auto"
    return "mineru" if mineru else "pdftotext"


def _warn_if_mineru_missing(mineru: bool | None) -> None:
    """The user chose MinerU explicitly; tell them now if it is not installed, since
    ingest would otherwise be the first place the missing binary shows up."""
    if mineru is not True:
        return
    import convert_source
    if convert_source.find_mineru_kit() is None:
        print("⚠ MinerU (mineru-kit) was not found. converter: mineru is written to CLAUDE.md, but "
              "PDF ingest will fail until MinerU is installed (or set MINERU_KIT_BIN).")


def _parse_yes_no(text: str) -> bool:
    """Interactive answer to the MinerU question; empty means the recommended Yes."""
    return text.strip().lower() not in ("n", "no", "none", "false", "0")
```

Change the signature:

```python
def scaffold(root: str, title: str, domains: list[str], lang: str,
             primary_lang: str = "English", private_zones: list[str] = None,
             mineru: bool | None = None) -> None:
```

Just before `claude_md = f"""# {title} Knowledge Base` (after `private_block = ...`), add:

```python
    converter_line = f"converter: {_converter_for(mineru)}".ljust(23)
    _warn_if_mineru_missing(mineru)
```

In the conversion block replace the line `converter: auto        # auto | mineru | pdftotext | command | none` with:

```
{converter_line}# auto | mineru | pdftotext | command | none
```

and replace the paragraph ending `...the remote\nMinerU service is not used.` by appending one sentence so it reads:

```
`auto` tries your `command`, then local MinerU (`mineru-kit`), then `pdftotext` (lossy, text-layer
PDFs only). Set `converter: none` to turn conversion off. Documents are never uploaded: the remote
MinerU service is not used. The `converter:` line was set when this vault was scaffolded; edit it to change converter.
```

In `main()`, after the `--private-zones` argument add:

```python
    parser.add_argument("--mineru", choices=["yes", "no"], default=None,
                        help="Use local MinerU for PDF conversion (yes -> converter: mineru, "
                             "no -> converter: pdftotext). Omit to be asked.")
```

After the `private_zones = [...]` line and before the `scaffold(...)` call add:

```python
    if args.mineru is None:
        print("\nEnable MinerU for PDF conversion? (powerful local parser: tables, formulas, scanned pages;")
        print("heavier than pdftotext, runs locally, never uploads documents)")
        print("Options: yes (recommended) · no (pdftotext only: text-layer PDFs)")
        mineru = _parse_yes_no(input("> "))
    else:
        mineru = args.mineru == "yes"
```

and change the call to `scaffold(..., private_zones=private_zones, mineru=mineru)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scaffold_conversion.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the full suite**

Run: `pytest`
Expected: all PASS (slow tests deselected).

- [ ] **Step 6: Commit (only when the user asks)**

```bash
git add llm-wiki/scripts/scaffold.py tests/test_scaffold_conversion.py
git commit -m "feat(scaffold): ask whether to enable MinerU and write the converter"
```

---

### Task 2: wizard reference and SKILL.md

**Files:**
- Modify: `llm-wiki/references/scaffold-wizard.md`
- Modify: `llm-wiki/SKILL.md` (lines ~82, ~243, ~603)

**Interfaces:**
- Consumes: the `--mineru yes|no` flag from Task 1.

- [ ] **Step 1: Edit the wizard**

In `scaffold-wizard.md`:
- "Ask these five questions" → "Ask these six questions".
- Rename `## Question 5 — Semantic Search (recommended)` to `## Question 6 — Semantic Search (recommended)`.
- Insert before it:

```markdown
## Question 5 — PDF Conversion (MinerU)

> "Do you want to enable **MinerU** for PDF conversion? It is a powerful local parser
> (tables, formulas, scanned pages, page-accurate citations). It runs on your machine and
> never uploads documents, but is heavier than `pdftotext`.
> Options: Yes (recommended) · No (use `pdftotext` only — text-layer PDFs; scanned PDFs are refused)"

Yes writes `converter: mineru`, No writes `converter: pdftotext` into the `## Document
conversion` section of `CLAUDE.md`. If MinerU (`mineru-kit`) is not installed the scaffold
script warns. The line can be edited later (`converter: none` turns conversion off).
```

- In the scaffold command add `--mineru <answer5>` and a bullet: "`--mineru` = Q5 (`yes` or `no`)."
- "If the user answered **Yes** to Question 5:" → "Question 6:".

- [ ] **Step 2: Edit SKILL.md**

- Line ~82: "run the three-question setup interview," → "run the setup interview,".
- Line ~243: "asks five things:" → "asks six things:", and insert "whether to enable **MinerU** for PDF conversion (`--mineru yes|no`); " before "whether to enable **semantic search**".
- Line ~603: "(Questions 1–4: domains, primary language, secondary language, semantic search + scaffold command)" → "(Questions 1–6: domains, primary language, secondary language, private zone, MinerU PDF conversion, semantic search + scaffold command)".

- [ ] **Step 3: Verify the docs**

Run: `grep -n "five\|three-question\|Questions 1" llm-wiki/SKILL.md llm-wiki/references/scaffold-wizard.md`
Expected: no remaining stale counts. Run `grep -n "mineru" llm-wiki/references/scaffold-wizard.md` and confirm the flag appears in the command and the bullet.

- [ ] **Step 4: Run the full suite and a scratch scaffold**

Run: `pytest` then
`python3 llm-wiki/scripts/scaffold.py /tmp/mineru-check "Check" --domains AI --primary-lang English --lang no --private-zones "" --mineru yes && grep -n "^converter:" /tmp/mineru-check/CLAUDE.md`
Expected: tests PASS; the grep prints `converter: mineru`. Remove `/tmp/mineru-check` afterwards (use the scratchpad directory instead of `/tmp` when executing).

- [ ] **Step 5: Commit (only when the user asks)**

```bash
git add llm-wiki/references/scaffold-wizard.md llm-wiki/SKILL.md docs/superpowers
git commit -m "docs(wizard): add the MinerU question, fix interview question counts"
```
