"""Lint checks 17-19: page kinds, typed relations, citation targets. A vault that declares none of
it is untouched."""
import json

import export_graph
import lint_wiki
import schema

CLAUDE = """# Test KB

## Page types
- variant: dir=concepts; required=hgvs_c,acmg_class; enum.acmg_class=Pathogenic|VUS|Benign
- gene: dir=entities; required=gene_symbol

## Relation types
- causes
- treats
"""


def _vault(tmp_path, claude=CLAUDE):
    for d in ("wiki/concepts", "wiki/entities", "raw"):
        (tmp_path / d).mkdir(parents=True)
    (tmp_path / "CLAUDE.md").write_text(claude, encoding="utf-8")
    (tmp_path / "raw" / "case.md").write_text("line one\nPVS1 fires ^ev-pvs1\nthird\n", encoding="utf-8")
    return tmp_path


def _page(root, rel, front, body="# T\n"):
    p = root / "wiki" / rel
    p.write_text(f"---\ntype: concept\ndate: 2026-11-01\n{front}---\n\n{body}", encoding="utf-8")


def _run(check, root):
    lint_wiki.errors.clear(); lint_wiki.warnings.clear(); lint_wiki.suggestions.clear()
    check(root / "wiki", root)
    return list(lint_wiki.errors), list(lint_wiki.warnings)


def test_page_types_and_relation_types_parse(tmp_path):
    root = _vault(tmp_path)
    kinds = schema.load_page_types(root)
    assert kinds["variant"] == {"dir": "concepts", "required": ["hgvs_c", "acmg_class"],
                                "enum": {"acmg_class": ["Pathogenic", "VUS", "Benign"]}}
    assert kinds["gene"]["required"] == ["gene_symbol"]
    assert schema.load_relation_types(root) == {"causes", "treats"}


def test_valid_kind_passes(tmp_path):
    root = _vault(tmp_path)
    _page(root, "concepts/v1.md", "kind: variant\nhgvs_c: c.1A>G\nacmg_class: VUS\n")
    assert _run(lint_wiki.check_page_kinds, root) == ([], [])


def test_kind_missing_required_and_bad_enum(tmp_path):
    root = _vault(tmp_path)
    _page(root, "concepts/v1.md", "kind: variant\nacmg_class: Maybe\n")
    errors, _ = _run(lint_wiki.check_page_kinds, root)
    assert any("requires 'hgvs_c'" in e for e in errors)
    assert any("'Maybe'" in e for e in errors)


def test_undeclared_kind_and_wrong_dir_warn(tmp_path):
    root = _vault(tmp_path)
    _page(root, "concepts/g1.md", "kind: gene\ngene_symbol: X\n")
    _page(root, "concepts/d1.md", "kind: drug\n")
    errors, warnings = _run(lint_wiki.check_page_kinds, root)
    assert errors == []
    assert any("belongs in wiki/entities" in w for w in warnings)
    assert any("'drug' is not declared" in w for w in warnings)


def test_no_declarations_means_no_findings(tmp_path):
    root = _vault(tmp_path, claude="# Plain KB\n")
    _page(root, "concepts/v1.md", "kind: anything\n")
    assert _run(lint_wiki.check_page_kinds, root) == ([], [])
    assert _run(lint_wiki.check_relations, root) == ([], [])


def test_relation_with_anchor_evidence_passes(tmp_path):
    root = _vault(tmp_path)
    _page(root, "entities/drug-x.md", "")
    _page(root, "concepts/v1.md",
          'relations:\n  - {type: treats, target: entities/drug-x, evidence: "raw/case.md#ev-pvs1", '
          'confidence: high, as_of: 2026-11-01}\n  - {type: causes, target: "mondo:0005148"}\n')
    errors, warnings = _run(lint_wiki.check_relations, root)
    assert errors == []
    assert any("no evidence" in w for w in warnings)      # the external-id edge has none


def test_relation_problems(tmp_path):
    root = _vault(tmp_path)
    _page(root, "concepts/v1.md",
          "relations:\n"
          '  - {type: treats, target: entities/missing, evidence: "raw/case.md#nope"}\n'
          '  - {type: invents, target: "mondo:1", evidence: "raw/case.md#ev-pvs1", confidence: sure, as_of: May}\n'
          "  - not a mapping\n"
          "  - {target: entities/x}\n")
    errors, warnings = _run(lint_wiki.check_relations, root)
    text = "\n".join(errors + warnings)
    assert "target does not exist" in text
    assert "anchor #nope not found" in text
    assert "type 'invents'" in text
    assert "confidence must be" in text and "as_of must be" in text
    assert "unparseable relation" in text
    assert "needs both 'type' and 'target'" in text


def test_citation_targets(tmp_path):
    root = _vault(tmp_path)
    (root / "raw" / "dup.md").write_text("a ^d\nb ^d\n", encoding="utf-8")
    _page(root, "concepts/v1.md", "",
          "# T\n\n- ok (raw/case.md#ev-pvs1)\n- gone (raw/case.md#nope)\n- ambiguous (raw/dup.md#d)\n"
          "- past (raw/case.md, L2-9)\n- missing file (raw/none.md, L1)\n")
    errors, warnings = _run(lint_wiki.check_citation_targets, root)
    assert len(errors) == 2 and any("#nope" in e for e in errors) and any("ambiguous" in e for e in errors)
    assert any("past the end" in w for w in warnings) and any("none.md does not exist" in w for w in warnings)


def test_private_zone_is_never_opened(tmp_path):
    root = _vault(tmp_path, claude=CLAUDE + "\n## Private zones\n- `raw/secret`\n")
    (root / "raw" / "secret").mkdir()
    (root / "raw" / "secret" / "p.md").write_text("x ^a1\n", encoding="utf-8")
    _page(root, "concepts/v1.md", "", "# T\n\n- c (raw/secret/p.md#zzz)\n")
    assert _run(lint_wiki.check_citation_targets, root) == ([], [])


def test_export_graph(tmp_path):
    root = _vault(tmp_path)
    _page(root, "entities/drug-x.md", "title: Drug X\n")
    _page(root, "concepts/v1.md",
          'relations:\n  - {type: treats, target: entities/drug-x, evidence: "raw/case.md#ev-pvs1"}\n'
          '  - {type: causes, target: "mondo:0005148"}\n', body="# T\n\nSee [[entities/drug-x]].\n")
    graph = export_graph.build_graph(root, include_wikilinks=True)
    ids = {n["id"]: n for n in graph["nodes"]}
    assert ids["mondo:0005148"]["external"] is True and ids["entities/drug-x"]["title"] == "Drug X"
    typed = [e for e in graph["edges"] if e["typed"]]
    assert {(e["type"], e["target"]) for e in typed} == {("treats", "entities/drug-x"), ("causes", "mondo:0005148")}
    assert [e["type"] for e in graph["edges"] if not e["typed"]] == ["links_to"]
    json.dumps(graph)
