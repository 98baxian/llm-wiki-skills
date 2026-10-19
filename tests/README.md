# Tests

Unit + integration tests for the deterministic `llm-wiki/scripts/` layer — the parts the
skill trusts to be correct (slug/hash bookkeeping, index generation, link repair, scaffold
structure, and the hot-cache wiring).

These tests live at the repo root, **outside `llm-wiki/`**, so they are not bundled into
scaffolded vaults (`scaffold.py` only copies the `llm-wiki/` folder).

## Run

```bash
pip install pytest        # once
pytest                    # from the repo root
```

## What's covered

| File | Script under test | Focus |
|------|-------------------|-------|
| `test_fast_ingest.py` | `fast_ingest.py` | slug generation, SHA-256, source-type detection, title/metadata extraction, the `/save` personal-writing stub |
| `test_build_index.py` | `build_index.py` | frontmatter parsing, domain loading from `CLAUDE.md`, domain routing, end-to-end index build |
| `test_fix_links.py` | `fix_links.py` | `norm` keying, link resolution, wrong-category repair, stub creation |
| `test_textnorm.py` | `textnorm.py` | case-preserving normalizer, fold/loose views, hyphen variants, offset map |
| `test_vault_config.py` | `vault_config.py` | CLAUDE.md sections, key/values, private zones |
| `test_convert_source.py` | `convert_source.py` | config, slugs, pdftotext / command / MinerU converters, page map, `convert()`, CLI |
| `test_fast_ingest_pdf.py`, `test_batch_ingest.py` | `fast_ingest.py`, `batch_ingest.py` | PDF conversion at ingest, dedupe by PDF hash |
| `test_lint_claims.py`, `test_lint_conversion.py` | `lint_wiki.py` | check 13 normalization, stale / lossy conversions |
| `test_convert_real.py` | `convert_source.py` | real MinerU run, opt in with `-m slow` and `LLM_WIKI_TEST_PDF` |
| `test_scaffold_and_hot.py` | `scaffold.py`, `lint_wiki.py` | scaffold structure, `hot.md` frontmatter, and the regression guard that lint treats `hot.md` as a system file (never an orphan/stale page) |
