---
name: llm-wiki
description: >-
  Build and maintain a self-compiling Markdown wiki / knowledge base (the
  "Karpathy-style LLM wiki" pattern): an agent ingests raw sources into a vault,
  compiles persistent cross-linked source / concept / entity / comparison /
  synthesis pages with line-level citations, splits large topics into
  divide-and-conquer folder hierarchies, and answers questions grounded in the
  compiled vault instead of re-reading raw docs each time — so knowledge
  compounds across sessions instead of being rediscovered. Nine logged
  operations: scaffold, ingest, batch_ingest, save, query, lint, audit, reflect,
  compile. Use whenever someone works against their wiki, knowledge
  base, vault, or "second brain" — even casually: ingesting or adding a
  paper/article/note ("fold this into the wiki"), scaffolding a new knowledge
  base, saving the current chat or answer into the wiki ("save this", "keep this
  conversation"), querying it ("based on my wiki, what do we know about X"),
  batch-ingesting a folder of files, running a lint or health check (dead links,
  orphans, stale pages), processing audit feedback or inline FIX comments,
  splitting or restructuring an overgrown page, comparing two things from the
  notes, or finding patterns across everything ingested (reflect).
---

## Core idea

Rather than re-retrieving raw docs on every query (RAG), the LLM **compiles** raw
sources into a persistent, cross-linked Markdown wiki and answers from it. The
compile cost is paid once at ingest and amortized across every later query, so
knowledge compounds instead of being rediscovered.

Two cross-cutting principles run through every operation:
- **Divide-and-conquer** — split big things into small, linkable pieces so the
  wiki stays navigable as it grows (see § Divide-and-conquer).
- **Compile vs. verify-fresh** — summarize concepts into the wiki, but re-check
  specifics (numbers, dates, quotes) against `raw/`, since a faithful summary can
  still drop precision (see § Compile vs. verify-fresh).

- **You** own: sourcing raw material, asking questions, steering direction,
  filing feedback on anything the AI got wrong.
- **LLM** owns: all writing, cross-referencing, filing, bookkeeping, and
  acting on your feedback.
- **Python scripts** own: deterministic work — hashing, file creation,
  health checks, index building, audit scanning. The LLM calls them and reads
  their output.

## Session start routine

Execute in this order at the start of every session:

1. **Read `CLAUDE.md`** — the single config + schema file: wiki scope/domains,
   language, naming conventions, and current article list (plus any vault-specific
   rules the vault's `CLAUDE.md` defines).
2. **Skills are auto-discovered** — Claude Code loads every skill in
   `.claude/skills/` natively (progressive disclosure: each skill's `description`
   is always in context; the body loads when a task matches). You do **not**
   manually read or "enable" skills. To add one, drop its folder in
   `.claude/skills/<name>/` — nothing else. When two skills overlap, the
   preference lives in each skill's own `description` (and `CLAUDE.md` for
   safety-critical routing). Stable user preferences and
   learned rules live in `CLAUDE.md` (§ Notes for the LLM).
3. **Read `wiki/hot.md`** — the recent-context cache (~400 words). This is the
   fastest way to resume: it summarizes the last thing done, key recent facts, and
   open threads, so you rarely need to crawl further. See § Hot cache.
4. **Read `wiki/index.md`** — current state of the knowledge base.
5. **Read last entry of `log/`** — `ls log/ | tail -1`, then read the file.
6. **Check `audit/`** — if any `*.md` files exist (not `.gitkeep`), note the
   count and mention it to the user.
7. **Staleness alert** — run:
   ```bash
   python3 .claude/skills/llm-wiki/scripts/lint_wiki.py <wiki-root> --stale-only
   ```
   If any pages are returned, show the list and ask: "These concept pages
   haven't been reviewed recently — want me to add them to the freshen queue?"
8. **qmd status** (only if `search_tool: qmd` in `CLAUDE.md` `## Search`; off by default):
   ```bash
   qmd status
   ```
   Report indexed file count. If indexed count < actual wiki page count,
   suggest: "qmd index appears stale — run `qmd embed` to sync."
   If qmd is not configured, skip silently.

If the wiki has not been scaffolded yet (no `CLAUDE.md`):
Read `references/scaffold-wizard.md` — run the three-question setup interview,
then execute the scaffold script as directed there.

## Directory layout

```
<wiki-root>/
├── CLAUDE.md              ← Single config + schema (scope/domains, conventions, articles)
├── log/                   ← Per-day operation log (one file per day)
│   └── YYYYMMDD.md
├── audit/                 ← Human feedback inbox (one file per comment)
│   └── resolved/          ← Processed feedback
├── raw/                   ← Immutable source documents (LLM reads, never writes)
│   ├── articles/
│   ├── papers/
│   ├── pdfs/              ← PDF source files
│   ├── converted/         ← Script-written markdown of PDFs (line-citable; never edit)
│   ├── notes/
│   ├── images/
│   ├── clippings/         ← Obsidian Web Clipper output
│   ├── personal/          ← Personal writing
│   └── refs/              ← Pointer files for large binaries
├── <private-zone>/        ← Optional (scaffold --private-zones). Sensitive data; agent NEVER reads directly
├── .claude/               ← Claude Code project config (hidden from Obsidian's graph)
│   ├── skills/            ← Skills Claude Code discovers natively
│   │   ├── llm-wiki/      ←   This skill — bundled into the vault at scaffold time
│   │   └── <domain-skill>/ ←  Any other skills you install (pdf, finance, research, …)
│   └── commands/          ← Slash-command wrappers (arg-parsing entry points)
├── wiki/                  ← LLM-generated knowledge (LLM writes, you read)
│   ├── index.md           ← Lightweight master catalog (domain anchors + top-5)
│   ├── index/             ← Full per-domain catalogs (auto-generated)
│   │   ├── <domain-a>.md
│   │   └── <domain-b>.md
│   ├── hot.md             ← Recent-context cache (rolling ~400-word summary; read first)
│   ├── overview.md        ← Health dashboard
│   ├── QUESTIONS.md       ← Open questions queue
│   ├── sources/           ← Per-source summary pages
│   ├── concepts/          ← Concept/topic pages
│   ├── entities/          ← People, tools, papers, organizations
│   ├── comparisons/       ← Side-by-side analysis of two concepts, tools, or approaches
│   ├── synthesis/         ← Cross-source analysis (multi-source patterns, not pairwise)
│   └── templates/         ← Page templates
└── outputs/               ← All query answers and reports
    └── queries/
```

## System architecture

| Layer                        | Owner  | Rule                                                                          |
|------------------------------|--------|-------------------------------------------------------------------------------|
| `raw/`                       | Human  | IMMUTABLE. LLM reads only, never modifies.                                    |
| `raw/converted/`             | Script | Written once per PDF by `convert_source.py`. The LLM never edits it. Cite this markdown, not the PDF. |
| `<private-zone>/` (if any)   | Human  | NEVER read directly (`Read`/`cat`/etc.). Sensitive data; pipeline-only access. See `CLAUDE.md` § Private zones. |
| `wiki/`                      | LLM    | LLM fully owns. Human browses, does not edit.                                 |
| `wiki/hot.md`                | LLM    | Rolling recent-context cache; overwritten each session. Read FIRST at session start. See § Hot cache. |
| `outputs/`                   | LLM    | All answers persist here. Never lost in chat.                                 |
| `log/`                       | LLM    | Append-only operation log.                                                    |
| `audit/`                     | Shared | Human writes feedback; LLM processes it.                                      |
| `.claude/skills/`            | Shared | Skills auto-discovered by Claude Code. Drop a folder to add one.               |
| `CLAUDE.md`                  | Shared | Single config + schema. Human sets scope/domains/settings; LLM maintains articles. |

Skill dispatch is native (by each skill's `description`); cross-skill preferences and
safety-critical routing live in those descriptions and `CLAUDE.md`. Stable user
preferences and learned rules also live in `CLAUDE.md` (§ Notes for the LLM). A
multi-step procedure that recurs belongs in its own skill rather than bloating this one.

---

## Hot cache

`wiki/hot.md` is a rolling ~400-word summary of recent context — the last thing done,
the key recent facts, what changed, and any open threads. It exists so a new session
resumes instantly without crawling the full index, and so **another project can borrow
this vault's context cheaply** (read `hot.md` first, ≈500 tokens, before the index — see
`references/tooling-tips.md` § Cross-project referencing).

It is a **cache, not a journal**: overwrite it completely each time and keep it short.
Refresh it after every state-changing operation — `ingest`, `save`, `batch_ingest`, a
substantive `query`, `reflect`, `compile` — and at session end. `build_index.py` never
touches it; the LLM owns it. It carries `type: system-hot` + `graph-excluded: true`, and
lint skips it (it is in `SYSTEM_FILES`), so it never surfaces as an orphan or stale page.

If `hot.md` ever contradicts a compiled page, **trust the page** — the cache can lag.
Structure: `## Last updated` · `## Key recent facts` · `## Recent changes`
(Created / Updated / Flagged wikilinks) · `## Active threads`.

---

## Divide-and-conquer

Big things become small, linkable pieces — on the way in, in how the vault is
shaped, and on the way out. This is what keeps the wiki navigable as it grows: a
single flat index degrades past ~100–200 pages, so structure is split early
rather than patched later.

- **On ingest (large source).** Don't distill a 400-page document in one pass.
  First skim it into a short outline (sections + their line ranges), then distill
  one section at a time into focused pages, each linked back to the source.
  Scaffold before you summarize.
- **On structure (large topic).** When a concept page would pass its ~1200-word
  ceiling, split it into a folder: a short `index.md` (definition + map of
  sub-pages) plus one focused 400–1200 word page per aspect, shown as indented
  bullets in the master index. The master `wiki/index.md` stays light; full
  per-domain catalogs live in `wiki/index/<domain>.md`. Detail and signs-to-split
  are in `references/article-guide.md` § Length Targets & Divide-and-Conquer.
- **On query (broad question).** Decompose a wide question into sub-questions,
  answer each against its own slice of the vault, then synthesize — labeling the
  cross-cutting inferences `[synthesis]`.

The operations below implement this loop: `ingest` and `compile` split pages,
`build_index.py` maintains the hierarchical index, and `query` fans out then
re-synthesizes.

---

## Compile vs. verify-fresh (synthesis-time)

A compiled wiki page is a *summary*: the agent chose what to keep at **ingest
time** and stored the result. That is the point — it pays the synthesis cost once
and amortizes it across every later query — but it has one failure mode worth
designing against. A summary can be faithful yet lose precision: a page records
"net-30 with early-payment discounts" while the raw contract says "2% if paid
within 10 days." Once the summary is cited as authoritative the dropped detail is
invisible — `lint` checks page-to-page consistency, not page-to-source fidelity.

So split knowledge by *when* it should be synthesized:

- **Compile — trust the wiki.** Definitions, concepts, relationships, stable
  structure: things that summarize well and rarely change. This is the wiki's job,
  and why it compounds.
- **Verify fresh — trust `raw/`.** Specific numbers, dates, percentages,
  thresholds, exact quotes, legal/financial terms. The wiki page may *point* to
  these, but the raw source stays authoritative — re-read it at query time instead
  of trusting the compiled value.

A vault's `CLAUDE.md` can make the routing explicit, declaring source types that
are raw-only and never compiled (contracts, logs, live tables). Three mechanisms
keep the layers honest: `ingest` records every specific as
`claims_requiring_verification`; `query` re-checks specifics against `raw/`; and
`lint`'s deep-lint and check 13 re-verify stored claims against their source.
Compile concepts, verify specifics fresh.

---

## The nine operations

Every action on the wiki is one of these nine. Each appends an entry to the
current day's log file (`log/YYYYMMDD.md`). See `references/log-guide.md`. The
heaviest three (`ingest`, `query`, `lint`) keep a load-bearing summary here and
push their full step-by-step into `references/ops/op-*.md` — read that file when
you run the operation.

### 1. `scaffold`

Bootstrap a new wiki. Run:

```bash
python3 .claude/skills/llm-wiki/scripts/scaffold.py <wiki-root> "<Topic Title>" \
  --domains "AI,Finance" --primary-lang zh --lang en --private-zones ""
```

The setup interview (see `references/scaffold-wizard.md`) asks five things: **domains**;
the **primary language** (it leads in titles/headings); an optional **secondary
language** (annotated in parens — this makes the vault bilingual; pass `--lang no` for
monolingual); whether to enable **semantic search**; and whether you need a **private
zone**. The primary/secondary answers are recorded in `CLAUDE.md` — the `## Notes →
Language` line *and* a generated `### Bilingual format` block under Naming conventions
that states which language leads and shows a worked example. **The skill itself stays
language-agnostic and reads the order from `CLAUDE.md`; nothing about Chinese-vs-English
is hardcoded in the skill or templates.**

`--private-zones "<folder[,folder]>"` creates each folder under the wiki root and writes
a MANDATORY `## Private zones` + `## Strict workflow rules` block into `CLAUDE.md`. A
private zone is a path the agent must **NEVER read directly** (`Read`, `Bash(cat …)`,
etc.) — it holds sensitive data (PHI, personal info, confidential records) reachable only
through a designated pipeline. The folder lives outside `wiki/`, so it is never indexed,
linted, or queried. Pass `""` for none. In non-interactive/agent runs always pass
`--primary-lang`, `--lang`, and `--private-zones` so the script doesn't stop to prompt.

Creates the full directory tree, `CLAUDE.md` (the single config + schema file),
`wiki/index.md`, all templates, and the first log entry. It also **bundles the llm-wiki skill into
the new vault** (`<wiki-root>/.claude/skills/llm-wiki/` — SKILL.md + references/ +
scripts/), so the vault is self-contained: its documented
`.claude/skills/llm-wiki/scripts/...` commands resolve locally and you can move or
share the vault without a separate install. See the script's docstring for details.

After scaffolding:
1. Review and customize `CLAUDE.md` — define scope, naming conventions.
2. Start ingesting sources.

### 2. `ingest`

**Trigger words:** ingest, process this, add this source

Add one source to the knowledge base. Default: the agent reads the raw source
**directly** (no preprocessing skill in front) and compiles it into wiki pages; if the
vault's `CLAUDE.md` mandates a preprocess/safety step for a source type, follow that
first. For a large source, scaffold before you summarize (§ Divide-and-conquer).

**PDFs:** `fast_ingest.py` first converts a PDF into line-citable markdown under
`raw/converted/<slug>/` (local MinerU by default, your own tool or `pdftotext` otherwise; see
the vault `CLAUDE.md` § Document conversion) and ingests that markdown, so citations stay
`(raw/converted/<slug>/<slug>.md, Lxx)`. `<slug>.pages.json` maps lines back to PDF pages.
A source stub from a PDF carries `source_pdf` and `source_pdf_sha256`.

```bash
python3 .claude/skills/llm-wiki/scripts/fast_ingest.py <wiki-root> <raw-file-path>
```

`fast_ingest.py` does bookkeeping only — SHA-256, slug, source-type detection, and a
stub at `wiki/sources/<slug>.md` (it warns if the stub exists; delete to re-ingest).
Then read the raw file, fill the stub, and compile concepts/entities/comparisons. Two
rules are load-bearing and easy to skip:

- **Concept alignment (before creating any concept page):** map the concept to an English
  lowercase-hyphen slug, then search `wiki/concepts/` **and** existing pages' `aliases`
  (in *either* language) — if it already exists, UPDATE it; never create a near-duplicate.
- **Verify-fresh specifics:** every number / date / percentage / threshold / exact quote
  goes verbatim into the stub's `claims_requiring_verification` (lint check 13 re-verifies
  each string against the raw file).

