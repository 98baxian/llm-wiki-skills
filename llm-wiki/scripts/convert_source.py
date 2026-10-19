#!/usr/bin/env python3
"""
convert_source.py - convert a PDF into line-citable markdown under raw/converted/.

Usage:
    python3 convert_source.py <wiki-root> <pdf> [--force]

The converter is chosen from the vault CLAUDE.md "## Document conversion" section.
Output, written only by this script (never by an LLM):

    raw/converted/<slug>/<slug>.md            line-citable markdown
    raw/converted/<slug>/<slug>.pages.json    PDF page -> line range (optional)
    raw/converted/<slug>/<slug>.convert.json  provenance
    raw/converted/<slug>/<slug>.dropped.txt   header/footer/footnote text the markdown omits
    raw/converted/<slug>/images/

Exit codes: 0 ok, 2 usage/unavailable, 3 conversion failed, 4 scanned PDF,
5 input inside a private zone, 6 output exists for a different PDF (use --force).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import textnorm
import vault_config

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_FAILED = 3
EXIT_SCANNED = 4
EXIT_PRIVATE = 5
EXIT_EXISTS = 6

DEFAULT_TIMEOUT = 900
WINDOW_PAGES = 8
MIN_PDFTOTEXT_CHARS = 20
VALID_CONVERTERS = {"auto", "mineru", "pdftotext", "command", "none"}
VALID_TIERS = {"flash", "basic", "standard", "advanced"}
CPU_TIERS = {"flash", "basic"}


class ConvertError(Exception):
    def __init__(self, message: str, code: int = EXIT_FAILED):
        super().__init__(message)
        self.code = code


@dataclass
class Config:
    converter: str = "auto"
    tier: str = "basic"
    timeout: int = DEFAULT_TIMEOUT
    command: str = ""


def load_config(root) -> Config:
    kv = vault_config.key_values(vault_config.section_lines(root, "Document conversion"))
    cfg = Config()
    if "converter" in kv:
        if kv["converter"] not in VALID_CONVERTERS:
            raise ConvertError(f"invalid converter {kv['converter']!r}; use one of {sorted(VALID_CONVERTERS)}", EXIT_USAGE)
        cfg.converter = kv["converter"]
    if "tier" in kv:
        if kv["tier"] not in VALID_TIERS:
            raise ConvertError(f"invalid tier {kv['tier']!r}; use one of {sorted(VALID_TIERS)}", EXIT_USAGE)
        cfg.tier = kv["tier"]
    if "timeout" in kv:
        if not kv["timeout"].isdigit() or int(kv["timeout"]) <= 0:
            raise ConvertError(f"invalid timeout {kv['timeout']!r}; use a positive number of seconds", EXIT_USAGE)
        cfg.timeout = int(kv["timeout"])
    if "command" in kv:
        cfg.command = kv["command"]
    return cfg


def sanitize_slug(filename: str) -> str:
    """Folder/file slug from a PDF filename: lower-case, letters and digits (any script) kept."""
    stem = Path(filename).stem
    slug = "".join(ch if ch.isalnum() else "-" for ch in stem.lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug or "untitled"


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Output:
    """What a converter hands back; ``convert()`` turns it into files."""
    markdown: str
    converter: str
    version: str = "unknown"
    lossy: bool = False
    pages: list[dict] | None = None        # [{"page", "line_start", "line_end", "how"}]
    match_rate: float | None = None
    confidence: str | None = None          # "high" | "low"
    page_map: str = "none"                 # "exact" | "aligned" | "none"
    pages_total: int | None = None
    dropped_lines: list[str] = field(default_factory=list)
    images: dict[str, bytes] = field(default_factory=dict)   # "images/x.jpg" -> bytes
    dropped_types: list[str] = field(default_factory=list)


def tool_version(exe: str, flag: str = "-v") -> str:
    try:
        p = subprocess.run([exe, flag], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    first = (p.stderr or p.stdout).strip().splitlines()
    return first[0].strip() if first else "unknown"


def pdftotext_pages(pdf, timeout: int = DEFAULT_TIMEOUT) -> list[str]:
    exe = shutil.which("pdftotext")
    if not exe:
        raise ConvertError("pdftotext not found (install poppler-utils), or configure another converter", EXIT_USAGE)
    try:
        p = subprocess.run([exe, str(pdf), "-"], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ConvertError(f"pdftotext timed out after {timeout}s", EXIT_FAILED)
    if p.returncode != 0:
        raise ConvertError(f"pdftotext failed: {p.stderr.strip()[:200]}", EXIT_FAILED)
    pages = p.stdout.split("\x0c")
    if pages and not pages[-1].strip():
        pages.pop()
    return pages


def convert_pdftotext(pdf, timeout: int = DEFAULT_TIMEOUT) -> Output:
    pages = pdftotext_pages(pdf, timeout)
    if sum(len(re.sub(r"\s", "", p)) for p in pages) < MIN_PDFTOTEXT_CHARS:
        raise ConvertError("scanned PDF: pdftotext found no text layer; needs MinerU or your own converter", EXIT_SCANNED)
    lines: list[str] = []
    ranges: list[dict] = []
    for number, text in enumerate(pages, 1):
        if lines:
            lines.append("")                      # blank separator, owned by the previous page
            ranges[-1]["line_end"] = len(lines)
        body = [ln.rstrip() for ln in text.strip("\n").split("\n")]
        start = len(lines) + 1
        lines.extend(body)
        ranges.append({"page": number, "line_start": start, "line_end": len(lines), "how": "exact"})
    exe = shutil.which("pdftotext") or "pdftotext"
    return Output(
        markdown="\n".join(lines) + "\n", converter="pdftotext", version=tool_version(exe),
        lossy=True, pages=ranges, match_rate=1.0, confidence="high", page_map="exact",
        pages_total=len(pages),
    )


def convert_command(template: str, pdf, slug: str, timeout: int = DEFAULT_TIMEOUT) -> Output:
    """Run the user's own converter: ``{input}``, ``{outdir}`` and ``{slug}`` are substituted,
    no shell is involved. The tool must write ``{outdir}/<slug>.md``; ``<slug>.pages.json``
    and an ``images/`` directory are optional."""
    if not template.strip():
        raise ConvertError('converter "command" needs a `command:` line under ## Document conversion', EXIT_USAGE)
    pdf = Path(pdf)
    with tempfile.TemporaryDirectory(prefix="llmwiki-cmd-") as tmp:
        outdir = Path(tmp)
        argv = [
            tok.replace("{input}", str(pdf)).replace("{outdir}", str(outdir)).replace("{slug}", slug)
            for tok in shlex.split(template)
        ]
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, cwd=tmp)
        except FileNotFoundError:
            raise ConvertError(f"converter command not found: {argv[0]}", EXIT_USAGE)
        except subprocess.TimeoutExpired:
            raise ConvertError(f"converter command timed out after {timeout}s", EXIT_FAILED)
        if p.returncode != 0:
            raise ConvertError(f"converter command failed ({p.returncode}): {p.stderr.strip()[:300]}", EXIT_FAILED)
        md_path = outdir / f"{slug}.md"
        if not md_path.is_file():
            raise ConvertError(f"converter command did not write {slug}.md into the output directory", EXIT_FAILED)
        out = Output(markdown=md_path.read_text(encoding="utf-8"), converter="command", version="custom")
        pages_path = outdir / f"{slug}.pages.json"
        if pages_path.is_file():
            try:
                data = json.loads(pages_path.read_text(encoding="utf-8"))
                if isinstance(data.get("pages"), list) and data["pages"]:
                    out.pages = data["pages"]
                    out.match_rate = data.get("match_rate")
                    out.confidence = data.get("confidence", "low")
                    out.page_map = "supplied"
                    out.pages_total = len(out.pages)
            except (ValueError, AttributeError):
                pass  # an unreadable pages.json only costs the page map
        image_dir = outdir / "images"
        if image_dir.is_dir():
            for f in sorted(image_dir.rglob("*")):
                if f.is_file():
                    out.images[f"images/{f.relative_to(image_dir).as_posix()}"] = f.read_bytes()
        return out


SKIP_TYPES = {"header", "footer", "page_number", "page_footnote", "table"}
DROPPED_TYPES = ("header", "footer", "page_number", "page_footnote")
KEY_LEN = 25
MIN_BLOCK_CHARS = 12
CONFIDENCE_THRESHOLD = 0.8


def flat_text(content) -> str:
    """Flatten a MinerU block ``content`` (str, list or dict) into plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(flat_text(c) for c in content)
    if isinstance(content, dict):
        return flat_text(content.get("content", ""))
    return ""


