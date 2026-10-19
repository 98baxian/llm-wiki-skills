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
