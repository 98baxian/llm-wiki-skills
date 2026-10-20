"""vq_store - identity, fingerprint, cache and raw-file loading for verify_quotes.

Part of verify_quotes (see verify_quotes.py). ``algo_hash`` fingerprints the code that decides
verdicts, so editing any ``vq_*.py`` module (or verify_quotes.py, textnorm.py, citations.py)
invalidates cached results.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path

import textnorm
import vault_config
from vq_extract import Claim
from vq_match import Doc, Result

CODE_DIR = Path(__file__).resolve().parent     # where the verdict-deciding modules live (hashed by algo_hash)

CACHE_SCHEMA = "llm-wiki.verify-quotes-cache/1"
RAW_SUFFIXES = {".md", ".txt"}
OTHER_RAW_MAX_FILES = 200
OTHER_RAW_MAX_BYTES = 20_000_000


def format_locs(locs: tuple) -> str:
    parts = []
    for loc in locs:
        if loc[0] == "L":
            parts.append(f"L{loc[1]}" if loc[1] == loc[2] else f"L{loc[1]}-{loc[2]}")
        elif loc[0] == "p":
            parts.append(f"p.{loc[1]}" if loc[1] == loc[2] else f"p.{loc[1]}-{loc[2]}")
        else:
            parts.append("location unknown")
    return ", ".join(parts)


def claim_key(claim: Claim) -> str:
    """Identity of a quote at a place: normalized quote, cited file and locations."""
    material = "|".join([textnorm.normalize(claim.quote), claim.raw, format_locs(claim.locs)])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def claim_id(claim: Claim) -> str:
    return claim_key(claim)[:8]


def algo_hash() -> str:
    """Fingerprint of the code that decides verdicts: a cache entry made by other code is stale."""
    names = sorted({f.name for f in CODE_DIR.glob("vq_*.py")} | {"verify_quotes.py", "textnorm.py", "citations.py"})
    h = hashlib.sha256()
    for name in names:
        h.update((CODE_DIR / name).read_bytes())
    return h.hexdigest()[:16]


def corpus_token(root: Path) -> str:
    """Changes whenever any raw .md/.txt file is added, removed or modified."""
    h = hashlib.sha256()
    raw = Path(root) / "raw"
    if raw.is_dir():
        for f in sorted(raw.rglob("*")):
            if f.is_file() and f.suffix.lower() in RAW_SUFFIXES:
                st = f.stat()
                h.update(f"{f.relative_to(root).as_posix()}|{st.st_size}|{st.st_mtime_ns}\n".encode())
    return h.hexdigest()[:16]


class Cache:
    """Verdicts keyed by quote + place. An entry is reused only for the same evidence:
    raw content, page map, conversion record, threshold, strictness and verdict code.
    A ``not_found`` entry also depends on the other raw files (they may hold the quote)."""

    def __init__(self, path: Path | None):
        self.path = Path(path) if path else None
        self.entries: dict = {}
        if self.path and self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if data.get("schema") == CACHE_SCHEMA and isinstance(data.get("entries"), dict):
                    self.entries = data["entries"]
            except (OSError, ValueError, AttributeError):
                self.entries = {}

    def get(self, key: str, fingerprint: dict, corpus: str) -> dict | None:
        if self.path is None:
            return None
        entry = self.entries.get(key)
        if not entry or entry.get("fp") != fingerprint:
            return None
        if entry.get("corpus") is not None and entry["corpus"] != corpus:
            return None
        return entry["result"]

    def put(self, key: str, fingerprint: dict, corpus: str, result: Result) -> None:
        if self.path is None:
            return
        self.entries[key] = {
            "fp": fingerprint,
            "corpus": corpus if result.verdict == "not_found" else None,
            "result": asdict(result),
        }

    def prune(self, keep: set) -> None:
        self.entries = {k: v for k, v in self.entries.items() if k in keep}

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"schema": CACHE_SCHEMA, "entries": self.entries}, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class RawStore:
    """Loads cited raw files once; refuses private zones, PDFs and anything outside the vault."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self._docs: dict[str, tuple[Doc | None, str]] = {}
        self._meta: dict[str, tuple[dict | None, dict | None, str, str]] = {}

    def load(self, rel: str) -> tuple[Doc | None, str]:
        if rel in self._docs:
            return self._docs[rel]
        path = (self.root / rel).resolve()
        suffix = path.suffix.lower()
        if self.root not in path.parents:
            result: tuple[Doc | None, str] = (None, "path is outside the vault")
        elif vault_config.in_private_zone(self.root, path):
            result = (None, "raw file is inside a private zone (not read)")
        elif suffix == ".pdf":
            result = (None, "cited file is a PDF; cite the converted markdown under raw/converted/")
        elif suffix not in RAW_SUFFIXES:
            result = (None, f"unsupported raw file type {suffix or '(none)'}")
        elif not path.is_file():
            result = (None, "raw file missing")
        else:
            result = (Doc(path.read_text(encoding="utf-8", errors="replace")), "")
        self._docs[rel] = result
        return result

    def sidecars(self, rel: str) -> tuple[dict | None, dict | None, str, str]:
        """``(convert.json, pages.json, convert_sha, pages_sha)`` next to a converted markdown file."""
        if rel in self._meta:
            return self._meta[rel]
        path = (self.root / rel)
        convert = pages = None
        convert_sha = pages_sha = ""
        if "converted" in path.parts:
            for suffix, slot in ((".convert.json", "convert"), (".pages.json", "pages")):
                f = path.with_name(path.stem + suffix)
                if f.is_file():
                    data = f.read_bytes()
                    try:
                        parsed = json.loads(data)
                    except ValueError:
                        parsed = None
                    if slot == "convert":
                        convert, convert_sha = parsed, sha256_bytes(data)
                    else:
                        pages, pages_sha = parsed, sha256_bytes(data)
        self._meta[rel] = (convert, pages, convert_sha, pages_sha)
        return self._meta[rel]

    def dropped(self, rel: str) -> tuple[Doc | None, str]:
        """The ``<slug>.dropped.txt`` next to a converted markdown file, and its sha256."""
        path = self.root / rel
        f = path.with_name(path.stem + ".dropped.txt")
        if "converted" not in path.parts or not f.is_file():
            return None, ""
        data = f.read_bytes()
        return Doc(data.decode("utf-8", errors="replace")), sha256_bytes(data)

    def others(self, exclude: str):
        """Other raw documents, for finding a quote cited to the wrong file (capped)."""
        files = size = 0
        for f in sorted((self.root / "raw").rglob("*")):
            if not f.is_file() or f.suffix.lower() not in RAW_SUFFIXES:
                continue
            rel = f.relative_to(self.root).as_posix()
            if rel == exclude:
                continue
            size += f.stat().st_size
            files += 1
            if files > OTHER_RAW_MAX_FILES or size > OTHER_RAW_MAX_BYTES:
                return
            doc, _ = self.load(rel)
            if doc is not None:
                yield rel, doc


def lines_to_pages(pages_meta: dict | None, matched: list[int]) -> str | None:
    """PDF pages (``"2"`` or ``"2-3"``) covering the matched markdown lines, from pages.json."""
    if not pages_meta or not matched:
        return None
    first, last = matched[0], matched[-1]
    hit = [p["page"] for p in pages_meta.get("pages", [])
           if p["line_start"] <= last and p["line_end"] >= first]
    if not hit:
        return None
    return str(min(hit)) if min(hit) == max(hit) else f"{min(hit)}-{max(hit)}"
