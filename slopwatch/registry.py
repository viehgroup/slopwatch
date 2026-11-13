"""Thin clients for the PyPI JSON API and the npm registry.

Only the stdlib is used on purpose: a tool that guards installs shouldn't
need you to install a pile of other things first.
"""

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

from . import __version__

USER_AGENT = f"slopwatch/{__version__} (+https://github.com/viehgroup/slopwatch)"
CACHE_TTL = 6 * 3600


class RegistryError(Exception):
    pass


@dataclass
class PackageInfo:
    ecosystem: str
    name: str
    exists: bool
    created: Optional[datetime] = None
    latest_version: Optional[str] = None
    release_count: int = 0
    maintainers: int = 0
    repository: Optional[str] = None
    install_scripts: List[str] = field(default_factory=list)
    sdist_only: bool = False
    deprecated: Optional[str] = None
    yanked: bool = False


def normalize(ecosystem: str, name: str) -> str:
    if ecosystem == "pypi":
        # PEP 503
        return re.sub(r"[-_.]+", "-", name).lower()
    return name.lower()


def _parse_time(s: str) -> Optional[datetime]:
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class _Cache:
    def __init__(self, enabled: bool = True):
        base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
        self.dir = os.path.join(base, "slopwatch")
        self.enabled = enabled and os.environ.get("SLOPWATCH_NO_CACHE") != "1"

    def _path(self, url: str) -> str:
        return os.path.join(self.dir, hashlib.sha256(url.encode()).hexdigest() + ".json")

    def get(self, url: str):
        if not self.enabled:
            return None
        p = self._path(url)
        try:
            if time.time() - os.path.getmtime(p) > CACHE_TTL:
                return None
            with open(p, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    def put(self, url: str, data) -> None:
        if not self.enabled:
            return
        try:
            os.makedirs(self.dir, exist_ok=True)
            tmp = self._path(url) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.replace(tmp, self._path(url))
        except OSError:
            pass  # cache is best effort


class Registry:
    PYPI = "https://pypi.org/pypi/{}/json"
    NPM = "https://registry.npmjs.org/{}"

    def __init__(self, timeout: float = 10.0, use_cache: bool = True):
        self.timeout = timeout
        self.cache = _Cache(use_cache)

    def _get_json(self, url: str):
        """Returns parsed JSON, or None on a 404."""
        cached = self.cache.get(url)
        if cached is not None:
            return cached.get("body")
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                self.cache.put(url, {"body": None})
                return None
            raise RegistryError(f"{url}: HTTP {e.code}") from e
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            raise RegistryError(f"{url}: {e}") from e
        self.cache.put(url, {"body": body})
        return body

    def lookup(self, ecosystem: str, name: str) -> PackageInfo:
        if ecosystem == "pypi":
            return self._pypi(name)
        if ecosystem == "npm":
            return self._npm(name)
        raise ValueError(f"unknown ecosystem: {ecosystem}")

    def _pypi(self, name: str) -> PackageInfo:
        data = self._get_json(self.PYPI.format(urllib.parse.quote(name)))
        if data is None:
            return PackageInfo("pypi", name, exists=False)
        return parse_pypi(name, data)

    def _npm(self, name: str) -> PackageInfo:
        # scoped names keep the @ but the slash must be encoded
        data = self._get_json(self.NPM.format(name.replace("/", "%2F")))
        if data is None:
            return PackageInfo("npm", name, exists=False)
        return parse_npm(name, data)


def parse_pypi(name: str, data: dict) -> PackageInfo:
    info = data.get("info") or {}
    releases = data.get("releases") or {}

    upload_times = []
    for files in releases.values():
        for f in files or []:
            t = _parse_time(f.get("upload_time_iso_8601") or f.get("upload_time"))
            if t:
                upload_times.append(t)

    urls = data.get("urls") or []
    pkg_types = {f.get("packagetype") for f in urls}
    sdist_only = bool(urls) and "bdist_wheel" not in pkg_types

    repo = None
    project_urls = info.get("project_urls") or {}
    for key, val in project_urls.items():
        if any(k in key.lower() for k in ("source", "repository", "code", "github", "homepage")):
            repo = val
            break
    repo = repo or info.get("home_page") or None

    maint = sum(1 for x in (info.get("author"), info.get("author_email"),
                            info.get("maintainer"), info.get("maintainer_email")) if x)

    return PackageInfo(
        ecosystem="pypi",
        name=info.get("name") or name,
        exists=True,
        created=min(upload_times) if upload_times else None,
        latest_version=info.get("version"),
        release_count=sum(1 for files in releases.values() if files),
        maintainers=maint,
        repository=repo,
        sdist_only=sdist_only,
        yanked=bool(info.get("yanked")),
    )


INSTALL_HOOKS = ("preinstall", "install", "postinstall", "prepare")


def parse_npm(name: str, data: dict) -> PackageInfo:
    times = data.get("time") or {}
    latest = (data.get("dist-tags") or {}).get("latest")
    versions = data.get("versions") or {}
    manifest = versions.get(latest) or {}

    scripts = manifest.get("scripts") or {}
    # `prepare` only runs for git installs / local dev, but it's still worth showing
    hooks = [h for h in INSTALL_HOOKS if h in scripts]

    repo = data.get("repository") or manifest.get("repository")
    if isinstance(repo, dict):
        repo = repo.get("url")

    return PackageInfo(
        ecosystem="npm",
        name=data.get("name") or name,
        exists=True,
        created=_parse_time(times.get("created")),
        latest_version=latest,
        release_count=len(versions),
        maintainers=len(data.get("maintainers") or []),
        repository=repo or data.get("homepage"),
        install_scripts=[f"{h}: {scripts[h]}" for h in hooks],
        deprecated=manifest.get("deprecated"),
    )
