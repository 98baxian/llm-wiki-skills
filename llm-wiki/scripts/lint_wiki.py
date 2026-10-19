#!/usr/bin/env python3
"""
lint_wiki.py — Health check for an LLM Wiki (12+ passes).

Usage:
    python3 lint_wiki.py <wiki-root>
    python3 lint_wiki.py <wiki-root> --structural   # Skip uncited-claims check
    python3 lint_wiki.py <wiki-root> --stale-only   # Only report stale pages (fast)

Example:
    python3 lint_wiki.py ~/wikis/ai-research
    python3 lint_wiki.py ~/wikis/ai-research --stale-only

Checks:
  1. YAML frontmatter     — missing type/date fields; duplicate keys (invalid YAML)
  2. Broken wikilinks     — [[Target]] where Target.md doesn't exist
  3. Index consistency    — wiki pages not listed in wiki/index.md
  4. Stub pages           — articles under 80 words
  5. SHA-256 integrity    — raw file modified since ingest
  6. Stale pages          — last reviewed beyond domain_volatility threshold
  7. Orphan pages         — wiki pages with no inbound wikilinks
  8. Near-duplicate names — concept slugs with Jaccard > 0.7
  9. Uncited claims       — factual sentences without (raw/..., Lxx) or [synthesis]
 10. log/ shape           — filename and H1 validation
 11. audit/ shape         — YAML frontmatter validation
 12. Audit targets        — every open audit's target file exists
 13. Claims verification  — verbatim strings in claims_requiring_verification exist in raw source
 14. Citation format      — inline ([[sources/...]], Lxx) citations that should point to raw/
 15. Duplicate list links — a wikilink repeated in a Concepts/Entities Extracted or Sources section

Exit codes:
  0 — no issues found
  1 — issues found
"""

import re
import sys
import hashlib
import datetime
import argparse
from pathlib import Path
from collections import defaultdict

import textnorm


# ── Constants ─────────────────────────────────────────────────────────────────

SYSTEM_FILES = {"index.md", "log.md", "overview.md", "QUESTIONS.md", "hot.md"}
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?]]")
LOG_FILENAME_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})\.md$")
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
CITATION_RE = re.compile(r"\(raw/[^)]+,\s*(L\d|p\.\d)|location unknown\)|\[synthesis\]")
FACTUAL_LINE_RE = re.compile(r"^[-•]\s.{50,}$|^\d+\.\s.{50,}$")
# A source-page wikilink used as an inline citation, e.g. "([[sources/foo]], L10)".
# Citations must point to raw/ evidence, not a compiled summary page (see check 14).
SRC_WIKILINK_CITATION_RE = re.compile(r"\(\s*\[\[sources/[^\]]+\]\]\s*,\s*(?:L\d|p\.\d)")
# Section headers (lowercased, no leading #) whose bullet wikilinks must be unique.
DEDUP_LIST_SECTIONS = {"concepts extracted", "entities extracted", "sources"}
LIST_LINK_RE = re.compile(r"^[-*]\s*\[\[([^\]|#]+)")
# Format: "verbatim text" (raw/path/file.md, L42)
CLAIM_ENTRY_RE = re.compile(r'^"([^"]+)"\s*\(([^,)]+),\s*(L\d+[^)]*|p\.\d+[^)]*)\)')

AUDIT_REQUIRED_FIELDS = {
    "id", "target", "target_lines", "anchor_before", "anchor_text",
    "anchor_after", "severity", "author", "source", "created", "status",
}
VALID_SEVERITIES = {"info", "suggest", "warn", "error"}
VALID_STATUSES = {"open", "resolved"}
VALID_SOURCES = {"obsidian-plugin", "web-viewer", "manual"}

TODAY = datetime.date.today()
TODAY_ISO = TODAY.isoformat()


# ── Issue tracking ────────────────────────────────────────────────────────────

errors: list[str] = []
warnings: list[str] = []
suggestions: list[str] = []


