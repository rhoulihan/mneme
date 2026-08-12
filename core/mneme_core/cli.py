"""mneme CLI — deterministic operations behind bin/mneme (spec §4.1)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, flags, lint, paths, registry, scan, staging
from .errors import MnemeError


class _Parser(argparse.ArgumentParser):
    """Argparse parser whose usage errors honour the mneme exit-code contract.

    Stock argparse exits 2 on a bad argument, but 2 is reserved for findings
    (scan blockers / lint errors). Raising MnemeError instead routes usage errors
    through main()'s handler, which reports them on stderr and exits 1.
    Subparsers inherit this class, so their errors take the same path.
    """

    def error(self, message: str) -> None:  # type: ignore[override]
        raise MnemeError(f"{message} (try 'mneme --help')")


def _build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="mneme")
    # store_true, not action="version": the latter raises SystemExit, which would
    # escape main() and break in-process testing of the exit-code contract.
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--home", type=Path, default=None, help="override MNEME_HOME")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("init")
    sub.add_parser("home")
    sub.add_parser("context")

    p_flag = sub.add_parser("flag")
    p_flag.add_argument("text")
    p_flag.add_argument("--kind", default="golden-path", choices=sorted(flags.KINDS))
    p_flag.add_argument("--session", default=None)

    p_reg = sub.add_parser("registry")
    reg_sub = p_reg.add_subparsers(dest="registry_command", required=True)
    p_add = reg_sub.add_parser("add")
    p_add.add_argument("name")
    p_add.add_argument("--repo", required=True)
    p_add.add_argument("--path", default=None)
    p_add.add_argument("--mode", default="pr", choices=sorted(registry.MODES))
    p_add.add_argument(
        "--sensitivity", default="internal", choices=sorted(registry.SENSITIVITIES)
    )
    p_add.add_argument("--exclude", action="append", default=[])
    reg_sub.add_parser("list")
    p_rm = reg_sub.add_parser("remove")
    p_rm.add_argument("name")

    p_stage = sub.add_parser("stage")
    stage_sub = p_stage.add_subparsers(dest="stage_command", required=True)
    p_slist = stage_sub.add_parser("list")
    p_slist.add_argument("--all", action="store_true")

    p_scan = sub.add_parser("scan")
    p_scan.add_argument("path")

    p_lint = sub.add_parser("lint")
    p_lint.add_argument("path", type=Path)

    p_index = sub.add_parser("index")
    index_sub = p_index.add_subparsers(dest="index_command", required=True)
    index_sub.add_parser("rebuild")
    index_sub.add_parser("status")

    p_srch = sub.add_parser("search")
    p_srch.add_argument("query")
    p_srch.add_argument("--k", type=int, default=10)
    p_srch.add_argument("--kind", choices=["skill", "fact"], default=None)
    p_srch.add_argument("--plugin", default=None)

    p_new = sub.add_parser("new")
    p_new.add_argument("name")
    p_new.add_argument("--dir", type=Path, default=None)
    p_new.add_argument("--description", default="")
    p_new.add_argument("--owner", default="maintainers")
    p_new.add_argument("--repo", default="")
    p_new.add_argument("--mode", default="pr", choices=sorted(registry.MODES))
    p_new.add_argument(
        "--sensitivity", default="internal", choices=sorted(registry.SENSITIVITIES)
    )

    p_distill = sub.add_parser("distill")
    distill_sub = p_distill.add_subparsers(dest="distill_command", required=True)
    p_prep = distill_sub.add_parser("prepare")
    p_prep.add_argument("--transcript", default="(not provided)")

    p_db = sub.add_parser("db")
    db_sub = p_db.add_subparsers(dest="db_command", required=True)
    p_query = db_sub.add_parser("query")
    p_query.add_argument("sql")
    db_sub.add_parser("enable")
    db_sub.add_parser("disable")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
        if args.version:
            print(__version__)
            return 0
        home = args.home if args.home is not None else paths.mneme_home()

        if args.command == "init":
            paths.ensure_layout(home)
            if not paths.registry_path(home).exists():
                registry.save_registry(home, [])
            print(str(home))
            return 0
        if args.command == "home":
            print(str(home))
            return 0
        if args.command == "context":
            from . import routing, templates

            print(templates.NOTICING_BRIEF)
            scope_list = routing.scopes(home)
            if not scope_list:
                print("Registered knowledge plugins: none — run 'mneme new <name>' to create one.")
                return 0
            print("Registered knowledge plugins:")
            for s in scope_list:
                first = s.statement.splitlines()[0] if s.statement else "(no scope statement)"
                print(f"- {s.name} [{s.sensitivity}/{s.mode}]: {first}")
            return 0
        if args.command == "flag":
            flags.add_flag(home, args.text, kind=args.kind, session=args.session)
            print("flagged")
            return 0
        if args.command == "registry":
            return _registry_cmd(home, args)
        if args.command == "stage":
            for cand in staging.load_candidates(home, include_quarantined=args.all):
                print(f"{cand.id}  {cand.type}/{cand.edit}  {cand.target}  {cand.status}")
            return 0
        if args.command == "scan":
            return _scan_cmd(args.path)
        if args.command == "lint":
            return _lint_cmd(args.path)
        if args.command == "index":
            return _index_cmd(home, args)
        if args.command == "search":
            return _search_cmd(home, args)
        if args.command == "distill":
            return _distill_cmd(home, args)
        if args.command == "db":
            return _db_cmd(home, args)
        if args.command == "new":
            from . import scaffold

            target = scaffold.create(
                home,
                args.name,
                directory=args.dir,
                description=args.description,
                owner=args.owner,
                repo_url=args.repo,
                mode=args.mode,
                sensitivity=args.sensitivity,
            )
            print(f"created {target}")
            print(f"registered {args.name}")
            return 0
        parser.print_help()
        return 1
    except MnemeError as e:
        print(f"mneme: {e}", file=sys.stderr)
        return 1
    except (OSError, UnicodeDecodeError) as e:
        # Unreadable/missing/binary files must fail gracefully like any MnemeError,
        # never as a raw traceback.
        print(f"mneme: {e}", file=sys.stderr)
        return 1


def _registry_cmd(home: Path, args: argparse.Namespace) -> int:
    if args.registry_command == "add":
        plugin_path = args.path or str(paths.repos_dir(home) / args.name)
        registry.add_plugin(
            home,
            registry.Plugin(
                name=args.name,
                repo=args.repo,
                path=plugin_path,
                mode=args.mode,
                sensitivity=args.sensitivity,
                exclusions=args.exclude,
            ),
        )
        print(f"registered {args.name}")
        return 0
    if args.registry_command == "list":
        for p in registry.load_registry(home):
            print(f"{p.name}  {p.mode}  {p.sensitivity}  {p.repo}")
        return 0
    if args.registry_command == "remove":
        registry.remove_plugin(home, args.name)
        print(f"removed {args.name}")
        return 0
    return 1


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as e:
        raise MnemeError(f"cannot read {path}: {e.strerror or e}") from e


def _scan_cmd(path_arg: str) -> int:
    if path_arg == "-":
        text = sys.stdin.read()
    else:
        text = _read_text(Path(path_arg))
    findings = scan.scan_text(text)
    for f in findings:
        print(f"{f.rule} {f.severity} {f.line_no} {f.excerpt}")
    return 2 if scan.has_blockers(findings) else 0


def _lint_cmd(target: Path) -> int:
    if not target.exists():
        raise MnemeError(f"no such path: {target}")
    if target.is_dir() and (target / "SKILL.md").exists():
        issues = lint.lint_skill(target)
    elif target.is_dir():
        issues = lint.lint_repo(target)
    elif target.suffix == ".md":
        issues = lint.lint_fact_file(target)
    else:
        raise MnemeError(f"cannot lint: {target}")
    for i in issues:
        print(f"{i.path}:{i.line} {i.code} {i.severity} {i.message}")
    return 2 if lint.has_errors(issues) else 0


def _require_index_db(home: Path):
    from mneme_index import db as index_db

    db_file = paths.db_path(home)
    if not db_file.exists():
        raise MnemeError("index not built (run: mneme index rebuild)")
    return index_db.open_db_readonly(db_file)


def _index_cmd(home: Path, args: argparse.Namespace) -> int:
    if args.index_command == "rebuild":
        from . import indexing

        for s in indexing.rebuild(home):
            print(
                f"indexed {s.plugin}: {s.skills} skills,"
                f" {s.facts} facts, {len(s.skipped)} skipped"
            )
            for sk in s.skipped:
                print(f"skipped: {sk}")
        return 0
    if args.index_command == "status":
        from mneme_index import search as index_search

        conn = _require_index_db(home)
        try:
            st = index_search.status(conn)
        finally:
            conn.close()
        for p in st["plugins"]:
            print(f"{p['name']}  skills={p['skills']}  facts={p['facts']}  built_at={p['built_at']}")
        print(f"total_units={st['total_units']}")
        return 0
    return 1


def _search_cmd(home: Path, args: argparse.Namespace) -> int:
    from mneme_index import search as index_search

    conn = _require_index_db(home)
    try:
        hits = index_search.search(conn, args.query, k=args.k, kind=args.kind, plugin=args.plugin)
    finally:
        conn.close()
    for h in hits:
        print(f"{h['score']:.2f}\t{h['plugin']}\t{h['id']}\t{h['description']}")
    return 0


def _readonly_authorizer(action, arg1, arg2, dbname, source):
    """Defense in depth on top of the read-only connection.

    Denies the actions that could reach outside the index or mutate connection
    state (ATTACH/DETACH/PRAGMA); everything else a SELECT needs is allowed.
    """
    import sqlite3

    if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH, sqlite3.SQLITE_PRAGMA):
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _db_cmd(home: Path, args: argparse.Namespace) -> int:
    if args.db_command == "enable":
        from mneme_index import db as index_db

        from . import indexing, registry as registry_mod

        paths.ensure_layout(home)
        if registry_mod.load_registry(home):
            for s in indexing.rebuild(home):
                print(
                    f"indexed {s.plugin}: {s.skills} skills,"
                    f" {s.facts} facts, {len(s.skipped)} skipped"
                )
        else:
            index_db.open_db(paths.db_path(home)).close()
        print(f"index enabled at {paths.db_path(home)}")
        return 0
    if args.db_command == "disable":
        db_file = paths.db_path(home)
        if db_file.exists():
            db_file.unlink()
        print("index disabled")
        return 0

    import sqlite3

    if not args.sql.lstrip().lower().startswith("select"):
        raise MnemeError("only SELECT queries are allowed")
    conn = _require_index_db(home)
    conn.set_authorizer(_readonly_authorizer)
    try:
        try:
            rows = conn.execute(args.sql).fetchall()
        except sqlite3.Error as e:
            raise MnemeError(f"query failed: {e}")
        for r in rows:
            print("\t".join(str(v) for v in tuple(r)))
    finally:
        conn.close()
    return 0


def _distill_cmd(home: Path, args: argparse.Namespace) -> int:
    if args.distill_command == "prepare":
        import json as json_mod

        from . import flags as flags_mod
        from . import routing, templates

        scope_list = routing.scopes(home)
        if scope_list:
            scope_lines = "\n".join(
                f"- {s.name} [{s.sensitivity}/{s.mode}]:"
                f" {' '.join(s.statement.split()) or '(no scope statement)'}"
                for s in scope_list
            )
        else:
            scope_lines = "- (none registered)"
        flag_records = flags_mod.read_flags(home)
        flag_lines = (
            "\n".join(json_mod.dumps(f) for f in flag_records)
            if flag_records
            else "(no flags this session)"
        )
        prompt = templates.render(
            templates.DISTILLER_PROMPT,
            scopes=scope_lines,
            flags=flag_lines,
            transcript_path=args.transcript,
        )
        print(json_mod.dumps({"prompt": prompt, "flag_count": len(flag_records)}))
        return 0
    return 1
