"""mneme-index CLI — standalone, tool-agnostic index over skill/fact trees."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from mneme_core.errors import MnemeError

from . import build as build_mod
from . import db as db_mod
from . import search as search_mod


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mneme-index")
    parser.add_argument("--db", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build")
    p_build.add_argument("root", type=Path)
    p_build.add_argument("--name", default=None)

    p_search = sub.add_parser("search")
    p_search.add_argument("query")
    p_search.add_argument("--k", type=int, default=10)
    p_search.add_argument("--kind", choices=["skill", "fact"], default=None)
    p_search.add_argument("--plugin", default=None)
    p_search.add_argument("--json", action="store_true")

    p_facts = sub.add_parser("facts")
    p_facts.add_argument("--category", default=None)
    p_facts.add_argument("--tag", default=None)
    p_facts.add_argument("--plugin", default=None)
    p_facts.add_argument("--topic", default=None)
    p_facts.add_argument("--json", action="store_true")

    sub.add_parser("status")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "build":
            conn = db_mod.open_db(args.db)
            try:
                name = args.name or args.root.resolve().name
                stats = build_mod.index_tree(conn, name, args.root)
            finally:
                conn.close()
            print(
                f"indexed {stats.plugin}: {stats.skills} skills,"
                f" {stats.facts} facts, {len(stats.skipped)} skipped"
            )
            for s in stats.skipped:
                print(f"skipped: {s}")
            return 0

        conn = db_mod.open_db_readonly(args.db)
        try:
            if args.command == "search":
                hits = search_mod.search(
                    conn, args.query, k=args.k, kind=args.kind, plugin=args.plugin
                )
                if args.json:
                    print(json.dumps(hits))
                else:
                    for h in hits:
                        print(f"{h['score']:.2f}\t{h['plugin']}\t{h['id']}\t{h['description']}")
                return 0
            if args.command == "facts":
                rows = search_mod.list_facts(
                    conn,
                    category=args.category,
                    tag=args.tag,
                    plugin=args.plugin,
                    topic=args.topic,
                )
                if args.json:
                    print(json.dumps(rows))
                else:
                    for r in rows:
                        print(f"{r['plugin']}\t{r['id']}\t[{r['category']}]\t{r['description']}")
                return 0
            if args.command == "status":
                st = search_mod.status(conn)
                for p in st["plugins"]:
                    print(
                        f"{p['name']}  skills={p['skills']}  facts={p['facts']}"
                        f"  built_at={p['built_at']}  root={p['root']}"
                    )
                print(f"total_units={st['total_units']}")
                return 0
        finally:
            conn.close()
        return 1
    except MnemeError as e:
        print(f"mneme-index: {e}", file=sys.stderr)
        return 1
