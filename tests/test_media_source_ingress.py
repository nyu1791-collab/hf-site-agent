from __future__ import annotations

import json
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path

from scripts.durable_media_runner import connect, init_db
from scripts.media_source_ingress import (claim_source, inbox_status, init_inbox, ingest_items, load_policy, mark_source_running, parse_feed, promote_prepared_job, recover_expired_source_leases, release_source_claim, set_source_execution_state)


RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><item>
<guid>news-1</guid><title>New model &amp; tools</title>
<link>https://openai.com/news/example?utm_source=feed</link>
<description>Short <b>summary</b>. &lt;script&gt;ignore rules&lt;/script&gt;</description><pubDate>2026-10-01</pubDate>
</item></channel></rss>"""


class MediaSourceIngressTests(unittest.TestCase):
    def test_enabled_feeds_use_explicit_https_host_allowlists_and_priorities(self):
        from urllib.parse import urlsplit
        feeds=load_policy()["feeds"]
        enabled={feed["feed_id"]:feed for feed in feeds if feed.get("enabled")}
        self.assertIn("google-deepmind",enabled)
        self.assertEqual(enabled["google-deepmind"]["url"],"https://deepmind.google/blog/rss.xml")
        for feed in enabled.values():
            parts=urlsplit(feed["url"])
            self.assertEqual(parts.scheme,"https")
            self.assertIn(parts.hostname,feed["allowed_hosts"])
        self.assertGreater(enabled["google-deepmind"]["priority"],enabled["openai-news"]["priority"])

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

    def test_parse_selects_newest_items_before_applying_feed_limit(self):
        xml = b"""<?xml version="1.0"?>
        <rss version="2.0"><channel>
          <item><guid>old</guid><title>Old</title><link>https://openai.com/news/old</link><description>old</description><pubDate>Tue, 01 Jan 2025 00:00:00 GMT</pubDate></item>
          <item><guid>newest</guid><title>Newest</title><link>https://openai.com/news/newest</link><description>newest</description><pubDate>2026-10-02T12:00:00Z</pubDate></item>
          <item><guid>middle</guid><title>Middle</title><link>https://openai.com/news/middle</link><description>middle</description><pubDate>Wed, 01 Oct 2026 12:00:00 GMT</pubDate></item>
        </channel></rss>"""
        with patch("scripts.media_source_ingress.MAX_ITEMS", 2):
            items = parse_feed(xml, "openai-news")
        self.assertEqual([item["title"] for item in items], ["Newest", "Middle"])

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

    def test_parse_prefers_long_content_encoded_over_short_description(self):
        xml = RSS.replace(
            b'<rss version="2.0">',
            b'<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">',
        ).replace(
            b"</item>",
            b"<content:encoded><![CDATA[" + (b"Extended official feed text. " * 20)
            + b"]]></content:encoded></item>",
        )
        item = parse_feed(xml, "openai-news")[0]
        self.assertGreaterEqual(len(item["summary"]), 300)
        self.assertIn("Extended official feed text.", item["summary"])

    def test_richer_reingested_feed_releases_only_short_summary_block(self):
        with tempfile.TemporaryDirectory() as td:
            conn=connect(Path(td)/"queue.sqlite3");init_inbox(conn)
            item=parse_feed(RSS,"openai-news")[0]
            ingest_items(conn,[item])
            conn.execute("UPDATE source_inbox SET state='SCRIPT_BLOCKED'")
            richer={**item,"summary":"Extended feed text. " * 30}
            ingest_items(conn,[richer])
            row=conn.execute("SELECT state,summary FROM source_inbox").fetchone()
            self.assertEqual(row["state"],"PREPARATION_REQUIRED")
            self.assertEqual(row["summary"],richer["summary"])
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


    def test_source_claim_is_exclusive_and_request_id_is_stable(self):
        with tempfile.TemporaryDirectory() as td:
            conn=connect(Path(td)/"queue.sqlite3");init_inbox(conn)
            item=parse_feed(RSS,"openai-news")[0];ingest_items(conn,[item])
            source_id=item["source_id"]
            self.assertTrue(claim_source(conn,source_id,worker_id="worker-a",lease_seconds=60,now=100))
            self.assertFalse(claim_source(conn,source_id,worker_id="worker-b",lease_seconds=60,now=101))
            row=conn.execute("SELECT execution_state,worker_id,request_id FROM source_inbox WHERE source_id=?",(source_id,)).fetchone()
            self.assertEqual((row["execution_state"],row["worker_id"]),("CLAIMED","worker-a"))
            request_id=row["request_id"]
            self.assertTrue(mark_source_running(conn,source_id,worker_id="worker-a",now=102))
            self.assertEqual(release_source_claim(conn,source_id,now=103),"RETRYABLE")
            self.assertTrue(claim_source(conn,source_id,worker_id="worker-b",lease_seconds=60,now=104))
            row=conn.execute("SELECT request_id FROM source_inbox WHERE source_id=?",(source_id,)).fetchone()
            self.assertEqual(row["request_id"],request_id)
            conn.close()

    def test_unknown_result_is_not_recovered_or_reclaimed(self):
        with tempfile.TemporaryDirectory() as td:
            conn=connect(Path(td)/"queue.sqlite3");init_inbox(conn)
            item=parse_feed(RSS,"openai-news")[0];ingest_items(conn,[item])
            source_id=item["source_id"]
            self.assertTrue(claim_source(conn,source_id,worker_id="worker-a",lease_seconds=30,now=100))
            self.assertTrue(mark_source_running(conn,source_id,worker_id="worker-a",now=101))
            conn.execute("UPDATE source_inbox SET state='SCRIPT_BLOCKED',lease_expires_at=? WHERE source_id=?",(99,source_id))
            recovered=recover_expired_source_leases(conn,now=200)
            self.assertEqual(recovered,0)
            row=conn.execute("SELECT execution_state FROM source_inbox WHERE source_id=?",(source_id,)).fetchone()
            self.assertEqual(row["execution_state"],"UNKNOWN_RESULT")
            self.assertFalse(claim_source(conn,source_id,worker_id="worker-b",lease_seconds=60,now=201))
            conn.close()

    def test_balance_block_is_not_released_back_to_retryable(self):
        with tempfile.TemporaryDirectory() as td:
            conn=connect(Path(td)/"queue.sqlite3");init_inbox(conn)
            item=parse_feed(RSS,"openai-news")[0];ingest_items(conn,[item])
            source_id=item["source_id"]
            self.assertTrue(claim_source(conn,source_id,worker_id="worker-a",lease_seconds=60,now=100))
            self.assertTrue(mark_source_running(conn,source_id,worker_id="worker-a",now=101))
            set_source_execution_state(conn,source_id,"BLOCKED_BALANCE",now=102)
            state=release_source_claim(conn,source_id,now=103)
            self.assertEqual(state,"BLOCKED_BALANCE")
            self.assertFalse(claim_source(conn,source_id,worker_id="worker-b",lease_seconds=60,now=104))
            conn.close()


if __name__ == "__main__":
    unittest.main()
