# llm-wiki-skill

**A code-driven agent skill for building Karpathy-style LLM knowledge bases.**

> Experimental skill — will iterate over time.
> Please send your feedback in GitHub issues.

Inspired by [Andrej Karpathy's llm-wiki Gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) and the community's work building on it.

## What this is

Instead of RAG (re-retrieving raw docs on every query), this pattern has the LLM **compile** raw sources into a persistent, cross-linked Markdown wiki and answer from it. The compile cost is paid once at ingest and amortized across every later query, so knowledge **compounds** across sessions instead of being rediscovered.

Two principles run through every operation:

- **Divide-and-conquer** — split big things into small, linkable pieces (on ingest, in how the vault is shaped, and on query) so the wiki stays navigable as it grows.
- **Compile vs. verify-fresh** — summarize concepts into the wiki, but re-check specifics (numbers, dates, quotes, legal/financial terms) against `raw/` at query time, since a faithful summary can still drop precision.

Three owners, clearly separated:

- **You** own: sourcing raw material, asking good questions, steering direction, filing feedback on things the AI got wrong.
- **LLM** owns: all writing, cross-referencing, filing, bookkeeping, and acting on your feedback.
- **Python scripts** own: deterministic work — hashing, file creation, health checks, index building, audit scanning. The LLM calls them and reads their output.

Every action on the wiki is one of **nine logged operations** — `scaffold`, `ingest`, `batch_ingest`, `save`, `query`, `lint`, `audit`, `reflect`, `compile` — and each appends an entry to the current day's log (`log/YYYYMMDD.md`). `save` files the current conversation or answer into the wiki (materialized to `raw/personal/`, then ingested), so insights don't vanish into chat history.

A small **hot cache** (`wiki/hot.md`) — a rolling ~400-word summary of recent context — is read first at session start so a new session (or another project pointing at the vault) resumes instantly without crawling the full index.

The skill comes with two companion tools in this repo:

- **`plugins/obsidian-audit/`** — an Obsidian plugin: select text in any page, leave a comment with severity, and the comment is written into `audit/` as an anchored markdown file.
- **`web/`** — a local Node.js preview server: renders the wiki with mermaid, KaTeX, and wikilinks, lets you select + file feedback from the browser, and shows open audits per page.

Both tools share a single TypeScript library (`audit-shared/`) so audit files written from Obsidian and the web viewer are byte-identical in shape.

## Install

```bash
# Copy the skill into your agent's skills directory
cp -r llm-wiki/ ~/.claude/skills/llm-wiki/
# or for Codex
cp -r llm-wiki/ ~/.codex/skills/llm-wiki/
```

Then reference it in your agent config, or simply paste `llm-wiki/SKILL.md` into your agent context.

> `scaffold.py` also **bundles a copy of the skill into each new vault** at `<wiki-root>/.claude/skills/llm-wiki/`, so a scaffolded vault is self-contained — its documented `.claude/skills/llm-wiki/scripts/...` commands resolve locally and you can move or share the vault without a separate install.

## Quick start

```bash
# 1. Scaffold a new wiki. The setup interview asks three things:
#    domains, an optional second language, and whether you need a private zone.
python3 llm-wiki/scripts/scaffold.py ~/my-wiki "My Research Topic"

# Non-interactive example (Chinese wiki with English annotations, no private zone).
# Always pass --private-zones in agent/non-interactive runs so it doesn't prompt.
python3 llm-wiki/scripts/scaffold.py ~/my-wiki "AI Research" \
  --domains "AI/ML,Technology" --primary-lang zh --lang en --private-zones ""

# 2. Add a source
cp my-article.md ~/my-wiki/raw/articles/

# 3. Ingest it (SHA-256 hash, slug, stub creation; then the agent reads the raw
#    file directly and fills the stub)
python3 llm-wiki/scripts/fast_ingest.py ~/my-wiki raw/articles/my-article.md

# 3b. Save the current chat/answer into the wiki (say "save this" to the agent).
#     It writes the conversation to raw/personal/<date>-<slug>.md, then ingests it —
#     so a saved note is a real, hashable, citable source, not a floating note.
python3 llm-wiki/scripts/fast_ingest.py ~/my-wiki raw/personal/2026-07-06-my-note.md

# 4. Batch-ingest a directory (skips already-ingested files by SHA-256)
python3 llm-wiki/scripts/batch_ingest.py ~/my-wiki --dir raw/articles

# 5. Batch-ingest PDFs. --pdf (auto-convert via a PDF skill) is OFF by default;
#    use it only if this vault's CLAUDE.md calls for a preprocessing step.
python3 llm-wiki/scripts/batch_ingest.py ~/my-wiki --dir raw/pdfs --ext .pdf --pdf

# 6. Ask questions: "what does the wiki say about X?" (the agent answers grounded
#    in the wiki, verifying specifics against raw/ where it matters)

# 7. Rebuild the index after ingesting (regenerates wiki/index.md + per-domain catalogs)
python3 llm-wiki/scripts/build_index.py ~/my-wiki

# 8. Quick staleness check (fast session-start scan of concept review dates)
python3 llm-wiki/scripts/lint_wiki.py ~/my-wiki --stale-only

# 9. Full health check (13+ checks: links, SHA, stale, orphans, claims, …)
python3 llm-wiki/scripts/lint_wiki.py ~/my-wiki

# 10. Propagate FIX comments when source files change (run after lint reports SHA mismatches)
python3 llm-wiki/scripts/cascade_flag.py ~/my-wiki --dry-run   # preview
python3 llm-wiki/scripts/cascade_flag.py ~/my-wiki             # apply

# 11. Repair broken wikilinks (deterministic counterpart to lint's detection)
python3 llm-wiki/scripts/fix_links.py ~/my-wiki                # dry run — preview
python3 llm-wiki/scripts/fix_links.py ~/my-wiki --apply --stub # repair + stub missing targets

# 12. File feedback from the web viewer or Obsidian plugin, then process it
python3 llm-wiki/scripts/audit_review.py ~/my-wiki --open
# then tell the agent: "audit: process the open comments"
```

> **Config:** there is no separate config file and no sync step. `CLAUDE.md` at the
> wiki root is the **single config + schema file** — edit it directly to change scope,
> domains, naming conventions, language, or private zones. After changing the domain
> list, re-run `build_index.py` so the per-domain catalogs match.

## Repo contents

```
llm-wiki-skill/
├── llm-wiki/                    ← The skill
│   ├── SKILL.md                 ← Main skill file (read by agent)
│   ├── references/
│   │   ├── schema-guide.md      ← CLAUDE.md (single config + schema) + YAML frontmatter specs
│   │   ├── article-guide.md     ← Line tracing, confidence gating, divide & conquer, language
│   │   ├── log-guide.md         ← log/ folder convention, allowed operations
│   │   ├── audit-guide.md       ← Dual feedback surfaces (audit/ files + inline FIX comments)
│   │   ├── scaffold-wizard.md   ← First-time setup interview (domains, languages, search, private zone)
│   │   ├── tooling-tips.md      ← qmd setup, Obsidian config, web viewer, cross-project referencing
│   │   └── ops/                 ← Full step-by-step for the heaviest operations
│   │       ├── op-ingest.md     ← Full ingest procedure (also used by save + batch stub-filling)
│   │       ├── op-query.md      ← Full query navigation + output formats
│   │       ├── op-lint.md       ← Full lint post-run playbook + deep lint
│   │       └── op-reflect.md    ← Full reflect procedure (Stage 0–3)
│   └── scripts/
│       ├── scaffold.py          ← Bootstrap a new vault (incl. wiki/hot.md); bundle the skill into it
│       ├── fast_ingest.py       ← SHA-256 hashing, slug generation, stub creation (also powers save)
│       ├── batch_ingest.py      ← Incremental batch ingest (skips known SHA-256 hashes)
│       ├── build_index.py       ← Generate hierarchical index (wiki/index.md + per-domain)
│       ├── lint_wiki.py         ← 13+ check health scan (links, SHA, stale, orphans, claims)
│       ├── cascade_flag.py      ← Auto-insert FIX comments on citations to modified sources
│       ├── fix_links.py         ← Repair wrong-category / wrong-slug / missing wikilinks
│       └── audit_review.py      ← Scan audit/ files + inline <!-- FIX --> comments
├── tests/                       ← pytest suite for the deterministic scripts (31 tests)
├── pytest.ini
├── audit-shared/                ← Shared TypeScript library
│   └── src/{schema,anchor,id,serialize,index}.ts
├── plugins/obsidian-audit/      ← Obsidian plugin — file audit from the vault
└── web/                         ← Local Node.js preview + feedback server
    ├── server/                  ← Express + markdown-it + KaTeX + wikilinks
    └── client/                  ← Vanilla-TS SPA with mermaid + selection popover
```

## Wiki directory structure (after scaffold)

```
<wiki-root>/
├── CLAUDE.md              ← Single config + schema (scope/domains, conventions, articles)
├── log/
│   └── YYYYMMDD.md        ← One file per day, append-only operation log
├── audit/
│   ├── *.md               ← Open human feedback (YAML-anchored)
│   └── resolved/          ← Processed feedback
├── raw/                   ← Immutable source documents (LLM reads, never writes)
│   ├── articles/
│   ├── papers/
│   ├── pdfs/              ← PDF source files
│   ├── notes/
│   ├── images/
│   ├── clippings/         ← Obsidian Web Clipper output
│   ├── personal/          ← Personal writing (does not count toward source_count)
│   └── refs/              ← Pointer files for large binaries
├── .claude/               ← Claude Code project config (hidden from Obsidian's graph)
│   ├── skills/
│   │   └── llm-wiki/      ← This skill, bundled into the vault at scaffold time
│   └── commands/          ← Slash-command wrappers
├── wiki/                  ← LLM-generated knowledge (LLM writes, you read)
│   ├── index.md           ← Lightweight master catalog (domain anchors + top-5)
│   ├── index/
│   │   └── <domain>.md    ← Full per-domain catalog (auto-generated by build_index.py)
│   ├── hot.md             ← Recent-context cache (rolling ~400-word summary; read first)
│   ├── overview.md        ← Health dashboard
│   ├── QUESTIONS.md       ← Open questions queue
│   ├── sources/           ← Per-source summary pages
│   ├── concepts/          ← Concept / topic pages
│   ├── entities/          ← People, tools, papers, organizations
│   ├── comparisons/       ← Side-by-side analysis of two concepts/tools/approaches
│   ├── synthesis/         ← Cross-source analysis (multi-source patterns)
│   └── templates/         ← Page templates
└── outputs/               ← All query answers and reports
    └── queries/
```

## Language support

`scaffold.py` supports any primary language and an optional secondary language for
bilingual term annotations:

```bash
# English wiki, no secondary language
python3 scaffold.py ~/wiki "Topic" --primary-lang English --lang no

# Chinese wiki with English annotations
python3 scaffold.py ~/wiki "主题" --primary-lang zh --lang en

# Interactive mode — prompts for both languages
python3 scaffold.py ~/wiki "Topic"
```

Both languages are recorded in the vault's `CLAUDE.md`; when a secondary language is
set, new terms get bilingual annotations per `references/article-guide.md`
§ Secondary Language Annotation.

## Extending with skills

The llm-wiki skill is one skill in your agent's skills directory. You can add more
skills alongside it — Claude Code auto-discovers every skill in `.claude/skills/`
natively (progressive disclosure: each skill's `description` is always in context;
the body loads when a task matches).

To add a skill:

```bash
# Drop any skill folder into .claude/skills/
cp -r /path/to/my-skill ~/.claude/skills/my-skill/

# That's it — no install step, no config change. The next session discovers it.
```

When two skills might overlap (e.g., two PDF skills), encode the preference in each
skill's own `description` field, and in the vault's `CLAUDE.md` for safety-critical
routing. A multi-step procedure that recurs belongs in its own skill rather than
bloating llm-wiki.

## Key design decisions

| Problem                                  | Solution                                                                                                                            |
| ---------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| RAG re-reads raw docs on every query     | Compile sources into a persistent wiki once; answer from it. Compile cost is amortized across every later query.                     |
| Summaries silently lose precision        | Compile vs. verify-fresh: ingest records `claims_requiring_verification`; `query` and lint re-check specifics against `raw/`.        |
| Flat index fails at 100+ pages           | Hierarchical index: `wiki/index.md` (lightweight) + `wiki/index/<domain>.md` (full catalog), auto-rebuilt by `build_index.py`.       |
| A concept page grows unmanageably large  | Divide-and-conquer: split past ~1200 words into a folder (`index.md` + focused sub-pages); shown as indented bullets in the index.   |
| Modified sources go undetected           | SHA-256 stored in every source stub; `lint_wiki.py` checks all; `cascade_flag.py` auto-inserts `<!-- FIX -->` on affected citations. |
| Full lint is slow at session start       | `lint_wiki.py --stale-only` — fast scan that only checks review dates on concept pages.                                             |
| Re-ingesting unchanged files wastes time | `batch_ingest.py` collects all known SHA-256 hashes from source stubs and skips matches.                                            |
| Broken wikilinks accumulate              | `fix_links.py` deterministically repairs wrong-category / wrong-slug links and stubs genuinely missing targets.                      |
| Semantic search is optional, not forced  | qmd is **off by default**; below `search_tool_threshold` (50) the agent scans `index.md`, and only uses qmd when explicitly enabled. |
| Adding skills requires config edits      | Skills are auto-discovered from `.claude/skills/`; drop a folder in and it works — no config, no install step.                       |
| Resuming a session re-crawls the vault   | `wiki/hot.md` — a rolling ~400-word recent-context cache, read first at session start and refreshed after each state-changing op. Also lets other projects borrow context for ~500 tokens. |
| Good answers vanish into chat history    | `save` materializes the conversation to `raw/personal/`, then ingests it — a saved note is a real, hashable, citable source with the same rigor as any ingest. |
| Script regressions corrupt real vaults   | `tests/` — a 31-test pytest suite over the deterministic layer (slug/hash, index build, link repair, scaffold, hot-cache wiring). Run `pytest` before shipping a change. |

## Running the web viewer

```bash
# one-time setup (builds audit-shared, installs deps, bundles client)
cd audit-shared && npm install && npm run build && cd ..
cd web && npm install && npm run build && cd ..

# start the server against a wiki
cd web
npm start -- --wiki "/path/to/your/wiki-root" --port 4175
# open http://127.0.0.1:4175
```

## Building the Obsidian plugin

```bash
cd audit-shared && npm install && npm run build && cd ..
cd plugins/obsidian-audit
npm install
npm run build
npm run link -- "/path/to/your/Obsidian vault"
# Enable 'LLM Wiki Audit' in Obsidian → Settings → Community plugins.
```

## Tests

The deterministic `llm-wiki/scripts/` layer has a pytest suite (kept at the repo root, so
it is not bundled into scaffolded vaults):

```bash
pip install pytest
pytest
```

It covers slug/SHA bookkeeping, index generation, wikilink repair, scaffold structure, and
the hot-cache wiring (including the regression guard that lint never flags `hot.md`). See
[`tests/README.md`](tests/README.md).

## Use cases

- **Research deep-dive** — reading papers/articles on a topic over weeks
- **Personal wiki** — Farzapedia-style: journal entries compiled into a personal encyclopedia
- **Team knowledge base** — fed by Slack threads, meeting notes, docs
- **Reading companion** — building a rich companion wiki as you read a book

## Acknowledgements

- [Andrej Karpathy's original Gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
- [lewislulu/llm-wiki-skill](https://github.com/lewislulu/llm-wiki-skill) — reference implementation
- [LLM Wiki Feishu Doc](https://hcn9zwu8a0fz.feishu.cn/wiki/AM3ewXySViopPdkE8Gic90BDnRb)
- [pedronauck/skills karpathy-kb](https://github.com/pedronauck/skills/tree/main/skills/karpathy-kb) — full Obsidian vault integration
- [Astro-Han/karpathy-llm-wiki](https://github.com/Astro-Han/karpathy-llm-wiki) — example implementation
- [qmd](https://github.com/tobi/qmd) — semantic search for Markdown wikis
- [hermes-llm-wiki](https://github.com/NousResearch/hermes-agent/blob/main/skills/research/llm-wiki/SKILL.md)
- [llmwiki-compiler](https://github.com/atomicmemory/llm-wiki-compiler/tree/main) — compile raw sources into an interlinked markdown wiki

## License

MIT
