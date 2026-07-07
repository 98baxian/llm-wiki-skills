"""Shared pytest fixtures + path setup for the llm-wiki script tests.

The scripts under ``llm-wiki/scripts/`` are plain modules (underscore names), so we
add that directory to ``sys.path`` and import them by name.
"""
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "llm-wiki" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture
def scaffolded_vault(tmp_path):
    """Scaffold a fresh monolingual vault in a temp dir and return its root Path.

    Uses ``bundle=False`` semantics implicitly — scaffold always bundles the skill,
    but into a temp dir that pytest cleans up, so it is harmless here.
    """
    import scaffold as scaffold_mod

    root = tmp_path / "vault"
    scaffold_mod.scaffold(
        str(root),
        "Test Wiki",
        domains=["AI", "Finance"],
        lang="none",
        primary_lang="English",
        private_zones=[],
    )
    return root
