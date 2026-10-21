# Scaffold Wizard — First-Time Setup Interview

Run this interview when `CLAUDE.md` does not yet exist. Ask these six questions,
collect answers, then run the scaffold script.

The language answers (Q2 + Q3) decide the vault's **bilingual order** — they are written
into `CLAUDE.md`, and every downstream operation reads the order from there. The skill
and templates stay language-agnostic; nothing is hardcoded to a specific language.

---

## Question 1 — Knowledge Domains

> "What knowledge domains do you want this wiki to cover? Examples:
> Economics · Finance · Technology · AI/ML · Science · Medicine · Law · History
> · General (for everything). Feel free to type your own."

---

## Question 2 — Primary Language

> "What is the **primary** language for this wiki? It leads in titles, headings, and a
> term's first mention. Options: English · Chinese · Japanese · Other (specify).
> (Default: English.)"

---

## Question 3 — Secondary Language (bilingual?)

> "Do you want a **secondary** language, annotated in parentheses after the primary?
> This makes the vault bilingual — e.g. with Chinese primary + English secondary, titles
> read `经济（Economy）`; with English primary + Chinese secondary, `Economy（经济）`.
> Options: No (monolingual) · English · Chinese · Japanese · Other (specify)."

After Q2 + Q3, confirm the order back to the user before scaffolding, e.g.:
"Got it — **Chinese primary, English secondary**, so titles will read `中文名（English Name）`."

---

## Question 4 — Private Zone (optional)

> "Do you need a **private zone** — a folder the agent must NEVER read directly, for
> sensitive data (PHI, personal information, confidential records) reachable only through
> a designated pipeline? Name the folder(s), or say No.
> Options: No · `private_zone` · other folder name(s)."

If yes, the scaffold creates the folder(s) and writes a MANDATORY `## Private zones` +
`## Strict workflow rules` block into `CLAUDE.md`. These paths sit outside `wiki/`, so they
are never indexed, linted, or queried — and the agent must not open them with `Read`/`cat`.

---

## Question 5 — PDF Conversion (MinerU)

> "Do you want to enable **MinerU** for PDF conversion? It is a powerful local parser
> (tables, formulas, scanned pages, page-accurate citations). It runs on your machine and
> never uploads documents, but is heavier than `pdftotext`.
> Options: Yes (recommended) · No (use `pdftotext` only — text-layer PDFs; scanned PDFs are refused)"

Yes writes `converter: mineru`, No writes `converter: pdftotext` into the `## Document
conversion` section of `CLAUDE.md`. If MinerU (`mineru-kit`) is not installed the scaffold
script warns. The line can be edited later (`converter: none` turns conversion off).

---

## Question 6 — Semantic Search (recommended)

> "Would you like to set up qmd for semantic search? Recommended for all wikis —
> it enables fast vector search so queries stay accurate as your wiki grows.
> Install: `npm install -g @tobilu/qmd`
> Options: Yes (recommended) · No (index-based search only)"

---

## After collecting answers — run scaffold

```bash
python3 .claude/skills/llm-wiki/scripts/scaffold.py <wiki-root> "<Topic>" \
  --domains "<answer1>" --primary-lang <answer2> --lang <answer3> --private-zones "<answer4>" --mineru <answer5>
```

- `--primary-lang` = Q2 (the leading language). `--lang` = Q3 (the secondary; pass `no`
  for a monolingual vault).
- `--private-zones` = Q4 (comma-separated folders the agent never reads; pass `""` for none).
- `--mineru` = Q5 (`yes` or `no`).
- The script writes the `Language:` line + a `### Bilingual format` block, and — if a
  private zone was named — the `## Private zones` + `## Strict workflow rules` block, into
  `CLAUDE.md`.

If the user answered **Yes** to Question 6:

```bash
qmd collection add wiki/ --name <qmd_collection>   # exact command printed by scaffold script
qmd embed                                           # build initial index
```
