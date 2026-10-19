# verify_quotes — design

Status: DRAFT for user review. Spec B of two; depends on Spec A (`2026-10-19-document-conversion-design.md`).
Date: 2026-10-19
Supersedes: the externally supplied "verify_quotes.py — Implementation Plan", which was written
without reading this repo. Its page-based parsing, pdftotext page cache and OCR sections are
dropped (see §12).

## 1. Goal

A deterministic checker that, for every quoted passage in `wiki/` that carries a raw citation,
decides whether the passage really occurs in the cited raw file, at the cited place. It reports; it
edits wiki pages only with `--flag`. It does not judge paraphrase, meaning or numbers outside quotes.

Why: lint check 13 covers only the `claims_requiring_verification` frontmatter list. Quotes in the
page body, which is where a hallucinated "quotation" does the damage, are unchecked.

### Non-goals (v1)

Numbers/dates without quotation marks; paraphrase fidelity; quotes spanning a page break;
translation matching beyond the cross-language rule in §5; running inside `fast_ingest.py`
(never); OCR (Spec A's converter does that).

## 2. Repo facts this design rests on (read from `83e4331`, `lint_wiki.py` / `cascade_flag.py`)

- Citation grammar already linted: `CITATION_RE = \(raw/[^)]+,\s*(L\d|p\.\d)|location unknown\)|\[synthesis\]`;
  `CLAIM_ENTRY_RE` accepts `Lxx[-yy]` and `p.N`. So raw citations come as `(raw/x.md, L14-22)`,
  `(raw/x.md, p.12)` and `(raw/x.md, location unknown)`. Check 14 forbids `[[sources/…]]` citations.
  Those regexes are not reused: `CITATION_RE` is a three-way top-level alternation, `CLAIM_ENTRY_RE`
  does not accept `location unknown`, and `cascade_flag.CITATION_LINE_RE` needs a comma.
  `verify_quotes` defines one citation pattern of its own,
  `\((raw/[^,)]+?)\s*,\s*(L\d+(?:-\d+)?|p\.\d+(?:-\d+)?|location unknown)\)`.
- Repo's own anti-hallucination example (`SKILL.md:487`): `The model has "175 billion parameters" (raw/articles/gpt3.md, L8)`.
- `lint()` runs checks into module-level `errors/warnings/suggestions` via `log_issue(level, check, msg)`
  and returns 1 iff there is an ERROR. It writes `outputs/lint-DATE.md`. There is **no `--since`**;
  `--stale-only` is a separate mode that only lists stale concept pages.
- `cascade_flag.py` inserts a fixed `FIX_TAG` on the line after any line matching
  `\((raw/[^,)]+),` that cites a modified source; idempotent by looking at the next line.
- `audit_review.py` turns any inline `<!-- FIX: … -->` into an audit entry (`fix-<stem>-L<line>`), so a
  new FIX text needs no new consumer.
- Wiki page folders: `concepts, sources, entities, comparisons, synthesis` (as in check 14);
  `templates`, `index.md`, `hot.md`, `overview.md`, `QUESTIONS.md` are skipped.
- Test baseline: 31 passing (`uv run --with pytest python -m pytest`).

## 3. Components

One module, `llm-wiki/scripts/verify_quotes.py` (split only if it passes ~500 lines), importable and CLI.
Shared helper `textnorm.normalize_for_match` from Spec A.

```
extract_claims(page_text, page_path) -> (claims, uncited)      # §4
resolve_target(claim, vault)         -> Target                  # §5
verify_claim(claim, target, thr)     -> Result                  # §6
verify_wiki(root, pages, thr, cache) -> Report                  # §8
main(argv)                                                      # §9
```

`Claim`: `page, line_no, quote, raw_path, loc` where `loc` is `("L", a, b)`, `("p", n)` or `("unknown",)`.
`Result`: `claim, verdict, severity, score, matched_lines, note`.

## 4. Extracting quotes and binding them to citations

Quote = text inside straight `"…"`, curly `“…”`, `「…」`, `『…』`, or a markdown blockquote line (`>`).
Minimum length: Latin 3 words, CJK 12 characters; a quote that contains a digit qualifies from
2 words / 6 characters (so the repo's own `"175 billion parameters"` example is checked).
Constants, not flags. Single quotes are never quote delimiters. Links and inline code are removed
before extraction. A quote must sit on one line (hard-wrapped quotes are a known limit, §13).
Ignored: YAML frontmatter, fenced code, inline code, HTML comments (including FIX comments), and
the `claims_requiring_verification` list (check 13 owns it).

Binding rule (explicit, documented in the docstring): a quote binds to the **first raw citation to its
right on the same line**; failing that, the first citation in the same paragraph (contiguous
non-blank lines; a blockquote group counts as one unit and may use the line right after it). A table
row is its own binding boundary: a quote never binds to a citation in another row. A run of
consecutive `>` lines is one quote located at its first line. Several quotes before one citation
all bind to it. No citation → counted as `uncited`, never a failure.

Ellipsis: a quote containing `[...]`, `[…]`, `...` or `…` is split into fragments; fragments under
the minimum length are dropped; at least one must remain; every remaining fragment must be found,
in order, in the target; the result takes the weakest verdict of the fragments.

## 5. Choosing the search target

| Citation | Target text | Window |
|---|---|---|
| `Lxx` / `Lxx-yy` | the raw file | lines xx-10 … yy+10 first (`SLACK = 10`), then the whole file |
| `p.N`, raw has `pages.json` | the raw file | page N's line range ±`SLACK`, then the whole file |
| `p.N`, no page map | the raw file | whole file, note "no page map" |
| `location unknown` | the raw file | whole file |
| raw path ends in `.pdf` | none | `unverifiable`, note "cite raw/converted/…" |
| raw missing / unreadable / other type | none | `unverifiable` |

Secondary target: `<slug>.dropped.txt` next to a converted file (page margins), searched last.

**Cross-language rule**: if the quote's dominant script (CJK vs Latin) differs from the raw file's, the
verdict is `unverifiable`, note "cross-language quote; cite verbatim or paraphrase". A
Chinese-primary vault citing English sources would otherwise fill the report with false
`not_found`. The report counts these separately (`unverifiable_cross_language`) so they stay visible.

## 6. Matching and verdicts

Both sides pass through `normalize_for_match` (NFKC, unescape Markdown, quote/dash folding,
de-hyphenation, whitespace collapse, lowercase). Matching is case-insensitive because truncated
quotes routinely change the capital letter, with one guard: a quote of 5 words or fewer (CJK:
under 12 characters) that matches only when case is ignored is `fuzzy` (score 95, note "case
differs"), not `exact`, since short strings collide easily (`"act"` vs `"ACT"`). The note records
whether the match was literal or only after normalization.

Order inside the window, then the whole file, then `dropped.txt`:

1. normalized substring → `exact`;
2. similarity ≥ threshold (default 85, `--threshold`) → `fuzzy` with score. Never a blind sliding
   scan of the whole file (1 MB ≈ 10^5 windows per quote): rank candidate windows
   (`len(quote) ± 15%`) by character-trigram overlap using an inverted trigram index built once per
   raw file, then run `difflib.SequenceMatcher` on the top 5 only (`rapidfuzz` if importable, same
   tests for both). Windows are in characters, so CJK works.

| Found | Verdict |
|---|---|
| in the cited window (tiers 1–2) | `exact` |
| in the cited window (tier 3) | `fuzzy` |
| elsewhere in the file, or only in `dropped.txt` | `wrong_location` (matched lines reported) — except `dropped.txt`, which is `exact` with note "page margin" |
| nowhere | `not_found` |
| cannot be checked (§5) | `unverifiable` |

**Severity**: only `not_found` can be `severity: error`. It is downgraded to `warning` when the
raw's `convert.json` says `lossy: true`, or its `pages.json` says `confidence: low` and the
citation is a `p.N`; each downgrade prints a CLI notice, and `--strict` keeps such results as
errors, so a fabricated quote cannot hide behind a lossy conversion. Everything else is a warning
(`unverifiable` a suggestion in lint). Only an `error` fails the run, appears in lint as ERROR, or
is written by `--flag`.

Line numbers in reports are translated to "PDF p.N" through `pages.json` when present.

## 7. Cache (incremental runs)

Because lint has no `--since`, speed comes from a cache, borrowing the evidence-version idea from
LangChain OpenWiki (claim carries the evidence version; staleness is a deterministic comparison).

`.cache/verify_quotes.json` at the vault root, atomic write. Key:
`sha256(normalized quote | raw path | loc)`. Value: `{verdict, severity, score, matched_lines,
raw_sha256, pages_sha256, convert_sha256, dropped_sha256, threshold, algo, strict}`. An entry is
reused only if all of these are unchanged. `algo` is the sha256 of the bytes of `verify_quotes.py`
and `textnorm.py`, so no manual version can be forgotten. Entries for quotes no longer present are
dropped on full runs. Writes go to a temp file then `os.replace`; last writer wins (it is only a
cache, so no lock). `--no-cache` disables it.
`scaffold.py` adds `.cache/` to the vault `.gitignore` if it creates one (to be checked in the plan).

## 8. Report

`outputs/verify-quotes-YYYY-MM-DD.json` (override with `--report`), nothing else is written:

```json
{"schema": "llm-wiki.verify-quotes/1", "generated_at": "…", "pages_checked": 41,
 "counts": {"exact": 0, "fuzzy": 0, "wrong_location": 0, "not_found_error": 0,
            "not_found_warning": 0, "unverifiable": 0, "uncited": 0, "from_cache": 0},
 "results": [{"page": "wiki/concepts/x.md", "line": 12, "quote": "…", "raw": "raw/…",
              "loc": "L14-22", "pdf_page": "2-3", "verdict": "exact", "severity": "none",
              "score": 100, "matched_lines": [15, 16], "note": "normalized"}]}
```

## 9. CLI and exit codes

```
verify_quotes.py <wiki-root> [--page PATH]... [--threshold N] [--report PATH]
                 [--flag [--dry-run]] [--strict] [--no-cache]
```

`--page` is repeatable and restricts the run to those wiki files. Exit 0: no error-severity
`not_found`. Exit 1: at least one. Exit 2: usage or environment error (bad args, no `wiki/`).
Without `--flag`, wiki and raw are not modified (tested by hashing both trees).

### `--flag`

For each error-severity `not_found`, insert one comment on the line after the quote's line:
`<!-- FIX: quote not found [vq:<id>] in <raw> <loc> -->`, where `id` is the first 8 hex of
`sha256(normalized quote | raw path | loc)`. Idempotency is by `[vq:<id>]` present in the FIX
comments directly following the quote's line, so editing the wording of a line never duplicates a
comment. Per file, insertions are applied from the last line to the first so earlier line numbers
stay valid. Only `not_found` errors are ever flagged; `--dry-run` prints the intended edits.

`cascade_flag.py` cannot be reused as is (hard-coded tag, matches by cited source, not by line). The
plan includes a behaviour-preserving refactor: extract `insert_fix_comment(path, line_idx, tag,
dry_run) -> bool` (idempotent on the following line), make `flag_citations_in_file` call it, keep
the existing outputs byte-identical (regression test), and have `verify_quotes` call the same function.
`audit_review.py` then picks the comments up unchanged.

## 10. Lint integration

`lint(root, structural, verify_quotes=False)` and a new `--verify-quotes` option. When on, it
imports `verify_wiki` (no shell-out), which returns a `Report` and touches no globals; `lint`
loops over the results and calls its own `log_issue`, so the module-level `errors/warnings/
suggestions` and the exit code stay lint's business. Mapping: `not_found` error → `ERROR (quote-verification)`;
`fuzzy`, `wrong_location`, downgraded `not_found` → `WARNING`; `unverifiable` → `SUGGESTION`.
`--stale-only` is a different mode and is unaffected. It is **off by default** until the false-error
rate has been tuned on a real vault copy; then the default can be flipped in a separate change.
`fast_ingest.py` is not touched (checked with `git diff --stat`).

## 11. Tests

Written first, red then green, one behaviour per test. Fixtures under `tests/fixtures/vq/`, copied to `tmp_path`.

- Extraction: each quote style, blockquote run = one quote, min length (incl. the 3-word and the
  digit rule, the `"175 billion parameters"` example), code/frontmatter/comment exclusion, binding
  rule (same line, same paragraph, none), table rows as boundaries, multiple quotes per citation,
  ellipsis fragments, apostrophes ignored.
- Targets: `Lxx`, `Lxx-yy`, `p.N` with and without `pages.json`, `location unknown`, `.pdf`, missing file.
- Matching: exact; whitespace/curly-quote/dash/hyphen-wrap/`\*` escape → exact; case-only
  difference → exact; one word changed → fuzzy ≥ 85; unrelated sentence → not fuzzy; CJK window;
  wrong_location with reported lines; not_found; margin text via `dropped.txt`; cross-language →
  unverifiable.
- Severity: `lossy:true` and `confidence:low` downgrade; `--strict` restores error; plain
  `not_found` stays error. Short quote differing only in case → `fuzzy`.
- Cache: hit, invalidation on raw / pages / convert / dropped change, threshold change, algo change.
- CLI: report schema; exit 0/1/2; trees byte-identical without `--flag`; `--page`, `--threshold`,
  `--report`.
- `--flag`: exactly one comment, idempotent after the quote line is reworded, several errors in one
  file all land on the right lines, only `not_found` errors, `--dry-run` writes nothing;
  `cascade_flag.py` output unchanged after the refactor.
- Lint: `--verify-quotes` maps verdicts to levels; default run unchanged; the 31 existing tests pass.
- Performance guard (`slow`): 1 MB raw, 50 quotes, every one a miss, budget 10 s (this exercises the
  trigram prefilter).
- Real-vault pass: run once on a copy of a real vault, read the report, tune only the documented
  constants (`SLACK`, minimum lengths, threshold).

## 12. What changed from the external plan

| External plan | Here | Why |
|---|---|---|
| `[[slug#page=12]]`, `[[slug]], p. 12`, footnotes | `(raw/…, Lxx)`, `p.N`, `location unknown` | what the repo and lint already use; check 14 forbids source-page wikilinks |
| pdftotext + `\x0c` page cache + `.cache/raw_pages` | Spec A converted markdown + `pages.json` | one format, OCR and tables handled upstream |
| unreadable PDF → unverifiable | converter fallback chain; direct `.pdf` cite → unverifiable | scanned PDFs are no longer a blind spot when MinerU is present |
| respect `--stale-only` / `--since` | own `--page` + cache | lint has no `--since`; `--stale-only` is a different mode |
| reuse `cascade_flag` as is | small refactor to share `insert_fix_comment` | its tag and matching are hard-coded |
| case-sensitive scoring | case ignored | truncated quotes change capitalisation |
| translated quote = not_found | cross-language → unverifiable | bilingual vaults |
| fixed schema | adds `severity`, `pdf_page`, `from_cache` | lossy conversions, human tracing, speed |

## 13. Known limits (to be stated in SKILL.md)

A quote that wraps across source lines in the wiki page is not extracted; quotes crossing a page
break are not matched across the break unless the converter placed the text contiguously; reconversion changes line numbers (existing check 5 and `cascade_flag` flag the
affected citations); a quote of a figure caption or table cell depends on how the converter
rendered it; a quote cited to the wrong file reads as `not_found`, not as "wrong source".

## 14. Open items to settle in the plan

1. Whether `scaffold.py` creates a `.gitignore` (decides how `.cache/` is excluded).
2. Exact `ALGO_VERSION` bump policy (suggest: manual constant, bumped with any change to
   extraction, normalization or matching).
3. The real-vault tuning pass needs a vault copy; none is available locally, so the plan
   schedules it as the final task after a copy is chosen.
4. Whether `rapidfuzz` is worth an optional dependency; default is difflib only.
5. In the plan, the `dropped.txt` search and the `pdf_page` translation are the last tasks, so they
   can be cut without touching the rest.

## 15. Review log

Independent review by agy (design and code inlined, no tools), verdict REVISE. Accepted: reverse-order
and id-based `--flag`, 3-word/digit minimum, case guard for short quotes, trigram prefilter,
complete cache fingerprint with source-hash `algo`, `--strict`, table and blockquote rules, own
citation regex, explicit lint sink. Partly accepted: `SLACK` raised to 10 (agy suggested 15; to be
tuned on a real vault), cross-language kept but counted separately. Not accepted: cache file lock
(cache only; atomic replace), hard-wrapped quotes (known limit), dropping `dropped.txt` (kept, built
last), single-quote handling (single quotes are never delimiters).
