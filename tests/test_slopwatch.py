import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timedelta, timezone
from unittest import mock

from slopwatch import cli
from slopwatch.parse import npm_spec_name, parse_command, read_manifest, requirement_name
from slopwatch.registry import PackageInfo, parse_npm, parse_pypi
from slopwatch.scoring import BLOCK, OK, WARN, score
from slopwatch.similarity import lookalike, osa_distance

NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)


def info(**kw):
    base = dict(ecosystem="pypi", name="somepkg", exists=True,
                created=NOW - timedelta(days=2000), latest_version="1.0",
                release_count=20, maintainers=1, repository="https://github.com/a/b")
    base.update(kw)
    return PackageInfo(**base)


class SimilarityTests(unittest.TestCase):
    def test_distance(self):
        self.assertEqual(osa_distance("requests", "reqeusts"), 1)  # swap
        self.assertEqual(osa_distance("numpy", "numpyy"), 1)
        self.assertGreater(osa_distance("flask", "django", limit=2), 2)

    def test_lookalikes(self):
        self.assertEqual(lookalike("pypi", "reqeusts")[0], "requests")
        self.assertEqual(lookalike("pypi", "python-requests")[0], "requests")
        self.assertEqual(lookalike("npm", "crossenv")[0], "cross-env")
        self.assertEqual(lookalike("npm", "@someone/lodash")[0], "lodash")
        self.assertIsNone(lookalike("pypi", "requests"))
        self.assertIsNone(lookalike("pypi", "Requests"))  # normalised

    def test_short_names_left_alone(self):
        self.assertIsNone(lookalike("pypi", "sixx"))


class ScoringTests(unittest.TestCase):
    def test_missing_package_blocks(self):
        v = score(PackageInfo("pypi", "flask-jwt-simple-auth", exists=False), NOW)
        self.assertEqual(v.level, BLOCK)

    def test_established_package_ok(self):
        self.assertEqual(score(info(), NOW).level, OK)

    def test_fresh_lookalike_blocks(self):
        v = score(info(name="reqeusts", created=NOW - timedelta(days=3), release_count=1,
                       repository=None), NOW)
        self.assertEqual(v.level, BLOCK)

    def test_old_lookalike_only_mentioned(self):
        v = score(info(name="behavex"), NOW)
        self.assertEqual(v.level, OK)

    def test_npm_install_script_on_new_package(self):
        v = score(info(ecosystem="npm", name="totally-new-thing", created=NOW - timedelta(days=5),
                       release_count=1, install_scripts=["postinstall: node x.js"]), NOW)
        self.assertEqual(v.level, BLOCK)

    def test_popular_package_never_flagged(self):
        v = score(info(name="requests", created=NOW - timedelta(days=1), release_count=1,
                       repository=None), NOW)
        self.assertEqual(v.level, OK)

    def test_npm_security_placeholder(self):
        v = score(info(ecosystem="npm", name="lodahs", latest_version="0.0.1-security",
                       release_count=1), NOW)
        self.assertEqual(v.level, BLOCK)

    def test_moderately_new_is_warn(self):
        v = score(info(name="brand-new-tool", created=NOW - timedelta(days=10),
                       release_count=3), NOW)
        self.assertEqual(v.level, WARN)


