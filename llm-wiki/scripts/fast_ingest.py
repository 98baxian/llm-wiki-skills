#!/usr/bin/env python3
"""
fast_ingest.py — Deterministic ingest preprocessing for LLM Wiki.

Takes a raw file path, computes SHA-256, generates a slug, detects source type,
and creates a stub wiki page with pre-filled YAML frontmatter. Prints a
structured prompt to stdout so the LLM agent knows what to do next.

Usage:
    python3 fast_ingest.py <wiki-root> <raw-file-path>

Example:
    python3 fast_ingest.py ~/wikis/ai-research raw/articles/attention-paper.md

The raw-file-path can be absolute or relative to <wiki-root>.
"""

import hashlib
import os
import re
import sys
import argparse
from datetime import date, datetime
from pathlib import Path


def compute_sha256(filepath: str) -> str:
    """Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def generate_slug(filename: str, title: str | None = None) -> str:
    """Generate a URL-friendly slug from filename or title.

    Converts to lowercase, replaces non-alphanumeric chars with hyphens,
    collapses multiple hyphens, strips leading/trailing hyphens.
    """
    source = title if title else Path(filename).stem

    # Remove date prefixes like "2026-04-28-"
    source = re.sub(r"^\d{4}-\d{2}-\d{2}-?", "", source)

    slug = source.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    slug = slug.strip("-")

    return slug or "untitled"


def extract_title_from_md(filepath: str) -> str | None:
    """Try to extract title from the first # heading or YAML frontmatter."""
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            in_frontmatter = False
            for line in f:
                line = line.strip()

                # Check frontmatter
                if line == "---":
                    in_frontmatter = not in_frontmatter
                    continue
                if in_frontmatter and line.startswith("title:"):
                    title = line[6:].strip().strip("\"'")
                    if title:
                        return title
                    continue

                # Check H1 heading (outside frontmatter)
                if not in_frontmatter and line.startswith("# "):
                    return line[2:].strip()

    except (OSError, UnicodeDecodeError):
        pass
    return None


