"""Turns registry metadata into a verdict.

The weights are deliberately simple so anyone can read them and argue
with them. Tune via the thresholds rather than by tweaking every weight.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

from .registry import PackageInfo
from .similarity import is_popular, lookalike

OK, WARN, BLOCK = "ok", "warn", "block"
WARN_AT = 30
BLOCK_AT = 60


@dataclass
class Finding:
    points: int
    message: str


@dataclass
class Verdict:
    ecosystem: str
    name: str
    score: int
    level: str
    findings: List[Finding] = field(default_factory=list)
    info: Optional[PackageInfo] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        d = {
            "ecosystem": self.ecosystem,
            "name": self.name,
            "score": self.score,
            "level": self.level,
            "findings": [{"points": f.points, "message": f.message} for f in self.findings],
        }
        if self.error:
            d["error"] = self.error
        if self.info and self.info.exists:
            d["created"] = self.info.created.isoformat() if self.info.created else None
            d["latest_version"] = self.info.latest_version
            d["repository"] = self.info.repository
        return d


def _level(score: int) -> str:
    if score >= BLOCK_AT:
        return BLOCK
    if score >= WARN_AT:
        return WARN
    return OK


def score(info: PackageInfo, now: Optional[datetime] = None) -> Verdict:
    now = now or datetime.now(timezone.utc)
    eco, name = info.ecosystem, info.name
    found: List[Finding] = []

    if not info.exists:
        found.append(Finding(100, "doesn't exist on the registry. Likely a hallucinated "
                                  "name, and anyone could register it"))
        hit = lookalike(eco, name)
        if hit:
            found.append(Finding(0, f"did you mean '{hit[0]}'? ({hit[1]})"))
        return Verdict(eco, name, 100, BLOCK, found, info)

    popular = is_popular(eco, name)

    if eco == "npm" and (info.latest_version or "").endswith("-security"):
        found.append(Finding(60, "npm security placeholder: a malicious package with "
                                 "this name was removed"))

    age = (now - info.created).days if info.created else None
    established = age is not None and age > 365 and info.release_count >= 5

    hit = lookalike(eco, name)
    if hit:
        # Plenty of legit projects have similar names (typer/vyper,
        # behave/behavex). Squats tend to be young and thin, so a lookalike
        # with years of releases behind it is only worth a mention.
        pts = 5 if established else 40
        found.append(Finding(pts, f"looks like '{hit[0]}' ({hit[1]})"))

    if age is not None:
        if age < 14:
            found.append(Finding(40, f"first published {age} day{'s' if age != 1 else ''} ago"))
        elif age < 90:
            found.append(Finding(20, f"first published {age} days ago"))
    else:
        found.append(Finding(15, "no release dates on record"))

    if info.release_count <= 1:
        found.append(Finding(10, "only one release"))

    if not info.repository:
        found.append(Finding(10, "no source repository or homepage listed"))

    if info.install_scripts:
        # Install hooks are normal for popular native modules, so on their
        # own they carry modest weight.
        pts = 5 if popular else 25
        found.append(Finding(pts, "runs code on install: " + "; ".join(info.install_scripts)))

    if info.sdist_only and not popular:
        found.append(Finding(10, "source dist only, so setup.py runs at install time"))

    if info.deprecated:
        found.append(Finding(10, f"deprecated: {info.deprecated}"))

    if info.yanked:
        found.append(Finding(15, "latest release was yanked"))

    total = min(100, sum(f.points for f in found))
    if popular:
        # Very widely used; age/repo signals are noise here.
        total = min(total, WARN_AT - 1)

    return Verdict(eco, name, total, _level(total), found, info)
