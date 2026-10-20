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
  `CLAIM_ENTRY_RE` accepts `Lxx[-yy]` and `p.N`. Check 14 forbids `[[sources/…]]` citations. None of
  these patterns is reused: `CITATION_RE` is a three-way top-level alternation, `CLAIM_ENTRY_RE` rejects
  `location unknown`, and `cascade_flag.CITATION_LINE_RE = \((raw/[^,)]+),` needs a paren in front and a
  comma straight after the path.
- **What a real vault contains** (one personal vault, all wiki pages): besides `L9`, citations use
  `L11-L13`, `L110-113`, `L39/L63`, `L10-L36; L81-L93` and longer lists; comma lists `L61-62, L91` and
  several sources in one parenthesis `(raw/a.md, L9; raw/b.md, L13)`; paths with CJK, spaces and
  full-width punctuation (`？`, `！`, `：`). A citation with a literal `…` inside the path also occurs.
  The Spec-B regex of the first draft, `L\d+(?:-\d+)?`, would have missed `L11-L13`, lists and
  multi-source parentheses.
- Page folders also contain Syncthing conflict copies (`*.sync-conflict-*.md`); they duplicate real pages.
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

Three modules, all in `llm-wiki/scripts/`:

- `citations.py` (new, shared with `cascade_flag.py`): `find_citations(line) -> [Citation]`,
  `malformed(line) -> [str]`. Grammar: `raw_path ws? [,，] ws? locs`; `raw_path` = `raw/` + anything
  (no newline, no parenthesis) ending in a file extension of 1-8 alphanumerics; `locs` = `loc (ws? [,，;；/] ws? loc)*`;
  `loc` = `[Ll]\d+ (ws? [-–—] ws? [Ll]?\d+)?` | `[Pp]\.\s*\d+ (ws? [-–—] ws? \d+)?` | `location unknown`.
  Cites are found by scanning for `raw_path , locs` anywhere on the line (so several in one
  parenthesis are separate citations). A `(raw/` fragment with no cite starting at it is `malformed`.
- `textnorm.py` from Spec A (`normalize`, `fold_case`, `loose`, `normalize_with_map`).
- `verify_quotes.py` (split only if it passes ~500 lines), importable and CLI.

```
extract_claims(page_text, page_path) -> (claims, uncited)      # §4
resolve_target(claim, vault)         -> Target                  # §5
verify_claim(claim, target, thr)     -> Result                  # §6
verify_wiki(root, pages, thr, cache) -> Report                  # §8
main(argv)                                                      # §9
```

`Claim`: `page, line_no, quote, raw_path, locs` where `locs` is a list of `("L", a, b)`, `("p", a, b)`
or `("unknown",)`. `Result`: `claim, verdict, severity, score, matched_lines, note, suggested_raw`.

## 4. Extracting quotes and binding them to citations

