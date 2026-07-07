# Tooling Tips — Skills, Search, and Integrations

Practical setup and usage notes for the LLM Wiki stack.

---

## Skills — auto-discovered, no config

Every folder in `.claude/skills/` is a skill. Claude Code discovers it natively via
its `SKILL.md` `description` frontmatter — there is no enable list and no install step.

- To **add** a skill: drop its folder in `.claude/skills/<name>/`.
- To **pause** a skill: move its folder out of `.claude/skills/`.
- When two skills overlap, encode the preference in each skill's `description`.
- Skills live under `.claude/`, so they stay out of Obsidian's graph view.

---

## Index Rebuilding with build_index.py

After every ingest, compile, or restructuring operation, regenerate the index:

```bash
python3 scripts/build_index.py <wiki-root>
```

What it does:
- Scans all `wiki/**/*.md` frontmatter
- Writes `wiki/index.md` (lightweight master catalog)
- Writes `wiki/index/<domain>.md` for each domain in `CLAUDE.md` (§ Scope)
- Never touches `raw/` or concept/source/entity content

This replaces the old pattern of manually updating `wiki/index.md` and
the "Current articles" section in `CLAUDE.md`. Both are now auto-maintained.

---

## Batch Ingestion with batch_ingest.py

To ingest a full directory of files:

```bash
# Ingest all new markdown files in raw/articles/
python3 scripts/batch_ingest.py <wiki-root> --dir raw/articles

# Ingest all PDFs with auto-conversion
python3 scripts/batch_ingest.py <wiki-root> --dir raw/pdfs --ext .pdf --pdf

# Dry run — see what would be processed without creating stubs
python3 scripts/batch_ingest.py <wiki-root> --dir raw/ --dry-run
```

Files whose SHA-256 already matches an existing `wiki/sources/` stub are
skipped automatically, so re-running is safe.

---

## qmd Search

