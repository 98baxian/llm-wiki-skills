#!/usr/bin/env python3
"""
build_index.py — Auto-generate wiki/index.md and wiki/index/<domain>.md.

Usage:
    python3 build_index.py <wiki-root>

Example:
    python3 build_index.py ~/wikis/ai-research

Generates:
  - wiki/index.md         — lightweight master catalog (domain anchors + top-5 per domain)
  - wiki/index/<domain>.md — full domain catalog with sortable tables

This script replaces manual maintenance of wiki/index.md.
Run it after every ingest, compile, or structural change.
"""

import re
import sys
import argparse
from pathlib import Path
from datetime import date
from collections import defaultdict


TODAY = date.today().isoformat()


def parse_frontmatter(path: Path) -> dict:
    """Parse YAML frontmatter from a markdown file."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    fm: dict = {}
    for line in text[3:end].splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            k = k.strip()
            v = v.strip().strip("\"'[]")
            fm[k] = v
    return fm


def load_domains(root: Path) -> list[str]:
    """Load the domain list from CLAUDE.md's `## Scope` → 'What this wiki covers:' bullets.

    CLAUDE.md is the single config + schema file (there is no separate wiki-config.md).
    """
    claude = root / "CLAUDE.md"
    if not claude.exists():
        return ["General"]
    try:
        text = claude.read_text(encoding="utf-8")
        domains: list[str] = []
        in_covers = False
        for line in text.splitlines():
            s = line.strip()
            if s.lower().startswith("what this wiki covers"):
                in_covers = True
                continue
            if in_covers:
                if s.startswith("- "):
                    d = s[2:].strip()
                    if d and not d.startswith("<"):    # skip placeholder bullets
                        domains.append(d)
                elif s.lower().startswith("what this wiki") or s.startswith("#"):
                    break                              # 'excludes' list or next section
        return domains if domains else ["General"]
    except Exception:
        return ["General"]


def slug_to_domain(slug: str, all_domains: list[str], fm: dict) -> str:
    """Determine the domain for a page from its frontmatter or slug."""
    explicit = fm.get("domain", "").strip().strip("\"'")
    if explicit:
        for d in all_domains:
            if d.lower() == explicit.lower():
                return d
        return explicit

    tags = fm.get("tags", "")
    for d in all_domains:
        if d.lower() in tags.lower() or d.lower().replace(" ", "-") in slug.lower():
            return d

    return all_domains[0] if all_domains else "General"


def superseded_ref(fm: dict) -> str:
    """The page a ``superseded_by`` field points to, as ``category/slug`` ("" if none).
    parse_frontmatter() has already stripped quotes and [[ ]]; drop a trailing comment,
    a ``wiki/`` prefix, ``.md``, an alias and a heading."""
    v = re.sub(r"(^|\s)#.*$", "", fm.get("superseded_by", "")).strip().strip("\"'[]").strip()
    v = v.split("|")[0].split("#")[0].strip()
    if v.startswith("wiki/"):
        v = v[len("wiki/"):]
    if v.endswith(".md"):
        v = v[:-3]
    return v.strip("/")


def page_link(p: dict, in_table: bool = True) -> str:
    """Wikilink to a page; a superseded page also names its replacement."""
    sep = "\\|" if in_table else "|"
    link = f"[[{p['rel']}{sep}{p['title']}]]"
    if p.get("superseded_by"):
        link += f" *(superseded → [[{p['superseded_by']}]])*"
    return link


def current(pages: list[dict]) -> list[dict]:
    """Pages that are not superseded."""
    return [p for p in pages if not p.get("superseded_by")]


def collect_pages(wiki_path: Path, all_domains: list[str]) -> dict:
    """Collect all wiki pages grouped by type and domain."""
    pages: dict[str, list[dict]] = {
        "concepts": [],
        "entities": [],
        "sources": [],
        "synthesis": [],
    }

    for subdir in ("concepts", "entities", "sources", "synthesis"):
        target = wiki_path / subdir
        if not target.exists():
            continue
        for f in sorted(target.rglob("*.md")):
            if "templates" in f.parts:
                continue
            fm = parse_frontmatter(f)
            if not fm:
                continue
            page_type = fm.get("type", "")
            if page_type in ("system-index", "system-overview", "system-questions",
                             "domain-index", "lint-report"):
                continue
            rel = str(f.relative_to(wiki_path).with_suffix(""))
            slug = f.stem
            title = fm.get("title", slug.replace("-", " ").title())
            domain = slug_to_domain(slug, all_domains, fm)
            pages[subdir].append({
                "slug": slug,
                "rel": rel,
                "title": title,
                "domain": domain,
                "confidence": fm.get("confidence", "low"),
                "source_count": fm.get("source_count", "0"),
                "last_reviewed": fm.get("last_reviewed", fm.get("updated", fm.get("date", ""))),
                "domain_volatility": fm.get("domain_volatility", "medium"),
                "entity_type": fm.get("entity_type", ""),
                "date": fm.get("date", ""),
                "superseded_by": superseded_ref(fm),
            })

    return pages


def build_domain_catalog(wiki_path: Path, domain: str, pages: dict,
                         all_domains: list[str]) -> str:
    """Generate the full domain catalog markdown."""
    domain_slug = re.sub(r"[^a-z0-9]+", "-", domain.lower()).strip("-")

    concepts = [p for p in pages["concepts"] if p["domain"] == domain]
    entities = [p for p in pages["entities"] if p["domain"] == domain]
    sources = [p for p in pages["sources"] if p["domain"] == domain]
    synthesis = [p for p in pages["synthesis"] if p["domain"] == domain]

    lines = [
        "---",
        f"type: domain-index",
        f"domain: {domain_slug}",
        "graph-excluded: true",
        f"updated: {TODAY}",
        "generated_by: scripts/build_index.py",
        "---",
        "",
        f"# {domain} Domain Index",
        "",
    ]

    # Concepts table
    lines += [f"## Concepts ({len(concepts)})", ""]
    if concepts:
        concepts_sorted = sorted(concepts, key=lambda x: (-int(x["source_count"] or 0), x["slug"]))
        lines += ["| Page | Confidence | Last Reviewed | Sources | Volatility |",
                  "|------|-----------|---------------|---------|------------|"]
        for p in concepts_sorted:
            lines.append(
                f"| {page_link(p)} "
                f"| {p['confidence']} "
                f"| {p['last_reviewed'] or '—'} "
                f"| {p['source_count']} "
                f"| {p['domain_volatility']} |"
            )
    else:
        lines.append("*(none yet)*")

    lines.append("")

    # Entities table
    lines += [f"## Entities ({len(entities)})", ""]
    if entities:
        entities_sorted = sorted(entities, key=lambda x: (x["entity_type"], x["slug"]))
        lines += ["| Page | Type | Date |",
                  "|------|------|------|"]
        for p in entities_sorted:
            lines.append(
                f"| {page_link(p)} "
                f"| {p['entity_type'] or '—'} "
                f"| {p['date'] or '—'} |"
            )
    else:
        lines.append("*(none yet)*")

    lines.append("")

    # Sources table
    lines += [f"## Sources ({len(sources)})", ""]
    if sources:
        sources_sorted = sorted(sources, key=lambda x: x.get("date", ""), reverse=True)
        lines += ["| Page | Date |",
                  "|------|------|"]
        for p in sources_sorted:
            lines.append(
                f"| {page_link(p)} "
                f"| {p['date'] or '—'} |"
            )
    else:
        lines.append("*(none yet)*")

    lines.append("")

    # Synthesis
    if synthesis:
        lines += [f"## Synthesis ({len(synthesis)})", ""]
        for p in synthesis:
            lines.append(f"- {page_link(p, in_table=False)}")
        lines.append("")

    return "\n".join(lines)


def build_master_index(wiki_path: Path, pages: dict, all_domains: list[str]) -> str:
    """Generate the lightweight master index.md."""
    total_concepts = len(pages["concepts"])
    total_sources = len(pages["sources"])
    total_entities = len(pages["entities"])
    total_synthesis = len(pages["synthesis"])

    # Count per domain
    domain_counts: dict[str, dict[str, int]] = {
        d: {"concepts": 0, "entities": 0, "sources": 0, "synthesis": 0}
        for d in all_domains
    }
    for ptype in ("concepts", "entities", "sources", "synthesis"):
        for p in pages[ptype]:
            d = p["domain"]
            if d in domain_counts:
                domain_counts[d][ptype] += 1

    lines = [
        "---",
        "type: system-index",
        "graph-excluded: true",
        f"updated: {TODAY}",
        "generated_by: scripts/build_index.py",
        "---",
        "",
        "# Knowledge Base Index",
        "",
        f"> Sources: {total_sources} | Concepts: {total_concepts} "
        f"| Entities: {total_entities} | Synthesis: {total_synthesis}",
        "",
        "## Domains",
        "",
    ]

    # Domain navigation table
    lines += ["| Domain | Concepts | Entities | Sources |",
              "|--------|----------|----------|---------|"]
    for d in all_domains:
        dc = domain_counts.get(d, {})
        d_slug = re.sub(r"[^a-z0-9]+", "-", d.lower()).strip("-")
        lines.append(
            f"| [index/{d_slug}](index/{d_slug}.md) "
            f"| {dc.get('concepts', 0)} "
            f"| {dc.get('entities', 0)} "
            f"| {dc.get('sources', 0)} |"
        )

    lines.append("")

    # Top 5 concepts per domain (by source_count)
    lines += ["## Top Concepts by Domain", ""]
    for d in all_domains:
        domain_concepts = [p for p in current(pages["concepts"]) if p["domain"] == d]
        if not domain_concepts:
            continue
        top5 = sorted(domain_concepts,
                      key=lambda x: -int(x["source_count"] or 0))[:5]
        wikilinks = " · ".join(f"[[{p['rel']}|{p['title']}]]" for p in top5)
        lines.append(f"**{d}:** {wikilinks}")
        lines.append("")

    # Recent sources (last 7 ingested by date)
    all_sources = sorted(current(pages["sources"]), key=lambda x: x.get("date", ""),
                         reverse=True)[:7]
    if all_sources:
        lines += ["## Recent Sources", ""]
        for p in all_sources:
            lines.append(f"- [[{p['rel']}|{p['title']}]] — {p['date'] or '—'}")
        lines.append("")

    # Synthesis
    if current(pages["synthesis"]):
        lines += ["## Synthesis", ""]
        for p in current(pages["synthesis"])[:10]:
            lines.append(f"- [[{p['rel']}|{p['title']}]]")
        lines.append("")

    lines += ["## Open Questions",
              "*(see wiki/QUESTIONS.md)*"]

    return "\n".join(lines)


def build_index(root: str) -> int:
    root_path = Path(root)
    wiki_path = root_path / "wiki"

    if not wiki_path.exists():
        print(f"ERROR: wiki/ directory not found at {wiki_path}", file=sys.stderr)
        return 1

    all_domains = load_domains(root_path)
    pages = collect_pages(wiki_path, all_domains)

    total = sum(len(v) for v in pages.values())
    print(f"Found {total} wiki pages across {len(all_domains)} domain(s)")

    # Write domain catalogs
    index_dir = wiki_path / "index"
    index_dir.mkdir(exist_ok=True)
    for domain in all_domains:
        catalog = build_domain_catalog(wiki_path, domain, pages, all_domains)
        d_slug = re.sub(r"[^a-z0-9]+", "-", domain.lower()).strip("-")
        catalog_path = index_dir / f"{d_slug}.md"
        catalog_path.write_text(catalog, encoding="utf-8")
        dc = sum(
            1 for ptype in ("concepts", "entities", "sources", "synthesis")
            for p in pages[ptype] if p["domain"] == domain
        )
        print(f"  ✓ wiki/index/{d_slug}.md — {dc} entries")

    # Write master index
    master = build_master_index(wiki_path, pages, all_domains)
    index_path = wiki_path / "index.md"
    index_path.write_text(master, encoding="utf-8")
    print(f"  ✓ wiki/index.md — {len(all_domains)} domains, {total} total pages")

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Auto-generate wiki/index.md and wiki/index/<domain>.md.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("wiki_root", help="Path to the wiki root directory")
    args = parser.parse_args()
    sys.exit(build_index(args.wiki_root))


if __name__ == "__main__":
    main()
