# op-query — full query procedure

Answer a question **grounded in the wiki**, not general knowledge. The `query` summary in
`SKILL.md` keeps the essentials; this file is the complete navigation + output detail.

## Index navigation — try in order, stop at first success

### Path A — qmd (only if `search_tool: qmd` in CLAUDE.md `## Search`; off by default)

```bash
qmd query "<question>" -c <qmd_collection> -n 5   # qmd_collection: read from CLAUDE.md ## Search
```
Read returned pages in full; follow one level of wikilinks.
Also check `wiki/comparisons/` for relevant pairwise analysis.
If qmd is unreachable → fall through to Path B immediately.

### Path B — Index scanning (default fallback)

- **Small wiki** (page count < `search_tool_threshold`, default 50):
  Read `wiki/index.md` directly. Scan for relevant pages.
- **Large wiki** (≥ threshold):
  Read `wiki/index.md` → identify relevant domain → read `wiki/index/<domain>.md` → navigate to pages.

## Steps

1. Navigate via index (Path A or B above)
2. Read identified pages in full; follow one level of wikilinks.
   Also check `wiki/comparisons/` for any relevant pairwise analysis.
3. If the wiki doesn't have enough material, say so. Suggest what to ingest.
4. Synthesize answer. Every claim traces to a specific source page.
5. Save to `outputs/queries/YYYY-MM-DD-<question-slug>.md`.
6. If answer has reuse value → promote to `wiki/concepts/`, add to `index.md`.
7. Refresh `wiki/hot.md` if the exchange was substantive (new open thread, notable finding).
8. Log: `## [HH:MM] query | <question-slug>`

## Synthesis-time routing

For any answer that turns on a specific number, date, percentage, threshold, exact quote,
or legal/financial term, verify against the cited `raw/` source rather than trusting the
compiled value — see § Compile vs. verify-fresh.

Note confidence level. Explicitly flag conflicts between sources.
End with **Confidence Notes** listing all low/medium-confidence citations.

## Output format

- General → Markdown prose
- Comparison → Markdown table
- Presentation → Marp slides (`marp: true` in frontmatter)
- Data/trends → Python matplotlib code block
- List → Structured bullet list