[qmd](https://github.com/tobilu/qmd) (`@tobilu/qmd`) is a local semantic search engine for
Markdown files. It is **optional and off by default** — the wiki uses index scanning
unless you opt in (see "Opting into qmd" below).

### Setup (one-time per machine)

```bash
npm install -g @tobilu/qmd
```

### Setup (one-time per wiki)

```bash
# Register the wiki's wiki/ directory as a named collection.
# Pick any collection name (one per wiki).
qmd collection add wiki/ --name <collection-name>
```

### Core commands

```bash
# After every ingest batch — rebuild embeddings
qmd embed

# Re-index without rebuilding embeddings (faster, use after lint/compile)
qmd update

# Semantic search (used by QUERY and REFLECT)
qmd query "attention vs retrieval" -c <qmd_collection> -n 5

# Check index health and file count
qmd status
```

### Opting into qmd

qmd is off by default. To turn it on, note it in `CLAUDE.md` (e.g. under § Notes for
the LLM) so the agent uses the qmd paths instead of index scanning:

```
search_tool: qmd            # collection: <collection-name>
```

**If qmd is unavailable**, all operations fall back to index scanning
(`wiki/index.md` → domain indexes). Operations remain correct but slower.

---

## Obsidian Setup

### Essential settings

1. **Attachment folder**: Settings → Files and links → `raw/images/`
2. **New file location**: Settings → Files and links → `wiki/concepts/`
3. **Download attachments hotkey**: `Ctrl+Shift+D`

### Recommended plugins

- **Obsidian Web Clipper** (browser extension) — clip web pages to `raw/clippings/`
- **Dataview** — query frontmatter for dynamic dashboards
- **Marp** — render wiki content as slide decks
- **`plugins/obsidian-audit/`** (this repo) — select text → file feedback → `audit/`

### Web Clipper configuration

- Save location: `raw/clippings`
- Note name: `{{date:YYYY-MM-DD}}-{{title}}`
- Properties: `title`, `source`, `author`, `date`, `tags: ["raw", "raw/clipping"]`, `processed: false`
- Attachment path: `raw/images/`

### Graph view

Graph view (`Ctrl+G`) shows wiki shape:
- Dense hub = well-connected concept page
- Isolated node = orphan (needs links or removal; `lint_wiki.py` flags these)
- Cluster = sub-topic worth a folder-split

### Useful Dataview queries

```
TABLE date, source_count, confidence
FROM "wiki/concepts"
SORT source_count DESC
LIMIT 20

LIST FROM "wiki/sources" WHERE processed = false SORT date DESC

TABLE date, last_reviewed, domain_volatility
FROM "wiki/concepts"
WHERE domain_volatility = "high"
SORT last_reviewed ASC

LIST FROM "wiki/concepts"
WHERE last_reviewed < date(today) - dur(90d)
AND domain_volatility = "high"
SORT last_reviewed ASC
LIMIT 10
```

The last query mirrors what `lint_wiki.py --stale-only` reports, useful for
keeping a live Obsidian dashboard of pages needing review.

---

## Obsidian Audit Plugin — `plugins/obsidian-audit/`

Select text in any wiki page → add feedback → writes to `audit/`.

```bash
cd plugins/obsidian-audit
npm install && npm run build
npm run link -- "/path/to/your/vault"
# Enable 'LLM Wiki Audit' in Obsidian → Settings → Community plugins
```

Commands:
- **Audit: Add feedback on selection** — opens modal with severity + comment
- **Audit: List open feedback for current file** — shows notice
- **Audit: Open audit folder** — reveals `audit/` in explorer

---

## Web Viewer — `web/`

Local Node.js server — preview wiki with mermaid, KaTeX, wikilinks.

```bash
cd web && npm install && npm run build
npm start -- --wiki "/path/to/wiki-root" --port 4175
# open http://127.0.0.1:4175
```

Features:
- Left sidebar: navigation from `wiki/index.md`
- Main pane: rendered markdown with mermaid + KaTeX
- Right sidebar: open audits for current page
- Select text → "💬 Add feedback" popover → writes to `audit/`

---

## Generating Charts

For quantitative analyses, generate matplotlib scripts:

```python
# outputs/charts/my-analysis.py
import matplotlib.pyplot as plt
# ... chart code ...
plt.savefig('outputs/charts/my-analysis.png')
```

Embed in wiki: `![[my-analysis.png]]`

---

## Git Workflow

The wiki is git-friendly:

```bash
git add .
git commit -m "ingest: 3 papers on attention mechanisms"
git push
```

Keep large files in `.gitignore`. Use pointer files in `raw/refs/` instead.

---

## Cascade Flag Workflow

When `lint_wiki.py` reports `ERROR: sha256` (a raw source has been modified),
the affected wiki pages contain stale citations. Run:

```bash
python3 scripts/cascade_flag.py <wiki-root>
```

This finds all wiki pages with `(raw/<modified-file>, L...)` citations and
inserts a `<!-- FIX: raw source changed (sha256 mismatch) — re-verify claims -->` 
comment on each citation line. These are then processed by the `audit` operation.

The session-start staleness alert will suggest running this automatically when
sha256 mismatches exist.

---

## Cross-project referencing

A compiled vault is worth reusing from your *other* Claude Code projects — a coding repo,
an assistant, a writing workflow — without duplicating its context into each one. The
`wiki/hot.md` cache makes this cheap: it is a ~500-token recent-context summary, so another
project can borrow the vault's state for the price of one small read.

In the other project's `CLAUDE.md`, add a pointer like:

```markdown
## Wiki knowledge base
Path: ~/path/to/vault

When you need context this project doesn't already have:
1. Read wiki/hot.md first (recent context, ~500 tokens).
2. If that's not enough, read wiki/index.md (full catalog).
3. For domain specifics, read wiki/index/<domain>.md, then drill into individual pages.

Do NOT read the wiki for general coding questions, or for anything already in this
project's own files or the current conversation.
```

The read order matters: `hot.md` (cheapest) → `index.md` → per-domain index → pages. Most
lookups stop at step 1 or 2. The vault stays the single source of truth; every project
reads from it instead of keeping its own drifting copy.
