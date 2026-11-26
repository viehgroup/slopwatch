"""Keep slopwatch's popular-package lists current.

    python tools/update_data.py            # update slopwatch/data/*_top.txt in place
    python tools/update_data.py --check    # report what would change, write nothing

The lists do two jobs: packages on them are never blocked, and they're what
lookalikes are measured against ("reqeusts" imitates "requests").

Sources, both on GitHub:
  * PyPI: the 5,000 most downloaded projects, from hugovk/top-pypi-packages
    (top-pypi-packages.min.json, refreshed monthly from PyPI's download data)
  * npm: the 2,000 most downloaded packages, from wooorm/npm-high-impact
    (lib/top-download.js), plus the hand-picked names in data/npm_curated.txt

The run stops with an error, and changes nothing, if a source looks wrong: too few
names, a name that isn't a valid package name, or more than a quarter of the list
replaced at once.
"""

import argparse
import json
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "slopwatch", "data")
PYPI_SOURCE = "https://raw.githubusercontent.com/hugovk/top-pypi-packages/main/top-pypi-packages.min.json"
NPM_SOURCE = "https://raw.githubusercontent.com/wooorm/npm-high-impact/main/lib/top-download.js"
PYPI_COUNT, NPM_COUNT = 5000, 2000
MAX_CHURN = 0.25
PYPI_NAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")
NPM_NAME = re.compile(r"^(?:@[a-z0-9~-][a-z0-9._~-]*/)?[a-z0-9~-][a-z0-9._~-]*$")


class SourceError(Exception):
    """A source looks wrong; refuse to publish."""


def fetch(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "slopwatch-data-updater"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(20 * 1024 * 1024)


def pypi_names(raw: bytes, count: int = PYPI_COUNT):
    try:
        data = json.loads(raw)
        rows = data["rows"]
    except (ValueError, KeyError, TypeError) as e:
        raise SourceError(f"PyPI list isn't the expected JSON: {e}") from e
    names = [r.get("project", "") for r in rows[:count]]
    if len(names) < count:
        raise SourceError(f"PyPI list has only {len(names)} projects (expected {count})")
    bad = [n for n in names if not PYPI_NAME.match(n)]
    if bad:
        raise SourceError(f"PyPI list has invalid names: {bad[:3]}")
    return names, str(data.get("last_update", ""))[:10]


def npm_names(raw: bytes, curated: list, count: int = NPM_COUNT):
    text = raw.decode("utf-8")
    ranked = re.findall(r"^\s*['\"]([^'\"]+)['\"],?\s*$", text, re.M)
    if len(ranked) < count:
        raise SourceError(f"npm list has only {len(ranked)} names (expected at least {count})")
    bad = [n for n in ranked[:count] if not NPM_NAME.match(n)]
    if bad:
        raise SourceError(f"npm list has invalid names: {bad[:3]}")
    out, seen = [], set()
    for n in curated + ranked[:count]:  # hand-picked first, then by downloads
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def check_churn(label: str, old: list, new: list):
    if old:
        gone = len(set(old) - set(new))
        if gone > len(old) * MAX_CHURN:
            raise SourceError(f"{label}: {gone} of {len(old)} names would be replaced at once")


def read_list(path: str) -> list:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="report changes without writing")
    args = ap.parse_args(argv)
    pypi_path, npm_path = os.path.join(DATA, "pypi_top.txt"), os.path.join(DATA, "npm_top.txt")
    old_pypi, old_npm = read_list(pypi_path), read_list(npm_path)
    curated = read_list(os.path.join(DATA, "npm_curated.txt"))
    try:
        if not curated:
            raise SourceError("data/npm_curated.txt is missing or empty")
        new_pypi, pypi_date = pypi_names(fetch(PYPI_SOURCE))
        new_npm = npm_names(fetch(NPM_SOURCE), curated)
        check_churn("PyPI", old_pypi, new_pypi)
        # npm grows from the hand-picked list on the first run, so only check churn on later runs
        if len(old_npm) > len(curated):
            check_churn("npm", old_npm, new_npm)
    except (SourceError, OSError, UnicodeDecodeError) as e:
        print(f"update_data: stopped, nothing changed: {e}", file=sys.stderr)
        return 2
    notes = []
    for label, old, new in (("PyPI", old_pypi, new_pypi), ("npm", old_npm, new_npm)):
        if old != new:
            plus, minus = len(set(new) - set(old)), len(set(old) - set(new))
            notes.append(f"{label}: {len(new)} names (+{plus}, -{minus}"
                         + (f"; source updated {pypi_date}" if label == "PyPI" and pypi_date else "") + ")")
    if not notes:
        print("update_data: already up to date")
        return 0
    print("update_data: " + "; ".join(notes))
    if args.check:
        return 0
    for path, new, old in ((pypi_path, new_pypi, old_pypi), (npm_path, new_npm, old_npm)):
        if new != old:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(new) + "\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("## slopwatch data update\n\n" + "\n".join(f"- {n}" for n in notes) + "\n")
    with open(os.path.join(HERE, "..", ".data-changes.txt"), "w", encoding="utf-8") as fh:
        fh.write("; ".join(notes)[:900] + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