Quote = text inside straight `"…"`, curly `“…”`, `「…」`, `『…』`, or a markdown blockquote line (`>`).
Minimum length: Latin 3 words, CJK 12 characters; a quote that contains a digit qualifies from
2 words / 6 characters (so the repo's own `"175 billion parameters"` example is checked).
Constants, not flags. Single quotes are never quote delimiters. Links and inline code are removed
before extraction. A quote must sit on one line (hard-wrapped quotes are a known limit, §13).
Ignored: YAML frontmatter, fenced code, inline code, HTML comments (including FIX comments), the
`claims_requiring_verification` list (check 13 owns it), and wiki files named `*.sync-conflict-*`.

Binding rule (explicit, documented in the docstring): a quote binds to the **first raw citation to its
right on the same line**; failing that, the first citation in the same paragraph (contiguous
non-blank lines; a blockquote group counts as one unit and may use the line right after it). A table
row is its own binding boundary: a quote never binds to a citation in another row. A run of
consecutive `>` lines is one quote located at its first line. Several quotes before one citation
all bind to it, and a `not_found` among them carries the note "shares citation with N other quotes".
No citation → counted as `uncited`, never a failure. A citation with several locations
(`L61-62, L91`) gives a window per location; the quote may match in any of them.

Ellipsis: a quote containing `[...]`, `[…]`, `...`, `…`, `……` or `⋯` is split into fragments; fragments under
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
| raw missing / unreadable / other type | none | `unverifiable`, listed in `missing_raw_files` |
| path resolves under a private zone (vault `CLAUDE.md` `## Private zones`) | none, **not read** | `unverifiable` |

Secondary target: `<slug>.dropped.txt` next to a converted file (page margins), searched last.

**Cross-language rule** (applied only after every match attempt has failed, never before): the
verdict `not_found` becomes `unverifiable`, note "cross-language quote; cite verbatim or paraphrase",
when the scripts cannot overlap. With `cjk_share` = CJK letters / all letters: the quote has at least 6
CJK letters and the raw file's share is below 0.05, **or** the quote has no CJK letter, at least 3
words, and the raw file's share is above 0.6. A quote with fewer than 6 CJK letters, or any quote against a raw file whose share lies between the two limits, is just
`not_found`. The report counts these separately (`unverifiable_cross_language`) so they stay visible.

## 6. Matching and verdicts

Matching runs as a ladder; the first rung that succeeds decides, and later rungs add notes only.
Offsets are mapped back to raw line numbers with `normalize_with_map` (`matched_lines`).

1. **Literal**: `normalize` (case-preserving) quote in `normalize` window → `exact`. When the raw
   contains `-\n`, both hyphen variants are tried.
2. **Case-folded**: `fold_case(normalize(...))`. A quote of **more than 5 words** (CJK: 12 or more
   characters) → `exact`, note "case differs"; a **short** quote that matches only here → `fuzzy`,
   score 95, note "case differs" (short strings collide easily: `"act"` / `"ACT"`).
3. **Loose**: `loose(...)` on both sides (drops whitespace, Markdown markers and punctuation; this is
   what makes ASR transcripts with spaces between CJK words match) → `exact`, note "loose". A
   **short** quote that matches only here → `fuzzy`, score 95, note "loose match on short quote",
   unless the only difference is whitespace between characters (the quote also matches a
   case-folded view with all whitespace removed and punctuation kept), which stays `exact`.
4. **Script fold** (only if `zhconv` is importable): Traditional → Simplified, then rungs 1-3 →
   `exact`, note "script-folded".
5. **Fuzzy**: similarity ≥ threshold (default 85, `--threshold`) → `fuzzy` with score. No blind
   sliding scan of the whole file (1 MB ≈ 10^5 windows per quote): candidate windows
   (`len(quote) ± 15%`) are ranked by character-trigram overlap from an inverted trigram index built once
   per raw file, then `difflib.SequenceMatcher` runs on the top 5 only. difflib only in v1 (no
   `rapidfuzz`). Windows are in characters, so CJK works.

Ellipsis fragments each climb the ladder in order, with a cursor so the next fragment must start
after the previous one; the verdict is the weakest rung used.
Fragments under the minimum length ("short fragments") are kept, never dropped: each must be found
in order after the previous fragment, at the literal, case-folded or loose rung, and is never
fuzzy-matched. A missing short fragment makes the quote `not_found` (or `near_miss` when the long
fragments themselves score in that band), never `exact` or `fuzzy`. A quote made only of short
fragments is still too short to check. In the fuzzy stage each fragment is searched after the
previous fragment's match end.

Search scope per rung: the cited window first, then the whole file, then `dropped.txt`.

| Found | Verdict |
|---|---|
| in the cited window | `exact` or `fuzzy` per the ladder |
| elsewhere in the file | `wrong_location` (matched lines reported) |
| only in `dropped.txt` | `exact`, note "page margin" |
| nowhere, best similarity ≥ `NEAR_MISS` (default 60) and < threshold | `near_miss`: wording differs, probably a paraphrase in quotation marks |
| nowhere, best similarity < `NEAR_MISS` | `not_found` |
| cannot be checked (§5) | `unverifiable` |

On `not_found` only, the quote is also looked up (literal + loose, no fuzzy) in the other
`raw/**/*.md|txt` files, capped at 200 files / 20 MB. A hit sets `suggested_raw` and the note
"found verbatim in <file>"; the verdict stays `not_found` and an error, because a quote cited to the
wrong file is still a wrong citation, and `--flag` puts the suggestion into the FIX comment.

**Severity**: `not_found` is `error`. `near_miss`, `fuzzy`, `wrong_location` and `unverifiable` are
warnings (`unverifiable` a suggestion in lint). A `not_found` is downgraded to `warning` when the raw's
`convert.json` says `lossy: true`, or its `pages.json` says `confidence: low` and the citation is a
`p.N`; each downgrade prints a CLI notice. `--strict` promotes both the downgraded `not_found` and
`near_miss` to errors, so a fabricated quote cannot hide behind a lossy conversion or a loose
rewording. Only an `error` fails the run, appears in lint as ERROR, or is written by `--flag`.
Consequence: with `--strict` a `near_miss` or downgraded `not_found` makes the run exit 1, but
`--flag` still annotates only error-severity `not_found` results, never a `near_miss`.

Line numbers in reports are translated to "PDF p.N" through `pages.json` when present.

## 7. Cache (incremental runs)

Because lint has no `--since`, speed comes from a cache, borrowing the evidence-version idea from
LangChain OpenWiki (claim carries the evidence version; staleness is a deterministic comparison).

`.cache/verify_quotes.json` at the vault root, atomic write. Key:
`sha256(normalized quote | raw path | loc)`. Value: `{verdict, severity, score, matched_lines,
raw_sha256, pages_sha256, convert_sha256, dropped_sha256, threshold, algo, strict}`. An entry is
reused only if all of these are unchanged. `algo` is the sha256 of the bytes of `verify_quotes.py`,
`textnorm.py` and `citations.py`, so no manual version can be forgotten; a comment-only edit just
rebuilds the cache once, which is cheap. Entries for quotes no longer present are
dropped on full runs. Writes go to a temp file then `os.replace`; last writer wins (it is only a
cache, so no lock). `--no-cache` disables it.
`scaffold.py` adds `.cache/` to the vault `.gitignore` if it creates one (to be checked in the plan).

## 8. Report

`outputs/verify-quotes-YYYY-MM-DD.json` (override with `--report`), nothing else is written:

```json
{"schema": "llm-wiki.verify-quotes/1", "generated_at": "…", "pages_checked": 41,
 "counts": {"exact": 0, "fuzzy": 0, "wrong_location": 0, "near_miss": 0, "not_found_error": 0,
            "not_found_warning": 0, "unverifiable_infrastructure": 0,
            "unverifiable_cross_language": 0, "uncited": 0, "malformed_citations": 0,
            "from_cache": 0},
 "missing_raw_files": [{"raw": "raw/…", "citations": 53}],
 "results": [{"page": "wiki/concepts/x.md", "line": 12, "quote": "…", "raw": "raw/…",
              "loc": "L14-22", "pdf_page": "2-3", "verdict": "exact", "severity": "none",
              "score": 100, "matched_lines": [15, 16], "note": "normalized", "suggested_raw": null}]}
```

The CLI summary prints every count, `missing_raw_files` (a real vault had many citations
pointing at deleted files) and the `malformed_citations` with page and line.

## 9. CLI and exit codes

```
verify_quotes.py <wiki-root> [--page PATH]... [--threshold N] [--report PATH]
                 [--flag [--dry-run]] [--strict] [--no-cache]
```

`--page` is repeatable and restricts the run to those wiki files. Exit 0: no error-severity
result. Exit 1: at least one; normally only `not_found` is an error, and `--strict` also fails the
run for `near_miss` (and for a downgraded `not_found`). Exit 2: usage or environment error (bad args, no `wiki/`).
Without `--flag`, wiki and raw are not modified (tested by hashing both trees).

### `--flag`

For each error-severity `not_found`, insert one comment on the line after the quote's line:
`<!-- FIX: quote not found [vq:<id>] in <raw> <loc>[; found in <suggested_raw>] -->`, where `id` is the first 8 hex of
`sha256(normalized quote | raw path | loc)`. Idempotency is by `[vq:<id>]` present in the FIX
comments directly following the quote's line, so editing the wording of a line never duplicates a
comment. Per file, insertions are applied from the last line to the first so earlier line numbers
stay valid. Only error-severity `not_found` results are ever flagged (never a `near_miss`, even
under `--strict`, which fails the run for it); `--dry-run` prints the intended edits.

`cascade_flag.py` cannot be reused as is (hard-coded tag, matches by cited source, not by line, and its
citation regex needs `(raw/…,` at the start of a parenthesis). The plan includes a refactor: extract
`insert_fix_comment(path, line_idx, tag, dry_run) -> bool` (idempotent on the following lines),
make `flag_citations_in_file` call it and use `citations.find_citations` instead of its own regex.
Output stays byte-identical for every form the old regex handled (regression test); the new regex
additionally finds the second source in `(raw/a.md, L9; raw/b.md, L13)`, which the old one missed.
`verify_quotes` calls the same functions.
`audit_review.py` then picks the comments up unchanged.

## 10. Lint integration

`lint(root, structural, verify_quotes=False)` and a new `--verify-quotes` option. When on, it
imports `verify_wiki` (no shell-out), which returns a `Report` and touches no globals; `lint`
loops over the results and calls its own `log_issue`, so the module-level `errors/warnings/
suggestions` and the exit code stay lint's business. Mapping: `not_found` error → `ERROR (quote-verification)`;
`near_miss`, `fuzzy`, `wrong_location`, downgraded `not_found` → `WARNING`; `unverifiable` → `SUGGESTION`.
A WARNING is added when `unverifiable_infrastructure` (missing raw, `.pdf` cites, unreadable) is more
than 10% of the cited quotes; cross-language quotes are not counted there, so a bilingual vault does
not trip it. `--stale-only` is a different mode and is unaffected. It is **off by default** until the false-error
rate has been tuned on a real vault copy; then the default can be flipped in a separate change.
`fast_ingest.py` is not touched (checked with `git diff --stat`).

## 11. Tests

Written first, red then green, one behaviour per test. Fixtures under `tests/fixtures/vq/`, copied to `tmp_path`.

- Extraction: each quote style, blockquote run = one quote, min length (incl. the 3-word and the
  digit rule, the `"175 billion parameters"` example), code/frontmatter/comment exclusion, binding
  rule (same line, same paragraph, none), table rows as boundaries, multiple quotes per citation,
  ellipsis fragments, apostrophes ignored.
- Citations (the shared grammar): `L9`, `L11-L13`, `L110-113`, `L39/L63`, `L61-62, L91`, `L10-L36; L81-L93`,
  `(raw/a.md, L9; raw/b.md, L13)`, CJK / space / full-width characters in the path, a comma or
  parentheses inside a file name, `P.3`, en-dash range, full-width comma, trailing spaces,
  `location unknown`, a literal `…` in a path → `malformed`. Sage's cases T01-T11 are the starting
  table; the old regex must fail the ones listed there and the new one pass them.
- Targets: `Lxx`, `Lxx-yy`, several locations, `p.N` with and without `pages.json`, `location unknown`, `.pdf`, missing file,
  private-zone path (not read).
- Matching: exact; whitespace/curly-quote/dash/`\*` escape → exact; `well-\nknown` and
  `inter-\nnational` both match; case-only difference → exact for long quotes, `fuzzy` for short ones;
  spaced-out CJK transcript → loose exact; Chinese ellipsis `……` fragments in order and in reverse
  order (the latter `not_found`); one word changed → fuzzy ≥ 85; reworded 60-85 → `near_miss`;
  unrelated sentence → `not_found`; CJK window; `wrong_location` with reported lines; `matched_lines`
  correct after normalization; margin text via `dropped.txt`; `suggested_raw`; cross-language only
  after a failed match, Sage's mixed-script samples T24-T26 not misclassified.
- Severity: `lossy:true` and `confidence:low` downgrade; `--strict` restores error; plain
  `not_found` stays error. Short quote differing only in case → `fuzzy`.
- Cache: hit, invalidation on raw / pages / convert / dropped change, threshold change, algo change.
- CLI: report schema; exit 0/1/2; trees byte-identical without `--flag`; `--page`, `--threshold`,
  `--report`.
- `--flag`: exactly one comment, idempotent after the quote line is reworded, several errors in one
  file all land on the right lines, only `not_found` errors, `--dry-run` writes nothing;
  `cascade_flag.py` output unchanged after the refactor.
- Lint: `--verify-quotes` maps verdicts to levels; default run unchanged; unverifiable-share WARNING
  excludes cross-language; the 31 existing tests pass.
- Performance guard (`slow`): a real converted paper (the full-length output of Spec A, not random
  words) plus 1 MB of concatenated vault raw files, 50 quotes, every one a miss, budget 10 s.
- Real-vault pass (`slow`, skipped when the path is absent): run on a **copy** of
  the owner's vault, compare the counts with the baseline in §16 (within a tolerance, since
  the vault changes), and tune only the documented constants (`SLACK`, minimum lengths, threshold,
  `NEAR_MISS`).

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
| fixed schema | adds `severity`, `pdf_page`, `from_cache`, `near_miss`, `suggested_raw`, `missing_raw_files` | lossy conversions, human tracing, speed, real-vault findings |
| `L\d+(-\d+)?` only | full citation grammar in `citations.py` | real citations use `L11-L13`, lists, multi-source parentheses |