After filling pages: rebuild the index (`build_index.py`), refresh `wiki/hot.md`, check
`wiki/QUESTIONS.md`, and log `## [HH:MM] ingest | <slug> — … (touched N pages)`.

**Full step-by-step — frontmatter fields, bilingual title rules, comparison triggers,
the personal-writing flow, and the qmd re-embed step — is in `references/ops/op-ingest.md`.
Read it when you ingest.**

### 3. `batch_ingest`

**Trigger words:** batch ingest, ingest folder, process multiple files,
ingest all PDFs, process everything in raw/, bulk add

```bash
python3 .claude/skills/llm-wiki/scripts/batch_ingest.py <wiki-root> [--dir raw/articles] [--ext .md .pdf]
```

Default: each source is read directly, same as single `ingest`. Skips files already
ingested (SHA-256 match). After the script runs, fill each stub following `ingest`
Steps 2–5, then rebuild the index once all stubs are complete.

> PDFs (`--ext .pdf`) are converted automatically before ingest (see `ingest`). Pass
> `--no-convert` to skip them. The old `--pdf` flag is a deprecated no-op. Files under
> `raw/converted/` are never scanned as separate sources.

**After batch ingest — compile immediately (MANDATORY).** When you process 3+
sources in one batch, each source will independently extract concepts. This
produces shallow one-line stubs with no cross-source synthesis. Before creating
individual concept pages:
1. Read ALL source stubs first. Identify concepts that appear in 2+ sources —
   these are the highest-value targets.
