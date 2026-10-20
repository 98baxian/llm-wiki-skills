#!/usr/bin/env python3
"""
verify_quotes.py - check that quoted passages in wiki pages really occur in the raw
file they cite, at the cited place.

Usage:
    python3 verify_quotes.py <wiki-root> [--page PATH]... [--threshold N] [--report PATH]
                             [--flag [--dry-run]] [--strict] [--no-cache]

Reports only; wiki pages are edited solely with --flag (one FIX comment per quote that is
not found). Exit 0: no error-severity quote. Exit 1: at least one. Exit 2: usage/environment.

Modules (all in this directory; this file is the entry point and the public facade, so
``import verify_quotes as vq; vq.Doc`` etc. keep working):
    vq_extract.py  quote extraction and binding to citations
    vq_match.py    matching ladder, fuzzy fallback, verdicts
    vq_store.py    claim identity, algo_hash, cache, raw-file store
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

# Names below are re-exported for callers and tests that use ``verify_quotes.<name>``.
from vq_extract import (  # noqa: F401
    ELLIPSIS_RE, LIST_START, QUOTE_RES, SHORT_CJK, SHORT_WORDS, WIKILINK_RE, INLINE_CODE_RE,
    Claim, ExtractResult, body_lines, extract_claims, fragments_of, is_cjk, long_enough,
    prepare, strip_delimiters,
)
from vq_match import (  # noqa: F401
    MAX_POSTINGS, MIN_FUZZY_CHARS, NEAR_MISS, RUNGS, SCAN_LIMIT, SLACK, THRESHOLD, TOP_CANDIDATES,
    Context, Doc, Result, fuzzy_best, is_short, match_fragments, needle_for, page_lines,
    partial_match, scripts_cannot_overlap, verify_claim, windows_for,
)
from vq_store import (  # noqa: F401
    CACHE_SCHEMA, OTHER_RAW_MAX_BYTES, OTHER_RAW_MAX_FILES, RAW_SUFFIXES,
    Cache, RawStore, algo_hash, claim_id, claim_key, corpus_token, format_locs, lines_to_pages,
    sha256_bytes,
)

WIKI_DIRS = ("concepts", "sources", "entities", "comparisons", "synthesis")
REPORT_SCHEMA = "llm-wiki.verify-quotes/1"


class UsageError(Exception):
    """Bad arguments or an unusable vault (exit code 2)."""


def wiki_pages(root: Path, pages: list[str] | None) -> list[Path]:
    wiki = root / "wiki"
    if pages:
        chosen = []
        for p in pages:
            path = Path(p) if Path(p).is_absolute() else root / p
            if not path.is_file():
                raise UsageError(f"page not found: {p}")
            chosen.append(path.resolve())
        return sorted(set(chosen))
    found = []
    for d in WIKI_DIRS:
        base = wiki / d
        if base.is_dir():
            found.extend(f for f in base.rglob("*.md") if "sync-conflict" not in f.name)
    return sorted(found)


def verify_wiki(root, pages: list[str] | None = None, *, threshold: int = THRESHOLD,
                near_miss: int = NEAR_MISS, strict: bool = False, use_cache: bool = True) -> dict:
    """Check every quote in the wiki pages and return the report as a dict."""
    root = Path(root).resolve()
    if not (root / "wiki").is_dir():
        raise UsageError(f"wiki/ directory not found at {root / 'wiki'}")
    store = RawStore(root)
    cache = Cache(root / ".cache" / "verify_quotes.json" if use_cache else None)
    algo, corpus = algo_hash(), corpus_token(root)
    files = wiki_pages(root, pages)
    counts: Counter = Counter()
    results, malformed, seen = [], [], set()
    missing: Counter = Counter()
    for f in files:
        page = f.relative_to(root).as_posix()
        extracted = extract_claims(f.read_text(encoding="utf-8", errors="replace"), page)
        counts["uncited"] += extracted.uncited
        counts["too_short"] += extracted.too_short
        for p, line_no, fragment in extracted.malformed:
            malformed.append({"page": p, "line": line_no, "fragment": fragment})
        for claim in extracted.claims:
            doc, reason = store.load(claim.raw)
            convert, pages_meta, convert_sha, pages_sha = store.sidecars(claim.raw)
            dropped_doc, dropped_sha = store.dropped(claim.raw)
            if reason == "raw file missing":
                missing[claim.raw] += 1
            key = claim_key(claim)
            seen.add(key)
            fingerprint = {
                "raw_sha256": sha256_bytes(doc.text.encode("utf-8")) if doc else reason,
                "pages_sha256": pages_sha, "convert_sha256": convert_sha, "dropped_sha256": dropped_sha,
                "threshold": threshold, "near_miss": near_miss, "strict": strict, "algo": algo,
            }
            cached = cache.get(key, fingerprint, corpus)
            if cached is not None:
                result = Result(**cached)
                counts["from_cache"] += 1
            else:
                ctx = Context(doc, reason, convert, pages_meta,
                              others=lambda rel=claim.raw: store.others(rel), dropped=dropped_doc,
                              threshold=threshold, near_miss=near_miss, strict=strict)
                result = verify_claim(claim, ctx)
                cache.put(key, fingerprint, corpus, result)
            tally(counts, result)
            results.append({
                "id": claim_id(claim), "page": page, "line": claim.line_no, "quote": claim.quote, "raw": claim.raw,
                "loc": format_locs(claim.locs), "pdf_page": lines_to_pages(pages_meta, result.matched_lines),
                "verdict": result.verdict,
                "severity": result.severity, "score": result.score,
                "matched_lines": result.matched_lines, "note": result.note,
                "suggested_raw": result.suggested_raw,
            })
    counts["malformed_citations"] = len(malformed)
    if pages is None:
        cache.prune(seen)
    cache.save()
    names = ("exact", "fuzzy", "wrong_location", "near_miss", "not_found_error", "not_found_warning",
             "unverifiable_infrastructure", "unverifiable_cross_language", "uncited",
             "malformed_citations", "from_cache", "too_short")
    return {
        "schema": REPORT_SCHEMA,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pages_checked": len(files),
        "counts": {n: counts.get(n, 0) for n in names},
        "errors": sum(1 for r in results if r["severity"] == "error"),
        "missing_raw_files": [{"raw": r, "citations": n} for r, n in missing.most_common()],
        "malformed": malformed,
        "results": results,
    }


def tally(counts: Counter, result: Result) -> None:
    if result.verdict == "not_found":
        counts["not_found_error" if result.severity == "error" else "not_found_warning"] += 1
    elif result.verdict == "unverifiable":
        counts["unverifiable_cross_language" if result.kind == "cross_language" else "unverifiable_infrastructure"] += 1
    else:
        counts[result.verdict] += 1


def print_summary(report: dict, report_path: Path | None, root: Path) -> None:
    c = report["counts"]
    checked = sum(c[n] for n in ("exact", "fuzzy", "wrong_location", "near_miss", "not_found_error",
                                 "not_found_warning", "unverifiable_infrastructure", "unverifiable_cross_language"))
    print(f"verify_quotes: {report['pages_checked']} pages, {checked} cited quotes")
    print(f"  exact {c['exact']}  fuzzy {c['fuzzy']}  wrong_location {c['wrong_location']}  near_miss {c['near_miss']}  "
          f"not_found {c['not_found_error']} (+{c['not_found_warning']} downgraded)")
    print(f"  unverifiable {c['unverifiable_infrastructure']} infrastructure, {c['unverifiable_cross_language']} cross-language"
          f"  uncited {c['uncited']}  malformed citations {c['malformed_citations']}  from cache {c['from_cache']}")
    if report["missing_raw_files"]:
        total = sum(m["citations"] for m in report["missing_raw_files"])
        print(f"  missing raw files: {len(report['missing_raw_files'])} ({total} citations)")
    for r in [r for r in report["results"] if r["severity"] == "error"][:10]:
        print(f"  ERROR {r['page']}:{r['line']} quote not found in {r['raw']} ({r['loc']}): \"{r['quote'][:60]}\"")
    if report["errors"] > 10:
        print(f"  ... and {report['errors'] - 10} more errors; see the report")
    if report_path:
        print(f"Report: {report_path.relative_to(root) if root in report_path.parents else report_path}")


def flag_report(root: Path, report: dict, dry_run: bool) -> int:
    """Insert one FIX comment under each quote whose result is an error-severity not_found."""
    import cascade_flag

    edits: dict[str, list] = defaultdict(list)
    for r in report["results"]:
        if r["verdict"] != "not_found" or r["severity"] != "error":
            continue
        marker = f"[vq:{r['id']}]"
        tag = f"<!-- FIX: quote not found {marker} in {r['raw']} {r['loc']}"
        if r["suggested_raw"]:
            tag += f"; found in {r['suggested_raw']}"
        edits[r["page"]].append((r["line"] - 1, tag + " -->", marker))
    total = 0
    for page, page_edits in sorted(edits.items()):
        added = cascade_flag.insert_fix_comments(root / page, page_edits, dry_run=dry_run)
        total += added
        if added:
            print(f"  {'would add' if dry_run else 'added'} {added} FIX comment(s): {page}")
    print(f"FIX comments {'that would be ' if dry_run else ''}added: {total}")
    return total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify quoted passages in wiki pages against the raw files they cite.")
    parser.add_argument("wiki_root", help="Path to the wiki root directory")
    parser.add_argument("--page", action="append", default=[], help="Only check this wiki file (repeatable)")
    parser.add_argument("--threshold", type=int, default=THRESHOLD, help=f"Fuzzy similarity that counts as a match (default {THRESHOLD})")
    parser.add_argument("--report", default=None, help="Report path (default outputs/verify-quotes-YYYY-MM-DD.json)")
    parser.add_argument("--strict", action="store_true", help="Keep downgraded not_found and near_miss results as errors")
    parser.add_argument("--no-cache", action="store_true", help="Do not read or write .cache/verify_quotes.json")
    parser.add_argument("--flag", action="store_true",
                        help="Insert a <!-- FIX: --> comment under every quote that is not found (error severity only)")
    parser.add_argument("--dry-run", action="store_true", help="With --flag: print the edits, change nothing")
    args = parser.parse_args(argv)
    if args.dry_run and not args.flag:
        parser.error("--dry-run only makes sense with --flag")
    root = Path(args.wiki_root).resolve()
    try:
        report = verify_wiki(root, args.page or None, threshold=args.threshold,
                             strict=args.strict, use_cache=not args.no_cache)
    except UsageError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    path = Path(args.report) if args.report else root / "outputs" / f"verify-quotes-{datetime.now().strftime('%Y-%m-%d')}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print_summary(report, path, root)
    if args.flag:
        flag_report(root, report, args.dry_run)
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