def dropped_lines(pages: list[dict]) -> list[str]:
    """``p<N>\\t<type>\\t<text>`` for blocks MinerU leaves out of the markdown, once per page."""
    out: list[str] = []
    for page in pages:
        number = page.get("page_idx", 0) + 1
        seen: set[tuple[str, str]] = set()
        for block in page.get("blocks", []):
            kind = block.get("type")
            if kind not in DROPPED_TYPES:
                continue
            text = " ".join(flat_text(block.get("content", "")).split())
            if not text or (kind, text) in seen:
                continue
            seen.add((kind, text))
            out.append(f"p{number}\t{kind}\t{text}")
    return out


def align_pages(markdown: str, pages: list[dict]) -> tuple[list[dict], float, str]:
    """Map MinerU pages to markdown line ranges.

    Body blocks are searched forward from the previous match (so repeated text maps to
    its later occurrence) by their first ``KEY_LEN`` characters after case folding.
    Ranges are returned as a partition of the markdown: each page runs up to the line
    before the next page starts. Returns ``(ranges, match_rate, confidence)``.
    """
    lines = markdown.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    folded = [textnorm.fold_case(line) for line in lines]
    total = len(lines)
    cursor = 0
    body = matched = 0
    found: list[list[int]] = []
    for page in pages:
        hits: list[int] = []
        for block in page.get("blocks", []):
            if block.get("type") in SKIP_TYPES:
                continue
            text = textnorm.fold_case(flat_text(block.get("content", "")))
            if len(text) < MIN_BLOCK_CHARS:
                continue
            body += 1
            key = text[:KEY_LEN]
            for i in range(cursor, total):
                if key in folded[i]:
                    hits.append(i + 1)
                    cursor = i
                    matched += 1
                    break
        found.append(hits)
    starts: list[int] = []
    last_end = 0
    for hits in found:
        previous = starts[-1] if starts else 1
        if hits:
            starts.append(max(min(hits), previous))
            last_end = max(hits)
        else:
            starts.append(max(last_end + 1, previous))
    if starts:
        starts[0] = 1
    starts = [min(s, max(total, 1)) for s in starts]
    ranges = []
    for k, page in enumerate(pages):
        end = starts[k + 1] - 1 if k + 1 < len(starts) else total
        ranges.append({
            "page": page.get("page_idx", k) + 1,
            "line_start": starts[k],
            "line_end": max(end, starts[k]),
            "how": "aligned" if found[k] else "interpolated",
        })
    rate = matched / body if body else 0.0
    return ranges, rate, "high" if rate >= CONFIDENCE_THRESHOLD else "low"