## 13. Known limits (to be stated in SKILL.md)

A quote that wraps across source lines in the wiki page is not extracted; quotes crossing a page
break are not matched across the break unless the converter placed the text contiguously; reconversion changes line numbers (existing check 5 and `cascade_flag` flag the
affected citations); a quote of a figure caption or table cell depends on how the converter
rendered it; a quote cited to the wrong file is `not_found` (with `suggested_raw` when it exists elsewhere in
`raw/`); a quote that is a close paraphrase of the source reads as `near_miss`, and the 60 / 85 band
is a judgement call; one-line quotes only, so wiki agents should not hard-wrap a quotation.

## 14. Open items to settle in the plan

1. Whether `scaffold.py` creates a `.gitignore` (decides how `.cache/` is excluded).
2. ~~ALGO_VERSION policy~~ resolved: `algo` is a source hash (§7).
3. ~~Vault copy~~ resolved: the owner's vault (read-only for us; tests use a copy in scratch).
4. ~~rapidfuzz~~ resolved: difflib only.
5. In the plan, the `dropped.txt` search, the `pdf_page` translation and the optional `zhconv` rung are the last
   tasks, so they can be cut without touching the rest.
6. `NEAR_MISS = 60` and the 85 fuzzy threshold are first guesses; §16 shows the score spread
   (most misses sit between 0.5 and 0.85); tune on the vault copy.