def detect_source_type(raw_file: str, root: str) -> str:
    """Detect whether the source is personal writing or external.

    Returns 'personal-writing' or 'source-summary'.
    """
    rel_path = os.path.relpath(raw_file, root)

    # Path-based detection
    if "personal" in rel_path.lower():
        return "personal-writing"

    # Frontmatter-based detection
    try:
        with open(raw_file, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(2000)  # Read first 2KB for frontmatter
            if content.startswith("---"):
                end = content.find("\n---", 3)
                if end != -1:
                    fm_block = content[3:end]
                    for line in fm_block.splitlines():
                        if line.strip().startswith("type:"):
                            val = line.split(":", 1)[1].strip().strip("\"'")
                            if val == "personal-writing":
                                return "personal-writing"
    except (OSError, UnicodeDecodeError):
        pass

    return "source-summary"


def extract_metadata(raw_file: str) -> dict:
    """Extract available metadata from raw file frontmatter."""
    meta = {}
    try:
        with open(raw_file, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(3000)

        if not content.startswith("---"):
            return meta

        end = content.find("\n---", 3)
        if end == -1:
            return meta

        for line in content[3:end].splitlines():
            if ":" not in line:
                continue
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip().strip("\"'")
            if key in ("title", "source", "source_url", "author", "date", "domain"):
                meta[key] = val

    except (OSError, UnicodeDecodeError):
        pass

    return meta


def _convert_pdf_source(root: str, pdf_abs: str) -> tuple[str, str]:
    """Convert a PDF with convert_source before ingesting it.

    Returns ``(path of the markdown to ingest, extra frontmatter lines)``. With
    ``converter: none`` the PDF is ingested unchanged, as before.
    """
    import convert_source

    try:
        cfg = convert_source.load_config(root)
        if cfg.converter == "none":
            print("ℹ  converter: none — ingesting the PDF without conversion (no line citations)")
            return pdf_abs, ""
        res = convert_source.convert(pdf_abs, root, config=cfg)
    except convert_source.ConvertError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(e.code)
    state = "reused" if res.skipped else f"converted with {res.converter}"
    print(f"✓ PDF {state}: {os.path.relpath(res.md_path, root)}" + (" (lossy)" if res.lossy else ""))
    frontmatter = (
        f'source_pdf: "{os.path.relpath(pdf_abs, root)}"\n'
        f'source_pdf_sha256: "{res.source_pdf_sha256}"\n'
    )
    if res.lossy:
        frontmatter += "source_lossy: true\n"
    return str(res.md_path), frontmatter


def fast_ingest(root: str, raw_file_path: str) -> None:
    """Run the deterministic ingest preprocessing."""
    root = os.path.abspath(root)
    today = date.today().isoformat()
    now_hm = datetime.now().strftime("%H:%M")

    # Resolve raw file path
    if os.path.isabs(raw_file_path):
        raw_abs = raw_file_path
    else:
        raw_abs = os.path.join(root, raw_file_path)

    if not os.path.exists(raw_abs):
        print(f"ERROR: File not found: {raw_abs}", file=sys.stderr)
        sys.exit(1)

    # ── Step 0: a PDF is converted to line-citable markdown first ─────────────
    pdf_fm = ""
    if raw_abs.lower().endswith(".pdf"):
        raw_abs, pdf_fm = _convert_pdf_source(root, raw_abs)

    raw_rel = os.path.relpath(raw_abs, root)

    # ── Step 1: Compute SHA-256 ───────────────────────────────────────────────
    sha256 = compute_sha256(raw_abs)
    print(f"✓ SHA-256: {sha256[:16]}...{sha256[-8:]}")

    # ── Step 2: Extract metadata and title ────────────────────────────────────
    title = extract_title_from_md(raw_abs)
    meta = extract_metadata(raw_abs)
    if not title:
        title = meta.get("title", "")
    source_url = meta.get("source_url", meta.get("source", ""))
    author = meta.get("author", "")
    domain = meta.get("domain", "")

    # ── Step 3: Generate slug ─────────────────────────────────────────────────
    slug = generate_slug(os.path.basename(raw_abs), title)
    print(f"✓ Slug: {slug}")

    # ── Step 4: Detect source type ────────────────────────────────────────────
    source_type = detect_source_type(raw_abs, root)
    print(f"✓ Source type: {source_type}")

    # ── Step 5: Check for existing stub ───────────────────────────────────────
    stub_path = os.path.join(root, "wiki", "sources", f"{slug}.md")
    if os.path.exists(stub_path):
        print(f"\n⚠️  Stub already exists at: wiki/sources/{slug}.md")
        print("    The raw file may have been ingested before.")
        print("    If re-ingesting, delete the existing stub first.")
        return

    # ── Step 6: Check for file size ───────────────────────────────────────────
    file_size = os.path.getsize(raw_abs)
    line_count = 0
    try:
        with open(raw_abs, "r", encoding="utf-8", errors="replace") as f:
            line_count = sum(1 for _ in f)
    except OSError:
        pass

    # ── Step 7: Determine possibly_outdated ───────────────────────────────────
    possibly_outdated = False
    file_date = meta.get("date", "")
    if file_date:
        try:
            from datetime import datetime as dt
            source_date = dt.fromisoformat(file_date.replace("Z", "+00:00"))
            days_old = (datetime.now() - source_date.replace(tzinfo=None)).days
            if days_old > 730:  # older than 2 years
                possibly_outdated = True
        except (ValueError, TypeError):
            pass

    # ── Step 8: Create stub file ──────────────────────────────────────────────
    display_title = title or slug.replace("-", " ").title()

    if source_type == "personal-writing":
        stub_content = f"""---
type: personal-writing
title: "{display_title}"
date: {today}
status: draft
topic_tags: []
confidence_at_writing: medium
superseded_by: ""
raw_file: "{raw_rel}"
raw_sha256: "{sha256}"
{pdf_fm}last_verified: {today}
tags: [wiki, wiki/source]
processed: true
---

# {display_title}

## Core Argument

<!-- Read {raw_rel} and summarize the core argument here. -->

## Key Claims

<!-- Extract key claims. No line citations for personal writing. -->

## Evidence Referenced

<!-- If the writing cites external sources, link them here. -->

## Limitations
"""
    else:
        stub_content = f"""---
type: source-summary
title: "{display_title}"
date: {today}
source_url: "{source_url}"
domain: "{domain}"
author: "{author}"
tags: [wiki, wiki/source]
processed: true
raw_file: "{raw_rel}"
raw_sha256: "{sha256}"
{pdf_fm}last_verified: {today}
possibly_outdated: {str(possibly_outdated).lower()}
language: "English"
canonical_source: ""
---

# {display_title}

## Summary

<!-- Read {raw_rel} ({line_count} lines) and write 2-4 sentences.
     Every factual claim must end with: ({raw_rel}, L<start>-<end>) -->

## Key Points

<!-- Extract key points with line citations:
     - Point one. ({raw_rel}, L45) -->

## Concepts Extracted

<!-- List concept wikilinks:
     - [[concepts/concept-slug]] -->

## Entities Extracted

<!-- List entity wikilinks:
     - [[entities/entity-slug]] -->

## Contradictions

## My Notes
"""

    os.makedirs(os.path.dirname(stub_path), exist_ok=True)
    with open(stub_path, "w", encoding="utf-8") as f:
        f.write(stub_content)

    # ── Step 9: Print instructions for the LLM ───────────────────────────────
    print(f"\n✅ Stub created at: wiki/sources/{slug}.md")
    print(f"   Raw file: {raw_rel} ({line_count} lines, {file_size:,} bytes)")
    if possibly_outdated:
        print(f"   ⚠️  Source is >2 years old — marked possibly_outdated: true")

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║  AGENT: Please complete the following steps:                ║
╠══════════════════════════════════════════════════════════════╣
║  1. Read the raw file: {raw_rel:<38s}║
║  2. Fill in the Summary section with line citations         ║
║  3. Fill in the Key Points section                          ║
║  4. Extract concepts → create/update wiki/concepts/ pages   ║
║  5. Extract entities → create/update wiki/entities/ pages   ║
║  6. Update wiki/index.md                                    ║
║  7. Check wiki/QUESTIONS.md for answerable questions        ║
║  8. Append to log/YYYYMMDD.md                               ║
╚══════════════════════════════════════════════════════════════╝""")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deterministic ingest preprocessing for LLM Wiki.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("wiki_root", help="Path to the wiki root directory")
    parser.add_argument("raw_file", help="Path to the raw file to ingest")

    args = parser.parse_args()
    fast_ingest(args.wiki_root, args.raw_file)


if __name__ == "__main__":
    main()
