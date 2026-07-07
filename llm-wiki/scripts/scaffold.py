#!/usr/bin/env python3
"""
scaffold.py — Bootstrap a new LLM Wiki directory structure.

Usage:
    python3 scaffold.py <wiki-root> "<Topic Title>"
    python3 scaffold.py <wiki-root> "<Topic>" --domains "AI,Finance" --primary-lang zh --lang en --private-zones ""
    python3 scaffold.py <wiki-root>                # interactive mode

Example:
    python3 scaffold.py ~/wikis/ai-research "AI Research" --domains "AI/ML,Technology" \
        --primary-lang zh --lang en --private-zones "private_zone"

Creates the full wiki directory tree including CLAUDE.md (the single config +
schema file), wiki/index.md, all templates, log/, audit/, and the first log entry.
"""

import os
import sys
import argparse
from datetime import date, datetime


_LANG_NAMES = {
    "zh": "Chinese", "zh-cn": "Chinese", "zh-hans": "Chinese", "chinese": "Chinese",
    "en": "English", "english": "English",
    "ja": "Japanese", "japanese": "Japanese",
    "ko": "Korean", "korean": "Korean",
    "fr": "French", "de": "German", "es": "Spanish",
}


def _lang_display(code: str) -> str:
    """Map a language code/name to a display name (zh → Chinese). Unknown → Title-cased."""
    c = (code or "").strip()
    if not c:
        return ""
    return _LANG_NAMES.get(c.lower(), c[:1].upper() + c[1:])


def _bilingual_example(primary_display: str, secondary_display: str) -> str:
    """A worked title example showing primary（secondary） for the chosen pair."""
    if primary_display == "Chinese":
        return "经济（Economy）"
    if secondary_display == "Chinese":
        return "Economy（经济）"
    return f"<{primary_display} name>（<{secondary_display} name>）"


def _bilingual_block(primary_display: str, secondary_display: str) -> str:
    """Markdown for CLAUDE.md § Naming conventions recording THIS vault's bilingual order.
    The skill stays language-agnostic; the primary/secondary decision is captured here,
    in the per-vault config, from the scaffold interview answers."""
    example = _bilingual_example(primary_display, secondary_display)
    return (
        f"\n### Bilingual format ({primary_display} primary)\n"
        f"- Body prose and section headings (`## …`) are written in {primary_display} **only** — "
        f"section headings are plain {primary_display} labels, never annotated with {secondary_display}.\n"
        f"- The bilingual pair `{example}` ({primary_display} leads, {secondary_display} in parens) "
        f"appears **only** on the page title (`# H1`) and a technical term's first appearance.\n"
        f"- `title:` frontmatter = the {primary_display} primary name (the graph-node label); "
        f"`aliases` holds **all** names, in both {primary_display} and {secondary_display}.\n"
        f"- Slugs and wikilinks always stay **English lowercase-hyphen**, independent of prose language.\n"
    )


def _private_zones_block(zones: list[str]) -> str:
    """The MANDATORY ## Private zones + ## Strict workflow rules sections for CLAUDE.md.
    A private zone is a path the agent must NEVER read directly — sensitive data (PHI,
    personal info, confidential records) reachable only through a designated pipeline.
    Returns "" when no private zone is configured."""
    zones = [z.strip().rstrip("/") for z in (zones or []) if z.strip()]
    if not zones:
        return ""
    bullets = "\n".join(f"- `{z}/`" for z in zones)
    return f"""
## Private zones

Paths containing sensitive data (PHI, personal information, confidential records, etc.).
**NEVER read files here directly** — always use the designated pipeline.

{bullets}

## Strict workflow rules — MANDATORY, no exceptions

These rules override default agent behavior. Violating them corrupts data integrity.

### Private zones — NEVER read directly

See `## Private zones` for the current list of protected paths.

**FORBIDDEN:** Any direct file read (`Read`, `Bash(cat ...)`, etc.) on private zone paths.

**Why:** Direct reading bypasses data protection measures.
"""