2. Merge related low-overlap concepts into broader umbrella pages (e.g.,
   `short-squeeze` + `options-volatility` → `options-market-mechanics`).
3. Flesh out the merged pages with cross-source synthesis, Evolution Log,
   and line citations from every contributing source.
4. Only then create standalone pages for genuinely unique concepts.
5. Delete absorbed stubs; update source page wikilinks to point to new merged
   concepts; rebuild index.

Rule of thumb: a batch of N related sources should produce roughly N–2N fleshed-out
concept pages, not 5N one-line stubs. A concept page without an Evolution Log or
cross-source synthesis is an ingest TODO, not a finished page.

After the batch is fully compiled, rebuild the index and refresh `wiki/hot.md` once.

Log: `## [HH:MM] batch_ingest | N files processed, M already ingested`

### 4. `save`

**Trigger words:** save this, save that answer, keep this, file this conversation, save to wiki

Persist the current chat — an answer, an insight, a decision — into the wiki so it doesn't
vanish into history. `save` honors the same source-of-truth rule as `ingest`: knowledge is
only trustworthy if it traces to an immutable artifact. So `save` is a thin wrapper over
`ingest`, not a separate writing path.

1. **Decide what to save** — the whole conversation, or one specific answer. Ask only if ambiguous.
2. **Materialize it to `raw/`** — write the content verbatim to
   `raw/personal/YYYY-MM-DD-<slug>.md` with short frontmatter (`title`, `date`,
   `type: personal-writing`). This makes it hashable and citable — a real source, not a
   floating note.
