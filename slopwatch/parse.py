"""Pull package names out of install commands and manifest files."""

import json
import os
import re
import shlex
from typing import Iterable, List, Optional, Sequence, Tuple

Pkg = Tuple[str, str]  # (ecosystem, name)

_PEP508_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")

# flags that take a value and so swallow the next argument
_PIP_VALUE_FLAGS = {
    "-i", "--index-url", "--extra-index-url", "-f", "--find-links", "-c", "--constraint",
    "-t", "--target", "--prefix", "--root", "--platform", "--python-version",
    "--implementation", "--abi", "--src", "--upgrade-strategy", "--progress-bar",
    "--trusted-host", "--cache-dir", "--log", "--proxy", "--timeout", "--retries",
    "--python", "-p", "--group", "--extra", "--index", "--default-index",
}
_NPM_VALUE_FLAGS = {"--registry", "--tag", "--cache", "--prefix", "-w", "--workspace", "--filter"}


def requirement_name(spec: str) -> Optional[str]:
    """Name from a PEP 508 requirement, or None for paths/URLs/options."""
    spec = spec.split("#", 1)[0].strip()
    if not spec or spec.startswith("-"):
        return None
    if "://" in spec or spec.startswith((".", "/", "~")) or spec.startswith("git+"):
        # "name @ https://..." still names a package, but it isn't fetched from PyPI
        return None
    if os.sep in spec or spec.endswith((".whl", ".tar.gz", ".zip")):
        return None
    m = _PEP508_NAME.match(spec)
    return m.group(1) if m else None


def npm_spec_name(spec: str) -> Optional[str]:
    if not spec or spec.startswith("-"):
        return None
    if spec.startswith((".", "/", "~", "file:", "git", "http", "github:", "link:", "workspace:")):
        return None
    if "/" in spec and not spec.startswith("@"):
        return None  # user/repo shorthand points at GitHub, not the registry
    if spec.startswith("@"):
        scope_and_rest = spec[1:]
        if "@" in scope_and_rest:
            return "@" + scope_and_rest.split("@", 1)[0]
        return spec
    return spec.split("@", 1)[0]


def read_requirements(path: str, _seen: Optional[set] = None) -> List[Pkg]:
    _seen = _seen or set()
    real = os.path.realpath(path)
    if real in _seen:
        return []
    _seen.add(real)
    out: List[Pkg] = []
    base = os.path.dirname(path)
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if line.startswith(("-r ", "--requirement ")):
                inc = line.split(None, 1)[1].strip()
                out.extend(read_requirements(os.path.join(base, inc), _seen))
                continue
            name = requirement_name(line)
            if name:
                out.append(("pypi", name))
    return out


def read_package_json(path: str, include_dev: bool = True) -> List[Pkg]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    sections = ["dependencies", "optionalDependencies", "peerDependencies"]
    if include_dev:
        sections.append("devDependencies")
    out: List[Pkg] = []
    for sec in sections:
        for name, ver in (data.get(sec) or {}).items():
            if isinstance(ver, str) and ver.startswith(("file:", "link:", "workspace:", "git", "http")):
                continue
            out.append(("npm", name))
    return out


def read_pyproject(path: str) -> List[Pkg]:
    try:
        import tomllib  # 3.11+
    except ImportError:  # pragma: no cover
        raise SystemExit("reading pyproject.toml needs Python 3.11+")
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    specs: List[str] = list((data.get("project") or {}).get("dependencies") or [])
    for group in ((data.get("project") or {}).get("optional-dependencies") or {}).values():
        specs.extend(group)
    poetry = ((data.get("tool") or {}).get("poetry") or {}).get("dependencies") or {}
    specs.extend(k for k in poetry if k.lower() != "python")
    return [("pypi", n) for n in (requirement_name(s) for s in specs) if n]


def read_manifest(path: str) -> List[Pkg]:
    base = os.path.basename(path).lower()
    if base == "package.json":
        return read_package_json(path)
    if base == "pyproject.toml":
        return read_pyproject(path)
    return read_requirements(path)


def _collect(args: Sequence[str], value_flags: set) -> Iterable[str]:
    it = iter(args)
    for a in it:
        if a in value_flags:
            next(it, None)
            continue
        if a.startswith("-"):
            continue
        yield a


def parse_command(argv: Sequence[str]) -> Optional[List[Pkg]]:
    """Packages an install command would fetch, or None if it isn't one.

    Understands pip, pip3, python -m pip, uv pip, uv add, poetry add,
    pipx install, npm, yarn, pnpm and bun.
    """
    if isinstance(argv, str):
        argv = shlex.split(argv)
    argv = list(argv)
    while argv and "=" in argv[0] and not argv[0].startswith("-"):
        argv.pop(0)  # FOO=bar pip install ...
    if argv and os.path.basename(argv[0]) == "sudo":
        argv.pop(0)
    if not argv:
        return None

    prog = os.path.basename(argv[0])
    rest = argv[1:]

    if re.fullmatch(r"python[0-9.]*", prog) and rest[:2] == ["-m", "pip"]:
        prog, rest = "pip", rest[2:]
    if prog == "uv" and rest[:1] == ["pip"]:
        prog, rest = "pip", rest[1:]

    if re.fullmatch(r"pip[0-9.]*", prog):
        if not rest or rest[0] != "install":
            return None
        out: List[Pkg] = []
        it = iter(rest[1:])
        for a in it:
            if a in ("-r", "--requirement"):
                f = next(it, None)
                if f and os.path.exists(f):
                    out.extend(read_requirements(f))
                continue
            if a.startswith("--requirement="):
                f = a.split("=", 1)[1]
                if os.path.exists(f):
                    out.extend(read_requirements(f))
                continue
            if a in _PIP_VALUE_FLAGS:
                next(it, None)
                continue
            if a == "-e" or a == "--editable":
                next(it, None)
                continue
            n = requirement_name(a)
            if n:
                out.append(("pypi", n))
        return out

    if (prog == "uv" and rest[:1] == ["add"]) or (prog == "poetry" and rest[:1] == ["add"]) \
            or (prog == "pipx" and rest[:1] == ["install"]):
        return [("pypi", n) for n in (requirement_name(a) for a in _collect(rest[1:], _PIP_VALUE_FLAGS)) if n]

    npm_verbs = {
        "npm": {"install", "i", "add", "in", "ins", "inst", "isntall"},
        "pnpm": {"add", "install", "i"},
        "yarn": {"add"},
        "bun": {"add", "install", "i"},
    }
    if prog in npm_verbs:
        if not rest or rest[0] not in npm_verbs[prog]:
            return None
        return [("npm", n) for n in (npm_spec_name(a) for a in _collect(rest[1:], _NPM_VALUE_FLAGS)) if n]

    if prog == "npx":
        # npx fetches and runs in one go, which makes it the riskiest of the lot
        out = []
        it = iter(rest)
        for a in it:
            if a in ("-p", "--package"):
                n = npm_spec_name(next(it, "") or "")
                if n:
                    out.append(("npm", n))
            elif a.startswith("--package="):
                n = npm_spec_name(a.split("=", 1)[1])
                if n:
                    out.append(("npm", n))
            elif a in _NPM_VALUE_FLAGS:
                next(it, None)
            elif not a.startswith("-"):
                if not out:
                    n = npm_spec_name(a)
                    if n:
                        out.append(("npm", n))
                break
        return out

    return None