def scaffold(root: str, title: str, domains: list[str], lang: str,
             primary_lang: str = "English", private_zones: list[str] = None) -> None:
    """Create the full wiki directory tree and initial files."""
    today = date.today()
    today_iso = today.isoformat()
    today_compact = today.strftime("%Y%m%d")
    now_hm = datetime.now().strftime("%H:%M")

    # ── Create directories ────────────────────────────────────────────────────
    dirs = [
        "raw/articles",
        "raw/papers",
        "raw/notes",
        "raw/refs",
        "raw/images",
        "raw/pdfs",
        "raw/clippings",
        "raw/personal",
        "wiki/sources",
        "wiki/concepts",
        "wiki/entities",
        "wiki/comparisons",
        "wiki/synthesis",
        "wiki/templates",
        "wiki/index",
        "outputs/queries",
        "log",
        "audit",
        "audit/resolved",
    ]

    for d in dirs:
        os.makedirs(os.path.join(root, d), exist_ok=True)
    print(f"✓ Created directory tree under {root}/")

    # .gitkeep for empty dirs
    for gk in ["audit/.gitkeep", "audit/resolved/.gitkeep"]:
        _write(root, gk, "")

    # ── Private zones (agent NEVER reads these directly) ──────────────────────
    private_zones = [z.strip().rstrip("/") for z in (private_zones or []) if z.strip()]
    for z in private_zones:
        os.makedirs(os.path.join(root, z), exist_ok=True)
        _write(root, f"{z}/.gitkeep", "")
    if private_zones:
        print(f"✓ Created private zone(s): {', '.join(private_zones)} (agent must never read directly)")

    # ── Determine language config ─────────────────────────────────────────────
    # Bilingual order is a per-vault decision captured here from the interview answers:
    # the PRIMARY language leads (titles/headings), the SECONDARY is annotated in parens.
    # English is a legitimate secondary — only an explicit "no"/"none"/empty means monolingual.
    primary_display = _lang_display(primary_lang) or "English"
    secondary_raw = (lang or "").strip()
    is_bilingual = secondary_raw.lower() not in ("", "none", "no", "n")
    secondary_display = _lang_display(secondary_raw) if is_bilingual else "None"
    if is_bilingual:
        lang_note = f"{primary_display} (primary) + {secondary_display} (secondary)"
        bilingual_block = _bilingual_block(primary_display, secondary_display)
    else:
        lang_note = primary_display
        bilingual_block = ""
    private_block = _private_zones_block(private_zones)
    # ── CLAUDE.md (single config + schema file) ───────────────────────────────
    domains_bullets = "\n".join(f"- {d.strip()}" for d in domains)

    claude_md = f"""# {title} Knowledge Base

> Schema document — read at the start of every session together with `wiki/index.md`.
> Update after every major compile, ingest batch, or structural change.

## Scope

What this wiki covers:
{domains_bullets}

What this wiki deliberately excludes:
- <define out-of-scope areas>
{private_block}
## Operations

This wiki follows the llm-wiki skill's nine operations:
`scaffold`, `ingest`, `batch_ingest`, `save`, `query`, `lint`, `audit`, `reflect`, `compile`.
Every operation appends an entry to `log/YYYYMMDD.md`.

## Naming conventions

### Pages
- **Source pages** (`wiki/sources/`): kebab-case slug.
- **Concept pages** (`wiki/concepts/`): kebab-case slug.
- **Entity pages** (`wiki/entities/`): kebab-case slug.
- **Comparison pages** (`wiki/comparisons/`): `<a>-vs-<b>` slug. Pairwise only.
- **Synthesis pages** (`wiki/synthesis/`): kebab-case slug. Multi-source patterns.

### Wikilinks
- Always `[[category/english-lowercase-slug]]`.
{bilingual_block}
### Diagrams and formulas
- All diagrams are **mermaid**. No ASCII art.
- All formulas are **KaTeX** (inline `$...$` or block `$$...$$`).

### Raw file policy
- Small text sources → copy into `raw/<subfolder>/`.
- Large binaries → create `raw/refs/<slug>.md` pointer file.

## Current articles

### Concepts
*(none)*

### Entities
*(none)*

### Comparisons
*(none)*

### Sources
*(none)*

### Synthesis
*(none)*

## Open research questions
- <What do you want to understand better?>

## Research gaps
Sources to ingest:
- [ ] <URL or paper title> — why it's relevant

## Audit backlog
*(none — run `python3 .claude/skills/llm-wiki/scripts/audit_review.py {root} --open` to refresh)*

## Notes for the LLM
- Language: {lang_note}
- Tone: neutral
- Depth: survey-level
- Handling contradictions: state both, cite each, add to Open Research Questions.
"""
    _write(root, "CLAUDE.md", claude_md)
    print("✓ Created CLAUDE.md")

    # ── wiki/index.md ─────────────────────────────────────────────────────────
    index_md = f"""---
type: system-index
graph-excluded: true
updated: {today_iso}
---

# Index — {title}

> Domains: {', '.join(d.strip() for d in domains)} | Articles: 0

## 🔖 Navigation
- [[#Concepts]] · [[#Entities]] · [[#Comparisons]] · [[#Sources]] · [[#Synthesis]] · [[#Open Questions]]

## Concepts
*(none yet)*

## Entities
*(none yet)*

## Comparisons
*(none yet)*

## Sources (Processed)
*(none yet)*

## Sources (Unprocessed)
*(none yet)*

## Synthesis
*(none yet)*

## Open Questions
- <First research question>

## Recent Outputs
*(none yet)*
"""
    _write(root, "wiki/index.md", index_md)
    print("✓ Created wiki/index.md")

    # ── wiki/overview.md ──────────────────────────────────────────────────────
    overview_md = f"""---
type: system-overview
graph-excluded: true
updated: {today_iso}
---

# Knowledge Base Overview

## Health Dashboard

| Metric                       | Count |
| ---------------------------- | ----- |
| Sources (processed)          | 0     |
| Sources (unprocessed)        | 0     |
| Concepts (high confidence)   | 0     |
| Concepts (medium confidence) | 0     |
| Concepts (low confidence)    | 0     |
| Entities                     | 0     |
| Synthesis pages              | 0     |
| Open questions               | 0     |
| Stale pages                  | 0     |
| Orphan pages                 | 0     |

## Domain Coverage

## Knowledge Gaps
"""
    _write(root, "wiki/overview.md", overview_md)
    print("✓ Created wiki/overview.md")

    # ── wiki/QUESTIONS.md ─────────────────────────────────────────────────────
    questions_md = """---
type: system-questions
graph-excluded: true
---

# Open Questions

## Unanswered

## Answered
"""
    _write(root, "wiki/QUESTIONS.md", questions_md)
    print("✓ Created wiki/QUESTIONS.md")

    # ── wiki/hot.md (recent-context cache) ────────────────────────────────────
    hot_md = f"""---
type: system-hot
graph-excluded: true
updated: {today_iso}
---

# Hot Cache

> Rolling ~400-word summary of recent context. The LLM OVERWRITES this after
> ingest / save / significant query and at session end — it is a cache, not a
> journal. Read it first at session start to resume without crawling the vault.

## Last updated
{today_iso} — scaffolded. No sources ingested yet.

## Key recent facts
*(none yet)*

## Recent changes
*(none yet)*

## Active threads
- Vault just created. Suggested next step: ingest the first source.
"""
    _write(root, "wiki/hot.md", hot_md)
    print("✓ Created wiki/hot.md")

    # ── Templates ─────────────────────────────────────────────────────────────
    _write_templates(root, today_iso)
    print("✓ Created wiki/templates/ (6 templates, incl. comparison-template)")

    # ── log/<today>.md ────────────────────────────────────────────────────────
    log_md = f"""# {today_iso}

## [{now_hm}] scaffold | Initialized {title} knowledge base
- Created directory tree (raw/, wiki/, log/, audit/, outputs/)
- Created CLAUDE.md schema template (single config + schema file)
- Created wiki/index.md category skeleton
- Created wiki/hot.md, wiki/overview.md, wiki/QUESTIONS.md
- Created 6 page templates (incl. comparison-template)
- Domains: {', '.join(d.strip() for d in domains)}
- Language: {lang_note}
- Private zones: {', '.join(private_zones) if private_zones else 'none'}
"""
    _write(root, f"log/{today_compact}.md", log_md)
    print(f"✓ Created log/{today_compact}.md")

    # ── Bundle the llm-wiki skill into the vault (always) ─────────────────────
    _bundle_skill(root)
    script_prefix = ".claude/skills/llm-wiki/scripts"

    # ── Final summary ─────────────────────────────────────────────────────────
    print(f"""
✅ Wiki scaffolded at: {root}/

The llm-wiki skill is bundled into this vault at .claude/skills/llm-wiki/, so the
vault is self-contained and portable — the commands below resolve locally.

Next steps:
  1. Review and customize CLAUDE.md — define scope and naming conventions
  2. Add sources to raw/ (Obsidian Web Clipper for web; put PDFs in raw/pdfs/)
  3. Run ingest:       tell your LLM agent "ingest raw/<file>.md"
  4. For PDFs:         tell your agent "ingest raw/pdfs/<file>.pdf"
  5. Rebuild index:    python3 {script_prefix}/build_index.py {root}
  6. Ask questions:    "what does the wiki say about X?"
  7. Run lint:         python3 {script_prefix}/lint_wiki.py {root}
  8. Stale check:      python3 {script_prefix}/lint_wiki.py {root} --stale-only
  9. Process feedback: python3 {script_prefix}/audit_review.py {root} --open
""")