3. **Ingest it** —
   ```bash
   python3 .claude/skills/llm-wiki/scripts/fast_ingest.py <wiki-root> raw/personal/YYYY-MM-DD-<slug>.md
   ```
   The `raw/personal/` path auto-selects the personal-writing flow (no `source_count`
   bump, no self-citation bias; SHA-256 still recorded).
4. **Compile** — fill the stub's Core Argument / Key Claims and route insights into concept
   pages' "My Position", cross-linking entities/concepts. Claims drawn from external sources
   you discussed keep their `(raw/…, Lxx)` citations; your own reasoning is `[synthesis]`.
5. **Finish like any ingest** — rebuild the index, refresh `wiki/hot.md`, log
   `## [HH:MM] save | <slug> — <one-liner>`.

If the user explicitly wants only a quick durable note ("don't make a wiki page"), stop
after step 2. But the default is the full flow — an uncompiled note doesn't compound.

### 5. `query`

**Trigger words:** direct question, "based on my knowledge base", "what does the wiki say"

Answer grounded in the wiki, not general knowledge. Navigate the index (qmd if
`search_tool: qmd` is set in `CLAUDE.md`, else scan `wiki/index.md` → for a large wiki drill
into `wiki/index/<domain>.md`), read the identified pages in full, follow one level of
wikilinks, and also check `wiki/comparisons/`. Every claim traces to a specific source page.
**Verify specifics (numbers / dates / quotes / legal-financial terms) against the cited
`raw/` source**, not the compiled value (§ Compile vs. verify-fresh). If the wiki lacks the
material, say so and suggest what to ingest — then, if you answer from general knowledge,
clearly mark which parts are not from the wiki.

