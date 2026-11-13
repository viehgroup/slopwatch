import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import List

from . import __version__
from .parse import Pkg, parse_command, read_manifest
from .registry import PackageInfo, Registry, RegistryError
from .scoring import BLOCK, OK, WARN, Verdict, score

EXIT_OK, EXIT_WARN, EXIT_BLOCK, EXIT_USAGE = 0, 1, 2, 64


def _color(enabled: bool):
    codes = {OK: "32", WARN: "33", BLOCK: "31", "dim": "2", "bold": "1"}

    def paint(text: str, key: str) -> str:
        return f"\033[{codes[key]}m{text}\033[0m" if enabled else text
    return paint


def check_all(pkgs: List[Pkg], registry: Registry, jobs: int = 8) -> List[Verdict]:
    seen, unique = set(), []
    for eco, name in pkgs:
        key = (eco, name.lower())
        if key not in seen:
            seen.add(key)
            unique.append((eco, name))

    def one(p: Pkg) -> Verdict:
        eco, name = p
        try:
            return score(registry.lookup(eco, name))
        except RegistryError as e:
            # Fail open on network trouble, but say so loudly.
            v = Verdict(eco, name, 0, WARN, [], PackageInfo(eco, name, exists=True))
            v.error = str(e)
            return v

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        return list(pool.map(one, unique))


def render(verdicts: List[Verdict], color: bool, verbose: bool) -> str:
    paint = _color(color)
    lines = []
    width = max((len(v.name) for v in verdicts), default=0)
    for v in verdicts:
        tag = {OK: "  ok ", WARN: " warn", BLOCK: "block"}[v.level]
        head = f"{paint(tag, v.level)}  {v.ecosystem:<4}  {v.name:<{width}}  {paint(f'score {v.score:>3}', 'dim')}"
        lines.append(head)
        if v.error:
            lines.append(f"         {paint('could not reach registry: ' + v.error, WARN)}")
        if v.level != OK or verbose:
            for f in v.findings:
                pts = f"+{f.points}" if f.points else "  "
                lines.append(f"         {paint(pts.rjust(4), 'dim')}  {f.message}")
    return "\n".join(lines)


def worst(verdicts: List[Verdict]) -> str:
    levels = {v.level for v in verdicts}
    if BLOCK in levels:
        return BLOCK
    if WARN in levels:
        return WARN
    return OK


def exit_code(level: str, strict: bool) -> int:
    if level == BLOCK:
        return EXIT_BLOCK
    if level == WARN and strict:
        return EXIT_WARN
    return EXIT_OK


def _output(verdicts, args) -> None:
    if args.json:
        print(json.dumps([v.to_dict() for v in verdicts], indent=2))
    else:
        use_color = sys.stdout.isatty() and not args.no_color and "NO_COLOR" not in os.environ
        print(render(verdicts, use_color, args.verbose))


def cmd_check(args) -> int:
    pkgs = [(args.ecosystem, n) for n in args.packages]
    verdicts = check_all(pkgs, Registry(use_cache=not args.no_cache), args.jobs)
    _output(verdicts, args)
    return exit_code(worst(verdicts), args.strict)


def cmd_scan(args) -> int:
    pkgs: List[Pkg] = []
    for path in args.files:
        if not os.path.exists(path):
            print(f"slopwatch: {path}: no such file", file=sys.stderr)
            return EXIT_USAGE
        pkgs.extend(read_manifest(path))
    if not pkgs:
        print("slopwatch: no registry packages found", file=sys.stderr)
        return EXIT_OK
    verdicts = check_all(pkgs, Registry(use_cache=not args.no_cache), args.jobs)
    _output(verdicts, args)
    return exit_code(worst(verdicts), args.strict)


