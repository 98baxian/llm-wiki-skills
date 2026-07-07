# op-lint — full lint procedure

The `lint` summary in `SKILL.md` covers the common path. This file is the complete
post-run playbook plus deep lint.

```bash
python3 .claude/skills/llm-wiki/scripts/lint_wiki.py <wiki-root>
```

The script runs 13+ checks. Output: `outputs/lint-YYYY-MM-DD.md`.

**Flags:**
- `--stale-only` — only report stale pages (fast; used by session-start routine)
- `--structural` — skip uncited-claims check (faster for large wikis)

## After running

1. Show the user the summary (errors / warnings / suggestions count).
2. If `ERROR: sha256` errors exist, ask whether to propagate FIX comments:
   ```bash
   python3 .claude/skills/llm-wiki/scripts/cascade_flag.py <wiki-root>
   ```
   This writes `<!-- FIX: raw source changed — re-verify -->` on all citation
   lines in wiki pages that reference a modified source file.
3. If `broken-links` errors or warnings exist, run the maintainer (deterministic repair):
   ```bash
   python3 .claude/skills/llm-wiki/scripts/fix_links.py <wiki-root>               # dry run — preview
   python3 .claude/skills/llm-wiki/scripts/fix_links.py <wiki-root> --apply --stub  # repair + stub missing targets
   ```
   `fix_links.py` is the repair counterpart to lint's detection. It auto-fixes wrong-category and
   wrong-slug links (normalising both the link's category and slug to the target page's real filename
   turns a near-miss into an exact match), and with `--stub` creates minimal placeholder pages for genuinely missing targets so
   the link resolves and the page is flagged for filling. It reports anything ambiguous as `UNRESOLVED`
   for you to decide, and quarantines template-placeholder tokens. Re-run `lint_wiki.py` after to confirm
   `broken-links` is clear. (Trigger words: fix links, repair links, maintain.)
4. Ask whether to fix other flagged issues.
5. If `search_tool: qmd` in CLAUDE.md `## Search`, re-sync the search index:
   ```bash
   qmd update
   ```
6. Log: `## [HH:MM] lint | N issues found, M fixed`

## Deep Lint (trigger: "deep lint" — monthly)

1. Randomly select 5 source summary pages from `wiki/sources/`.
2. Read each alongside its `raw_file`.
3. Compare every factual claim sentence-by-sentence against the raw source.
4. Report deviations. Fix immediately.
5. Log: `## [HH:MM] lint | deep-lint — N deviations found in 5 pages`
