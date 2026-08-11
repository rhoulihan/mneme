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
