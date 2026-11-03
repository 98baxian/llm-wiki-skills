#!/usr/bin/env python3
"""
export_graph.py — export the wiki's typed relations as a graph (JSON) for a graph database.

Nodes are wiki pages (``category/stem``) plus every external id a relation points at
(``mondo:0005148``). Edges come from the ``relations:`` frontmatter of each page and keep their
type, evidence, confidence and as_of. Plain ``[[wikilinks]]`` are added with ``--wikilinks`` as
untyped ``links_to`` edges, so a consumer can tell curated edges from incidental ones.

Usage:
  python3 export_graph.py <wiki-root>                  # writes outputs/graph.json
  python3 export_graph.py <wiki-root> --wikilinks --out /tmp/graph.json
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

import schema
from lint_wiki import parse_frontmatter, wiki_files, wikilinks


def _node_id(wiki_dir: Path, f: Path) -> str:
    return f.relative_to(wiki_dir).with_suffix("").as_posix()


def build_graph(root: Path, include_wikilinks: bool = False) -> dict:
    wiki_dir = root / "wiki"
    pages = wiki_files(wiki_dir)
    nodes: dict[str, dict] = {}
    for f in pages:
        fm, _ = parse_frontmatter(f)
        nid = _node_id(wiki_dir, f)
        nodes[nid] = {"id": nid, "title": fm.get("title", f.stem), "type": fm.get("type", ""),
                      "kind": fm.get("kind", ""), "external": False}
    edges: list[dict] = []
    for f in pages:
        src = _node_id(wiki_dir, f)
        relations, _ = schema.parse_relations(f)
        for rel in relations:
            target = rel.get("target", "").strip("[]").split("|")[0].split("#")[0].strip()
            if not rel.get("type") or not target:
                continue
            if target not in nodes:
                nodes[target] = {"id": target, "title": target, "type": "", "kind": "",
                                 "external": schema.is_external_id(target)}
            edges.append({"source": src, "target": target, "type": rel["type"], "typed": True,
                          "evidence": rel.get("evidence", ""), "confidence": rel.get("confidence", ""),
                          "as_of": rel.get("as_of", "")})
        if include_wikilinks:
            _, body = parse_frontmatter(f)
            for link in sorted(set(wikilinks(body))):
                target = link.split("|")[0].split("#")[0].strip()
                if target in nodes and target != src:
                    edges.append({"source": src, "target": target, "type": "links_to", "typed": False,
                                  "evidence": "", "confidence": "", "as_of": ""})
    return {"generated": datetime.date.today().isoformat(), "nodes": list(nodes.values()), "edges": edges}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("wiki_root")
    parser.add_argument("--wikilinks", action="store_true", help="also export untyped [[wikilink]] edges")
    parser.add_argument("--out", help="output path (default <wiki-root>/outputs/graph.json)")
    args = parser.parse_args()
    root = Path(args.wiki_root)
    if not (root / "wiki").is_dir():
        print(f"ERROR: wiki/ directory not found at {root / 'wiki'}", file=sys.stderr)
        return 1
    graph = build_graph(root, args.wikilinks)
    out = Path(args.out) if args.out else root / "outputs" / "graph.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
    typed = sum(1 for e in graph["edges"] if e["typed"])
    print(f"{len(graph['nodes'])} nodes · {typed} typed edges · "
          f"{len(graph['edges']) - typed} wikilink edges → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
