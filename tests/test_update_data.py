import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import update_data as U  # noqa: E402

from slopwatch import similarity as S  # noqa: E402


def pypi_json(names, date="2026-11-01 06:41:05"):
    return json.dumps({"last_update": date, "rows": [{"project": n, "download_count": 1} for n in names]}).encode()


def npm_js(names):
    return ("export const topDownload = [\n" + "\n".join(f"  '{n}'," for n in names) + "\n]\n").encode()


class Sources(unittest.TestCase):
    def test_pypi(self):
        names, date = U.pypi_names(pypi_json(["boto3", "requests", "typing-extensions"]), count=2)
        self.assertEqual((names, date), (["boto3", "requests"], "2026-11-01"))
        with self.assertRaises(U.SourceError):
            U.pypi_names(pypi_json(["boto3"]), count=2)                 # too few
        with self.assertRaises(U.SourceError):
            U.pypi_names(pypi_json(["ok", "bad name!"]), count=2)       # invalid name
        with self.assertRaises(U.SourceError):
            U.pypi_names(b"<html>rate limited</html>", count=2)

    def test_npm_merges_curated_first(self):
        got = U.npm_names(npm_js(["semver", "debug", "@types/node", "react"]), curated=["react", "left-pad"], count=3)
        self.assertEqual(got, ["react", "left-pad", "semver", "debug", "@types/node"])
        with self.assertRaises(U.SourceError):
            U.npm_names(npm_js(["semver", "Not Valid"]), curated=[], count=2)
        with self.assertRaises(U.SourceError):
            U.npm_names(npm_js(["semver"]), curated=[], count=2)

    def test_churn_limit(self):
        old = [f"p{i}" for i in range(100)]
        U.check_churn("PyPI", old, old[10:] + [f"n{i}" for i in range(10)])  # 10% replaced: fine
        with self.assertRaises(U.SourceError):
            U.check_churn("PyPI", old, old[40:] + [f"n{i}" for i in range(40)])


class Scopes(unittest.TestCase):
    def test_familiar_scopes_are_not_lookalikes(self):
        top = frozenset({"react", "lodash", "jsdom", "@types/node", "@sentry/node"})
        with mock.patch.object(S, "popular", lambda eco: top):
            S._ordered.cache_clear()
            S.familiar_scopes.cache_clear()
            try:
                self.assertIsNone(S.lookalike("npm", "@types/jsdom"))
                self.assertIsNone(S.lookalike("npm", "@sentry/react"))
                self.assertEqual(S.lookalike("npm", "@evil/lodash"), ("lodash", "same name under an unfamiliar scope"))
            finally:
                S._ordered.cache_clear()
                S.familiar_scopes.cache_clear()


class ShippedLists(unittest.TestCase):
    def test_sizes_and_curated_names(self):
        self.assertEqual(len(S.popular("pypi")), 5000)
        npm = S.popular("npm")
        self.assertGreaterEqual(len(npm), 2000)
        curated = U.read_list(os.path.join(U.DATA, "npm_curated.txt"))
        self.assertTrue(set(curated) <= npm, "every hand-picked name stays on the npm list")


if __name__ == "__main__":
    unittest.main()
