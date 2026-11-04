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
  6. Stale pages          — concept last reviewed beyond domain_volatility threshold,
                            or any page past its stale_after date (superseded pages skipped)
  7. Orphan pages         — wiki pages with no inbound wikilinks
  8. Near-duplicate names — concept slugs with Jaccard > 0.7
  9. Uncited claims       — factual sentences without (raw/..., Lxx) or [synthesis]
 10. log/ shape           — filename and H1 validation
 11. audit/ shape         — YAML frontmatter validation
 12. Audit targets        — every open audit's target file exists
 13. Claims verification  — verbatim strings in claims_requiring_verification exist in raw source
 14. Citation format      — inline ([[sources/...]], Lxx) citations that should point to raw/
 15. Duplicate list links — a wikilink repeated in a Concepts/Entities Extracted or Sources section
 16. Quote verification   — quoted passages occur in the cited raw file (opt-in: --verify-quotes)
 17. Supersession         — superseded_by targets exist; no self-links, cycles or chains
 18. Contradictions       — every Contradictions entry cites both sides; open ones are listed

Exit codes:
  0 — no issues found
  1 — issues found
"""

from __future__ import annotations

import re
import sys
import hashlib
import json
import datetime
import argparse
from pathlib import Path
from collections import defaultdict

import citations
import textnorm
import vault_config


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

# Lifecycle fields (optional on any page; see schema-guide § Lifecycle fields).
FM_TRAILING_COMMENT_RE = re.compile(r"\s+#.*$")
# Contradictions sections (check 18).
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
LIST_ITEM_RE = re.compile(r"^(?:[-*+]|\d+[.)])\s+")
TABLE_SEPARATOR_RE = re.compile(r"^\|?[\s:|-]+\|?$")
SOURCE_LINK_RE = re.compile(r"\[\[\s*(sources/[^\]|#]+)")
CONTRADICTION_PLACEHOLDERS = {
    "none", "none.", "(none)", "*(none)*", "_none_", "none yet", "(none yet)", "*(none yet)*",
    "n/a", "-", "—", "无", "无。", "暂无",
}

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


def fm_scalar(fm: dict, key: str) -> str:
    """A frontmatter scalar without a trailing ``# comment`` or surrounding quotes."""
    return FM_TRAILING_COMMENT_RE.sub("", fm.get(key, "")).strip().strip("\"'").strip()


def page_ref(value: str) -> str:
    """Normalize a page reference — ``[[concepts/x|X]]``, ``wiki/concepts/x.md``,
    ``concepts/x`` or a bare ``x`` — to ``concepts/x`` / ``x``."""
    v = value.strip().strip("\"'").strip()
    if v.startswith("[[") and v.endswith("]]"):
        v = v[2:-2]
    v = v.split("|")[0].split("#")[0].strip()
    if v.startswith("wiki/"):
        v = v[len("wiki/"):]
    if v.endswith(".md"):
        v = v[:-3]
    return v.strip("/")


def page_catalog(wiki_dir: Path) -> tuple[dict[str, Path], dict[str, list[str]]]:
    """Wiki pages keyed by ``category/stem``, plus stem → [category/stem, ...]."""
    by_rel: dict[str, Path] = {}
    by_stem: dict[str, list[str]] = defaultdict(list)
    for f in wiki_files(wiki_dir):
        rel = f.relative_to(wiki_dir).with_suffix("").as_posix()
        by_rel[rel] = f
        by_stem[f.stem].append(rel)
    return by_rel, by_stem


def resolve_page(ref: str, by_rel: dict[str, Path],
                 by_stem: dict[str, list[str]]) -> tuple[str | None, str]:
    """Resolve a normalized reference. Returns (category/stem, problem) where problem
    is "" (resolved), "missing" or "ambiguous" (a bare stem that lives in several places)."""
    if ref in by_rel:
        return ref, ""
    if "/" not in ref:
        hits = by_stem.get(ref, [])
        if len(hits) == 1:
            return hits[0], ""
        if len(hits) > 1:
            return None, "ambiguous"
    return None, "missing"