Save the answer to `outputs/queries/YYYY-MM-DD-<slug>.md`; if it has reuse value, promote it
to `wiki/concepts/`. Refresh `wiki/hot.md` if the exchange was substantive. End with
**Confidence Notes** listing low/medium-confidence citations. Log `## [HH:MM] query | <slug>`.

**Output formats (prose / table / Marp slides / matplotlib / list) and the full navigation
detail are in `references/ops/op-query.md`.**

### 6. `lint`

**Trigger words:** lint, health check, check wiki

```bash
python3 .claude/skills/llm-wiki/scripts/lint_wiki.py <wiki-root>   # 13+ checks → outputs/lint-YYYY-MM-DD.md
```
Flags: `--stale-only` (fast; used at session start), `--structural` (skip the
uncited-claims check on large wikis), `--verify-quotes` (also check that quoted passages occur
in the raw files they cite; see `references/ops/op-verify-quotes.md`). After running, show the error / warning / suggestion
counts, then act on the findings:

- `ERROR: sha256` (raw source changed) → `cascade_flag.py <wiki-root>` writes
  `<!-- FIX -->` on every citation to a modified source.
- `broken-links` → `fix_links.py <wiki-root>` (dry run), then `--apply --stub` to repair and
  stub missing targets; re-run lint to confirm it's clear.