MINERU_SCHEMA = ("docvortex.middle", "2.0")


class WindowKilled(Exception):
    """mineru-kit died from a signal (rc -9 / 137): most likely out of memory."""


@dataclass
class WindowResult:
    markdown: str
    middle: dict
    images: dict[str, bytes]


def find_mineru_kit() -> str | None:
    env = os.environ.get("MINERU_KIT_BIN")
    if env and Path(env).exists():
        return env
    home = Path.home() / ".local" / "bin" / "mineru-kit"
    if home.exists():
        return str(home)
    return shutil.which("mineru-kit")


def pdf_page_count(pdf) -> int:
    exe = shutil.which("pdfinfo")
    if not exe:
        raise ConvertError("pdfinfo not found (install poppler-utils): needed to split the PDF into page windows", EXIT_USAGE)
    p = subprocess.run([exe, str(pdf)], capture_output=True, text=True, timeout=60)
    m = re.search(r"^Pages:\s+(\d+)", p.stdout, re.M)
    if p.returncode != 0 or not m:
        raise ConvertError(f"pdfinfo could not read {pdf}: {p.stderr.strip()[:200]}", EXIT_FAILED)
    return int(m.group(1))


def plan_windows(total: int, size: int = WINDOW_PAGES) -> list[tuple[int, int]]:
    return [(a, min(a + size - 1, total)) for a in range(1, total + 1, size)]


