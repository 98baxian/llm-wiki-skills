# Document conversion (PDF → raw) — design

Status: DRAFT for user review. Spec A of two. Spec B (`verify_quotes`) builds on the raw format defined here.
Date: 2026-10-19

## 1. Goal

When a PDF is ingested, convert it once into a line-citable markdown file under `raw/`, keep the
original PDF, and record enough metadata to trace any cited line back to a PDF page. The
default converter is local MinerU. Users can plug in their own tool. Without either, fall back to
`pdftotext` and mark the result lossy.

Why: the wiki cites `(raw/path.md, L14-22)`. A PDF has no stable lines, so today the agent reads
the PDF directly and the citation convention, lint check 13 and the future `verify_quotes` all
have nothing to check against.

### Non-goals

- docx / pptx / html conversion.
- Remote parsing (`mineru-kit --remote`, mineru.net). Documents would be uploaded; never implicit.
- MinerU `content_list.json` (absent from the 4.0.12 zip output) and `structured_content.json`
  (present, but `middle_json.json` is the documented source and is enough).
- Quote verification itself (Spec B).

## 2. Verified facts this design rests on

Measured on this machine, MinerU 4.0.12, `mineru-kit parse`, tier `basic` (CPU). Two corpora:
(a) first 3 pages of a 22-page single-column preprint; (b) pages 1-6 of BERT (arXiv 1810.04805v2,
ACL two-column format, one table, some formulas).

| Fact | Evidence |
|---|---|
| `-f zip` writes in **one pass**: `markdown.md`, `middle_json.json`, `structured_content.json`, `model_output.json`, `images/`. In `markdown.md` images are already files (`![](images/page_2_image_4.jpg)`): 8 KB for 3 pages, against 297 KB for the default markdown, which embeds base64 | measured (a) |
| `middle_json.json`: schema `docvortex.middle` 2.0, `pages[].page_idx` (**absolute** PDF page index even when `-p` selects a range), `pages[].blocks[]` with `type`, `bbox`, `content`; `metadata.producer.version` | measured (a), (b) |
| Block types seen: `header`, `footer`, `doc_title`, `text`, `paragraph_title`, `aside_text`, `image`, `page_footnote`, `table` | measured |
| **Not in the markdown**: `header`, `footer`, page numbers and `page_footnote` (11 of 12 footnote blocks missing). `*`, `~` are backslash-escaped. A `table` block is HTML in `middle_json` but a pipe table in the markdown, so its text cannot be matched directly | measured |
| Alignment (forward search from the previous match, normalized, body blocks only): (a) 17/18; (b) pp.1-2 24/25, pp.3-6 text/title/image blocks 35/40 before folding curly apostrophes; monotone in every chunk | measured |
| Speed ≈ 0.6 page/s plus ≈ 5 s start-up; 2 pages ≈ 8 s, peak RSS 1.0 GB; 4 pages (pp.3-6) peak ≥ 2.7 GB | measured |
| **A 4-page run was SIGKILLed** (rc -9 / 137) in this session because its cgroup has `memory.max` = 10 GB shared with other processes; the same pages succeed as two 2-page runs (peak 1.8 / 2.0 GB). The limit is environmental, but any machine can be in the same position | measured |
| torch and mineru's llama-cpp native libraries crash if loaded in one process | measured earlier, see mcp-management.md |

**Not tested**: scanned PDFs, formulas as such (the kit ran formula recognition on pp.3-6), `standard`
and `advanced` tiers, CJK PDFs. Claims about those are expectations, not facts.

Repo facts (read from `83e4331`): citations are `(raw/…, Lxx)`; lint check 14 forbids
`[[sources/…]]` as inline citation; check 13 matches `verbatim in raw_text` exactly; check 5 compares
`raw_sha256` of `raw_file`; `fast_ingest.py` reads `raw_file` as text and dedupes by slug;
`batch_ingest.py` dedupes by `raw_sha256` and its `--pdf` path calls a `pdf-ingest` skill that is
not in the repo; `SKILL.md` says `raw/` is immutable, LLM reads only; baseline is 31 passing tests.

## 3. Configuration (vault `CLAUDE.md`)

New section, written by `scaffold.py`, parsed by line scanning like `load_domains`:

```
## Document conversion

converter: auto        # auto | mineru | pdftotext | command | none
tier: basic            # MinerU tier: flash | basic | standard | advanced
timeout: 900           # seconds per file
command: ""            # converter: command  → "mytool {input} {outdir}"
```

The section is also the user-facing explanation (what each value does, privacy note, how to
disable). Defaults when the section is absent: `converter: auto`, `tier: basic`, `timeout: 900`.

