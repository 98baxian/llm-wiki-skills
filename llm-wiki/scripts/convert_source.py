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
