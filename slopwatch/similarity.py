"""Lookalike detection against a list of popular packages."""

import re
from functools import lru_cache
from importlib import resources
from typing import FrozenSet, Optional, Tuple

from .registry import normalize

# Affixes people (and LLMs) bolt onto real names. "python-requests" and
# "requests-py" are classic examples.
AFFIXES = {
    "pypi": ("python-", "py-", "py", "-python", "-py", "python3-", "-lib", "lib", "-dev"),
    "npm": ("node-", "-js", "js", ".js", "-node", "nodejs-", "-npm", "-lib"),
}


@lru_cache(maxsize=None)
def popular(ecosystem: str) -> FrozenSet[str]:
    fname = "pypi_top.txt" if ecosystem == "pypi" else "npm_top.txt"
    text = resources.files("slopwatch").joinpath("data", fname).read_text(encoding="utf-8")
    return frozenset(normalize(ecosystem, line.strip()) for line in text.splitlines() if line.strip())


@lru_cache(maxsize=None)
def _ordered(ecosystem: str):
    return tuple(sorted(popular(ecosystem)))


def is_popular(ecosystem: str, name: str) -> bool:
    return normalize(ecosystem, name) in popular(ecosystem)


def _squash(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def osa_distance(a: str, b: str, limit: int = 3) -> int:
    """Optimal string alignment distance (Levenshtein + adjacent swaps).

    Bails out early once the distance is known to exceed ``limit``.
    """
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2 = None
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        best = cur[0]
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
            best = min(best, cur[j])
        if best > limit:
            return limit + 1
        prev2, prev = prev, cur
    return prev[-1]


def lookalike(ecosystem: str, name: str) -> Optional[Tuple[str, str]]:
    """Return (popular_name, reason) if ``name`` imitates a popular package."""
    norm = normalize(ecosystem, name)
    top = popular(ecosystem)
    if norm in top:
        return None

    # scoped npm package mimicking an unscoped one: @evil/lodash
    if ecosystem == "npm" and norm.startswith("@") and "/" in norm:
        bare = norm.split("/", 1)[1]
        if bare in top:
            return bare, "same name under an unfamiliar scope"

    squashed = _squash(norm)
    for real in _ordered(ecosystem):
        if _squash(real) == squashed:
            return real, "differs only in separators"

    for affix in AFFIXES.get(ecosystem, ()):
        if affix.startswith("-") or affix.startswith("."):
            if norm.endswith(affix) and norm[: -len(affix)] in top:
                return norm[: -len(affix)], f"popular name plus '{affix}'"
        else:
            if norm.startswith(affix) and norm[len(affix):] in top:
                return norm[len(affix):], f"popular name plus '{affix}'"
            if norm.endswith(affix) and norm[: -len(affix)] in top:
                return norm[: -len(affix)], f"popular name plus '{affix}'"

    # Edit distance. Short names collide constantly (e.g. "six" / "sip"),
    # so we only go there for names of reasonable length.
    if len(norm) < 5:
        return None
    limit = 1 if len(norm) < 9 else 2
    best = None
    for real in _ordered(ecosystem):
        if len(real) < 5:
            continue
        d = osa_distance(norm, real, limit)
        if d <= limit and (best is None or d < best[1]):
            best = (real, d)
            if d == 1:
                break
    if best:
        real, d = best
        return real, f"{d} character{'s' if d > 1 else ''} away"
    return None