def _write_templates(root: str, today_iso: str) -> None:
    """Create all 5 page template files."""

    # Source template
    _write(root, "wiki/templates/source-template.md", f"""---
type: source-summary
title: ""
date: {today_iso}
source_url: ""
domain: ""
author: ""
tags: [wiki, wiki/source]
processed: true
raw_file: ""
raw_sha256: ""
last_verified: {today_iso}
possibly_outdated: false
language: "English"
canonical_source: ""
claims_requiring_verification: []
  # List verbatim strings for any number, date, percentage, or threshold in this source.
  # Format: "exact text from source" (raw/path/file.md, Lxx)
  # Example: "2% discount if paid within 10 days" (raw/contracts/vendor.md, L14)
  # lint_wiki.py check 13 verifies each string exists verbatim in the raw file.
---

# {{Title}}

## Summary

{{2-4 sentences. Every factual claim: (raw/articles/filename.md, L14-22)}}

## Key Points

- Point one. (raw/articles/filename.md, L45)

## Concepts Extracted

- [[concepts/concept-slug]]

## Entities Extracted

- [[entities/entity-slug]]

## Contradictions

## My Notes
""")

    # Personal writing template
    _write(root, "wiki/templates/personal-writing-template.md", f"""---
type: personal-writing
title: ""
date: {today_iso}
status: draft
topic_tags: []
confidence_at_writing: medium
superseded_by: ""
raw_file: ""
raw_sha256: ""
last_verified: {today_iso}
tags: [wiki, wiki/source]
processed: true
---

# {{Title}}

## Core Argument

## Key Claims

## Evidence Referenced

## Limitations
""")

    # Concept template
    _write(root, "wiki/templates/concept-template.md", f"""---
type: concept
title: ""                    # primary-language name (per CLAUDE.md § language)
date: {today_iso}
updated: {today_iso}
tags: [wiki, wiki/concept]
source_count: 0
confidence: low
domain_volatility: medium
last_reviewed: {today_iso}
aliases: []                  # ALL names — every language (+ synonyms)
---

<!-- DEMO TEMPLATE. The H1 is the page's display name. Bilingual vault: lead with the
     primary language and annotate the secondary in （）; the order is set by
     CLAUDE.md § Bilingual format — NOT hardcoded here. Monolingual vault: just the name.
     The slug/filename always stays English. -->
# {{Primary Name}}（{{Secondary Name}}）

## Definition

{{2-4 sentences. Every factual sentence: (raw/file.md, L14-22)}}

## Key Points

- Point with citation. (raw/articles/source.md, L45)
- [synthesis] Cross-source insight — labeled, no line citation needed.

## My Position

## Contradictions

## Sources

- [[sources/source-slug]]

## Evolution Log

- {today_iso} (1 source): Created from [[sources/first-source]].
""")

    # Entity template
    _write(root, "wiki/templates/entity-template.md", f"""---
type: entity
title: ""                    # primary-language name (per CLAUDE.md § language)
date: {today_iso}
tags: [wiki, wiki/entity]
entity_type: person          # person | tool | paper | organization | company
aliases: []                  # ALL names — every language (+ ticker/synonyms)
---

<!-- DEMO TEMPLATE. The H1 is the display name. Bilingual vault: lead with the primary
     language and annotate the secondary in （）; order per CLAUDE.md § Bilingual format
     — NOT hardcoded here. Monolingual vault, or no native secondary name (e.g. a ticker):
     just the name. The slug/filename always stays English. -->
# {{Primary Name}}（{{Secondary Name}}）

## Description

## Key Contributions / Features

## Related Concepts

## Sources
""")

    # Synthesis template
    _write(root, "wiki/templates/synthesis-template.md", f"""---
type: synthesis
title: ""
date: {today_iso}
tags: [wiki, wiki/synthesis]
source_count: 0
confidence: low
---

# {{Synthesis Title}}

## Thesis

## Evidence

## Counter-Evidence

## Synthesis

## Confidence Notes

## Limitations

## Sources
""")

    # Comparison template
    _write(root, "wiki/templates/comparison-template.md", f"""---
type: comparison
title: ""
date: {today_iso}
updated: {today_iso}
tags: [wiki, wiki/comparison]
subjects: ["", ""]
confidence: low
---

# {{A}} vs {{B}}

## Summary

{{One sentence stating the key difference.}} [synthesis]

## Comparison Table

| Dimension | {{A}} | {{B}} |
|-----------|-------|-------|
| | | |

## {{A}} — Strengths

- Point with citation. (raw/articles/source.md, L45)

## {{B}} — Strengths

- Point with citation. (raw/articles/source.md, L67)

## When to use {{A}}

## When to use {{B}}

## Conflicts between sources

## Sources

- [[sources/source-slug]]

## Evolution Log

- {today_iso} (1 source): Created from [[sources/first-source]].
""")