Log `## [HH:MM] lint | N issues found, M fixed`.

**Deep lint** (trigger "deep lint", ~monthly — reads 5 random source pages against their
raw files) and the full post-run playbook are in `references/ops/op-lint.md`.

### 7. `audit`

**Trigger words:** audit, process feedback, check comments, fix errors

Process human feedback from two surfaces:

```bash
python3 .claude/skills/llm-wiki/scripts/audit_review.py <wiki-root> --open
```

The script scans:
- `audit/*.md` — YAML-anchored feedback files
- `wiki/**/*.md` — inline `<!-- FIX: ... -->` comments (including those written
  by `cascade_flag.py`)

For each piece of feedback:
1. Read the file. Locate the anchor/comment in the target.
2. Decide: **accept** / **partial** / **reject** / **defer**.
3. Apply edits to the target file (smallest edit that fixes the issue).
4. For audit files: append `# Resolution`, move to `audit/resolved/`.
5. For inline FIX comments: remove the comment after applying the fix.
6. Log: `## [HH:MM] audit | resolved <id> — <one-liner>`

### 8. `reflect`

**Trigger words:** reflect, 综合分析, 发现规律

Cross-source synthesis — discover patterns, validate conclusions, identify gaps.
Suggested: monthly, or after every 10 new sources.

Read `references/ops/op-reflect.md` for the full procedure:
Stage 0 (reverse validation) → Stage 1 (pattern scan) → Stage 2 (synthesis) → Stage 3 (gap report).

After all stages: update `wiki/overview.md`, rebuild index, refresh `wiki/hot.md`, log.

### 9. `compile`

