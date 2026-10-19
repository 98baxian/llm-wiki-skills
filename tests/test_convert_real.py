"""Real MinerU conversion. Opt in with: LLM_WIKI_TEST_PDF=/path/to/any.pdf pytest -m slow"""
import json
import os
import shutil

import pytest

import convert_source

pytestmark = pytest.mark.slow

PDF = os.environ.get("LLM_WIKI_TEST_PDF", "")


@pytest.mark.skipif(not PDF or not os.path.isfile(PDF), reason="set LLM_WIKI_TEST_PDF to a PDF")
@pytest.mark.skipif(convert_source.find_mineru_kit() is None, reason="mineru-kit not installed")
def test_real_mineru_conversion(tmp_path):
    root = tmp_path / "vault"
    (root / "raw" / "pdfs").mkdir(parents=True)
    pdf = root / "raw" / "pdfs" / "sample.pdf"
    shutil.copy(PDF, pdf)
    cfg = convert_source.Config(converter="mineru", tier="basic", timeout=1800)
    res = convert_source.convert(pdf, root, config=cfg)
    meta = json.loads((res.directory / "sample.convert.json").read_text())
    pages = json.loads((res.directory / "sample.pages.json").read_text())
    assert res.md_path.stat().st_size > 0
    assert meta["converter"] == "mineru-kit" and meta["lossy"] is False and meta["tier"] == "basic"
    assert pages["pages"] and pages["pages"][0]["line_start"] == 1
    assert "data:image" not in res.md_path.read_text()          # images are files, not base64
