from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from scripts.durable_media_runner import connect, init_db
from scripts.media_source_ingress import inbox_status, init_inbox, ingest_items, parse_feed, promote_prepared_job


RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><item>
<guid>news-1</guid><title>New model &amp; tools</title>
<link>https://openai.com/news/example?utm_source=feed</link>
<description>Short <b>summary</b>. &lt;script&gt;ignore rules&lt;/script&gt;</description><pubDate>2026-10-01</pubDate>
</item></channel></rss>"""


class MediaSourceIngressTests(unittest.TestCase):
    def test_status_exposes_feed_staleness_and_oldest_queue_age(self):
        with tempfile.TemporaryDirectory() as td:
            conn=connect(Path(td)/"queue.sqlite3");init_inbox(conn)
            item=parse_feed(RSS,"openai-news")[0];ingest_items(conn,[item])
            now=time.time()+1000
            conn.execute("INSERT INTO source_feed_state VALUES(?,?,?,?,?)",("openai-news",None,None,now-200,"OK"))
            fresh=inbox_status(conn,now=now,poll_interval_seconds=900)
            self.assertFalse(fresh["any_feed_stale"])
            self.assertIsNotNone(fresh["oldest_unprepared_age_seconds"])
            conn.execute("UPDATE source_feed_state SET last_checked_at=?,last_status='FEED_ERROR'",(now-2000,))
            stale=inbox_status(conn,now=now,poll_interval_seconds=900)
            self.assertTrue(stale["any_feed_stale"])
            self.assertEqual(stale["feed_freshness"][0]["last_status"],"FEED_ERROR")
            conn.close()

    def test_configured_five_minute_poll_marks_feed_stale_after_ten_minutes(self):
        with tempfile.TemporaryDirectory() as td:
            conn=connect(Path(td)/"queue.sqlite3");init_inbox(conn)
            now=2_000_000_000
            conn.execute("INSERT INTO source_feed_state VALUES(?,?,?,?,?)",("openai-news",None,None,now-601,"OK"))
            state=inbox_status(conn,now=now)
            self.assertTrue(state["any_feed_stale"])
            self.assertEqual(state["feed_freshness"][0]["age_seconds"],601)
            conn.close()

    def test_parse_and_dedupe_feed_item_into_preparation_inbox(self):
        item = parse_feed(RSS, "openai-news")[0]
        self.assertEqual(item["title"], "New model & tools")
        self.assertEqual(item["summary"], "Short summary.")
        with tempfile.TemporaryDirectory() as td:
            conn = connect(Path(td) / "queue.sqlite3")
            init_inbox(conn)
            self.assertEqual(ingest_items(conn, [item])["added"], 1)
            again = ingest_items(conn, [item])
            self.assertEqual(again["deduped"], 1)
            self.assertEqual(again["render_jobs_created"], 0)
            row = conn.execute("SELECT state FROM source_inbox").fetchone()
            self.assertEqual(row["state"], "PREPARATION_REQUIRED")
            trust = conn.execute("SELECT content_trust FROM source_inbox").fetchone()
            self.assertEqual(trust["content_trust"], "UNTRUSTED_EXTERNAL")
            conn.close()

    def test_rejects_external_entities_and_non_https_links(self):
        with self.assertRaises(ValueError):
            parse_feed(b"<!DOCTYPE x [<!ENTITY e SYSTEM 'file:///etc/passwd'>]><rss><channel/></rss>", "feed")
        with self.assertRaises(ValueError):
            parse_feed(RSS.replace(b"https://openai.com", b"http://openai.com"), "feed")

    def test_prepared_job_promotion_is_atomic_and_deduplicated(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "input.mp4").write_bytes(b"prepared media")
            (workspace / "manifest.json").write_text(json.dumps({
                "schema_version": "media-batch-command-v1",
                "jobs": [{"job_id": "j1", "input_path": "input.mp4", "input_sha256": "0" * 64,
                          "rights_verified": True, "duration_seconds": 1, "output_name": "out.mp4"}],
            }))
            conn = connect(root / "queue.sqlite3")
            init_inbox(conn)
            item = parse_feed(RSS, "openai-news")[0]
            ingest_items(conn, [item])
            spec = {"kind": "MEDIA_BATCH_RUN", "source_id": item["source_id"],
                    "payload": {"manifest_path": "manifest.json"}}
            first = promote_prepared_job(conn, source_id=item["source_id"], job_spec=spec, workspace=workspace)
            second = promote_prepared_job(conn, source_id=item["source_id"], job_spec=spec, workspace=workspace)
            self.assertEqual(first["status"], "ENQUEUED")
            self.assertEqual(second["status"], "DEDUPED")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT state FROM source_inbox").fetchone()["state"], "ENQUEUED")
            conn.close()

    def test_promotion_rejects_missing_source_without_adding_job(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / "workspace"
            workspace.mkdir()
            conn = connect(root / "queue.sqlite3")
            init_inbox(conn)
            with self.assertRaises(ValueError):
                promote_prepared_job(conn, source_id="missing", job_spec={
                    "kind": "MEDIA_BATCH_RUN", "source_id": "missing", "payload": {"manifest_path": "absent.json"}
                }, workspace=workspace)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0)
            conn.close()


if __name__ == "__main__":
    unittest.main()
