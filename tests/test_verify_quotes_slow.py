"""Opt-in checks: performance on 1 MB of text, and a pass over a real vault.

    pytest -m slow                                         # performance guard
    VQ_REAL_VAULT=/path/to/vault pytest -m slow            # also read a COPY of that vault
"""
import os
import random
import shutil
import time

import pytest

import verify_quotes as vq

pytestmark = pytest.mark.slow


def test_fifty_misses_on_one_megabyte_stay_under_ten_seconds(tmp_path):
    rng = random.Random(11)
    vocab = [f"word{i}" for i in range(4000)]
    paragraphs = [" ".join(rng.choice(vocab) for _ in range(120)) for _ in range(1200)]       # ~1 MB
    raw = tmp_path / "raw" / "articles"
    raw.mkdir(parents=True)
    (raw / "big.md").write_text("\n".join(paragraphs) + "\n", encoding="utf-8")
    (tmp_path / "wiki" / "concepts").mkdir(parents=True)
    (tmp_path / "CLAUDE.md").write_text("# Wiki\n", encoding="utf-8")
    quotes = [" ".join(rng.choice(vocab) + "x" for _ in range(12)) for _ in range(50)]          # none of these occur
    page = "".join(f'Q{i}: "{q}" (raw/articles/big.md, L5)\n' for i, q in enumerate(quotes))
    (tmp_path / "wiki" / "concepts" / "x.md").write_text(page, encoding="utf-8")
    start = time.time()
    report = vq.verify_wiki(tmp_path, use_cache=False)
    elapsed = time.time() - start
    assert report["counts"]["not_found_error"] + report["counts"]["near_miss"] == 50
    assert elapsed < 10, f"{elapsed:.1f}s"


@pytest.mark.skipif(not os.environ.get("VQ_REAL_VAULT"), reason="set VQ_REAL_VAULT to a vault path")
def test_real_vault_copy_runs_and_second_run_is_cached(tmp_path):
    copy = tmp_path / "vault"
    shutil.copytree(os.environ["VQ_REAL_VAULT"], copy, ignore=shutil.ignore_patterns(".git", ".obsidian", "node_modules"))
    first = vq.verify_wiki(copy)
    c = first["counts"]
    assert first["pages_checked"] > 0 and sum(c[k] for k in ("exact", "fuzzy", "wrong_location", "near_miss", "not_found_error")) > 0
    second = vq.verify_wiki(copy)
    assert second["counts"]["from_cache"] > 0 and second["errors"] == first["errors"]
