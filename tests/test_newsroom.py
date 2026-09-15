import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("newsroom", ROOT / "newsroom.py")
newsroom = importlib.util.module_from_spec(SPEC) if SPEC else None
if (ROOT / "newsroom.py").exists():
    SPEC.loader.exec_module(newsroom)


class NewsroomTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(hasattr(newsroom, "edition_window"), "newsroom implementation is missing")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.edition = self.folder / "2026-09-15"
        newsroom.initialise(self.edition, "2026-09-15", "Europe/London")
        self.snapshot = self.folder / "source.json"
        self.raw = {
            "ref": {"source": "named", "root_id": "story-1"},
            "root": {"id": "story-1", "created_at": 1789444800,
                     "author": "reporter", "title": "Election update",
                     "body": "A candidate reported a change."},
            "comments": {"items": [], "next_before": None},
        }
        self.snapshot.write_text(json.dumps(self.raw))
        newsroom.ingest(self.edition, self.snapshot, "discussion")
        self.draft = {
            "editor_note": "A source-linked edition.",
            "coverage": {"status": "sampled", "notes": ["Recent discovery is not a full archive."]},
            "articles": [{
                "section": "Civic affairs", "headline": "Candidate reports a change",
                "standfirst": "A claim, not an independently confirmed platform defect.",
                "news_source_ids": ["named:story-1"],
                "paragraphs": [{"text": "The reporter described a change.",
                                "source_ids": ["named:story-1"]}],
            }],
        }

    def test_window_uses_local_eight_am(self):
        start, end = newsroom.edition_window("2026-09-15", "Europe/London")
        self.assertEqual(start.isoformat(), "2026-09-14T07:00:00+00:00")
        self.assertEqual(end.isoformat(), "2026-09-15T07:00:00+00:00")

    def test_window_handles_both_dst_transitions(self):
        for date, hours in [("2026-03-29", 23), ("2026-10-25", 25)]:
            with self.subTest(date=date):
                start, end = newsroom.edition_window(date, "Europe/London")
                self.assertEqual((end - start).total_seconds(), hours * 3600)

    def test_initialise_refuses_existing_directory(self):
        with self.assertRaises(FileExistsError):
            newsroom.initialise(self.edition, "2026-09-15", "Europe/London")

    def test_ingest_persists_raw_and_normalised_source(self):
        corpus = newsroom.load(self.edition / "corpus.json")
        self.assertEqual(corpus["sources"][0]["body"], self.raw["root"]["body"])
        self.assertTrue((self.edition / corpus["snapshots"][0]["path"]).is_file())

    def test_reingest_deduplicates_sources(self):
        newsroom.ingest(self.edition, self.snapshot, "discussion")
        self.assertEqual(len(newsroom.load(self.edition / "corpus.json")["sources"]), 1)

    def test_bad_snapshot_does_not_mutate_corpus(self):
        before = (self.edition / "corpus.json").read_bytes()
        self.snapshot.write_text('{"unexpected":true}')
        with self.assertRaises(ValueError):
            newsroom.ingest(self.edition, self.snapshot, "discussion")
        self.assertEqual((self.edition / "corpus.json").read_bytes(), before)

    def test_feed_previews_are_not_evidence(self):
        self.snapshot.write_text(json.dumps({"items": [{"ref": {"source": "named"},
            "activity": {"preview": "Do not cite me"}}], "coverage": {"omitted_history": True}}))
        newsroom.ingest(self.edition, self.snapshot, "feed")
        self.assertEqual(len(newsroom.load(self.edition / "corpus.json")["sources"]), 1)

    def test_meatproxy_uses_full_blocks_and_exact_revision_link(self):
        self.raw["ref"]["source"] = "meatproxy"
        self.raw["ref"]["article_revision_id"] = "revision-1"
        self.raw["root"].pop("body")
        self.raw["root"]["revision_id"] = "revision-1"
        self.raw["root"]["blocks"] = [{"type": "paragraph", "text": "A complete story."}]
        self.raw["content"] = {"complete": True, "next_block_cursor": None}
        self.snapshot.write_text(json.dumps(self.raw))
        newsroom.ingest(self.edition, self.snapshot, "discussion")
        source = newsroom.load(self.edition / "corpus.json")["sources"][-1]
        self.assertEqual(source["body"], "A complete story.")
        self.assertEqual(source["id"], "meatproxy:story-1:revision-1")
        self.assertEqual(source["url"],
                         "https://getpostingboard.dev/v1/meatproxy/revisions/revision-1")

    def test_truncated_meatproxy_blocks_are_rejected(self):
        self.raw["ref"]["source"] = "meatproxy"
        self.raw["root"]["blocks"] = [{"type": "paragraph", "text": "Only the first page."}]
        self.raw["content"] = {"complete": False, "next_block_cursor": 10}
        self.snapshot.write_text(json.dumps(self.raw))
        with self.assertRaisesRegex(ValueError, "complete"):
            newsroom.ingest(self.edition, self.snapshot, "discussion")

    def test_source_controlled_id_cannot_inject_link(self):
        self.raw["root"]["id"] = 'x" onclick="alert(1)'
        self.snapshot.write_text(json.dumps(self.raw))
        newsroom.ingest(self.edition, self.snapshot, "discussion")
        source = newsroom.load(self.edition / "corpus.json")["sources"][-1]
        self.assertNotIn('"', source["url"])
        self.assertTrue(source["url"].startswith("https://getpostingboard.dev/v1/posts/"))

    def test_unknown_citation_is_rejected(self):
        self.draft["articles"][0]["paragraphs"][0]["source_ids"] = ["named:missing"]
        with self.assertRaisesRegex(ValueError, "source"):
            newsroom.validate(self.edition, self.draft)

    def test_unattributed_paragraph_is_rejected(self):
        self.draft["articles"][0]["paragraphs"][0]["source_ids"] = []
        with self.assertRaisesRegex(ValueError, "source"):
            newsroom.validate(self.edition, self.draft)

    def test_news_must_be_inside_half_open_window(self):
        for timestamp, valid in [(1789369200, True), (1789455600, False), (1789369199, False)]:
            with self.subTest(timestamp=timestamp):
                raw = copy.deepcopy(self.raw)
                raw["root"]["id"] = str(timestamp)
                raw["root"]["created_at"] = timestamp
                self.snapshot.write_text(json.dumps(raw))
                newsroom.ingest(self.edition, self.snapshot, "discussion")
                source_id = f"named:{timestamp}"
                draft = copy.deepcopy(self.draft)
                draft["articles"][0]["news_source_ids"] = [source_id]
                draft["articles"][0]["paragraphs"][0]["source_ids"] = [source_id]
                if valid:
                    newsroom.validate(self.edition, draft)
                else:
                    with self.assertRaisesRegex(ValueError, "window"):
                        newsroom.validate(self.edition, draft)

    def test_after_cutoff_source_cannot_support_paragraph(self):
        self.raw["root"]["id"] = "future"
        self.raw["root"]["created_at"] = 1789455600
        self.snapshot.write_text(json.dumps(self.raw))
        newsroom.ingest(self.edition, self.snapshot, "discussion")
        self.draft["articles"][0]["paragraphs"][0]["source_ids"].append("named:future")
        with self.assertRaisesRegex(ValueError, "cutoff"):
            newsroom.validate(self.edition, self.draft)

    def test_raw_tampering_is_rejected(self):
        corpus = newsroom.load(self.edition / "corpus.json")
        (self.edition / corpus["snapshots"][0]["path"]).write_text("{}")
        with self.assertRaisesRegex(ValueError, "snapshot"):
            newsroom.validate(self.edition, self.draft)

    def test_normalised_evidence_tampering_is_rejected(self):
        corpus = newsroom.load(self.edition / "corpus.json")
        corpus["sources"][0]["body"] = "Invented evidence"
        newsroom.write_json(self.edition / "corpus.json", corpus)
        with self.assertRaisesRegex(ValueError, "evidence"):
            newsroom.validate(self.edition, self.draft)

    def test_quiet_issue_requires_explanation(self):
        self.draft["articles"] = []
        with self.assertRaises(ValueError):
            newsroom.validate(self.edition, self.draft)
        self.draft["quiet_reason"] = "No notable developments found in the sampled sources."
        newsroom.validate(self.edition, self.draft)

    def test_html_escapes_public_content_and_links_citations(self):
        self.draft["articles"][0]["paragraphs"][0]["text"] = "<script>alert('x')</script>"
        newsroom.write_json(self.edition / "draft.json", self.draft)
        newsroom.render(self.edition)
        html = (self.edition / "issue.html").read_text()
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn('href="#source-1"', html)
        self.assertIn("Coverage: sampled", html)

    def test_completed_edition_is_immutable(self):
        newsroom.write_json(self.edition / "draft.json", self.draft)
        newsroom.render(self.edition)
        self.assertTrue((self.edition / "complete.json").is_file())
        with self.assertRaises(FileExistsError):
            newsroom.render(self.edition)
        with self.assertRaises(FileExistsError):
            newsroom.ingest(self.edition, self.snapshot, "discussion")

    def test_cli_reports_failure_without_publishing(self):
        newsroom.write_json(self.edition / "draft.json", {})
        result = subprocess.run([sys.executable, str(ROOT / "newsroom.py"), "render",
                                 str(self.edition)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("error", result.stderr.lower())
        self.assertFalse((self.edition / "complete.json").exists())

    def test_temporary_editions_start_empty_and_do_not_share_state(self):
        self.assertTrue(hasattr(newsroom, "initialise_temporary"))
        first = newsroom.initialise_temporary("2026-09-15", "Europe/London")
        second = newsroom.initialise_temporary("2026-09-15", "Europe/London")
        self.addCleanup(newsroom.cleanup_temporary, first)
        self.addCleanup(newsroom.cleanup_temporary, second)
        newsroom.ingest(first, self.snapshot, "discussion")
        self.assertNotEqual(first, second)
        self.assertFalse(first.is_relative_to(ROOT))
        self.assertEqual(newsroom.load(second / "corpus.json")["sources"], [])
        self.assertEqual(newsroom.load(second / "corpus.json")["snapshots"], [])

    def test_cleanup_removes_only_the_owned_temporary_run(self):
        self.assertTrue(hasattr(newsroom, "initialise_temporary"))
        folder = newsroom.initialise_temporary("2026-09-15", "Europe/London")
        (folder / "raw" / "evidence.json").write_text("temporary evidence")
        newsroom.cleanup_temporary(folder)
        self.assertFalse(folder.parent.exists())
        self.assertTrue(self.edition.exists())

    def test_cleanup_rejects_a_persistent_edition(self):
        self.assertTrue(hasattr(newsroom, "cleanup_temporary"))
        with self.assertRaises(ValueError):
            newsroom.cleanup_temporary(self.edition)
        self.assertTrue((self.edition / "corpus.json").is_file())

    def test_cleanup_rejects_symlink_to_an_owned_run(self):
        self.assertTrue(hasattr(newsroom, "initialise_temporary"))
        folder = newsroom.initialise_temporary("2026-09-15", "Europe/London")
        self.addCleanup(newsroom.cleanup_temporary, folder)
        link = self.folder / "linked-edition"
        link.symlink_to(folder, target_is_directory=True)
        with self.assertRaises(ValueError):
            newsroom.cleanup_temporary(link)
        self.assertTrue(folder.exists())


if __name__ == "__main__":
    unittest.main()
