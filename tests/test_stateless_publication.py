import base64
import subprocess
import unittest
from unittest.mock import patch

import publication


class Repository:
    def __init__(self, published=True, truncated=False):
        self.published = published
        self.truncated = truncated

    def request(self, method, path, payload=None):
        if method != "GET":
            raise AssertionError("Checking publication must never write to GitHub")
        if path == "git/ref/heads/main":
            return {"object": {"sha": "head"}}
        if path == "git/commits/head":
            return {"tree": {"sha": "tree"}}
        if path == "git/trees/tree?recursive=1":
            paths = ["newsroom.py", "README.md"]
            if self.published:
                paths += ["issues/2026-09-15/index.html", "index.html", "latest/index.html"]
            return {"truncated": self.truncated, "tree": [
                {"path": p, "type": "blob", "mode": "100644", "sha": p} for p in paths
            ]}
        if path.startswith("git/blobs/"):
            return {"encoding": "base64", "content": base64.b64encode(b"published").decode()}
        raise AssertionError(f"Unexpected API request: {path}")


class StatelessPublicationTests(unittest.TestCase):
    def test_existing_edition_is_verified_without_a_local_corpus(self):
        self.assertTrue(hasattr(publication, "published_status"))
        with patch.object(publication.subprocess, "run", return_value=
                          subprocess.CompletedProcess([], 0, stdout=b"published")):
            result = publication.published_status("2026-09-15", Repository(), attempts=1)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["url"],
                         "https://ncksol.dev/getpostingboard/issues/2026-09-15/")

    def test_missing_edition_requires_fresh_reporting(self):
        self.assertTrue(hasattr(publication, "published_status"))
        result = publication.published_status("2026-09-15", Repository(published=False))
        self.assertEqual(result["status"], "not_published")

    def test_stale_pages_is_not_reported_as_missing_or_verified(self):
        self.assertTrue(hasattr(publication, "published_status"))
        with patch.object(publication.subprocess, "run", return_value=
                          subprocess.CompletedProcess([], 0, stdout=b"stale")):
            with self.assertRaisesRegex(RuntimeError, "unverified"):
                publication.published_status("2026-09-15", Repository(), attempts=1)

    def test_truncated_tree_cannot_claim_the_edition_is_missing(self):
        self.assertTrue(hasattr(publication, "published_status"))
        with self.assertRaisesRegex(ValueError, "truncated"):
            publication.published_status("2026-09-15", Repository(truncated=True))


if __name__ == "__main__":
    unittest.main()
