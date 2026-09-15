import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import newsroom

SPEC = importlib.util.spec_from_file_location("publication", ROOT / "publication.py")
publication = importlib.util.module_from_spec(SPEC)
if (ROOT / "publication.py").exists():
    SPEC.loader.exec_module(publication)


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(hasattr(publication, "prepare"), "public export is not implemented")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / "2026-09-15"
        newsroom.initialise(self.folder, "2026-09-15", "Europe/London")
        snapshot = self.folder.parent / "snapshot.json"
        snapshot.write_text(json.dumps({
            "ref": {"source": "named", "root_id": "post-1"},
            "root": {"id": "post-1", "created_at": 1789444800, "author": "reporter",
                     "title": "Public evidence", "body": "RAW-CORPUS-SENTINEL"},
            "comments": {"items": []},
        }))
        newsroom.ingest(self.folder, snapshot, "discussion")
        self.draft = {
            "editor_note": "Public reporting.",
            "coverage": {"status": "sampled", "notes": ["A selective report."]},
            "articles": [{
                "section": "News", "headline": "A reported update", "standfirst": "Attributed reporting.",
                "news_source_ids": ["named:post-1"],
                "paragraphs": [{"text": "An original paraphrase.", "source_ids": ["named:post-1"]}],
            }],
        }
        newsroom.write_json(self.folder / "draft.json", self.draft)
        newsroom.render(self.folder)
        newsroom.write_json(self.folder / "public-draft.json", self.draft)
        self.review()

    def review(self):
        newsroom.write_json(self.folder / "public-review.json", {
            "decision": "publish",
            "sha256": {name: hashlib.sha256((self.folder / name).read_bytes()).hexdigest()
                       for name in ("corpus.json", "public-draft.json")},
            "checks": {"original_paraphrases": True, "source_reuse_reviewed": True,
                       "no_private_data": True, "no_unpublished_material": True},
            "note": "Only original paraphrases of public named-board messages are included.",
        })

    def test_prepare_exports_only_public_html(self):
        before = (self.folder / "issue.html").read_bytes()
        result = publication.prepare(self.folder)
        html = result.read_text()
        self.assertEqual(result.name, "public.html")
        self.assertIn("Public edition", html)
        self.assertNotIn("RAW-CORPUS-SENTINEL", html)
        self.assertNotIn("Private reading copy", html)
        self.assertNotIn("local corpus", html)
        self.assertNotIn("/v1/posts/", html)
        self.assertEqual(before, (self.folder / "issue.html").read_bytes())

    def test_prepare_preserves_reviewed_export_across_template_updates(self):
        publication.prepare(self.folder)
        original = (self.folder / "public.html").read_bytes()
        with patch.object(newsroom, "render_html", return_value="changed template"):
            publication.prepare(self.folder)
        self.assertEqual((self.folder / "public.html").read_bytes(), original)

    def test_missing_review_prevents_export(self):
        (self.folder / "public-review.json").unlink()
        with self.assertRaises(FileNotFoundError):
            publication.prepare(self.folder)
        self.assertFalse((self.folder / "public.html").exists())

    def test_changed_public_copy_invalidates_review(self):
        self.draft["editor_note"] = "Unreviewed change"
        newsroom.write_json(self.folder / "public-draft.json", self.draft)
        with self.assertRaisesRegex(ValueError, "review"):
            publication.prepare(self.folder)

    def test_unchecked_reuse_prevents_export(self):
        review = newsroom.load(self.folder / "public-review.json")
        review["checks"]["source_reuse_reviewed"] = False
        newsroom.write_json(self.folder / "public-review.json", review)
        with self.assertRaisesRegex(ValueError, "review"):
            publication.prepare(self.folder)

    def test_unfinished_local_issue_cannot_publish(self):
        (self.folder / "complete.json").unlink()
        with self.assertRaises(FileNotFoundError):
            publication.prepare(self.folder)

    def test_private_copy_tampering_is_detected(self):
        (self.folder / "issue.html").write_text("tampered")
        with self.assertRaisesRegex(ValueError, "Completed"):
            publication.prepare(self.folder)

    def test_meatproxy_evidence_is_rejected_for_public_export(self):
        snapshot = self.folder.parent / "meatproxy.json"
        snapshot.write_text(json.dumps({
            "ref": {"source": "meatproxy", "root_id": "fiction", "article_revision_id": "revision"},
            "root": {"id": "fiction", "revision_id": "revision", "created_at": 1789444800,
                     "title": "Unpublished fiction", "author": "author",
                     "blocks": [{"type": "paragraph", "text": "Unpublished text"}]},
            "comments": {"items": []}, "content": {"complete": True},
        }))
        # A separate complete local edition may legitimately contain unpublished material.
        other = self.folder.parent / "private"
        newsroom.initialise(other, "2026-09-15", "Europe/London")
        newsroom.ingest(other, snapshot, "discussion")
        draft = copy.deepcopy(self.draft)
        sid = "meatproxy:fiction:revision"
        draft["articles"][0]["news_source_ids"] = [sid]
        draft["articles"][0]["paragraphs"][0]["source_ids"] = [sid]
        newsroom.write_json(other / "draft.json", draft)
        newsroom.write_json(other / "public-draft.json", draft)
        newsroom.render(other)
        self.folder = other
        self.review()
        with self.assertRaisesRegex(ValueError, "Meatproxy"):
            publication.prepare(other)

    def test_archive_orders_dates_and_does_not_duplicate(self):
        html = publication.archive_html(["2026-09-14", "2026-09-15", "2026-09-15"])
        self.assertLess(html.index('issues/2026-09-15/'), html.index('issues/2026-09-14/'))
        self.assertEqual(html.count('href="issues/2026-09-15/"'), 1)
        with self.assertRaises(ValueError):
            publication.archive_html(['"><script>'])

    def test_public_bundle_has_exact_allowlist_and_does_not_regress_latest(self):
        publication.prepare(self.folder)
        files = publication.bundle(self.folder, ["2026-09-16"], b"newer issue")
        self.assertEqual(set(files), {".nojekyll", "index.html", "latest/index.html",
                                     "issues/2026-09-15/index.html"})
        self.assertEqual(files["latest/index.html"], b"newer issue")
        self.assertFalse(any("RAW-CORPUS-SENTINEL" in value.decode() for value in files.values()))

    def test_changed_export_cannot_be_uploaded(self):
        publication.prepare(self.folder)
        (self.folder / "public.html").write_text("unreviewed HTML")
        with self.assertRaisesRegex(ValueError, "export"):
            publication.bundle(self.folder, [], None)

    def test_export_date_cannot_disagree_with_reviewed_issue(self):
        publication.prepare(self.folder)
        export = newsroom.load(self.folder / "public-export.json")
        export["date"] = "2026-09-14"
        newsroom.write_json(self.folder / "public-export.json", export)
        with self.assertRaisesRegex(ValueError, "date"):
            publication.bundle(self.folder, [], None)

    def test_pages_redirect_is_followed_without_allowing_http_downgrade(self):
        publication.prepare(self.folder)
        files = {"issues/2026-09-15/index.html": b"exact live issue"}

        def curl_response(command, **kwargs):
            follows_secure_redirect = (
                "--location" in command and "--proto-redir" in command
                and command[command.index("--proto-redir") + 1] == "=https"
            )
            return subprocess.CompletedProcess(command, 0, stdout=(
                b"exact live issue" if follows_secure_redirect else b"301 redirect page"
            ))

        with patch.object(publication.subprocess, "run", side_effect=curl_response):
            publication.verify_live(self.folder, files, "commit-sha", attempts=1)
        self.assertEqual(newsroom.load(self.folder / "publication.json")["commit"], "commit-sha")

    def test_existing_dated_issue_cannot_be_replaced(self):
        with self.assertRaisesRegex(ValueError, "immutable"):
            publication.require_immutable(b"old", b"new")
        publication.require_immutable(b"same", b"same")

    def test_stale_live_page_is_not_a_successful_publication(self):
        with self.assertRaisesRegex(ValueError, "live"):
            publication.verify_bytes(b"expected issue", b"old cached issue")
        publication.verify_bytes(b"expected issue", b"expected issue")


if __name__ == "__main__":
    unittest.main()