def cmd_run(args) -> int:
    argv = args.command
    if argv and argv[0] == "--":
        argv = argv[1:]
    if not argv:
        print("slopwatch run: nothing to run", file=sys.stderr)
        return EXIT_USAGE

    pkgs = parse_command(argv)
    if pkgs:
        verdicts = check_all(pkgs, Registry(use_cache=not args.no_cache), args.jobs)
        flagged = [v for v in verdicts if v.level != OK]
        if flagged:
            print(render(flagged, sys.stderr.isatty(), args.verbose), file=sys.stderr)
        level = worst(verdicts)
        if level == BLOCK and not args.force:
            print("slopwatch: refusing to install. Re-run with --force if you're sure.", file=sys.stderr)
            return EXIT_BLOCK
        if level == WARN and args.strict and not args.force:
            print("slopwatch: warnings present and --strict is on.", file=sys.stderr)
            return EXIT_WARN

    try:
        return subprocess.call(argv)
    except FileNotFoundError:
        print(f"slopwatch: command not found: {argv[0]}", file=sys.stderr)
        return 127


def cmd_hook(args) -> int:
    """Claude Code PreToolUse hook. Reads the tool call as JSON on stdin.

    Exit code 2 tells Claude Code to block the call and shows our stderr
    to the model, so it gets a chance to pick a real package instead.
    """
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return EXIT_OK
    if event.get("tool_name") != "Bash":
        return EXIT_OK
    command = (event.get("tool_input") or {}).get("command") or ""

    pkgs: List[Pkg] = []
    for segment in _split_shell(command):
        try:
            found = parse_command(segment)
        except ValueError:
            continue
        if found:
            pkgs.extend(found)
    if not pkgs:
        return EXIT_OK

    verdicts = check_all(pkgs, Registry(), args.jobs)
    level = worst(verdicts)
    # An agent has nobody watching the terminal, so warnings block by default.
    if level == BLOCK or (level == WARN and not args.lenient):
        bad = [v for v in verdicts if v.level != OK]
        print("slopwatch blocked this install:\n" + render(bad, False, False), file=sys.stderr)
        print("Double-check the package name. Do not retry the same name without confirming "
              "it is the intended package.", file=sys.stderr)
        return EXIT_BLOCK
    return EXIT_OK


def _split_shell(command: str) -> List[str]:
    """Split on &&, ||, ; and | so `cd x && pip install y` is still seen."""
    import re
    return [p.strip() for p in re.split(r"&&|\|\||;|\||\n", command) if p.strip()]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="slopwatch",
        description="Catch hallucinated, typosquatted and brand-new packages before they get installed.",
    )
    p.add_argument("--version", action="version", version=f"slopwatch {__version__}")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="machine-readable output")
    common.add_argument("--strict", action="store_true", help="treat warnings as failures")
    common.add_argument("-v", "--verbose", action="store_true", help="show findings for clean packages too")
    common.add_argument("--no-color", action="store_true")
    common.add_argument("--no-cache", action="store_true")
    common.add_argument("-j", "--jobs", type=int, default=8, help="parallel lookups (default 8)")

    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", parents=[common], help="check package names directly")
    c.add_argument("ecosystem", choices=["pypi", "npm"])
    c.add_argument("packages", nargs="+")
    c.set_defaults(func=cmd_check)

    s = sub.add_parser("scan", parents=[common],
                       help="scan requirements.txt, pyproject.toml or package.json")
    s.add_argument("files", nargs="+")
    s.set_defaults(func=cmd_scan)

    r = sub.add_parser("run", parents=[common],
                       help="check an install command, then run it if it's clean")
    r.add_argument("--force", action="store_true", help="run even if something is blocked")
    r.add_argument("command", nargs=argparse.REMAINDER)
    r.set_defaults(func=cmd_run)

    h = sub.add_parser("hook", parents=[common], help="Claude Code PreToolUse hook (reads stdin)")
    h.add_argument("--lenient", action="store_true", help="only block on 'block', let warnings through")
    h.set_defaults(func=cmd_hook)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except BrokenPipeError:
        # e.g. `slopwatch ... | head`
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        return EXIT_OK
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