class ParseTests(unittest.TestCase):
    def test_requirement_names(self):
        self.assertEqual(requirement_name("flask[async]>=3.0 ; python_version>'3.8'"), "flask")
        self.assertIsNone(requirement_name("git+https://github.com/x/y"))
        self.assertIsNone(requirement_name("./local"))
        self.assertIsNone(requirement_name("--hash=sha256:abc"))

    def test_npm_specs(self):
        self.assertEqual(npm_spec_name("lodash@4.17.21"), "lodash")
        self.assertEqual(npm_spec_name("@types/node@20"), "@types/node")
        self.assertEqual(npm_spec_name("@types/node"), "@types/node")
        self.assertIsNone(npm_spec_name("user/repo"))
        self.assertIsNone(npm_spec_name("file:../x"))

    def test_commands(self):
        self.assertEqual(parse_command("pip install -U requests==2 -i https://x numpy"),
                         [("pypi", "requests"), ("pypi", "numpy")])
        self.assertEqual(parse_command("python3 -m pip install rich"), [("pypi", "rich")])
        self.assertEqual(parse_command("uv pip install httpx"), [("pypi", "httpx")])
        self.assertEqual(parse_command("uv add pydantic"), [("pypi", "pydantic")])
        self.assertEqual(parse_command("sudo pip3 install six"), [("pypi", "six")])
        self.assertEqual(parse_command("npm i -D lodash @types/node@20"),
                         [("npm", "lodash"), ("npm", "@types/node")])
        self.assertEqual(parse_command("yarn add react"), [("npm", "react")])
        self.assertEqual(parse_command("npx create-thing@latest my-app"), [("npm", "create-thing")])
        self.assertEqual(parse_command("npx -p cowsay cowsay hi"), [("npm", "cowsay")])
        self.assertIsNone(parse_command("pip list"))
        self.assertIsNone(parse_command("ls -la"))
        self.assertEqual(parse_command("npm install"), [])

    def test_manifests(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "requirements.txt"), "w") as fh:
                fh.write("requests\n-r more.txt\n# comment\n-e .\n")
            with open(os.path.join(d, "more.txt"), "w") as fh:
                fh.write("numpy==1.26\n-r requirements.txt\n")  # cycle on purpose
            self.assertEqual(read_manifest(os.path.join(d, "requirements.txt")),
                             [("pypi", "requests"), ("pypi", "numpy")])

            pj = os.path.join(d, "package.json")
            with open(pj, "w") as fh:
                json.dump({"dependencies": {"express": "^4", "x": "file:../x"},
                           "devDependencies": {"jest": "29"}}, fh)
            self.assertEqual(read_manifest(pj), [("npm", "express"), ("npm", "jest")])


class RegistryParsingTests(unittest.TestCase):
    def test_pypi(self):
        data = {
            "info": {"name": "demo", "version": "0.2", "author": "x",
                     "project_urls": {"Source": "https://github.com/x/demo"}},
            "releases": {
                "0.1": [{"upload_time_iso_8601": "2026-10-01T00:00:00Z"}],
                "0.2": [{"upload_time_iso_8601": "2026-10-20T00:00:00Z"}],
            },
            "urls": [{"packagetype": "sdist"}],
        }
        p = parse_pypi("demo", data)
        self.assertEqual(p.release_count, 2)
        self.assertEqual(p.created.day, 1)
        self.assertTrue(p.sdist_only)
        self.assertEqual(p.repository, "https://github.com/x/demo")

    def test_npm(self):
        data = {
            "name": "demo",
            "dist-tags": {"latest": "1.0.0"},
            "time": {"created": "2026-10-30T00:00:00.000Z"},
            "versions": {"1.0.0": {"scripts": {"postinstall": "node steal.js", "test": "jest"}}},
            "maintainers": [{"name": "a"}],
        }
        p = parse_npm("demo", data)
        self.assertEqual(p.install_scripts, ["postinstall: node steal.js"])
        self.assertIsNone(p.repository)


class RenderTests(unittest.TestCase):
    def test_long_findings_wrap_to_terminal(self):
        v = score(PackageInfo("pypi", "flask-jwt-simple-auth", exists=False), NOW)
        with mock.patch("shutil.get_terminal_size", return_value=os.terminal_size((60, 24))):
            out = cli.render([v], color=False, verbose=False)
        self.assertTrue(all(len(line) <= 60 for line in out.splitlines()), out)
        self.assertIn("registry", out)


class HookTests(unittest.TestCase):
    def _run_hook(self, payload, lookup):
        args = cli.build_parser().parse_args(["hook"])
        with mock.patch("sys.stdin", io.StringIO(json.dumps(payload))), \
                mock.patch.object(cli.Registry, "lookup", side_effect=lookup):
            err = io.StringIO()
            with redirect_stderr(err):
                return cli.cmd_hook(args), err.getvalue()

    def test_blocks_hallucinated_package(self):
        code, err = self._run_hook(
            {"tool_name": "Bash", "tool_input": {"command": "cd x && pip install reqeusts"}},
            lambda eco, name: PackageInfo(eco, name, exists=False))
        self.assertEqual(code, 2)
        self.assertIn("reqeusts", err)

    def test_ignores_other_tools_and_commands(self):
        code, _ = self._run_hook({"tool_name": "Read", "tool_input": {}}, None)
        self.assertEqual(code, 0)
        code, _ = self._run_hook({"tool_name": "Bash", "tool_input": {"command": "ls"}}, None)
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
