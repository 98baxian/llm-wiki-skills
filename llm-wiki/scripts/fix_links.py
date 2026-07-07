#!/usr/bin/env python3
"""
fix_links.py — Repair broken wikilinks and stub missing targets (the `maintain` op).

This is the deterministic counterpart to lint_wiki.py's broken-link *detection*.
lint tells you a link is broken; this script fixes what can be fixed mechanically
and stubs the rest, so the LLM only has to make the genuine judgement calls.

The design mirrors Synto's `synto maintain` ("repairs broken wikilinks and creates
stubs for missing targets") and follows the llm-wiki division of labour: scripts own
deterministic file work, the LLM owns writing and judgement.

Repair strategy, in order, per broken link `[[category/stem]]`:

  1. EXACT-NORM REPAIR. Normalise the stem (lowercase, non-alphanumerics → "-") and
     look it up against the normalised stem of every existing page. A hit rewrites the
     link to the page's *real* category and *real* stem in one shot. This is what fixes
     a link like `sources/my-paper-2024` → `concepts/My Paper 2024`:
     both the wrong directory and the wrong slug-form collapse to the same normalised key.

  2. FUZZY REPAIR. No exact normalised hit: compare against all normalised stems with
     difflib. A single match at/above --cutoff (default 0.86) rewrites the link.
     Ambiguous (0 or >1 matches) is left for the LLM.

  3. STUB (only with --stub). Still unresolved and not a known template placeholder:
     create a stub page in the linked category from that category's template (if one
     exists in wiki/templates/), marked `stub: true` so it is easy to find and fill.

  4. SKIP. Known template-placeholder tokens (concept-slug, entity-slug, …) are reported
     for manual removal — never auto-stubbed, since stubbing them would bake the
     placeholder into the graph.

Default run is a DRY RUN: it prints what it would do and changes nothing. Pass --apply
to rewrite links in place and (with --stub) create stub files.

Usage:
    python3 fix_links.py <wiki-root>                 # dry run, report only
    python3 fix_links.py <wiki-root> --apply         # repair links in place
    python3 fix_links.py <wiki-root> --apply --stub  # repair + create stubs for the rest
    python3 fix_links.py <wiki-root> --cutoff 0.9    # stricter fuzzy threshold

Exit codes:
  0 — no unresolved broken links remain (after the actions taken)
  1 — unresolved broken links remain (need LLM/human judgement)
"""

import re
import sys
import argparse
import datetime
import difflib
from pathlib import Path
from collections import defaultdict

SYSTEM_FILES = {"index.md", "log.md", "overview.md", "QUESTIONS.md", "hot.md"}
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?]]")

# Tokens that appear verbatim in wiki/templates/*.md. A link to one of these means an
# ingest left the template scaffolding unfilled — stubbing it would be wrong; flag instead.
PLACEHOLDER_STEMS = {
    "concept-slug", "entity-slug", "source-slug", "first-source",
    "filename", "source", "your-slug-here", "example-slug",
}

TODAY_ISO = datetime.date.today().isoformat()


def norm(stem: str) -> str:
    """Canonical comparison key: lowercase, runs of non-alphanumerics collapsed to '-'."""
    return re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")


def parse_frontmatter(text: str) -> tuple[str, str]:
    """Split a markdown file into (frontmatter_block_including_fences, body)."""
    if not text.startswith("---"):
        return "", text
    end = text.find("\n---", 3)
    if end == -1:
        return "", text
    return text[: end + 4], text[end + 4:]


def wiki_files(wiki_dir: Path) -> list[Path]:
    """Non-system, non-template, non-index wiki markdown files (link sources)."""
    return [
        f for f in wiki_dir.rglob("*.md")
        if f.name not in SYSTEM_FILES
        and "templates" not in f.parts
        and "index" not in [p.name for p in f.parents if p != wiki_dir]
    ]


def build_indexes(wiki_dir: Path):
    """Return (by_category_stem, by_norm) over every existing page.

    by_category_stem: set of "category/stem" that resolve (category may be "").
    by_norm: norm(stem) -> list of (category, real_stem), for repair lookups.
    """
    by_category_stem: set[str] = set()
    by_norm: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for f in wiki_dir.rglob("*.md"):
        rel = f.relative_to(wiki_dir)
        category = rel.parent.as_posix()
        category = "" if category == "." else category
        by_category_stem.add(f"{category}/{f.stem}" if category else f.stem)
        by_norm[norm(f.stem)].append((category, f.stem))
    return by_category_stem, by_norm


def link_resolves(target: str, by_category_stem: set[str]) -> bool:
    """A link resolves if its exact category/stem (or bare stem) exists, or it is a system file."""
    stem = target.split("/")[-1]
    if f"{stem}.md" in SYSTEM_FILES:
        return True
    if target in by_category_stem:
        return True
    # Bare stem link (no category) resolves if that stem exists in any category.
    if "/" not in target and any(cs.split("/")[-1] == stem for cs in by_category_stem):
        return True
    return False


# Category dir -> the `type:` value a page in it should carry.
SINGULAR = {
    "entities": "entity", "concepts": "concept", "sources": "source-summary",
    "comparisons": "comparison", "synthesis": "synthesis", "cases": "case",
}