### Resolution (`converter: auto`)

1. `command`, if non-empty.
2. `mineru-kit` — `MINERU_KIT_BIN` env, then `~/.local/bin/mineru-kit`, then `PATH`.
3. `pdftotext` — only for PDFs with a text layer. Result is `lossy: true`; a warning is printed.
4. Otherwise exit non-zero with an install hint.

Explicit values (`mineru`, `pdftotext`, `command`) use only that converter and fail if it is
unavailable. `none` disables conversion; the PDF is then ingested as before (agent reads it
directly, no line citations).

### Custom command contract

Exit 0 and write `<outdir>/<slug>.md`. Optional: `<slug>.pages.json` (schema below) and an
`images/` directory. Without `pages.json`, citations stay line-only. Nothing else is read.

### Tiers

`flash`/`basic` are CPU-only. `standard`/`advanced` use the llama-cpp VLM and share the GPU
with the local LLM; they run only when `tier:` names them explicitly.

## 4. Artifacts

Written by `convert_source.py`, never by the LLM, one directory per source:

```
raw/converted/<slug>/
  <slug>.md              images are files, links are relative: ![](images/fig-001.png)
  <slug>.pages.json      page → line range (below)
  <slug>.convert.json    provenance
  <slug>.dropped.txt     text of header/footer/page-number/page_footnote blocks the markdown omits (MinerU only)
  images/
```

`<slug>` comes from the **PDF filename**, sanitized (Unicode letters and digits kept, every other
run of characters becomes `-`, suffixed with the first 8 hex of the PDF sha256 on collision). It is
passed explicitly to `fast_ingest.py`, which keeps deriving the wiki-page slug from the title as it
does today; the two slugs may differ, because the stub records the explicit `raw_file` path.

`dropped.txt` is one block per line prefixed `p<N>\t<type>\t`, de-duplicated per page. It is
never cited as a line source; it exists so Spec B can still find DOIs, preprint ids, running
titles and footnote URLs that sit outside the body text.

Citation format is unchanged: `(raw/converted/<slug>/<slug>.md, L14-22)`.

`convert.json`:

```json
{"schema": "llm-wiki.convert/1", "converter": "mineru-kit", "version": "4.0.12",
 "tier": "basic", "lossy": false,
 "source_pdf": "raw/pdfs/x.pdf", "source_pdf_sha256": "…",
 "md_sha256": "…", "converted_at": "2026-10-19T14:50:00Z",
 "pages_total": 22, "page_map": "aligned|exact|none", "dropped": ["header", "footer"]}
```

`pages.json`:

```json
{"schema": "llm-wiki.pages/1", "match_rate": 0.94, "confidence": "high|low",
 "pages": [{"page": 1, "line_start": 1, "line_end": 20, "how": "aligned|interpolated|exact"}]}
```

### Page map construction

- **MinerU**: one `-f zip` run per page window (below); read `middle_json.json`. Verify
  `schema == "docvortex.middle"` and `schema_version == "2.0"`; if either differs, skip the page map,
  write `confidence: low`, log it, and still succeed (R10). Body blocks = everything except
  `header`, `footer`, `page_number`, `page_footnote` (those go to `dropped.txt`), `table` and text
  under 12 characters. Normalize both sides with `normalize` + `fold_case` (§7). For each block, search
  forward from the previous match for its first 25 normalized characters. Each page starts at its
  first matched line and runs up to the line before the next page starts, so the ranges partition the
  markdown (the first page starts at line 1, the last ends at the final line); a `table` block takes
  the lines between its neighbours. A page with no matched block is `interpolated`. `match_rate` = matched / body blocks, `confidence: low`
  below 0.8 (to be re-tuned with a second two-column fixture, §14).
- **pdftotext**: write the markdown page by page from the `\x0c`-split text, so ranges are exact.
- **Custom command**: only if it supplies `pages.json`.
- The map is for human tracing ("L14-22 ≈ PDF p.2") and lint output. It never changes a
  citation and never fails a conversion.

Rejected: injecting `<!-- page: N -->` into the markdown. It would split quotes that cross a
page break and pollute verbatim matching.

## 5. Ingest integration

- New `llm-wiki/scripts/convert_source.py`: importable `convert(pdf, wiki_root) -> Result` and a CLI
  `convert_source.py <wiki-root> <pdf> [--force]`. MinerU runs in a subprocess with a timeout, as
  `-f zip`, over **page windows of 8** (`-p 1-8`, `9-16`, …); the per-window markdown is concatenated
  and block `page_idx` values are already absolute. If a window is killed (rc -9 / 137, most likely
  out of memory), it is retried once as two half windows, down to a single page; a single page that
  still dies fails the file with exit 3 and "killed, probably out of memory".