def collect_stale(wiki_dir: Path, root: Path,
                  thresholds: dict[str, int]) -> list[tuple[str, int, str, str]]:
    """Every stale page as (path relative to root, days, kind, detail).

    kind "review":      a concept whose last review is older than its domain_volatility
                        limit — days = days since review, detail = the volatility tier.
    kind "stale_after": any page whose stale_after date has passed — days = days past
                        the date, detail = the date.
    Pages with a superseded_by are retired and never stale. An unparseable stale_after
    is reported by check 1, not here."""
    stale: list[tuple[str, int, str, str]] = []
    for f in sorted(wiki_files(wiki_dir)):
        fm, _ = parse_frontmatter(f)
        if fm_scalar(fm, "superseded_by"):
            continue
        rel = str(f.relative_to(root))
        if f.relative_to(wiki_dir).parts[0] == "concepts":
            vol = fm.get("domain_volatility", "medium")
            reviewed = fm.get("last_reviewed", fm.get("updated", fm.get("date", "")))
            if reviewed:
                try:
                    days = (TODAY - datetime.date.fromisoformat(reviewed)).days
                    if days > thresholds.get(vol, 180):
                        stale.append((rel, days, "review", vol))
                except ValueError:
                    pass
        expiry = fm_scalar(fm, "stale_after")
        if expiry:
            try:
                days = (TODAY - datetime.date.fromisoformat(expiry)).days
            except ValueError:
                continue
            if days > 0:
                stale.append((rel, days, "stale_after", expiry))
    return stale


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

        expiry = fm_scalar(fm, "stale_after")
        if expiry:
            try:
                datetime.date.fromisoformat(expiry)
            except ValueError:
                log_issue("WARNING", "frontmatter",
                          f"{f.relative_to(root)} — stale_after '{expiry}' is not a "
                          f"YYYY-MM-DD date (ignored by the staleness check)")


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


# ── Check 5b: Converted PDFs ──────────────────────────────────────────────────

def check_conversion_state(wiki_dir: Path, root: Path) -> None:
    """Source stubs that were ingested from a PDF (``source_pdf`` in frontmatter):
    warn when the PDF changed or vanished since conversion, and note lossy or
    low-confidence conversions."""
    src_dir = wiki_dir / "sources"
    if not src_dir.exists():
        return
    for f in sorted(src_dir.glob("*.md")):
        fm, _ = parse_frontmatter(f)
        pdf_rel = fm.get("source_pdf", "")
        if not pdf_rel:
            continue
        rel = f.relative_to(root)
        pdf_path = root / pdf_rel
        if not pdf_path.exists():
            log_issue("WARNING", "sha256", f"{rel} — source_pdf '{pdf_rel}' not found")
        elif fm.get("source_pdf_sha256") and sha256_file(pdf_path) != fm["source_pdf_sha256"]:
            log_issue("WARNING", "sha256",
                      f"{rel} — converted copy is stale: {pdf_rel} changed since conversion; "
                      f"re-run: python3 scripts/convert_source.py {root} {pdf_rel} --force")
        if fm.get("source_lossy", "").lower() == "true":
            log_issue("INFO", "conversion", f"{rel} — cited raw is a lossy conversion (pdftotext)")
        raw_file = fm.get("raw_file", "")
        pages_json = (root / raw_file).with_suffix(".pages.json") if raw_file else None
        if pages_json and pages_json.exists():
            try:
                confidence = json.loads(pages_json.read_text(encoding="utf-8")).get("confidence")
            except (OSError, ValueError):
                confidence = None
            if confidence == "low":
                log_issue("INFO", "conversion", f"{rel} — page translation unreliable for {raw_file}")


# ── Check 6: Stale Pages ─────────────────────────────────────────────────────

def check_stale(wiki_dir: Path, root: Path,
                thresholds: dict[str, int]) -> list[str]:
    """Return list of stale page paths (relative to root)."""
    stale: list[str] = []
    for rel, days, kind, detail in collect_stale(wiki_dir, root, thresholds):
        if kind == "review":
            msg = (f"{rel} — {days}d since review "
                   f"(limit for '{detail}': {thresholds.get(detail, 180)}d)")
        else:
            msg = (f"{rel} — past stale_after {detail} ({days}d ago); re-verify "
                   f"against raw/ and set a new stale_after")
        log_issue("WARNING", "stale", msg)
        if rel not in stale:
            stale.append(rel)
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


# ── Check 16: Quote Verification (opt-in: --verify-quotes) ───────────────────

UNVERIFIABLE_SHARE_WARNING = 0.10


def check_quote_verification(root: Path) -> None:
    """Quoted passages must occur in the raw file they cite (see verify_quotes.py).
    ERROR = not found; WARNING = fuzzy / wrong place / near miss / downgraded; INFO = unverifiable."""
    import verify_quotes

    report = verify_quotes.verify_wiki(root)
    level = {"error": "ERROR", "warning": "WARNING", "suggestion": "INFO"}
    for r in report["results"]:
        if r["severity"] in level:
            note = f" — {r['note']}" if r["note"] else ""
            log_issue(level[r["severity"]], "quote-verification",
                      f"{r['page']}:{r['line']} {r['verdict']} in {r['raw']} ({r['loc']}): "
                      f"\"{r['quote'][:60]}\"{note}")
    c = report["counts"]
    cited = sum(c[k] for k in ("exact", "fuzzy", "wrong_location", "near_miss", "not_found_error",
                               "not_found_warning", "unverifiable_infrastructure", "unverifiable_cross_language"))
    if cited and c["unverifiable_infrastructure"] / cited > UNVERIFIABLE_SHARE_WARNING:
        log_issue("WARNING", "quote-verification",
                  f"{c['unverifiable_infrastructure']} of {cited} cited quotes "
                  f"({100 * c['unverifiable_infrastructure'] // cited}%) could not be checked "
                  "(missing, PDF or unreadable raw files)")


