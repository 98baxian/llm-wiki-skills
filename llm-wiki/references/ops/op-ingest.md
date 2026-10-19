# op-ingest — full ingest procedure

The `ingest` summary in `SKILL.md` keeps the load-bearing rules (concept alignment,
verify-fresh specifics). This file is the complete step-by-step. Read it when you run
`ingest` (and when filling stubs during `batch_ingest` or `save`).

Default ingest reads the raw source **directly** — no preprocessing skill in front of
it — and compiles it into wiki pages. `fast_ingest.py` only does bookkeeping (hash, slug,
stub); it does not transform the source, except that a **PDF is converted first** into
line-citable markdown under `raw/converted/<slug>/` (`convert_source.py`, configured in the vault
`CLAUDE.md` § Document conversion) and the stub points at that markdown. If the vault's `CLAUDE.md` requires a
preprocessing or safety step for a source type, that overrides this default — follow it
first. For a large source, scaffold before you summarize (see § Divide-and-conquer):
outline the sections, then distill each.

## Step 1 — Run the bookkeeping script

```bash
python3 .claude/skills/llm-wiki/scripts/fast_ingest.py <wiki-root> <raw-file-path>
```

The script:
- Computes SHA-256 hash
- Generates a URL-friendly slug
- Detects source type (personal-writing vs external)
- Creates a stub file at `wiki/sources/<slug>.md` with pre-filled YAML frontmatter
- Prints instructions telling you what to do next

If the stub already exists, the script will warn — delete it first to re-ingest.

## Step 2 — Read the raw file and fill in the stub

- Write the Summary section (2-4 sentences, every claim cited)
- Write Key Points (each with line citation)
- Extract concepts and entities
- If source `date` is more than 2 years before today, set `possibly_outdated: true` in frontmatter
- If source contains **any number, date, percentage, or threshold**, populate
  `claims_requiring_verification` with each one verbatim:
  ```yaml
  claims_requiring_verification:
    - "2% discount if paid within 10 days" (raw/contracts/vendor.md, L14)
    - "deadline: 2026-03-31" (raw/contracts/vendor.md, L22)
  ```
  lint check 13 will verify each string exists in the raw file at every lint run.

## Step 3 — Concept alignment (mandatory before creating any concept page)

- Map each concept to an English lowercase-hyphen slug (map a Chinese-only name to its
  English slug, e.g. `第一性原理` → `first-principles-thinking`)
- Search `wiki/concepts/` for that slug
- Scan existing concept pages' `aliases` fields for a match in **either language**
  (Chinese↔Chinese, English↔English, Chinese↔English) — the idea may already live under
  a different-language alias or synonym
- If match found (slug OR any alias): UPDATE existing page, do not create a near-duplicate
- If no match: CREATE new page using `wiki/templates/concept-template.md`, filling
  `aliases` with **both the Chinese and English names** (+ synonyms)

## Step 4 — For each concept (create or update)

- Set `title:` to the **primary-language name** (whichever language `CLAUDE.md`
  § Bilingual format names as primary); the `# H1` / first line leads with the primary
  language and annotates the secondary in `（）` (Chinese-primary vault: `中文名（English Name）`)
- Add source to Sources section
- Append to Evolution Log: `YYYY-MM-DD (N sources): 强化/修正/新增分歧 — <one-liner>`
- Update `source_count`, `last_reviewed`, `updated`; keep `aliases` complete & bilingual
- Write **all prose and all section headings** in the primary language only — Chinese in a
  Chinese-primary vault (`## 描述`, `## 要点`), never bilingual headings like `## 描述（Description）`
- Apply the bilingual pair `primary（secondary）` (e.g. `中文（English）`) ONLY to the page title
  and a technical term's first appearance — not to section headings or ordinary prose — per article-guide
- Apply line citations to every factual claim

## Step 5 — For each entity

Same logic as concepts.

## Step 5b — Comparisons (create when source explicitly compares two things)

Create `wiki/comparisons/<a>-vs-<b>.md` (using `wiki/templates/comparison-template.md`) when:
- The source performs a direct pairwise comparison (two methods, two tools, two approaches)
- A query answer synthesizes a comparison worth reusing across future queries
- The difference is too structural to fit in either concept's Contradictions section

Use `[[comparisons/<a>-vs-<b>]]` wikilinks in both concept pages so the graph stays connected.
Do NOT create a comparisons page for general multi-source synthesis — that goes in `wiki/synthesis/`.

## Step 6 — Rebuild index

```bash
python3 .claude/skills/llm-wiki/scripts/build_index.py <wiki-root>
```
This regenerates `wiki/index.md` and `wiki/index/<domain>.md` automatically.
You do NOT manually update `wiki/index.md` after this step.

## Step 7 — Update search index (only if `search_tool: qmd` in CLAUDE.md `## Search`)

```bash
qmd embed
```
This rebuilds vector embeddings so the new source is immediately queryable.

## Step 8 — Refresh the hot cache

Overwrite `wiki/hot.md` with the new recent-context summary (what was ingested, key new
facts, pages created/updated). See § Hot cache. It is a cache, not a journal — keep it short.

## Step 9 — Check wiki/QUESTIONS.md

If this source answers an open question, notify the user.

## Step 10 — Log

Append to `log/YYYYMMDD.md`:
```
## [HH:MM] ingest | <slug> — <one-line description> (touched N pages)
```

## Personal Writing Flow (detected automatically by `fast_ingest.py`)

- Skip Summary section — no objective summary of your own writing
- Extract core argument → write into concept pages' "My Position"
- Do NOT increment `source_count` (no self-citation bias)
- SHA-256 still recorded

This is also the flow `save` reuses — anything written to `raw/personal/` ingests this way.