**Trigger words:** compile, restructure, clean up the wiki, split, merge

(Re)structure wiki content. Always two phases — never skip Phase 1.

**Phase 1 — Inventory (read-only):** list all pages, identify split/merge/promote candidates,
present the full plan, **wait for user confirmation** before any writes.

**Phase 2 — Restructuring:** execute splits (create sub-pages, update wikilinks), merges
(single canonical page, keep old slug as redirect), rebuild index, refresh `wiki/hot.md`.

```bash
python3 .claude/skills/llm-wiki/scripts/build_index.py <wiki-root>
```

Log: `## [HH:MM] compile | <what you did>`

> Config lives in `CLAUDE.md` (the single config + schema file) — edit it directly;
> there is no separate config file or sync step. After changing the domain list,
> re-run `build_index.py` so the per-domain catalogs match. To update a vault's
> bundled skill copy, copy the skill folder over (`cp -R`).

---


## Anti-hallucination rules (MANDATORY)

**Rule 1 — Line-Level Source Annotation**
Every factual claim ends with: `(raw/articles/filename.md, L14-22)`
If line not identifiable: `(raw/filename.md, location unknown)` — flag in lint.
The citation points to the **raw file path**, NEVER to a `[[sources/...]]` page
wikilink — a compiled source page is not verifiable evidence. The source-page
wikilink belongs only in the page's `## Sources` section, listed **once**.
- WRONG: `... bond issuance. ([[sources/nvda-bonds]], L10)`
- RIGHT: `... bond issuance. (raw/clippings/nvda-bonds.md, L10)`
lint checks 9 and 14 enforce this.

**Rule 2 — Verbatim Numbers and Quotes**
Never paraphrase numeric data.
- WRONG: "The model has approximately 175 billion parameters."
- RIGHT: The model has "175 billion parameters" (raw/articles/gpt3.md, L8).
Keep each quotation on one line, with its citation after it: `verify_quotes.py` checks quoted
passages against the cited raw lines and an unfinished or wrapped quotation is not checked.

**Rule 3 — Label All Synthesis**
Cross-source inferences: prefix with `[synthesis]`. No line citation needed.

**Rule 4 — Self-Check Before Saving**
Every factual sentence must have `(raw/..., Lxx)` or `[synthesis]` before saving.

**Rule 5 — Mermaid for Diagrams**
Any flow, sequence, hierarchy, or state diagram must be mermaid. No ASCII art.

**Rule 6 — KaTeX for Formulas**
Any formula must be KaTeX: inline `$...$` or block `$$...$$`.

**Rule 7 — PDF Citation Format**
For PDF sources, cite by page: `(raw/pdfs/filename.pdf → raw/articles/slug.md, p.14)`

---

## Confidence gating

| Sources                        | Confidence       | How set                                  |
|--------------------------------|------------------|------------------------------------------|
| 1                              | `low`            | Auto                                     |
| 3+                             | `medium`         | Auto                                     |
| 5+ no major conflicts          | candidate `high` | LLM shows definition + sources to user   |
| User replies "confirm" or "ok" | `high`           | Only then                                |

Personal writing does NOT count toward `source_count`.
`confidence: high` = user's active endorsement. **Never automatic.**

---

## Bilingual naming & secondary language

When `CLAUDE.md` (§ Notes for the LLM) sets a language order, the **first-named language
is primary and leads; the second is annotated in parentheses**. For a `Chinese (primary)
+ English` vault, all prose and section headings are primary-language only (`## 描述`, `## 要点`,
never `## 描述（Description）`); the bilingual pair `中文名（English Name）` (e.g. `经济（Economy）`)
appears **only** on the page title and a technical term's first appearance. The `title:` frontmatter
is the Chinese name, `aliases` holds both languages, and slugs/wikilinks stay English. Full rules in
`references/article-guide.md` § Bilingual Naming & Term Annotation.

---

## Reading the user