def log_issue(level: str, check: str, msg: str) -> None:
    entry = f"[{level}] ({check}) {msg}"
    if level == "ERROR":
        errors.append(entry)
    elif level == "WARNING":
        warnings.append(entry)
    else:
        suggestions.append(entry)


# ── Utilities ─────────────────────────────────────────────────────────────────

def parse_frontmatter(path: Path) -> tuple[dict, str]:
    """Parse YAML frontmatter from a markdown file. Returns (fields, body)."""
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm = {}
    for line in text[3:end].splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip().strip("\"'")
    return fm, text[end + 4:]


def wiki_files(wiki_dir: Path) -> list[Path]:
    """List all non-system, non-template, non-index wiki markdown files."""
    return [
        f for f in wiki_dir.rglob("*.md")
        if f.name not in SYSTEM_FILES
        and "templates" not in f.parts
        and "index" not in [p.name for p in f.parents if p != wiki_dir]
    ]


def wikilinks(text: str) -> list[str]:
    """Extract all wikilink targets from text.

    Links inside HTML comments are template hints (e.g. the
    `<!-- - [[concepts/concept-slug]] -->` scaffolding in source-template.md),
    not real edges — strip comments first so they are not flagged as broken.
    """
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    return WIKILINK_RE.findall(text)