- `batch_ingest.py`: for each `.pdf`, convert first (skip if `raw/converted/<slug>/` exists and the
  PDF sha matches `convert.json`), then ingest the markdown. `--pdf` stays as a deprecated no-op;
  `--no-convert` is added. The `stem in f.stem` output matching is removed (the result object
  returns the path).
- `fast_ingest.py`: given a `.pdf`, call `convert()` and continue with the converted markdown, so it
  never reads binary as text.
- Stub frontmatter gains `source_pdf`, `source_pdf_sha256`, and `source_lossy: true` when applicable.
  `raw_file` / `raw_sha256` keep pointing at the converted markdown, so every existing check and
  citation keeps working.
- `collect_known_hashes` indexes both `raw_sha256` and `source_pdf_sha256`, so an already
  ingested PDF is skipped on re-scan.
- Reconversion only on explicit `--force`. A converter version change never triggers it. The
  command prints: line numbers will change; existing citations will be flagged by check 5 and
  `cascade_flag.py`.

## 6. Lint changes

| Check | Change |
|---|---|
| 5 sha256 | Also compare `source_pdf_sha256` with the PDF on disk. Mismatch → WARNING "converted copy is stale; re-run convert_source.py --force". |
| 13 claims | Compare after `normalize` (case-preserving, §7) on both sides: fixes false ERRORs from `\*`, `\~` and line wraps without loosening the case-sensitive check. |
| new, info | `source_lossy: true` → "cited raw is a lossy conversion (pdftotext)". |
| new, info | `pages.json` `confidence: low` → "page translation unreliable for <slug>". |

Existing 31 tests must stay green; check 13 must still fail on a genuinely absent string.

## 7. Shared helper

`llm-wiki/scripts/textnorm.py` (no third-party imports at module level):

- `normalize(s)`: Unicode NFKC, remove Markdown backslash escapes (`\*`, `\~`, `\_`, …), curly
  quotes and apostrophes → straight, en/em dash → hyphen, strip zero-width characters, collapse
  whitespace. **Case-preserving.** Joins `-\n` hyphenation and also offers the hyphen-kept variant
  (`well-\nknown` → `well-known`): `variants(s)` returns both when the text contains `-\n`.
- `fold_case(s)`: `casefold` applied on top of `normalize`. Used by the page-map aligner and by
  Spec B only; never by lint check 13.
- `loose(s)`: `fold_case`, then drop Markdown markers (`* _ ~ \``), whitespace and punctuation,
  keeping letters, digits and CJK characters. Needed because ASR transcripts put spaces between
  CJK words.
- `normalize_with_map(text) -> (normalized, offsets)`: `offsets[i]` is the index in `text` of
  character `i` of the normalized string, so a match can be mapped back to raw line numbers.
- Optional `to_simplified(s)`: Traditional → Simplified through `zhconv` when it is importable,
  otherwise the identity. Off the critical path (it rescued only a handful of misses in the real-vault
  prototype).

## 8. Safety

- **Private zones**: the converter refuses any input whose path is under a directory listed in
  `## Private zones` of the vault `CLAUDE.md` (bullet form `- \`zone/\``). Exit non-zero, no output.
- **No upload**: `--remote`, `--remote-url`, `--api-key` are never passed.
- **GPU**: for `flash`/`basic` the subprocess gets `CUDA_VISIBLE_DEVICES=""`. Cheap hardening; the
  standalone ONNX tool environment should not touch CUDA anyway (inferred, not measured).
- **Isolation**: subprocess only, `timeout` enforced, partial outputs removed on failure.
- **Reproducibility**: exact converter name and version, tier and the source PDF sha256 are
  recorded in `convert.json`.
- **Immutability contract**: `SKILL.md` and the directory layout change to "`raw/converted/` is
  script-owned, written once per source; the LLM never edits it". `raw/pdfs/` stays human-owned.

## 9. Errors

| Case | Behaviour |
|---|---|
| Converter missing | auto: next in chain; explicit: exit 2 with install hint |
| Timeout / crash / killed (rc -9, 137) after window halving | exit 3, outputs removed, batch continues with next file |
| pdftotext result under 20 non-space characters | exit 4: "scanned PDF — needs MinerU or your own converter" |
| Private-zone path | exit 5 |
| Output slug dir exists, PDF sha differs, no `--force` | exit 6, nothing overwritten |

## 10. Tests