def _bundle_skill(root: str) -> None:
    """Copy the entire llm-wiki skill into <root>/.claude/skills/llm-wiki/ so the new
    vault is self-contained: its documented `.claude/skills/llm-wiki/scripts/...`
    commands resolve locally and the vault stays portable (move/share/clone it and it
    works). Always runs at scaffold time.
    """
    import shutil
    from pathlib import Path

    skill_src = Path(__file__).resolve().parent.parent            # <skill-root>/scripts/ → <skill-root>
    skill_dst = Path(root).resolve() / ".claude" / "skills" / "llm-wiki"

    if skill_src == skill_dst:
        print("  → llm-wiki skill already lives at the vault path; not re-copying")
        return
    if skill_dst.is_relative_to(skill_src) or skill_src.is_relative_to(skill_dst):
        print("  ⚠ skill source and vault path overlap; skipping bundle to avoid recursion")
        return

    skill_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        skill_src, skill_dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*-workspace", ".DS_Store"),
        dirs_exist_ok=True,
    )
    print("✓ Bundled llm-wiki skill → .claude/skills/llm-wiki/ (SKILL.md + references/ + scripts/)")


def _write(root: str, path: str, content: str) -> None:
    """Write content to a file, creating parent directories as needed."""
    full = os.path.join(root, path)
    os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bootstrap a new LLM Wiki directory structure.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("wiki_root", help="Path to the wiki root directory")
    parser.add_argument("title", nargs="?", default=None,
                        help="Topic title for the wiki")
    parser.add_argument("--domains", type=str, default=None,
                        help='Comma-separated domains, e.g. "AI,Finance,Technology"')
    parser.add_argument("--primary-lang", type=str, default=None,
                        help='Primary language for wiki content: "English", "zh", "ja", etc.')
    parser.add_argument("--lang", type=str, default=None,
                        help='Secondary language for bilingual annotations: "zh", "ja", "none", etc.')
    parser.add_argument("--private-zones", type=str, default=None,
                        help='Comma-separated private-zone folders the agent NEVER reads directly '
                             '(sensitive data; pipeline-only access). Pass "" for none. Example: "private_zone"')
    args = parser.parse_args()

    # ── Interactive fallback ──────────────────────────────────────────────────
    title = args.title
    if not title:
        title = input("What is the topic title for this wiki? ").strip()
        if not title:
            print("Error: Topic title is required.", file=sys.stderr)
            sys.exit(1)

    domains_str = args.domains
    if not domains_str:
        print("\nWhat knowledge domains do you want this wiki to cover?")
        print("Examples: Economics · Finance · Technology · AI/ML · Science")
        print('Type your domains separated by commas, or "General" for everything.')
        domains_str = input("> ").strip()
        if not domains_str:
            domains_str = "General"

    domains = [d.strip() for d in domains_str.split(",") if d.strip()]

    primary_lang = args.primary_lang
    if primary_lang is None:
        print("\nWhat is the PRIMARY language for wiki content?")
        print("It leads in titles, headings, and a term's first mention (the secondary is annotated in parens).")
        print("Examples: English · Chinese · Japanese · French  (default: English)")
        primary_lang = input("> ").strip()
        if not primary_lang:
            primary_lang = "English"

    lang = args.lang
    if lang is None:
        print("\nWould you like a SECONDARY language? (annotated in parens after the primary — makes the vault bilingual)")
        print("Options: no · zh (Chinese) · ja (Japanese) · en (English) · other")
        lang = input("> ").strip()
        if not lang or lang.lower() in ("no", "n", "none"):
            lang = "None"

    private_zones_str = args.private_zones
    if private_zones_str is None:
        print("\nDo you need a PRIVATE ZONE? (a folder the agent NEVER reads directly — for PHI,")
        print("personal data, or confidential records, reachable only via a designated pipeline)")
        print('Enter folder name(s), comma-separated, or "no" for none. Example: private_zone')
        private_zones_str = input("> ").strip()
        if private_zones_str.lower() in ("no", "n", "none"):
            private_zones_str = ""
    private_zones = [z.strip() for z in (private_zones_str or "").split(",") if z.strip()]

    scaffold(args.wiki_root, title, domains, lang, primary_lang=primary_lang,
             private_zones=private_zones)


if __name__ == "__main__":
    main()
