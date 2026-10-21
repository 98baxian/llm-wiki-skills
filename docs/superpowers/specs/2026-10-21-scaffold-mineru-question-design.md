# Scaffold question: enable MinerU for PDF conversion

Date: 2026-10-21
Status: approved 2026-10-21, implemented (uncommitted, awaiting independent verification)
Scope: `llm-wiki/` skill (wizard reference, `scaffold.py`, `SKILL.md`), tests

## Problem

New vaults get `converter: auto` hardcoded in the `## Document conversion` section of
`CLAUDE.md`. `auto` uses local MinerU (`mineru-kit`) only if it happens to be installed,
and silently falls back to `pdftotext` otherwise. Neither the setup wizard nor
`scaffold.py` asks the user about MinerU, so the choice is implicit.

## Decision

The init/scaffold flow asks whether to enable MinerU for PDF conversion:

| Answer | `converter:` written to `CLAUDE.md` |
|--------|-------------------------------------|
| Yes    | `mineru`                            |
| No     | `pdftotext`                         |

Explicit values replace `auto` in newly scaffolded vaults. `auto` remains a valid value that
users can set by hand, and the default when `scaffold()` is called without an answer (keeps
existing callers and tests unchanged).

## Changes

1. **`llm-wiki/references/scaffold-wizard.md`**: add Question 5, "PDF conversion (MinerU)",
   before semantic search. Semantic search becomes Question 6. Pass the answer to the script
   as `--mineru yes|no`.
2. **`llm-wiki/scripts/scaffold.py`**
   - New `--mineru {yes,no}` flag. If omitted in CLI mode, the interactive fallback asks the
     same question, like the other prompts.
   - `scaffold(..., mineru: bool | None = None)`. `None` keeps `converter: auto`.
   - The conversion block writes `converter: mineru` or `converter: pdftotext` from the answer.
   - On Yes with no MinerU found (`convert_source.find_mineru_kit()` returns `None`): print a
     warning with the install hint and still write `mineru`. The user made an explicit choice;
     ingest then fails with the existing install hint until MinerU is installed.
   - Reuse `find_mineru_kit()`; do not duplicate the lookup.
   - The conversion prose names every value already. Add one sentence saying the `converter:`
     line can be edited later to change it.
3. **`llm-wiki/SKILL.md`**: the interview is described as "three-question" (line ~82),
   "five things" (line ~243) and "Questions 1–4" (line ~603). Update these to match the
   wizard after the new question is added.

## Behaviour

| Answer | `mineru-kit` present | Result |
|--------|----------------------|--------|
| Yes | yes | PDFs converted with MinerU (`basic` tier, CPU) |
| Yes | no | Warning at scaffold time; ingest exits with the install hint |
| No | any | PDFs converted with `pdftotext` only (lossy, text-layer PDFs). Scanned PDFs are refused with exit 4 |
| no answer (direct `scaffold()` call) | any | Unchanged: `converter: auto` |

Consequence for the No answer: a scanned PDF in a No vault is refused rather than read
directly. Setting `converter: none` in `CLAUDE.md` restores direct reading.

## Testing

In `tests/test_scaffold_conversion.py`:

- Yes writes `mineru`; `convert_source.load_config()` returns `converter == "mineru"`.
- No writes `pdftotext`.
- No answer keeps `auto` (existing test still passes).
- Yes with `find_mineru_kit` patched to `None`: warning is printed and `CLAUDE.md` still says `mineru`.
- `--mineru` CLI flag parsed; interactive prompt answered via patched `input`.

Verification is done by someone other than the implementer: run the `llm-wiki` test suite
(command in `tests/README.md`) and check the generated `CLAUDE.md` on a scratch vault.

## Out of scope

- Installing MinerU from the wizard (large dependency; never automatic).
- Migrating existing vaults. Users edit the `converter:` line by hand.
- Changing what `auto` does.
- The stale copies of these specs under `Agent-Workspace/LLM-WIKI/specs/`.

## Self-review

- Placeholders: none.
- Consistency: the behaviour table matches the mapping table and the Changes section.
- Scope: one question, one flag, one template line, tests. Single implementation plan.
- Ambiguity resolved: "Yes" writes `mineru` explicitly, not `auto`; the missing-binary case
  warns and does not fall back silently.