def read_kit_zip(zip_path) -> WindowResult:
    with zipfile.ZipFile(zip_path) as z:
        markdown = z.read("markdown.md").decode("utf-8")
        middle = json.loads(z.read("middle_json.json"))
        images = {n: z.read(n) for n in z.namelist() if n.startswith("images/") and not n.endswith("/")}
    return WindowResult(markdown, middle, images)


def run_kit_window(kit: str, pdf, first: int, last: int, tier: str, timeout: int) -> WindowResult:
    """One ``mineru-kit parse -f zip`` run over pages first..last (1-based, inclusive).

    Never passes --remote / --remote-url / --api-key. CPU tiers run with the GPU hidden.
    """
    env = dict(os.environ)
    if tier in CPU_TIERS:
        env["CUDA_VISIBLE_DEVICES"] = ""
    span = str(first) if first == last else f"{first}-{last}"
    with tempfile.TemporaryDirectory(prefix="llmwiki-kit-") as tmp:
        cmd = [kit, "parse", str(pdf), "-o", tmp, "-f", "zip", "-p", span, "--tier", tier]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            raise ConvertError(f"mineru-kit timed out after {timeout}s on pages {span}", EXIT_FAILED)
        except OSError as e:
            raise ConvertError(f"cannot run mineru-kit: {e}", EXIT_USAGE)
        if p.returncode in (-9, 137):
            raise WindowKilled(f"pages {span}")
        if p.returncode != 0:
            raise ConvertError(f"mineru-kit failed on pages {span}: {p.stderr.strip()[-300:]}", EXIT_FAILED)
        zips = sorted(Path(tmp).glob("*.zip"))
        if not zips:
            raise ConvertError(f"mineru-kit wrote no zip for pages {span}", EXIT_FAILED)
        return read_kit_zip(zips[0])


def run_with_halving(runner, first: int, last: int) -> list:
    """Run a window; if it is killed, retry as two half windows, down to one page."""
    try:
        return [runner(first, last)]
    except WindowKilled:
        if first == last:
            raise ConvertError(f"mineru-kit was killed on page {first} (probably out of memory)", EXIT_FAILED)
        mid = (first + last) // 2
        return run_with_halving(runner, first, mid) + run_with_halving(runner, mid + 1, last)


def convert_mineru(pdf, tier: str = "basic", timeout: int = DEFAULT_TIMEOUT, *, runner=None,
                   total_pages: int | None = None, window: int = WINDOW_PAGES) -> Output:
    if runner is None:
        kit = find_mineru_kit()
        if not kit:
            raise ConvertError("mineru-kit not found (set MINERU_KIT_BIN or install MinerU), or configure another converter", EXIT_USAGE)

        def runner(first, last):
            return run_kit_window(kit, pdf, first, last, tier, timeout)

    total = total_pages or pdf_page_count(pdf)
    results: list[WindowResult] = []
    for first, last in plan_windows(total, window):
        results.extend(run_with_halving(runner, first, last))
    markdown = "\n\n".join(r.markdown.strip("\n") for r in results) + "\n"
    pages = [pg for r in results for pg in r.middle.get("pages", [])]
    images: dict[str, bytes] = {}
    for r in results:
        images.update(r.images)
    producer = results[0].middle.get("metadata", {}).get("producer", {})
    out = Output(
        markdown=markdown, converter="mineru-kit", version=str(producer.get("version", "unknown")),
        lossy=False, pages_total=total, images=images, dropped_types=list(DROPPED_TYPES),
    )
    schema_ok = all(
        (r.middle.get("schema"), r.middle.get("schema_version")) == MINERU_SCHEMA for r in results
    )
    if schema_ok:
        out.pages, out.match_rate, out.confidence = align_pages(markdown, pages)
        out.page_map = "aligned"
        out.dropped_lines = dropped_lines(pages)
    else:
        out.confidence = "low"
        print("warning: unexpected MinerU middle_json schema; page map skipped", file=sys.stderr)
    return out
