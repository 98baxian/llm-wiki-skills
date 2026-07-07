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
| `test_scaffold_and_hot.py` | `scaffold.py`, `lint_wiki.py` | scaffold structure, `hot.md` frontmatter, and the regression guard that lint treats `hot.md` as a system file (never an orphan/stale page) |