- Fake converter (shell script) for the command contract and fallback order.
- A small PDF built with `pdflatex` for the pdftotext path and exact `pages.json`.
- Trimmed `middle_json` + markdown fixtures (excerpts only; no third-party PDF is committed) from both
  measured runs: single-column pp.1-3 and two-column BERT pp.1-6 with a table and footnotes. Cases:
  repeated text that breaks first-occurrence matching, a page-number block that must not match,
  a `page_footnote` block going to `dropped.txt`, a `table` block placed between neighbours,
  absolute `page_idx` across windows.
- Window retry: a fake converter that exits -9 on windows larger than 2 pages; the result must equal
  the full-window result and a 1-page failure must exit 3.
- Schema guard: `middle_json` with another `schema_version` → no page map, `confidence: low`, success.
- `textnorm`: `normalize` keeps case; `fold_case`, `loose`, hyphen variants, `normalize_with_map`
  offsets; check 13 still rejects `US` against `tell us now` and `ACT` against `act`.
- Slug: CJK filenames, collisions, characters that would break a citation (`) , ( ;`).
- Real MinerU test marked `slow`, skipped when `mineru-kit` is absent.
- Check 5, check 13 (escape tolerance and still-fails-when-absent), dedupe by `source_pdf_sha256`.
- Private-zone refusal. No network access asserted by checking no remote flags in the argv.
- Run with `uv run --with pytest python -m pytest` (pytest is not installed globally).

## 11. Interface promised to Spec B

- Raw file for a PDF source = `raw/converted/<slug>/<slug>.md` (line-citable, images externalised).
- `convert.json.lossy` and `pages.json.confidence` are available so Spec B can downgrade verdicts.
- `textnorm.py` (`normalize`, `fold_case`, `loose`, `normalize_with_map`) is the single normalizer; `citations.py` (Spec B) is the single citation grammar.
- `dropped.txt` as a secondary search target, so margin text (DOI, preprint id) is not a false
  `not_found`.
- Known limit Spec B must state: reconversion changes line numbers.

## 12. Files touched

New: `llm-wiki/scripts/convert_source.py`, `llm-wiki/scripts/textnorm.py`,
`llm-wiki/scripts/vault_config.py` (CLAUDE.md section, key/value and private-zone readers),
`tests/pdf_helpers.py`, `tests/test_convert_source.py`, `tests/test_textnorm.py`, `tests/test_vault_config.py`,
`tests/test_fast_ingest_pdf.py`, `tests/test_batch_ingest.py`, `tests/test_lint_claims.py`,
`tests/test_lint_conversion.py`, `tests/test_scaffold_conversion.py`, `tests/test_convert_real.py` (slow).
Lint check 5 is extended by a sibling `check_conversion_state`, not by editing `check_sha256`.
`pytest.ini` gains a `slow` marker (excluded by default).
Modified: `batch_ingest.py`, `fast_ingest.py`, `lint_wiki.py`, `scaffold.py` (CLAUDE.md section),
`SKILL.md`, `references/ops/op-ingest.md`, `references/tooling-tips.md`, `tests/README.md`.

## 13. Review log

Independent review by agy (design + code inlined, no tools), verdict REVISE. Accepted: double-hash
dedupe, check 13 escape tolerance, stale-PDF detection in check 5, `raw/converted/` contract
amendment, private-zone refusal, timeout and CUDA hardening, version pinning and reconversion
warning. Not accepted: "prefix alignment fails ~45%" (the count included header/footer blocks
that are absent from the markdown by design; re-measured at 17/18, with a forward-search fix),
page-comment injection, dropping `command`, removing the pdftotext fallback (user requirements;
the fallback is loud and flagged lossy).

Second round, after the user supplied a real vault and a cloud reviewer
(Sage, 20 findings, no code read) and agy adjudicated: accepted the case-preserving `normalize` for
check 13 (R02), schema guard (R10), `zip` single pass (R04, tested), footnotes into `dropped.txt`,
window halving on kill, explicit slug from the PDF filename (agy NEW-5), `normalize_with_map`
(agy NEW-3). Added from measurement, not from review: absolute `page_idx`, table HTML vs pipe table.
Deferred: reconversion dry-run listing shifted citations (R14).

## 14. Open items to settle in the plan

1. ~~`zip` output contents~~ resolved (§2).
2. Page-map `confidence` threshold (0.8) rests on one two-column paper and one single-column paper;
   add a third fixture with several tables before treating `high` as reliable. The real-vault corpus has
   no PDFs, so fixtures come from public arXiv papers (excerpts only).
3. ~~Title extraction~~ resolved: `<slug>` comes from the PDF filename (§4).
4. Window size 8 is a guess; tune from the first real conversion of a long paper (time vs retries).
5. Whether to run formula recognition at all for `basic`: the 4-page kill happened while formulas
   were being recognised; check whether `mineru-kit` has a switch before adding one.