# ── Check 17: Supersession ───────────────────────────────────────────────────

def check_supersession(wiki_dir: Path, root: Path) -> None:
    """A page with ``superseded_by`` is retired in favour of another page. The target
    must exist, must not be the page itself, and must be current: a chain (A → B → C)
    should point straight at C, and a cycle leaves no current page at all."""
    by_rel, by_stem = page_catalog(wiki_dir)
    successor: dict[str, str] = {}
    for rel, f in sorted(by_rel.items()):
        fm, _ = parse_frontmatter(f)
        value = fm_scalar(fm, "superseded_by")
        if not value:
            continue
        shown = f.relative_to(root)
        ref = page_ref(value)
        target, problem = resolve_page(ref, by_rel, by_stem)
        if problem == "missing":
            log_issue("ERROR", "supersession",
                      f"{shown} — superseded_by '{value}' does not exist")
        elif problem == "ambiguous":
            log_issue("WARNING", "supersession",
                      f"{shown} — superseded_by '{value}' matches {sorted(by_stem[ref])}; "
                      f"use the category/slug form")
        elif target == rel:
            log_issue("ERROR", "supersession", f"{shown} — superseded_by points to itself")
        else:
            successor[rel] = target

    reported_cycles: set[frozenset] = set()
    for start in sorted(successor):
        path = [start]
        current = successor[start]
        while current in successor and current not in path:
            path.append(current)
            current = successor[current]
        if current in path:
            cycle = path[path.index(current):]
            if frozenset(cycle) not in reported_cycles:
                reported_cycles.add(frozenset(cycle))
                chain = " → ".join(f"[[{p}]]" for p in cycle + [current])
                log_issue("ERROR", "supersession",
                          f"superseded_by cycle {chain} — no current page; "
                          f"clear superseded_by on the page that should stay current")
        elif len(path) > 1:
            log_issue("WARNING", "supersession",
                      f"{by_rel[start].relative_to(root)} — superseded_by [[{successor[start]}]] "
                      f"is itself superseded; point to [[{current}]]")


# ── Check 18: Contradictions ─────────────────────────────────────────────────

def contradiction_entries(body: str) -> list[str]:
    """The entries of a page's Contradictions section(s), one string each.

    An entry is a top-level list item (indented continuation lines and nested bullets
    folded in), a table body row, or a plain paragraph. HTML comments, table header
    rows and placeholders ("None", "无", ...) are dropped. A heading counts as a
    Contradictions section when its text contains "contradiction" (any case)."""
    body = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
    entries: list[list[str]] = []
    in_section = False
    section_level = 0
    current: list[str] | None = None     # entry that continuation lines join
    last_item: list[str] | None = None   # last list item, for indented lines after a blank
    for line in body.splitlines():
        stripped = line.strip()
        heading = HEADING_RE.match(stripped)
        if heading:
            level = len(heading.group(1))
            if in_section and level > section_level:
                current = last_item = None         # sub-heading inside the section
                continue
            in_section = "contradiction" in heading.group(2).lower()
            section_level = level
            current = last_item = None
            continue
        if not in_section:
            continue
        if not stripped:
            current = None
            continue
        indented = line[:1].isspace()
        if stripped.startswith("|"):
            if TABLE_SEPARATOR_RE.match(stripped):
                if entries and entries[-1] and entries[-1][0].startswith("|"):
                    entries.pop()                  # the row above was the header
            else:
                entries.append([stripped])
            current = last_item = None
        elif LIST_ITEM_RE.match(stripped) and not indented:
            current = last_item = [LIST_ITEM_RE.sub("", stripped)]
            entries.append(current)
        elif indented and (current or last_item) is not None:
            (current if current is not None else last_item).append(stripped)
        elif current is not None:
            current.append(stripped)
        else:
            current = [stripped]
            last_item = None
            entries.append(current)
    texts = [" ".join(e).strip() for e in entries]
    return [t for t in texts if t.lower() not in CONTRADICTION_PLACEHOLDERS]


