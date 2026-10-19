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
- MinerU `content_list.json` (not verified to exist in 4.0.12 kit output; kit formats are
  `markdown | middle_json | zip`).
- Quote verification itself (Spec B).

## 2. Verified facts this design rests on

Measured on this machine, MinerU 4.0.12, `mineru-kit parse`, tier `basic` (CPU), first 3 pages of a
22-page two-author preprint (single column):

| Fact | Evidence |
|---|---|
| Default markdown embeds base64 images: 297 KB for 3 pages, 8 KB after stripping | measured |
| `-f middle_json` → schema `docvortex.middle` 2.0: `pages[].page_idx`, `pages[].blocks[]` with `type` (`header`, `footer`, `doc_title`, `text`, `image`, `paragraph_title`, …), `bbox`, `content`; `metadata.producer.version` | measured |
| Headers/footers are not in the markdown; `*`, `~` are backslash-escaped in it | measured |
| Block→line matching, body blocks only, after unescape + whitespace + lowercase: 17 of 18 matched; the miss was a page-number text block. First-occurrence matching mis-assigned one block, so the search must move forward from the previous match | measured |
| Speed ≈ 0.6 page/s plus ≈ 5 s start-up (3 pages ≈ 10 s wall) | measured |
| torch and mineru's llama-cpp native libraries crash if loaded in one process | measured earlier, see mcp-management.md |

**Not tested**: two-column reading order, tables, formulas, scanned PDFs, `standard`/`advanced`
tiers on this corpus. Claims about those are expectations, not facts.

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
  <slug>.dropped.txt     text of header/footer/page-number blocks the markdown omits (MinerU only)
  images/
```

`dropped.txt` is one block per line prefixed `p<N>\t<type>\t`, de-duplicated per page. It is
never cited as a line source; it exists so Spec B can still find DOIs, preprint ids and running
titles that sit in page margins.

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

- **MinerU**: run a second `-f middle_json` pass (or the single `zip` output if it contains both;
  to be checked in the plan). Take body blocks (skip `header`, `footer`, `page_number`, text under
  12 chars). Normalize both sides with `normalize_for_match` (§7). For each block, search forward
  from the previous match for its first 25 normalized characters. Page range = first and last
  matched line of its blocks. Pages with no matched block are `interpolated` between neighbours.
  `match_rate` = matched / body blocks; `confidence: low` below 0.8.
- **pdftotext**: write the markdown page by page from the `\x0c`-split text, so ranges are exact.
- **Custom command**: only if it supplies `pages.json`.
- The map is for human tracing ("L14-22 ≈ PDF p.2") and lint output. It never changes a
  citation and never fails a conversion.

Rejected: injecting `<!-- page: N -->` into the markdown. It would split quotes that cross a
page break and pollute verbatim matching.

## 5. Ingest integration

- New `llm-wiki/scripts/convert_source.py`: importable `convert(pdf, wiki_root) -> Result` and a CLI
  `convert_source.py <wiki-root> <pdf> [--force]`. MinerU runs in a subprocess with a timeout;
  images are extracted from base64 to `images/`.
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
| 13 claims | Compare after `normalize_for_match` on both sides (fixes false ERRORs from `\*`, `\~`, line wraps). |
| new, info | `source_lossy: true` → "cited raw is a lossy conversion (pdftotext)". |
| new, info | `pages.json` `confidence: low` → "page translation unreliable for <slug>". |

Existing 31 tests must stay green; check 13 must still fail on a genuinely absent string.

## 7. Shared helper

`llm-wiki/scripts/textnorm.py::normalize_for_match(s)`: Unicode NFKC, remove Markdown backslash
escapes (`\*`, `\~`, `\_`, …), curly quotes → straight, en/em dash → hyphen, strip zero-width
characters, join `-\n` hyphenation, collapse whitespace, lowercase. Used by the page-map aligner
and check 13 now; Spec B extends it and owns the decision on case sensitivity for quote
verification.

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
| Timeout / crash | exit 3, outputs removed, batch continues with next file |
| pdftotext result under 20 non-space characters | exit 4: "scanned PDF — needs MinerU or your own converter" |
| Private-zone path | exit 5 |
| Output slug dir exists, PDF sha differs, no `--force` | exit 6, nothing overwritten |

## 10. Tests

- Fake converter (shell script) for the command contract and fallback order.
- A small PDF built with `pdflatex` for the pdftotext path and exact `pages.json`.
- A trimmed `middle_json` + markdown fixture from the measured run for the aligner, including a
  repeated-text case that breaks first-occurrence matching and a page-number block that must not
  match.
- Real MinerU test marked `slow`, skipped when `mineru-kit` is absent.
- Check 5, check 13 (escape tolerance and still-fails-when-absent), dedupe by `source_pdf_sha256`.
- Private-zone refusal. No network access asserted by checking no remote flags in the argv.
- Run with `uv run --with pytest python -m pytest` (pytest is not installed globally).

## 11. Interface promised to Spec B

- Raw file for a PDF source = `raw/converted/<slug>/<slug>.md` (line-citable, images externalised).
- `convert.json.lossy` and `pages.json.confidence` are available so Spec B can downgrade verdicts.
- `normalize_for_match` is the single normalizer.
- `dropped.txt` as a secondary search target, so margin text (DOI, preprint id) is not a false
  `not_found`.
- Known limit Spec B must state: reconversion changes line numbers.

## 12. Files touched

New: `llm-wiki/scripts/convert_source.py`, `llm-wiki/scripts/textnorm.py`,
`tests/test_convert_source.py`, `tests/test_textnorm.py`, `tests/fixtures/convert/…`.
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

## 14. Open items to settle in the plan

1. Whether `zip` output contains both markdown and middle JSON (saves the second parse pass).
2. Aligner behaviour on two-column and table pages; needs a second fixture before trusting
   `confidence: high`.
3. Title extraction: MinerU sometimes drops the document title, and `fast_ingest.py` derives the
   slug from the title; fall back to the PDF filename.