| User says                | You do                                              |
|--------------------------|-----------------------------------------------------|
| ingest raw/...           | INGEST (script + steps; default reads source directly) |
| ingest *.pdf             | INGEST — default direct read; vault `CLAUDE.md` may mandate a preprocess step |
| batch / multiple files   | BATCH_INGEST (batch_ingest.py)                      |
| save this / keep this conversation / file this answer | SAVE — materialize chat to `raw/personal/`, then ingest |
| Direct question          | QUERY — check wiki first                            |
| lint / health check      | LINT script                                         |
| deep lint                | Deep Lint (5 pages vs raw source)                   |
| compile / clean up       | COMPILE — Phase 1 inventory → confirm → Phase 2 writes |
| audit / fix errors       | AUDIT (script + process feedback)                   |
| I want to understand X   | ADD-QUESTION → `wiki/QUESTIONS.md`                  |
| merge / deduplicate      | COMPILE → merge flow                                |
| reflect / find patterns  | REFLECT — Stage 0 → 1 → 2 → 3                      |
| compare X vs Y / X and Y | COMPARISONS → `wiki/comparisons/<x>-vs-<y>.md`     |
| freshen / stale pages    | LINT --stale-only → list stale pages                |
| fix links / repair links / maintain | fix_links.py → repair broken links + stub missing targets, then lint to confirm |
| add a skill | Drop its folder in `.claude/skills/` — auto-discovered, no install step |
| edited CLAUDE.md / added a domain | Config lives in `CLAUDE.md` (no separate file, no sync step). If the domain list changed, re-run `build_index.py` so per-domain catalogs match. |

When user asks a question without specifying wiki, still check wiki first.
If wiki has no relevant content, say so explicitly, then answer from general
knowledge — clearly distinguishing between the two.

---

## Quality checklist (before saving any wiki edit)

**Anti-hallucination:**
- [ ] Every factual sentence: `(raw/filename.md, L42-45)` or `(raw/pdfs/..., p.N)`
- [ ] Every number/statistic: verbatim quote from source
- [ ] Every cross-source inference: labeled `[synthesis]`

**Anti-drift:**
- [ ] Evolution Log updated if definition changed
- [ ] Contradictions section updated if sources conflict
- [ ] `last_reviewed` date updated

**Format:**
- [ ] All frontmatter fields present
- [ ] Bilingual: prose + section headings are primary-language only (no `（English）` on headings); only `title:`/`# H1` and each technical term's first mention use `primary（secondary）` (order per CLAUDE.md § Bilingual format); `aliases` has all languages
- [ ] Wikilinks: `[[category/english-lowercase-slug]]` (English slug, never Chinese)
- [ ] System files: `graph-excluded: true`
- [ ] Source pages: `raw_sha256` computed

**Confidence:**
- [ ] `source_count` = external sources only
- [ ] `confidence: high` NOT set without explicit user "confirm"

---

## References

- `references/schema-guide.md` — CLAUDE.md structure (single config + schema), YAML frontmatter specs, hierarchical index layout
- `references/article-guide.md` — line-level tracing, confidence gating, divide-and-conquer, extraction rules, secondary language annotation
- `references/log-guide.md` — log/ folder convention, allowed operations
- `references/audit-guide.md` — audit file format, anchor strategy, inline FIX comments, processing workflow
- `references/tooling-tips.md` — qmd setup, Obsidian config, web viewer
- `references/scaffold-wizard.md` — first-time setup interview (Questions 1–4: domains, primary language, secondary language, semantic search + scaffold command)
- `references/ops/op-ingest.md` — full ingest step-by-step (also used by `save` and stub-filling in `batch_ingest`)
- `references/ops/op-query.md` — full query navigation + output formats
- `references/ops/op-lint.md` — full lint post-run playbook + deep lint
- `references/ops/op-reflect.md` — full reflect procedure (Stage 0–3)
- `.claude/skills/<name>/SKILL.md` — auto-discovered by Claude Code; cross-skill preferences live in each skill's `description`