def contradiction_sides(entry: str, own_raw: str, source_raw: dict[str, str]) -> set[str]:
    """Distinct evidence an entry cites: raw files in ``(raw/..., Lxx)`` citations and
    ``[[sources/...]]`` links (mapped to their raw_file so one source never counts twice).
    On a source-summary page the page's own raw file is one side implicitly."""
    sides = {c.raw for c in citations.find_citations(entry)}
    for link in SOURCE_LINK_RE.findall(entry):
        ref = page_ref(link)
        sides.add(source_raw.get(ref) or ref)
    if own_raw:
        sides.add(own_raw)
    return sides


def research_questions(root: Path) -> list[str]:
    """Real bullets under CLAUDE.md ## Open research questions (placeholders skipped)."""
    out = []
    for line in vault_config.section_lines(root, "Open research questions"):
        m = re.match(r"^\s*[-*]\s+(?:\[[ xX]\]\s*)?(.+)$", line)
        if m and not re.fullmatch(r"<[^>]*>", m.group(1).strip()):
            out.append(m.group(1).strip())
    return out


def check_contradictions(wiki_dir: Path, root: Path) -> list[tuple[str, int]]:
    """Every Contradictions entry must state and cite both sides (article-guide
    § Handling Contradictions). Returns (page, entry count) for each page with open
    contradictions so the report can list them."""
    files = wiki_files(wiki_dir)
    source_raw: dict[str, str] = {}
    for f in files:
        rel = f.relative_to(wiki_dir).with_suffix("").as_posix()
        if rel.startswith("sources/"):
            fm, _ = parse_frontmatter(f)
            if fm_scalar(fm, "raw_file"):
                source_raw[rel] = fm_scalar(fm, "raw_file")

    open_pages: list[tuple[str, int]] = []
    for f in sorted(files):
        fm, body = parse_frontmatter(f)
        entries = contradiction_entries(body)
        if not entries:
            continue
        rel = str(f.relative_to(root))
        open_pages.append((rel, len(entries)))
        own_raw = fm_scalar(fm, "raw_file") if fm_scalar(fm, "type") == "source-summary" else ""
        for entry in entries:
            sides = contradiction_sides(entry, own_raw, source_raw)
            if len(sides) < 2:
                preview = (entry[:60] + "...") if len(entry) > 60 else entry
                log_issue("WARNING", "contradictions",
                          f'{rel} — contradiction cites {len(sides)} side(s); state both '
                          f'claims and cite each: "{preview}"')

    if open_pages and (root / "CLAUDE.md").exists() and not research_questions(root):
        log_issue("SUGGESTION", "contradictions",
                  f"{len(open_pages)} page(s) record contradictions but CLAUDE.md "
                  f"## Open research questions is empty — add them there")
    return open_pages


# ── Stale-Only Mode ───────────────────────────────────────────────────────────

def stale_only(root: str) -> int:
    """Fast mode: only report stale pages (concepts past their review limit, any page
    past its stale_after). Used by session-start routine."""
    root_path = Path(root)
    wiki_path = root_path / "wiki"
    thresholds = load_staleness_thresholds(root_path)

    if not wiki_path.exists():
        print(f"ERROR: wiki/ directory not found at {wiki_path}", file=sys.stderr)
        return 1

    stale = collect_stale(wiki_path, root_path, thresholds)
    if not stale:
        print("✓ No stale pages found.")
        return 0

    stale.sort(key=lambda x: (-x[1], x[0]))
    print(f"⚠️  {len(stale)} stale page(s):\n")
    for path, days, kind, detail in stale:
        if kind == "review":
            print(f"  {days:>4}d since review  [{detail}]  {path}")
        else:
            print(f"  {days:>4}d overdue  [stale_after {detail}]  {path}")

    print(f"\nRun full lint for all checks: python3 scripts/lint_wiki.py {root}")
    return 1


# ── Report ────────────────────────────────────────────────────────────────────

def lint(root: str, structural: bool = False, verify_quotes: bool = False) -> int:
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
    check_conversion_state(wiki_path, root_path)
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
    if verify_quotes:
        check_quote_verification(root_path)
    check_supersession(wiki_path, root_path)
    open_contradictions = check_contradictions(wiki_path, root_path)

    checks_run = "10 (structural)" if structural else "17+ (full)"
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

    if open_contradictions:
        n = len(open_contradictions)
        lines += ["", f"## Open Contradictions ({n} page{'' if n == 1 else 's'})", ""]
        for page, count in open_contradictions:
            lines.append(f"- {page} — {count} entr{'y' if count == 1 else 'ies'}")

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
    parser.add_argument("--verify-quotes", action="store_true",
                        help="Also check that quoted passages occur in the raw files they cite")

    args = parser.parse_args()

    if args.stale_only:
        sys.exit(stale_only(args.wiki_root))
    else:
        sys.exit(lint(args.wiki_root, structural=args.structural, verify_quotes=args.verify_quotes))


if __name__ == "__main__":
    main()