def sha256_file(path: Path) -> str:
    """Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def jaccard(a: str, b: str) -> float:
    """Character-level Jaccard similarity between two strings."""
    sa = set(a.replace("-", ""))
    sb = set(b.replace("-", ""))
    union = sa | sb
    return len(sa & sb) / len(union) if union else 0.0


def load_staleness_thresholds(root: Path) -> dict[str, int]:
    """Staleness thresholds in days. Built-in defaults — CLAUDE.md is the single config
    and these are not per-vault tunable; edit here if a vault ever needs different values."""
    return {"high": 90, "medium": 180, "low": 365}


# ── Check 1: YAML Frontmatter ────────────────────────────────────────────────

def check_frontmatter(wiki_dir: Path, root: Path) -> None:
    for f in wiki_files(wiki_dir):
        text = f.read_text(encoding="utf-8", errors="replace")
        if not text.startswith("---"):
            log_issue("ERROR", "frontmatter",
                      f"{f.relative_to(root)} — missing YAML frontmatter")
            continue

        # Duplicate top-level keys make the block invalid YAML — strict parsers and
        # Obsidian reject it outright, so the page won't render. parse_frontmatter()
        # silently last-wins over duplicates, so detect them from the raw lines here.
        # Count only top-level keys: skip indented lines (nested/list values), list
        # items ("- ..."), comments ("#"), and lines without a colon.
        end = text.find("\n---", 3)
        seen: dict[str, int] = defaultdict(int)
        if end != -1:
            for line in text[3:end].splitlines():
                if not line or line[0] in (" ", "\t", "#", "-") or ":" not in line:
                    continue
                seen[line.split(":", 1)[0].strip()] += 1
        dups = sorted(k for k, n in seen.items() if n > 1)
        if dups:
            log_issue("ERROR", "frontmatter",
                      f"{f.relative_to(root)} — duplicate frontmatter keys: "
                      f"{', '.join(dups)} (invalid YAML — page won't render)")

        fm, _ = parse_frontmatter(f)
        for field in ("type", "date"):
            if field not in fm:
                log_issue("WARNING", "frontmatter",
                          f"{f.relative_to(root)} — missing '{field}'")


# ── Check 2: Broken Wikilinks ────────────────────────────────────────────────

def check_broken_links(wiki_dir: Path, root: Path) -> None:
    # Resolve links by full category/stem, not bare stem — otherwise a link with the
    # right stem but the wrong directory (e.g. [[sources/X]] when X lives in cases/)
    # silently passes. Track where each stem actually lives so wrong-prefix links can
    # be reported as a repairable WARNING (fixable with fix_links.py) rather than missed.
    resolvable: set[str] = set()          # "category/stem" and bare "stem"
    stem_locations: dict[str, set[str]] = defaultdict(set)
    for f in wiki_dir.rglob("*.md"):
        category = f.relative_to(wiki_dir).parent.as_posix()
        category = "" if category == "." else category
        resolvable.add(f"{category}/{f.stem}" if category else f.stem)
        resolvable.add(f.stem)            # bare stem links resolve against any category
        stem_locations[f.stem].add(category)

    for f in wiki_files(wiki_dir):
        _, body = parse_frontmatter(f)
        for link in wikilinks(body):
            target = link.split("|")[0].split("#")[0].strip()
            stem = target.split("/")[-1]
            if f"{stem}.md" in SYSTEM_FILES:
                continue
            if target in resolvable:
                continue
            locs = stem_locations.get(stem)
            if locs:
                log_issue("WARNING", "broken-links",
                          f"{f.relative_to(root)} → [[{link}]] wrong category — "
                          f"'{stem}' lives in {sorted(locs)}; fix prefix or run fix_links.py")
            else:
                log_issue("ERROR", "broken-links",
                          f"{f.relative_to(root)} → [[{link}]] does not exist")


# ── Check 3: Index Consistency ────────────────────────────────────────────────

def check_index(wiki_dir: Path, root: Path) -> None:
    idx = wiki_dir / "index.md"
    if not idx.exists():
        log_issue("ERROR", "index", "wiki/index.md missing")
        return
    # The master index.md is lightweight (top-N per domain); the full per-domain
    # catalogs live in wiki/index/<domain>.md. A page is "indexed" if it appears in
    # either, so concatenate them before checking — otherwise every page beyond a
    # domain's top-N is a false positive.
    index_text = idx.read_text(encoding="utf-8", errors="replace")
    index_dir = wiki_dir / "index"
    if index_dir.is_dir():
        for catalog in index_dir.glob("*.md"):
            index_text += "\n" + catalog.read_text(encoding="utf-8", errors="replace")
    for f in wiki_files(wiki_dir):
        rel = str(f.relative_to(wiki_dir).with_suffix(""))
        if (f"[[{f.stem}]]" not in index_text
                and rel not in index_text
                and f.stem not in index_text):
            log_issue("WARNING", "index",
                      f"{f.relative_to(root)} — not listed in index.md or wiki/index/<domain>.md")


# ── Check 4: Stub Pages ──────────────────────────────────────────────────────

def check_stubs(wiki_dir: Path, root: Path) -> None:
    for f in wiki_files(wiki_dir):
        _, body = parse_frontmatter(f)
        if len(body.split()) < 80:
            log_issue("WARNING", "stubs",
                      f"{f.relative_to(root)} — under 80 words (stub)")


# ── Check 5: SHA-256 Integrity ───────────────────────────────────────────────

def check_sha256(wiki_dir: Path, root: Path) -> list[str]:
    """Return list of raw files that have changed since ingest."""
    modified: list[str] = []
    src_dir = wiki_dir / "sources"
    if not src_dir.exists():
        return modified
    for f in src_dir.glob("*.md"):
        fm, _ = parse_frontmatter(f)
        raw_file = fm.get("raw_file", "")
        stored = fm.get("raw_sha256", "")
        if not raw_file or not stored:
            continue
        raw_path = root / raw_file
        if not raw_path.exists():
            log_issue("WARNING", "sha256",
                      f"{f.relative_to(root)} — raw_file '{raw_file}' not found")
            continue
        if sha256_file(raw_path) != stored:
            log_issue("ERROR", "sha256",
                      f"WARNING SOURCE MODIFIED: {raw_file} — re-ingest required")
            modified.append(raw_file)
    return modified


# ── Check 6: Stale Pages ─────────────────────────────────────────────────────

def check_stale(wiki_dir: Path, root: Path,
                thresholds: dict[str, int]) -> list[str]:
    """Return list of stale page paths (relative to root)."""
    stale: list[str] = []
    concepts_dir = wiki_dir / "concepts"
    if not concepts_dir.exists():
        return stale
    for f in concepts_dir.rglob("*.md"):
        fm, _ = parse_frontmatter(f)
        vol = fm.get("domain_volatility", "medium")
        reviewed = fm.get("last_reviewed", fm.get("updated", fm.get("date", "")))
        if not reviewed:
            continue
        try:
            days = (TODAY - datetime.date.fromisoformat(reviewed)).days
            limit = thresholds.get(vol, 180)
            if days > limit:
                rel = str(f.relative_to(root))
                msg = (f"{rel} — {days}d since review "
                       f"(limit for '{vol}': {limit}d)")
                log_issue("WARNING", "stale", msg)
                stale.append(rel)
        except ValueError:
            pass
    return stale


# ── Check 7: Orphan Pages ────────────────────────────────────────────────────

def check_orphans(wiki_dir: Path, root: Path) -> None:
    inbound: dict[str, int] = defaultdict(int)
    files = wiki_files(wiki_dir)
    for f in files:
        _, body = parse_frontmatter(f)
        for link in wikilinks(body):
            inbound[link.split("/")[-1]] += 1
    for f in files:
        if inbound[f.stem] == 0:
            log_issue("SUGGESTION", "orphans",
                      f"{f.relative_to(root)} — no inbound wikilinks")


# ── Check 8: Near-Duplicate Concept Names ─────────────────────────────────────

def check_near_duplicates(wiki_dir: Path) -> None:
    concepts_dir = wiki_dir / "concepts"
    if not concepts_dir.exists():
        return
    slugs = [f.stem for f in concepts_dir.rglob("*.md")]
    for i, s1 in enumerate(slugs):
        for s2 in slugs[i + 1:]:
            if jaccard(s1, s2) > 0.7:
                log_issue("WARNING", "near-duplicates",
                          f"Similar slugs: '{s1}' vs '{s2}'")


# ── Check 9: Uncited Claims ──────────────────────────────────────────────────

def check_uncited(wiki_dir: Path, root: Path) -> None:
    for subdir in ("concepts", "sources", "entities"):
        target_dir = wiki_dir / subdir
        if not target_dir.exists():
            continue
        for f in target_dir.rglob("*.md"):
            _, body = parse_frontmatter(f)
            # The Evolution Log is a change-log of bookkeeping entries
            # ("- 2026-06-17 (2 sources): ..."), not factual claims — skip it so
            # those bullets aren't flagged as uncited.
            skip_section = False
            for line in body.splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    skip_section = stripped.lstrip("#").strip().lower() == "evolution log"
                    continue
                if skip_section:
                    continue
                if FACTUAL_LINE_RE.match(stripped) and not CITATION_RE.search(stripped):
                    preview = (stripped[:60] + "...") if len(stripped) > 60 else stripped
                    log_issue("WARNING", "uncited",
                              f'{f.relative_to(root)} — possible uncited: "{preview}"')


# ── Check 10: log/ Shape ─────────────────────────────────────────────────────

def check_log_shape(root: Path) -> None:
    log_path = root / "log"
    if not log_path.exists() or not log_path.is_dir():
        log_issue("SUGGESTION", "log-shape", "log/ directory not found")
        return
    for p in sorted(log_path.iterdir()):
        if p.is_dir() or p.name == ".gitkeep":
            continue
        m = LOG_FILENAME_RE.match(p.name)
        if not m:
            log_issue("WARNING", "log-shape",
                      f"{p.relative_to(root)} — filename doesn't match YYYYMMDD.md")
            continue
        y, mo, d = m.groups()
        iso = f"{y}-{mo}-{d}"
        first_line = p.read_text(encoding="utf-8", errors="replace").splitlines()[:1]
        if not first_line or first_line[0].strip() != f"# {iso}":
            log_issue("WARNING", "log-shape",
                      f"{p.relative_to(root)} — expected H1 '# {iso}'")


# ── Check 11: audit/ Shape ───────────────────────────────────────────────────

def check_audit_shape(root: Path) -> list[tuple[str, str]]:
    """Validate audit files and return list of (audit_id, target) for open audits."""
    audit_path = root / "audit"
    targets: list[tuple[str, str]] = []

    if not audit_path.exists() or not audit_path.is_dir():
        return targets

    audit_files = [p for p in audit_path.rglob("*.md") if p.name != ".gitkeep"]
    for p in audit_files:
        text = p.read_text(encoding="utf-8", errors="replace")
        rel = p.relative_to(root)

        fm_match = FRONTMATTER_RE.match(text)
        if not fm_match:
            log_issue("WARNING", "audit-shape", f"{rel} — missing YAML frontmatter")
            continue

        fm: dict[str, str] = {}
        for line in fm_match.group(1).splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                fm[k.strip()] = v.strip().strip("\"'")

        missing = AUDIT_REQUIRED_FIELDS - set(fm.keys())
        if missing:
            log_issue("WARNING", "audit-shape",
                      f"{rel} — missing fields: {', '.join(sorted(missing))}")
            continue

        if fm.get("severity", "") not in VALID_SEVERITIES:
            log_issue("WARNING", "audit-shape",
                      f"{rel} — invalid severity '{fm.get('severity')}'")

        if fm.get("source", "") not in VALID_SOURCES:
            log_issue("WARNING", "audit-shape",
                      f"{rel} — invalid source '{fm.get('source')}'")

        expected_status = "resolved" if "resolved" in str(p) else "open"
        if fm.get("status", "") != expected_status:
            log_issue("WARNING", "audit-shape",
                      f"{rel} — status '{fm.get('status')}' doesn't match "
                      f"directory (expected '{expected_status}')")

        if fm.get("status") == "open":
            targets.append((fm.get("id", "?"), fm.get("target", "")))

    return targets


# ── Helper: parse YAML list field from frontmatter ───────────────────────────

def parse_frontmatter_list(path: Path, key: str) -> list[str]:
    """Extract items from a YAML list field in frontmatter (e.g. claims_requiring_verification)."""
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---"):
        return []
    end = text.find("\n---", 3)
    if end == -1:
        return []
    items: list[str] = []
    in_list = False
    for line in text[3:end].splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{key}:"):
            in_list = True
            continue
        if in_list:
            if stripped.startswith("- "):
                # Unwrap only ONE outer matching quote layer (the YAML scalar quote),
                # preserving any inner quotes — claims_requiring_verification entries are
                # '"verbatim" (raw/path, Lxx)', whose inner double-quote check 13 needs.
                item = stripped[2:].strip()
                if len(item) >= 2 and item[0] == item[-1] and item[0] in "\"'":
                    item = item[1:-1]
                items.append(item)
            elif stripped and not stripped.startswith("#"):
                in_list = False
    return items


# ── Check 12: Audit Targets Exist ─────────────────────────────────────────────

def check_audit_targets(targets: list[tuple[str, str]], root: Path) -> None:
    wiki_path = root / "wiki"
    for audit_id, target in targets:
        target_path = root / target
        alt_path = wiki_path / target
        if not target_path.exists() and not alt_path.exists():
            log_issue("ERROR", "audit-targets",
                      f"Audit {audit_id} → {target} — target file not found")


# ── Check 13: Claims Requiring Verification ──────────────────────────────────

def check_claims_verification(wiki_dir: Path, root: Path) -> None:
    """For each source page, verify every entry in claims_requiring_verification
    appears verbatim in the referenced raw file."""
    src_dir = wiki_dir / "sources"
    if not src_dir.exists():
        return
    for f in src_dir.glob("*.md"):
        claims = parse_frontmatter_list(f, "claims_requiring_verification")
        if not claims:
            continue
        for claim in claims:
            m = CLAIM_ENTRY_RE.match(claim)
            if not m:
                log_issue("WARNING", "claims-verification",
                          f"{f.relative_to(root)} — unparseable claim entry: {claim!r} "
                          f"(expected: \"verbatim text\" (raw/path, Lxx))")
                continue
            verbatim, raw_rel, _ = m.group(1), m.group(2).strip(), m.group(3)
            raw_path = root / raw_rel
            if not raw_path.exists():
                log_issue("WARNING", "claims-verification",
                          f"{f.relative_to(root)} — raw file not found: {raw_rel}")
                continue
            raw_text = raw_path.read_text(encoding="utf-8", errors="replace")
            if not textnorm.contains(raw_text, verbatim):
                log_issue("ERROR", "claims-verification",
                          f"{f.relative_to(root)} — verbatim claim NOT FOUND in {raw_rel}: "
                          f"\"{verbatim}\"")


# ── Check 14: Source-Page Wikilink Citations ─────────────────────────────────

def check_wikilink_citations(wiki_dir: Path, root: Path) -> None:
    """Flag inline citations that point to a [[sources/...]] page instead of the
    raw file, e.g. "(... claim. ([[sources/foo]], L10))". Citations must trace to
    raw/ evidence; the source-page wikilink belongs only in the Sources section."""
    for subdir in ("concepts", "sources", "entities", "comparisons", "synthesis"):
        target_dir = wiki_dir / subdir
        if not target_dir.exists():
            continue
        for f in target_dir.rglob("*.md"):
            _, body = parse_frontmatter(f)
            for line in body.splitlines():
                if SRC_WIKILINK_CITATION_RE.search(line):
                    preview = line.strip()
                    preview = (preview[:60] + "...") if len(preview) > 60 else preview
                    log_issue("WARNING", "citation-format",
                              f'{f.relative_to(root)} — cite raw file, not source page: '
                              f'"{preview}"')


# ── Check 15: Duplicate List Entries ─────────────────────────────────────────

def check_duplicate_list_entries(wiki_dir: Path, root: Path) -> None:
    """Flag a wikilink listed more than once in a Concepts/Entities Extracted or
    Sources section — each target should appear exactly once."""
    for f in wiki_files(wiki_dir):
        _, body = parse_frontmatter(f)
        section = None
        seen: set[str] = set()
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                name = stripped.lstrip("#").strip().lower()
                section = name if name in DEDUP_LIST_SECTIONS else None
                seen = set()
                continue
            if section is None:
                continue
            m = LIST_LINK_RE.match(stripped)
            if m:
                target = m.group(1).strip()
                if target in seen:
                    log_issue("WARNING", "duplicate-link",
                              f"{f.relative_to(root)} — '{section}' lists "
                              f"[[{target}]] more than once")
                else:
                    seen.add(target)


# ── Stale-Only Mode ───────────────────────────────────────────────────────────

def stale_only(root: str) -> int:
    """Fast mode: only report stale concept pages. Used by session-start routine."""
    root_path = Path(root)
    wiki_path = root_path / "wiki"
    thresholds = load_staleness_thresholds(root_path)

    if not wiki_path.exists():
        print(f"ERROR: wiki/ directory not found at {wiki_path}", file=sys.stderr)
        return 1

    concepts_dir = wiki_path / "concepts"
    if not concepts_dir.exists():
        print("No concept pages found.")
        return 0

    stale: list[tuple[str, int, str]] = []
    for f in concepts_dir.rglob("*.md"):
        fm, _ = parse_frontmatter(f)
        vol = fm.get("domain_volatility", "medium")
        reviewed = fm.get("last_reviewed", fm.get("updated", fm.get("date", "")))
        if not reviewed:
            continue
        try:
            days = (TODAY - datetime.date.fromisoformat(reviewed)).days
            limit = thresholds.get(vol, 180)
            if days > limit:
                stale.append((str(f.relative_to(root_path)), days, vol))
        except ValueError:
            continue

    if not stale:
        print("✓ No stale concept pages found.")
        return 0

    stale.sort(key=lambda x: -x[1])
    print(f"⚠️  {len(stale)} stale concept page(s):\n")
    for path, days, vol in stale:
        print(f"  {days:>4}d overdue  [{vol}]  {path}")

    print(f"\nRun full lint for all checks: python3 scripts/lint_wiki.py {root}")
    return 1


# ── Report ────────────────────────────────────────────────────────────────────

def lint(root: str, structural: bool = False) -> int:
    root_path = Path(root)
    wiki_path = root_path / "wiki"
    outputs_path = root_path / "outputs"
    outputs_path.mkdir(parents=True, exist_ok=True)
    thresholds = load_staleness_thresholds(root_path)

    if not wiki_path.exists():
        print(f"ERROR: wiki/ directory not found at {wiki_path}", file=sys.stderr)
        return 1

    print("Running health checks...\n")

    check_frontmatter(wiki_path, root_path)
    check_broken_links(wiki_path, root_path)
    check_index(wiki_path, root_path)
    check_stubs(wiki_path, root_path)
    modified_sources = check_sha256(wiki_path, root_path)
    check_stale(wiki_path, root_path, thresholds)
    check_orphans(wiki_path, root_path)
    check_near_duplicates(wiki_path)
    if not structural:
        check_uncited(wiki_path, root_path)
    check_log_shape(root_path)
    audit_targets = check_audit_shape(root_path)
    check_audit_targets(audit_targets, root_path)
    check_claims_verification(wiki_path, root_path)
    check_wikilink_citations(wiki_path, root_path)
    check_duplicate_list_entries(wiki_path, root_path)

    checks_run = "10 (structural)" if structural else "15+ (full)"
    report_path = outputs_path / f"lint-{TODAY_ISO}.md"

    lines = [
        "---", "type: lint-report", "graph-excluded: true",
        f"date: {TODAY_ISO}", "---",
        "", f"# Wiki Health Report — {TODAY_ISO}",
        "", f"Checks run: {checks_run}",
        "", f"## Errors ({len(errors)})", "",
    ]
    lines += errors or ["None."]
    lines += ["", f"## Warnings ({len(warnings)})", ""]
    lines += warnings or ["None."]
    lines += ["", f"## Suggestions ({len(suggestions)})", ""]
    lines += suggestions or ["None."]

    if modified_sources:
        lines += [
            "",
            "## Modified Source Files",
            "Run `cascade_flag.py` to propagate FIX comments to affected wiki pages:",
            "```bash",
            f"python3 scripts/cascade_flag.py {root}",
            "```",
            "",
        ]
        for src in modified_sources:
            lines.append(f"- {src}")

    report_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"Lint complete — {len(errors)} errors · {len(warnings)} warnings · "
          f"{len(suggestions)} suggestions")
    print(f"Report: {report_path.relative_to(root_path)}")

    for e in errors[:5]:
        print(f"  {e}")
    if len(errors) > 5:
        print(f"  ... and {len(errors) - 5} more. See full report.")

    if modified_sources:
        print(f"\n⚠️  {len(modified_sources)} source file(s) modified since ingest.")
        print("   Run: python3 scripts/cascade_flag.py", root)

    return 0 if len(errors) == 0 else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Health check for an LLM Wiki.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("wiki_root", help="Path to the wiki root directory")
    parser.add_argument("--structural", action="store_true",
                        help="Skip uncited-claims check (faster)")
    parser.add_argument("--stale-only", action="store_true",
                        help="Only report stale pages (fast; used by session-start)")

    args = parser.parse_args()

    if args.stale_only:
        sys.exit(stale_only(args.wiki_root))
    else:
        sys.exit(lint(args.wiki_root, structural=args.structural))


if __name__ == "__main__":
    main()