## 15. Review log

Independent review by agy (design and code inlined, no tools), verdict REVISE. Accepted: reverse-order
and id-based `--flag`, 3-word/digit minimum, case guard for short quotes, trigram prefilter,
complete cache fingerprint with source-hash `algo`, `--strict`, table and blockquote rules, own
citation regex, explicit lint sink. Partly accepted: `SLACK` raised to 10 (agy suggested 15; to be
tuned on a real vault), cross-language kept but counted separately. Not accepted: cache file lock
(cache only; atomic replace), hard-wrapped quotes (known limit), dropping `dropped.txt` (kept, built
last), single-quote handling (single quotes are never delimiters).

Round 3 (cloud reviewer Sage, 20 findings and a 54-case test table, no repo code read; agy
adjudication; real-vault prototype). Accepted: tolerant citation grammar and `malformed_citations`
(R01, extended well beyond Sage's variants after reading the vault), case-preserving vs folded
normalization (R02), cross-language redefined as a post-failure rule (R03, replaces both the old
"dominant script" and Sage's bands), hyphen variants (R07), infrastructure-unverifiable warning
(R05, with agy: cross-language excluded, threshold 10%), `suggested_raw` (R09, as agy: stays an error),
private-zone targets (R11), difflib only (R15), one-line-quote rule for SKILL.md (R18), real-paper
performance guard (R20), `matched_lines` via `normalize_with_map` and a two-stage case rule (agy
NEW-2/3), shared `citations.py` also for `cascade_flag.py` (agy NEW-1, without forbidding commas in
file names). Not accepted: manual `ALGO_VERSION` (R08), report redaction (R12). Deferred: reconversion
dry-run (R14).

## 16. Real-vault baseline (prototype, summary)

A throw-away prototype of the ladder was run read-only on a copy of one real, Chinese-primary vault
(raw files are clippings and spoken-transcript notes, no PDFs). The vault's details are not published
here. In magnitude: about two thirds of the quote candidates matched exactly or after normalization, a
few percent were `not_found`, and the misses inspected by hand were paraphrases in quotation marks rather
than invented text; a noticeable share of citations pointed at raw files that no longer exist; folding
Traditional to Simplified rescued only a handful of misses. Tune `SLACK`, the minimum lengths,
`THRESHOLD` and `NEAR_MISS` on your own vault copy.
