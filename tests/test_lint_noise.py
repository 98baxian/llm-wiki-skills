"""Lint noise reductions: CJK-aware stub check, bookkeeping lines skipped by the uncited
check, folder index pages ignored by the near-duplicate check."""
import lint_wiki


def _reset():
    lint_wiki.errors.clear()
    lint_wiki.warnings.clear()
    lint_wiki.suggestions.clear()


def _page(root, rel, body):
    p = root / "wiki" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("---\ntype: concept\ntitle: T\n---\n" + body, encoding="utf-8")
    return p


def _issues(root, check):
    _reset()
    check(root)
    return list(lint_wiki.warnings)


# ── stubs ────────────────────────────────────────────────────────────────────

def test_long_cjk_page_is_not_a_stub(tmp_path):
    _page(tmp_path, "concepts/a.md", "# A\n\n" + "这是一段没有空格的中文正文内容，" * 20 + "\n")
    out = _issues(tmp_path, lambda r: lint_wiki.check_stubs(r / "wiki", r))
    assert not any("stub" in w for w in out)


def test_short_cjk_page_is_still_a_stub(tmp_path):
    _page(tmp_path, "concepts/a.md", "# A\n\n很短。\n")
    out = _issues(tmp_path, lambda r: lint_wiki.check_stubs(r / "wiki", r))
    assert any("stub" in w for w in out)


# ── uncited ──────────────────────────────────────────────────────────────────

CLAIM = "- The model has a very large number of parameters and trains for a long time on data."
CJK_CLAIM = "- 这是一个没有任何引用的中文事实句子，声称某个模型拥有非常多的参数，并且在大规模的数据上训练了很长很长的一段时间。"


def _uncited(root):
    return _issues(root, lambda r: lint_wiki.check_uncited(r / "wiki", r))


def test_real_uncited_claims_still_flagged(tmp_path):
    _page(tmp_path, "concepts/a.md", f"# A\n\n{CLAIM}\n{CJK_CLAIM}\n")
    assert len(_uncited(tmp_path)) == 2


def test_evolution_log_in_either_language_is_skipped(tmp_path):
    _page(tmp_path, "concepts/a.md", f"# A\n\n## 演化日志\n\n{CJK_CLAIM}\n\n## Evolution Log\n\n{CLAIM}\n")
    assert _uncited(tmp_path) == []


def test_navigation_and_bookkeeping_bullets_are_skipped(tmp_path):
    body = (
        "# A\n\n"
        "- [[concepts/b]] — 一个很长的链接说明文字，用来测试导航行不会被误判为事实句子而产生警告。\n"
        "- 2026-07-27：首次建立，整合了五份来源并记录了加法与减法两派之间的冲突和取舍情况。\n"
        "- 提示词写法：[[concepts/video-prompt-engineering/index]]（分加法和减法两派的写法）\n"
    )
    _page(tmp_path, "concepts/a.md", body)
    assert _uncited(tmp_path) == []


# ── near-duplicates ──────────────────────────────────────────────────────────

def test_folder_index_pages_are_not_near_duplicates(tmp_path):
    _page(tmp_path, "concepts/x/index.md", "# X\n")
    _page(tmp_path, "concepts/y/index.md", "# Y\n")
    out = _issues(tmp_path, lambda r: lint_wiki.check_near_duplicates(r / "wiki"))
    assert out == []


def test_similar_slugs_still_flagged(tmp_path):
    _page(tmp_path, "concepts/risk-model.md", "# R\n")
    _page(tmp_path, "concepts/risk-models.md", "# R\n")
    out = _issues(tmp_path, lambda r: lint_wiki.check_near_duplicates(r / "wiki"))
    assert len(out) == 1


# ── contradiction placeholders ───────────────────────────────────────────────

def test_dressed_up_placeholders_are_not_contradictions():
    for marker in ("*(暂无)*", "（无）", "_None yet_", "*(none)*", "N/A", "暂无。"):
        body = f"## Contradictions\n\n{marker}\n"
        assert lint_wiki.contradiction_entries(body) == [], marker


def test_real_contradiction_entry_is_still_listed():
    body = "## Contradictions\n\nSource A says 5, source B says 7.\n"
    assert len(lint_wiki.contradiction_entries(body)) == 1


# ── claims_requiring_verification with ellipsis ──────────────────────────────

def _source_with_claim(root, claim, raw_text):
    raw = root / "raw" / "x.md"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(raw_text, encoding="utf-8")
    p = root / "wiki" / "sources" / "s.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "---\ntype: source-summary\ntitle: S\nraw_file: raw/x.md\n"
        f"claims_requiring_verification:\n  - \"{claim}\" (raw/x.md, L1)\n---\n# S\n",
        encoding="utf-8",
    )


def _claim_errors(root):
    _reset()
    lint_wiki.check_claims_verification(root / "wiki", root)
    return list(lint_wiki.errors)


def test_claim_with_ellipsis_checks_each_fragment(tmp_path):
    _source_with_claim(tmp_path, "亚马逊...最多五百亿", "亚马逊都是核心投资人，承诺最多五百亿。")
    assert _claim_errors(tmp_path) == []


def test_claim_with_ellipsis_still_fails_when_a_fragment_is_missing(tmp_path):
    _source_with_claim(tmp_path, "亚马逊...最多九百亿", "亚马逊都是核心投资人，承诺最多五百亿。")
    assert len(_claim_errors(tmp_path)) == 1