def make_stub(category: str, stem: str, referenced_by: str) -> str:
    """Render a *minimal* valid stub for a missing target.

    Deliberately not seeded from the category template: templates carry example
    wikilinks ([[sources/source-slug]], [[sources/first-source]]) and placeholder
    tokens that would themselves become broken links. A stub's job is only to make
    the inbound link resolve and flag the page for filling — the LLM writes the body
    later during a normal ingest/compile pass.
    """
    title = stem.replace("-", " ")
    typ = SINGULAR.get(category, category.rstrip("s"))
    return (
        f"---\ntype: {typ}\ntitle: \"{title}\"\ndate: {TODAY_ISO}\n"
        f"stub: true\nstub_created: {TODAY_ISO}\nsource_count: 0\nconfidence: low\n---\n\n"
        f"# {title}\n\n"
        f"> **Stub** created by fix_links.py — first referenced from `{referenced_by}`.\n"
        f"> Fill from the source(s) that link here, then remove `stub: true`.\n"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Repair broken wikilinks; stub missing targets.")
    ap.add_argument("root", help="wiki root (the directory containing wiki/)")
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    ap.add_argument("--stub", action="store_true", help="create stub pages for unresolved targets")
    ap.add_argument("--cutoff", type=float, default=0.86, help="fuzzy match threshold (0-1)")
    args = ap.parse_args()

    wiki_dir = Path(args.root) / "wiki"
    if not wiki_dir.is_dir():
        print(f"error: {wiki_dir} not found", file=sys.stderr)
        return 2

    by_category_stem, by_norm = build_indexes(wiki_dir)

    repaired: list[str] = []      # (file, old → new)
    stubbed: list[str] = []       # created stub paths
    unresolved: list[str] = []    # need judgement
    placeholders: list[str] = []  # template leakage, remove manually

    stubs_to_create: dict[str, tuple[str, str, str]] = {}  # "cat/stem" -> (cat, stem, referrer)

    for f in wiki_files(wiki_dir):
        text = f.read_text(encoding="utf-8", errors="replace")
        fm, body = parse_frontmatter(text)
        rel = f.relative_to(Path(args.root))
        new_body = body
        # Ignore links inside HTML comments — those are template scaffolding hints
        # (e.g. source-template's `<!-- - [[concepts/concept-slug]] -->`), not real edges.
        scannable = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
        for link in WIKILINK_RE.findall(scannable):
            target = link.split("|")[0].split("#")[0].strip()
            if link_resolves(target, by_category_stem):
                continue
            stem = target.split("/")[-1]
            category = "/".join(target.split("/")[:-1])

            if stem in PLACEHOLDER_STEMS:
                placeholders.append(f"{rel} → [[{link}]]")
                continue

            # 1. exact normalised match → rewrite to the real category/stem
            matches = by_norm.get(norm(stem), [])
            if len(matches) == 1:
                real_cat, real_stem = matches[0]
                new_target = f"{real_cat}/{real_stem}" if real_cat else real_stem
                if new_target != target:
                    new_body = new_body.replace(f"[[{link}]]", f"[[{new_target}]]")
                    repaired.append(f"{rel}: [[{target}]] → [[{new_target}]]")
                continue
            if len(matches) > 1:
                opts = ", ".join(f"{c}/{s}" for c, s in matches)
                unresolved.append(f"{rel} → [[{link}]] (ambiguous; candidates: {opts})")
                continue

            # 2. fuzzy match against all normalised stems
            close = difflib.get_close_matches(norm(stem), list(by_norm), n=2, cutoff=args.cutoff)
            if len(close) == 1:
                real_cat, real_stem = by_norm[close[0]][0]
                new_target = f"{real_cat}/{real_stem}" if real_cat else real_stem
                new_body = new_body.replace(f"[[{link}]]", f"[[{new_target}]]")
                repaired.append(f"{rel}: [[{target}]] ~→ [[{new_target}]] (fuzzy {close[0]})")
                continue

            # 3. stub, or 4. report unresolved
            if args.stub and category:
                key = f"{category}/{stem}"
                stubs_to_create.setdefault(key, (category, stem, str(rel)))
                stubbed.append(f"{rel} → [[{link}]] (stub {key})")
            else:
                unresolved.append(f"{rel} → [[{link}]]")

        if args.apply and new_body != body:
            f.write_text(fm + new_body, encoding="utf-8")

    if args.apply and args.stub:
        for cat, stem, referrer in stubs_to_create.values():
            dest = wiki_dir / cat / f"{stem}.md"
            if dest.exists():
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(make_stub(cat, stem, referrer), encoding="utf-8")

    # ── Report ──
    mode = "APPLIED" if args.apply else "DRY RUN (no changes written; pass --apply)"
    print(f"fix_links — {mode}\n")
    print(f"  Repaired links : {len(repaired)}")
    print(f"  Stubs {'created' if args.apply and args.stub else 'proposed'} : {len(stubbed)}")
    print(f"  Unresolved     : {len(unresolved)}")
    print(f"  Placeholders   : {len(placeholders)} (remove manually — unfilled template text)\n")
    for section, items in (
        ("REPAIRED", repaired), ("STUBBED", stubbed),
        ("UNRESOLVED — need judgement", unresolved),
        ("PLACEHOLDERS — remove manually", placeholders),
    ):
        if items:
            print(f"── {section} ──")
            for it in items:
                print(f"  {it}")
            print()

    return 1 if unresolved else 0


if __name__ == "__main__":
    sys.exit(main())
