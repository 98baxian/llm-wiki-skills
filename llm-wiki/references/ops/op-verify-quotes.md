# op-verify-quotes - check quoted passages against the raw files they cite

`verify_quotes.py` is a deterministic checker. For every quotation in a wiki page that carries a
raw citation, it decides whether the passage really occurs in the cited raw file, at the cited
place. It reports; it edits wiki pages only with `--flag`. It does not judge paraphrase, meaning
or numbers outside quotation marks.

```bash
python3 .claude/skills/llm-wiki/scripts/verify_quotes.py <wiki-root>             # report + summary
python3 .claude/skills/llm-wiki/scripts/verify_quotes.py <wiki-root> --page wiki/concepts/x.md
python3 .claude/skills/llm-wiki/scripts/verify_quotes.py <wiki-root> --flag --dry-run
python3 .claude/skills/llm-wiki/scripts/lint_wiki.py <wiki-root> --verify-quotes   # as lint check 16
```

Options: `--page PATH` (repeatable), `--threshold N` (fuzzy similarity, default 85), `--report PATH`
(default `outputs/verify-quotes-YYYY-MM-DD.json`), `--strict`, `--no-cache`, `--flag [--dry-run]`.
Exit codes: 0 no error-severity quote, 1 at least one, 2 usage error.
Add `.cache/` to the vault's `.gitignore`; it holds the incremental verdict cache.

## Files

All in `llm-wiki/scripts/`: `verify_quotes.py` (entry point and public facade: `verify_wiki`, `main`,
report), `vq_extract.py` (quote extraction and binding to citations), `vq_match.py` (matching ladder,
fuzzy fallback, verdicts), `vq_store.py` (claim identity, cache, raw-file store). Copy all of them
together with `citations.py`, `textnorm.py` and `vault_config.py`. The cache fingerprint (`algo`) hashes
every `vq_*.py` plus `verify_quotes.py`, `textnorm.py` and `citations.py`, so editing any of them
invalidates cached verdicts.

## What counts as a quote

Text in `"..."`, curly quotes, `「...」` or `『...』`, and a run of `>` blockquote lines, on one line
(a quotation that wraps over several lines of the page is not extracted - do not hard-wrap quotes).
Minimum length: 3 Latin words or 12 CJK characters; with a digit, 2 words or 6 characters. A quote
binds to the first citation to its right on the same line, otherwise the first citation later in the
same paragraph, list item or blockquote group (never across table rows). A quote with no citation is
counted as `uncited`, never as a failure. `[...]`, `...`, `…`, `……` and `⋯` split a quote into fragments that must
all occur, in order. A fragment under the minimum length is still required (found in order, never
fuzzy-matched): `"X is fast [...] but inaccurate"` fails if `but inaccurate` is missing.

## Citation forms understood

`(raw/x.md, L14-22)`, `L11-L13`, `L39/L63`, `L61-62, L91`, `L10-L36; L81-L93`, `p.12`, `P.3-4`,
`location unknown`, several sources in one parenthesis, an en dash or full-width comma, extra spaces,
commas or parentheses inside the file name. A `(raw/` fragment that does not parse is reported as a
malformed citation. For a PDF cite the converted markdown (`raw/converted/<slug>/<slug>.md`).

## Verdicts

| Verdict | Meaning | Severity |
|---|---|---|
| `exact` | found in the cited lines (literal, case-folded for long quotes, loose, script-folded, or page margin) | none |
| `fuzzy` | similar (>= threshold) in the cited lines, or a short quote that differs only in case or matches only loosely | warning |
| `wrong_location` | found, but outside the cited lines (lines reported) | warning |
| `near_miss` | similarity 60-85: probably a paraphrase in quotation marks | warning |
| `not_found` | nowhere in the file; `suggested_raw` names another raw file that has it | **error** |
| `unverifiable` | raw missing, a PDF, a private zone, or a cross-language quote | suggestion |

`not_found` is downgraded to a warning when the raw file is a lossy conversion (`lossy: true`) or
the page map has low confidence and the citation is a page number; `--strict` keeps it an error and
also makes `near_miss` an error, so a strict run exits 1 for it; `--flag` still annotates only
error-severity `not_found` results, never a `near_miss`. A quote that cannot occur in the raw file because the scripts do
not overlap (6+ CJK letters against an English raw file, or the reverse) is `unverifiable` instead
of `not_found`, but only after every match attempt failed.

## Flagging

`--flag` inserts `<!-- FIX: quote not found [vq:<id>] in <raw> <loc> -->` under each quote that is
an error-severity `not_found` (with `; found in <file>` when it exists elsewhere). Running it twice
adds nothing. `audit` picks the comments up like any other inline FIX.

## Known limits

Quotes crossing a page break are matched only if the converter kept the text contiguous;
reconverting a PDF changes line numbers (lint check 5 and `cascade_flag.py` flag the affected
citations); table cells and figure captions depend on how the converter rendered them; a close
paraphrase reads as `near_miss`, and the 60 / 85 band is a judgement call to tune per vault.
