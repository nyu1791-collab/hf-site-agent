from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import urllib.error
from types import SimpleNamespace
import unittest
import wave
import array
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

from scripts.durable_media_runner import connect
from scripts.media_news_pipeline import ArticleSourceBlocked, DailyMediaCapReached, PaidMediaAlreadyAttempted, PaidMediaBudgetExceeded, PaidMediaMonthlyCapReached, PaidMediaPreflightUnavailable, PIPELINE_POLICY, _paid_reserved_cost_this_month, _pipeline_lock, _process_next, _requeue_voice, _resolve_news_package, _reserve_call, _reserve_paid_call, _paid_calls_used_today, _rss_summary_article, _validate_existing_package, _review_story_free, draft_story, extract_article, process_source, select_render_assets, synthesize_voice, validate_story, _post_chat
from scripts.media_source_ingress import ingest_items, init_inbox
from scripts.media_source_daemon import run as run_source_daemon


TEXT = "OpenAI announced a new feature for developers. The feature helps teams connect tools to their applications. The announcement explains the rollout and lists current availability."


class Response:
    def __init__(self, payload: bytes, content_type: str = "text/html"):
        self.payload = payload
        self.headers = type("Headers", (), {"get_content_type": lambda _self: content_type})()
    def __enter__(self): return self
    def __exit__(self, *args): return None
    def read(self, limit=-1): return self.payload[:limit]
    def geturl(self): return "https://openai.com/news/example"


def story():
    return {"title":"開発者向け新機能", "scenes":[
      {"scene_id":"what","title":"何が発表された？","source_excerpt":"OpenAI announced a new feature for developers.","dialogue":[
        {"id":"a","speaker":"ずんだもん","voice_text":"OpenAIが開発者向けの新機能を発表したのだ。"},
        {"id":"b","speaker":"四国めたん","voice_text":"つまり、アプリを作る人向けの発表ね。"}]},
      {"scene_id":"use","title":"何に役立つ？","source_excerpt":"The feature helps teams connect tools to their applications.","dialogue":[
        {"id":"c","speaker":"ずんだもん","voice_text":"チームがツールをアプリにつなげやすくするのだ。"},
        {"id":"d","speaker":"四国めたん","voice_text":"複数の道具をまとめて使う場面に関係するわ。"}]},
      {"scene_id":"availability","title":"提供状況","source_excerpt":"The announcement explains the rollout and lists current availability.","dialogue":[
        {"id":"e","speaker":"ずんだもん","voice_text":"提供開始の流れと、今使える範囲も案内されているのだ。"},
        {"id":"f","speaker":"四国めたん","voice_text":"詳しい条件は、発表ページの一覧を見るのが確実ね。"}]}
    ]}


class MediaNewsAutomationTests(unittest.TestCase):
    def test_uncertain_paid_attempt_is_blocked_and_timer_advances_to_next_source(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);workspace=root/"workspace";workspace.mkdir()
            conn=connect(root/"queue.sqlite3");init_inbox(conn)
            older="a"*64;newer="b"*64
            ingest_items(conn,[
                {"source_id":older,"feed_id":"openai-news","title":"older",
                 "url":"https://openai.com/news/older","summary":"","published":""},
                {"source_id":newer,"feed_id":"openai-news","title":"newer",
                 "url":"https://openai.com/news/newer","summary":"","published":""},
            ])
            conn.execute("UPDATE source_inbox SET created_at=100 WHERE source_id=?",(older,))
            conn.execute("UPDATE source_inbox SET created_at=200 WHERE source_id=?",(newer,))
            with patch.dict("os.environ",{"OPENROUTER_API_KEY":"test"}), patch(
                "scripts.media_news_pipeline.shutil.disk_usage",
                return_value=SimpleNamespace(free=3*1024**3)
            ), patch("scripts.media_news_pipeline.process_source",side_effect=[
                PaidMediaAlreadyAttempted("uncertain prior attempt"),
                ArticleSourceBlocked("old item inaccessible"),
            ]):
                first=_process_next(conn,workspace,min_seconds=60,max_seconds=300)
                self.assertEqual(first["source_id"],newer)
                self.assertEqual(first["status"],"SCRIPT_BLOCKED_PAID_ATTEMPT_UNKNOWN")
                self.assertEqual(conn.execute("SELECT state FROM source_inbox WHERE source_id=?",(newer,)).fetchone()["state"],"SCRIPT_BLOCKED")
                second=_process_next(conn,workspace,min_seconds=60,max_seconds=300)
                self.assertEqual(second["source_id"],older)
                self.assertEqual(second["status"],"ARTICLE_SOURCE_BLOCKED")
            conn.close()

    def test_openrouter_http_errors_report_safe_reason_without_upstream_text(self):
        import io
        response = urllib.error.HTTPError(
            "https://openrouter.ai/api/v1/chat/completions", 403, "Forbidden", {},
            io.BytesIO(b'{"error":{"code":403,"message":"Key limit reached; PRIVATE_TOKEN_SHOULD_NOT_LEAK"}}')
        )
        with patch("scripts.media_news_pipeline.urllib.request.urlopen", side_effect=response):
            with self.assertRaisesRegex(RuntimeError, "HTTP 403 \\(CREDIT_OR_KEY_BUDGET_LIMIT\\)") as caught:
                _post_chat({"model":"fixture"}, "hidden")
        self.assertNotIn("PRIVATE_TOKEN_SHOULD_NOT_LEAK", str(caught.exception))

    def test_pipeline_lock_blocks_overlapping_stage_commands(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/"queue.sqlite3"
            with _pipeline_lock(db):
                with self.assertRaises(RuntimeError):
                    with _pipeline_lock(db):
                        pass

    def test_source_id_must_be_a_sha256_digest(self):
        with tempfile.TemporaryDirectory() as td:
            conn=connect(Path(td)/"q.sqlite3");init_inbox(conn)
            with self.assertRaises(ValueError):
                process_source(conn,"../../escape",Path(td),image_hosts={"openai.com"})
            conn.close()

    def test_source_package_paths_cannot_escape_workspace_through_symlinks(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);workspace=root/"workspace";workspace.mkdir();outside=root/"outside";outside.mkdir()
            (workspace/"media-news").symlink_to(outside,target_is_directory=True)
            with self.assertRaises(ValueError): _resolve_news_package(workspace,"a"*64,create=True)
            self.assertEqual(list(outside.iterdir()),[])
            (workspace/"media-news").unlink()
            foreign=outside/("b"*64);foreign.mkdir()
            (workspace/"media-news").mkdir()
            with self.assertRaises(ValueError): _validate_existing_package(workspace,foreign)

    def test_daily_media_call_cap_is_transactional_and_bounded(self):
        with tempfile.TemporaryDirectory() as td:
            conn=connect(Path(td)/"q.sqlite3");init_inbox(conn)
            for _ in range(5): _reserve_call(conn)
            with self.assertRaises(RuntimeError): _reserve_call(conn)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM media_news_model_calls").fetchone()[0],5)
            conn.close()

    def test_daily_media_cap_leaves_article_queued_for_next_day(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);conn=connect(root/"q.sqlite3");init_inbox(conn)
            source_id="c"*64
            ingest_items(conn,[{"source_id":source_id,"feed_id":"openai-news","title":"title","url":"https://openai.com/news/x","summary":"","published":""}])
            for i in range(5):
                _reserve_paid_call(conn, "paid-call-"+str(i), __import__("decimal").Decimal("0.001"),
                    "deepseek/deepseek-v4.1-flash", __import__("decimal").Decimal("0.000000015"),
                    __import__("decimal").Decimal("0.0000012"))
            with patch.dict("os.environ",{"OPENROUTER_API_KEY":"configured"}), patch(
                "scripts.media_news_pipeline.shutil.disk_usage",
                return_value=SimpleNamespace(free=3 * 1024**3),
            ):
                result=_process_next(conn,root/"workspace",min_seconds=60,max_seconds=300)
            self.assertEqual(result["status"],"BLOCKED_DAILY_PAID_CALL_CAP")
            self.assertEqual(conn.execute("SELECT state FROM source_inbox").fetchone()["state"],"PREPARATION_REQUIRED")
            conn.close()

    def test_unknown_paid_attempt_is_reported_and_not_retried(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);conn=connect(root/"q.sqlite3");init_inbox(conn)
            source_id="f"*64
            ingest_items(conn,[{"source_id":source_id,"feed_id":"openai-news","title":"title",
                "url":"https://openai.com/news/x","summary":"","published":""}])
            with patch.dict("os.environ",{"OPENROUTER_API_KEY":"configured"}), patch(
                "scripts.media_news_pipeline.shutil.disk_usage",
                return_value=SimpleNamespace(free=3 * 1024**3),
            ), patch("scripts.media_news_pipeline.process_source",
                side_effect=PaidMediaAlreadyAttempted("reserved")) as paid:
                result=_process_next(conn,root/"workspace",min_seconds=60,max_seconds=300)
            self.assertEqual(result["status"],"SCRIPT_BLOCKED_PAID_ATTEMPT_UNKNOWN")
            self.assertTrue(result["request_may_have_been_sent"])
            self.assertFalse(result["automatic_retry"])
            paid.assert_called_once()
            self.assertEqual(conn.execute("SELECT state FROM source_inbox").fetchone()["state"],
                "SCRIPT_BLOCKED")
            conn.close()

    def test_per_call_paid_budget_error_is_returned_as_a_queued_block(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);conn=connect(root/"q.sqlite3");init_inbox(conn)
            source_id="9"*64
            ingest_items(conn,[{"source_id":source_id,"feed_id":"openai-news","title":"title",
                "url":"https://openai.com/news/x","summary":"","published":""}])
            with patch.dict("os.environ",{"OPENROUTER_API_KEY":"configured"}), patch(
                "scripts.media_news_pipeline.shutil.disk_usage",
                return_value=SimpleNamespace(free=3 * 1024**3),
            ), patch("scripts.media_news_pipeline.process_source",
                side_effect=PaidMediaBudgetExceeded("cap")):
                result=_process_next(conn,root/"workspace",min_seconds=60,max_seconds=300)
            self.assertEqual(result["status"],"BLOCKED_PAID_PER_CALL_BUDGET")
            self.assertFalse(result["request_sent"])
            self.assertEqual(conn.execute("SELECT state FROM source_inbox").fetchone()["state"],
                "PREPARATION_REQUIRED")
            conn.close()

    def test_paid_monthly_cost_cap_stops_before_reserving_another_call(self):
        from decimal import Decimal
        with tempfile.TemporaryDirectory() as td:
            conn=connect(Path(td)/"q.sqlite3");init_inbox(conn)
            policy=PIPELINE_POLICY["paid_script_generation"]
            old_monthly_cap=policy["maximum_reserved_cost_per_utc_month_usd"]
            policy["maximum_reserved_cost_per_utc_month_usd"]="0.05"
            try:
                _reserve_paid_call(conn,"a"*64,Decimal("0.04"),"deepseek/deepseek-v4.1-flash",
                    Decimal("0.000000015"),Decimal("0.0000012"))
                with self.assertRaises(PaidMediaMonthlyCapReached):
                    _reserve_paid_call(conn,"b"*64,Decimal("0.02"),"deepseek/deepseek-v4.1-flash",
                        Decimal("0.000000015"),Decimal("0.0000012"))
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM media_news_paid_calls").fetchone()[0],1)
                self.assertEqual(_paid_reserved_cost_this_month(conn),Decimal("0.04"))
            finally:
                policy["maximum_reserved_cost_per_utc_month_usd"]=old_monthly_cap
                conn.close()

    def test_monthly_paid_cap_leaves_article_queued_without_an_api_call(self):
        from decimal import Decimal
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);conn=connect(root/"q.sqlite3");init_inbox(conn)
            source_id="d"*64
            ingest_items(conn,[{"source_id":source_id,"feed_id":"openai-news","title":"title",
                "url":"https://openai.com/news/x","summary":"","published":""}])
            policy=PIPELINE_POLICY["paid_script_generation"]
            old_monthly_cap=policy["maximum_reserved_cost_per_utc_month_usd"]
            policy["maximum_reserved_cost_per_utc_month_usd"]="0.001"
            try:
                _reserve_paid_call(conn,"e"*64,Decimal("0.001"),"deepseek/deepseek-v4.1-flash",
                    Decimal("0.000000015"),Decimal("0.0000012"))
                with patch.dict("os.environ",{"OPENROUTER_API_KEY":"configured"}), patch(
                    "scripts.media_news_pipeline.shutil.disk_usage",
                    return_value=SimpleNamespace(free=3 * 1024**3),
                ):
                    result=_process_next(conn,root/"workspace",min_seconds=60,max_seconds=300)
                self.assertEqual(result["status"],"BLOCKED_MONTHLY_PAID_BUDGET")
                self.assertFalse(result["request_sent"])
                self.assertEqual(conn.execute("SELECT state FROM source_inbox").fetchone()["state"],
                    "PREPARATION_REQUIRED")
            finally:
                policy["maximum_reserved_cost_per_utc_month_usd"]=old_monthly_cap
                conn.close()

    def test_low_disk_blocks_before_api_or_voice_work_without_mutating_queue(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);conn=connect(root/"queue.sqlite3");init_inbox(conn)
            source_id="b"*64
            ingest_items(conn,[{"source_id":source_id,"feed_id":"openai-news","title":"title",
                "url":"https://openai.com/news/x","summary":TEXT*4,"published":""}])
            with patch.dict("os.environ",{"OPENROUTER_API_KEY":"configured"}), patch(
                "scripts.media_news_pipeline.shutil.disk_usage",
                return_value=SimpleNamespace(free=1024**3),
            ), patch("scripts.media_news_pipeline.process_source") as process_source:
                result=_process_next(conn,root/"workspace",min_seconds=60,max_seconds=300)
            self.assertEqual(result["status"],"BLOCKED_LOW_DISK_SPACE")
            self.assertFalse(result["automatic_deletion"])
            process_source.assert_not_called()
            self.assertEqual(conn.execute("SELECT state FROM source_inbox").fetchone()["state"],"PREPARATION_REQUIRED")
            conn.close()

    def test_source_daemon_one_shot_polls_once_and_releases_lock(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/"queue.sqlite3"
            with patch("scripts.media_source_daemon.poll_once",return_value={"feeds":[]} ) as poll:
                self.assertEqual(run_source_daemon(db,interval=300,once=True),0)
                self.assertEqual(run_source_daemon(db,interval=300,once=True),0)
            self.assertEqual(poll.call_count,2)

    def test_news_automation_uses_five_minute_poll_and_bounded_voice_retries(self):
        root=Path(__file__).resolve().parents[1]
        source_policy=json.loads((root/"config/media_source_ingress_policy.json").read_text())
        pipeline_policy=json.loads((root/"config/media_news_pipeline_policy.json").read_text())
        fast_path=json.loads((root/"config/media_automation_fast_path.json").read_text())
        read_gate=json.loads((root/"config/media_command_read_gate.json").read_text())
        small_host=json.loads((root/"config/media_small_host_policy.json").read_text())
        self.assertEqual(source_policy["poll_interval_seconds"],300)
        self.assertEqual(source_policy["stale_after_seconds"],600)
        self.assertEqual(pipeline_policy["poll_interval_seconds"],300)
        self.assertEqual(pipeline_policy["voice_retry"]["maximum_attempts"],3)
        self.assertEqual(pipeline_policy["voice_retry"]["retry_delays_seconds"],[60,300])
        self.assertEqual(pipeline_policy["resource_backpressure"]["minimum_workspace_free_bytes"],2*1024**3)
        self.assertEqual(pipeline_policy["resource_backpressure"]["maximum_unresolved_packages_per_queue"],1)
        self.assertFalse(pipeline_policy["resource_backpressure"]["automatic_artifact_deletion"])
        self.assertEqual(pipeline_policy["voice_checkpoint"]["persistent_wav_cache_environment"],"VOICEVOX_CACHE_DIR")
        self.assertIn("--interval-seconds 300",(root/"deploy/systemd/hf-site-agent-media-source.service").read_text())
        self.assertIn("OnUnitInactiveSec=5min",(root/"deploy/systemd/hf-site-agent-media-news.timer").read_text())
        service=(root/"deploy/systemd/hf-site-agent-media-news.service").read_text()
        user_service=(root/"deploy/systemd/user/hf-site-agent-media-news.service").read_text()
        self.assertIn("ExecStartPre=/usr/bin/python3 -m scripts.media_source_daemon --db %h/hf-site-agent/runtime/media-queue.sqlite3 --once",user_service)
        self.assertIn("VOICEVOX_CACHE_DIR=/var/lib/hf-site-agent/voice-cache",service)
        self.assertIn("/var/lib/hf-site-agent/voice-cache",service.split("ExecStartPre=",1)[1])
        remote=fast_path["extracted_pipeline"]["article_to_media_staging"]["optional_remote_render_handoff"]
        self.assertEqual(remote["status"],"IMPLEMENTED_LIVE_HEALTH_REQUIRED")
        self.assertFalse(remote["automatic_retry"])
        self.assertFalse(remote["automatic_local_fallback"])
        self.assertFalse(remote["publishing_enabled"])
        self.assertTrue(fast_path["extracted_pipeline"]["article_to_media_staging"]["final_ffmpeg_render_offloaded"])
        self.assertFalse(fast_path["extracted_pipeline"]["article_to_media_staging"]["gcp_local_video_rendering_allowed"])
        render_worker=json.loads((root/"config/media_render_worker_policy.json").read_text())
        self.assertEqual(render_worker["status"],"IMPLEMENTED_LIVE_HEALTH_REQUIRED")
        self.assertEqual(render_worker["live_connection"]["source_of_truth"],
            "python -m scripts.media_render_transport --check")
        self.assertTrue(render_worker["live_connection"]["static_policy_flags_must_not_be_used_as_live_status"])
        self.assertEqual(small_host["target"]["machine_type"],"e2-small")
        self.assertEqual(small_host["target"]["memory_gib"],2)
        self.assertTrue(small_host["execution"]["preparation_timer_enabled_by_default"])
        self.assertFalse(small_host["execution"]["render_timer_enabled_by_default"])
        self.assertEqual(pipeline_policy["paid_script_generation"]["model"],"deepseek/deepseek-v4.1-flash")
        self.assertEqual(pipeline_policy["paid_script_generation"]["maximum_estimated_cost_per_call_usd"],"0.05")
        self.assertEqual(pipeline_policy["paid_script_generation"]["maximum_reserved_cost_per_utc_day_usd"],"0.10")
        self.assertEqual(pipeline_policy["paid_script_generation"]["maximum_reserved_cost_per_utc_month_usd"],"0.50")
        self.assertEqual(fast_path["extracted_pipeline"]["article_to_media_staging"]["script_route"],
            "deepseek/deepseek-v4.1-flash")
        self.assertEqual(fast_path["extracted_pipeline"]["article_to_media_staging"]["monthly_reserved_cost_cap_usd"],
            "0.50")
        self.assertFalse(pipeline_policy["paid_script_generation"]["automatic_paid_fallback"])
        self.assertFalse(pipeline_policy["paid_script_generation"]["automatic_retry_after_request"])
        self.assertEqual(pipeline_policy["free_script_review"]["route_requirement"],"EXACT_ZERO_COST_FREE_MODEL_ONLY")
        self.assertTrue(pipeline_policy["free_script_review"]["advisory_only"])
        self.assertIn("PYTHONIOENCODING=utf-8:backslashreplace",user_service)
        user_render=(root/"deploy/systemd/user/hf-site-agent-media-render@.service").read_text()
        self.assertIn("--remote-render",user_render)
        self.assertNotIn(" --shell ",user_render)
        news_read_set=set(read_gate["trigger_sets"]["VIDEO_CREATION"]["conditional"]["if_user_requests_article_rss_or_resident_news_video_automation"])
        self.assertIn("docs/GCP_SMALL_HOST_DEPLOYMENT.md",news_read_set)
        self.assertNotIn("docs/VPS_MEDIA_NEWS_AUTOMATION.md",news_read_set)
        self.assertTrue({"config/media_render_worker_policy.json","scripts/media_render_transport.py",
            "scripts/media_render_worker.py","deploy/systemd/hf-render-worker-tunnel.service",
            "deploy/systemd/hf-site-agent-media-render@.service",
            "config/media_small_host_policy.json","docs/DURABLE_MEDIA_AUTOMATION.md",
            "deploy/systemd/user/hf-site-agent-media-news.service",
            "deploy/systemd/user/hf-site-agent-media-news.timer",
            "deploy/systemd/user/hf-site-agent-media-render@.service",
            "tests/test_media_small_host_deployment.py","tests/test_voicevox_lifecycle.py"}.issubset(news_read_set))

    def test_human_review_state_pauses_new_preparation(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);conn=connect(root/"queue.sqlite3");init_inbox(conn)
            ids=["1"*64,"2"*64]
            ingest_items(conn,[{"source_id":source_id,"feed_id":"openai-news","title":"title",
                "url":f"https://openai.com/news/{index}","summary":"","published":""}
                for index,source_id in enumerate(ids)])
            conn.execute("UPDATE source_inbox SET state='ASSET_REVIEW_REQUIRED' WHERE source_id=?",(ids[0],))
            with patch.dict("os.environ",{"OPENROUTER_API_KEY":"configured"}), patch(
                "scripts.media_news_pipeline.process_source",side_effect=AssertionError("must wait for review")
            ):
                result=_process_next(conn,root/"workspace",min_seconds=60,max_seconds=300)
            self.assertEqual(result["status"],"BLOCKED_PENDING_HUMAN_ACTION")
            self.assertEqual(result["source_id"],ids[0])
            self.assertEqual(conn.execute("SELECT state FROM source_inbox WHERE source_id=?",(ids[1],)).fetchone()["state"],"PREPARATION_REQUIRED")
            conn.close()

    def test_low_disk_pauses_before_api_or_voice_work_without_mutating_queue(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);conn=connect(root/"queue.sqlite3");init_inbox(conn)
            source_id="3"*64
            ingest_items(conn,[{"source_id":source_id,"feed_id":"openai-news","title":"title",
                "url":"https://openai.com/news/x","summary":"","published":""}])
            with patch.dict("os.environ",{"OPENROUTER_API_KEY":"configured"}), patch(
                "scripts.media_news_pipeline.shutil.disk_usage",return_value=SimpleNamespace(free=1024**3)
            ), patch("scripts.media_news_pipeline.process_source",side_effect=AssertionError("must wait for space")):
                result=_process_next(conn,root/"workspace",min_seconds=60,max_seconds=300)
            self.assertEqual(result["status"],"BLOCKED_LOW_DISK_SPACE")
            self.assertEqual(result["minimum_free_bytes"],2*1024**3)
            self.assertFalse(result["automatic_deletion"])
            self.assertEqual(conn.execute("SELECT state FROM source_inbox WHERE source_id=?",(source_id,)).fetchone()["state"],"PREPARATION_REQUIRED")
            conn.close()

    def test_render_assets_require_selection_rights_basis_and_credit(self):
        with tempfile.TemporaryDirectory() as td:
            package=Path(td);images=package/"images";images.mkdir()
            assets=[]
            for i in range(6):
                data=f"image-{i}".encode();path=images/f"{i}.png";path.write_bytes(data)
                import hashlib
                assets.append({"id":str(i),"downloaded":True,"selected_for_render":True,
                    "rights_verified":True,"rights_basis":"CC BY 4.0","rights_evidence_url":"https://example.org/license",
                    "credit":"Author","url":"https://openai.com/media/image.png","file":str(path),
                    "sha256":hashlib.sha256(data).hexdigest()})
            self.assertEqual(len(select_render_assets({"assets":assets},3,package)),6)
            for field,value in (("rights_verified",False),("rights_basis",""),("credit",""),("rights_evidence_url","")):
                broken=[dict(x) for x in assets];broken[0][field]=value
                with self.assertRaises(RuntimeError): select_render_assets({"assets":broken},3,package)
            with self.assertRaises(RuntimeError): select_render_assets({"assets":assets[:5]},3,package)
            with self.assertRaises(RuntimeError): select_render_assets({"assets":assets+[dict(assets[0],id="extra")]},3,package)
            duplicated=[dict(x) for x in assets];duplicated[1]["sha256"]=duplicated[0]["sha256"]
            with self.assertRaises(RuntimeError): select_render_assets({"assets":duplicated},3,package)
            escaped=[dict(x) for x in assets];escaped[0]["file"]="/etc/passwd"
            with self.assertRaises(RuntimeError): select_render_assets({"assets":escaped},3,package)
            linked=images/"linked.png";linked.symlink_to(images/"0.png")
            symlinked=[dict(x) for x in assets];symlinked[0]["file"]=str(linked)
            with self.assertRaisesRegex(RuntimeError,"symbolic link"):
                select_render_assets({"assets":symlinked},3,package)

    def test_rss_summary_fallback_is_bounded_and_requires_enough_source_text(self):
        row={"url":"https://openai.com/news/example","title":"Official update","summary":TEXT * 4}
        article=_rss_summary_article(row)
        self.assertEqual(article["article_text_origin"],"CONFIGURED_RSS_SUMMARY_FALLBACK")
        self.assertEqual(article["url"],row["url"])
        self.assertGreaterEqual(len(article["text"]),300)
        with self.assertRaises(ArticleSourceBlocked):
            _rss_summary_article({**row,"summary":"Too short."})

    def test_high_priority_feed_is_prepared_before_older_low_priority_backlog(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);conn=connect(root/"queue.sqlite3");init_inbox(conn)
            openai_id="a"*64
            deepmind_id="b"*64
            ingest_items(conn,[
                {"source_id":openai_id,"feed_id":"openai-news","title":"OpenAI article",
                 "url":"https://openai.com/news/x","summary":TEXT*4,"published":""},
                {"source_id":deepmind_id,"feed_id":"google-deepmind","title":"DeepMind article",
                 "url":"https://deepmind.google/blog/x","summary":TEXT*4,"published":""},
            ])
            conn.execute("UPDATE source_inbox SET created_at=1 WHERE source_id=?",(deepmind_id,))
            with patch.dict("os.environ",{"OPENROUTER_API_KEY":"configured"}), patch(
                "scripts.media_news_pipeline.load_policy",
                return_value={"feeds":[
                    {"feed_id":"openai-news","enabled":True,"priority":0},
                    {"feed_id":"google-deepmind","enabled":True,"priority":10},
                ]},
            ), patch(
                "scripts.media_news_pipeline.process_source",side_effect=ArticleSourceBlocked()
            ) as process_source, patch(
                "scripts.media_news_pipeline.shutil.disk_usage",
                return_value=SimpleNamespace(free=3*1024**3),
            ):
                result=_process_next(conn,root/"workspace",min_seconds=60,max_seconds=300)
            self.assertEqual(result["status"],"ARTICLE_SOURCE_BLOCKED")
            self.assertEqual(result["source_id"],deepmind_id)
            self.assertEqual(process_source.call_args.args[1],deepmind_id)
            self.assertEqual(conn.execute(
                "SELECT state FROM source_inbox WHERE source_id=?",(deepmind_id,)
            ).fetchone()["state"],"SCRIPT_BLOCKED")
            self.assertEqual(conn.execute(
                "SELECT state FROM source_inbox WHERE source_id=?",(openai_id,)
            ).fetchone()["state"],"PREPARATION_REQUIRED")
            conn.close()

    def test_article_redirect_failure_uses_bounded_rss_summary(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);workspace=root/"workspace";workspace.mkdir()
            conn=connect(root/"queue.sqlite3");init_inbox(conn)
            source_id="e"*64
            summary=TEXT*4
            ingest_items(conn,[{"source_id":source_id,"feed_id":"google-deepmind","title":"Official update",
                "url":"https://deepmind.google/blog/example","summary":summary,"published":""}])
            draft=story()
            for scene in draft["scenes"]:
                scene["image_search_hint"]="official update"
            with patch.dict("os.environ",{"OPENROUTER_API_KEY":"configured"}), patch(
                "scripts.media_news_pipeline.extract_article",
                side_effect=urllib.error.URLError("redirect left configured HTTPS hosts"),
            ), patch(
                "scripts.media_news_pipeline.draft_story",
                return_value=(draft,"fixture/news:free"),
            ) as draft_story:
                mission=process_source(conn,source_id,workspace,image_hosts={"deepmind.google"})
            article=json.loads((mission.parent/"article.json").read_text(encoding="utf-8"))
            self.assertEqual(article["article_text_origin"],"CONFIGURED_RSS_SUMMARY_FALLBACK")
            self.assertEqual(article["text"],summary)
            self.assertEqual(draft_story.call_args.args[1]["text"],summary)
            self.assertEqual(conn.execute("SELECT state FROM source_inbox").fetchone()["state"],"VOICE_PENDING")
            conn.close()

    def test_inaccessible_article_with_short_summary_is_skipped_without_retry_loop(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);conn=connect(root/"queue.sqlite3");init_inbox(conn)
            source_id="d"*64
            ingest_items(conn,[{"source_id":source_id,"feed_id":"openai-news","title":"title",
                "url":"https://openai.com/news/x","summary":"Too short.","published":""}])
            with patch.dict("os.environ",{"OPENROUTER_API_KEY":"configured"}), patch(
                "scripts.media_news_pipeline.extract_article",
                side_effect=urllib.error.URLError("redirect left configured HTTPS hosts"),
            ), patch(
                "scripts.media_news_pipeline.shutil.disk_usage",
                return_value=SimpleNamespace(free=3 * 1024**3),
            ):
                result=_process_next(conn,root/"workspace",min_seconds=60,max_seconds=300)
            self.assertEqual(result["status"],"ARTICLE_SOURCE_BLOCKED")
            self.assertTrue(result["will_try_next_source"])
            self.assertEqual(conn.execute("SELECT state FROM source_inbox").fetchone()["state"],"SCRIPT_BLOCKED")
            conn.close()

    def test_article_parser_extracts_text_and_only_allowlisted_image_urls(self):
        body = (TEXT + " ") * 3
        markup = ("<html><head><title>Official news</title><meta property='og:image' "
                  "content='https://images.ctfassets.net/openai/hero.png'></head><body>" + body +
                  "<img src='https://openai.com/media/slide.jpg'><img src='https://evil.invalid/a.jpg'>"
                  "<script>not article content</script></body></html>").encode()
        article = extract_article("https://openai.com/news/example", allowed_hosts={"openai.com","images.ctfassets.net"},
                                  opener=lambda *_a, **_k: Response(markup))
        self.assertIn("OpenAI announced", article["text"])
        self.assertNotIn("not article content", article["text"])
        self.assertEqual(len(article["images"]),2)
        self.assertTrue(all("evil.invalid" not in image["url"] for image in article["images"]))

    def test_story_requires_exact_evidence_and_both_real_speakers(self):
        result=validate_story(story(),TEXT)
        self.assertEqual(result["title"],"開発者向け新機能")
        bad=story();bad["scenes"][0]["source_excerpt"]="Not present in source"
        with self.assertRaises(ValueError): validate_story(bad,TEXT)
        bad=story()
        for scene in bad["scenes"]:
            for line in scene["dialogue"]: line["speaker"]="ずんだもん"
        with self.assertRaises(ValueError): validate_story(bad,TEXT)
        bad=story();bad["scenes"][1]["scene_id"]=bad["scenes"][0]["scene_id"]
        with self.assertRaises(ValueError): validate_story(bad,TEXT)
        bad=story();bad["scenes"][0]["title"]=""
        with self.assertRaises(ValueError): validate_story(bad,TEXT)
        bad=story();bad["scenes"][0]["dialogue"][0]["id"]="../escape"
        with self.assertRaises(ValueError): validate_story(bad,TEXT)

    def test_story_call_uses_exact_deepseek_v41_flash_with_live_price_cap_and_no_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            conn=connect(Path(td)/"q.sqlite3");init_inbox(conn)
            call={}
            catalog=[{"id":"deepseek/deepseek-v4.1-flash",
                "pricing":{"prompt":"0.000000015","completion":"0.0000012"},
                "supported_parameters":["response_format"]}]
            def requester(payload,key):
                call.update(payload=payload,key_seen=bool(key))
                return {"model":"deepseek/deepseek-v4.1-flash","choices":[{"message":{"content":json.dumps(story(),ensure_ascii=False)}}],
                    "usage":{"cost":"0.0001","prompt_tokens":200,"completion_tokens":200}}
            with patch.dict("os.environ",{"OPENROUTER_API_KEY":"test-secret-never-logged"}):
                result,model=draft_story(conn,{"title":"title","url":"https://openai.com/news/x","text":TEXT},
                    catalog=catalog,request_fn=requester,
                    planner_fn=lambda *_a,**_k:{"status":"READY","primary_model":"reviewer/model:free","provider_allow_fallbacks":False})
            self.assertEqual(model,"deepseek/deepseek-v4.1-flash")
            self.assertEqual(len(result["scenes"]),3)
            self.assertTrue(call["key_seen"])
            self.assertIs(call["payload"]["provider"]["allow_fallbacks"],False)
            self.assertEqual(call["payload"]["provider"]["sort"],"price")
            self.assertTrue(call["payload"]["usage"]["include"])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM media_news_paid_calls").fetchone()[0],1)
            conn.close()

    def test_paid_model_preflight_fails_closed_and_does_not_reserve_or_call(self):
        with tempfile.TemporaryDirectory() as td:
            conn=connect(Path(td)/"q.sqlite3");init_inbox(conn)
            bad_catalogs=[
                [{"id":"deepseek/deepseek-v4.1-flash","pricing":{"prompt":"0.000000015","completion":"0.0000012"},"supported_parameters":[]}],
                [{"id":"deepseek/deepseek-v4.1-flash","pricing":{"prompt":"unknown","completion":"0.0000012"},"supported_parameters":["response_format"]}],
            ]
            for catalog in bad_catalogs:
                with patch.dict("os.environ",{"OPENROUTER_API_KEY":"test"}):
                    with self.assertRaises(PaidMediaPreflightUnavailable):
                        draft_story(conn,{"title":"title","url":"https://openai.com/news/x","text":TEXT},
                            catalog=catalog,request_fn=lambda *_a:(_ for _ in ()).throw(AssertionError("must not call")))
            self.assertEqual(_paid_calls_used_today(conn),0)
            conn.close()

    def test_paid_attempt_is_never_sent_twice_without_a_script_checkpoint(self):
        with tempfile.TemporaryDirectory() as td:
            conn=connect(Path(td)/"q.sqlite3");init_inbox(conn)
            catalog=[{"id":"deepseek/deepseek-v4.1-flash",
                "pricing":{"prompt":"0.000000015","completion":"0.0000012"},
                "supported_parameters":["response_format"]}]
            calls=[]
            def requester(payload,key):
                calls.append(payload["model"])
                return {"model":payload["model"],"choices":[{"message":{"content":json.dumps(story(),ensure_ascii=False)}}],
                    "usage":{"cost":"0.0001"}}
            article={"title":"title","url":"https://openai.com/news/x","text":TEXT}
            with patch.dict("os.environ",{"OPENROUTER_API_KEY":"test"}):
                draft_story(conn,article,catalog=catalog,request_fn=requester)
                with self.assertRaises(PaidMediaAlreadyAttempted):
                    draft_story(conn,article,catalog=catalog,request_fn=requester)
            paid_calls=[model for model in calls if model=="deepseek/deepseek-v4.1-flash"]
            self.assertEqual(paid_calls,["deepseek/deepseek-v4.1-flash"])
            conn.close()

    def test_free_reviewer_uses_exact_free_model_and_is_advisory(self):
        with tempfile.TemporaryDirectory() as td:
            conn=connect(Path(td)/"q.sqlite3");init_inbox(conn)
            payloads=[]
            def request(payload,key):
                payloads.append(payload)
                return {"model":"reviewer/model:free","usage":{"cost":"0"},
                    "choices":[{"message":{"content":json.dumps({"decision":"FLAG","flags":["verify excerpt"]})}}]}
            with patch.dict("os.environ",{"OPENROUTER_API_KEY":"test"}):
                result=_review_story_free(conn,{"title":"title","url":"https://openai.com/news/x","text":TEXT},
                    story(),catalog=[{"id":"reviewer/model:free","pricing":{"prompt":"0","completion":"0"}}],
                    planner_fn=lambda *_a,**_k:{"status":"READY","primary_model":"reviewer/model:free","provider_allow_fallbacks":False},
                    request_fn=request)
            self.assertEqual(result["status"],"COMPLETE")
            self.assertEqual(result["decision"],"FLAG")
            self.assertEqual(payloads[0]["model"],"reviewer/model:free")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM media_news_model_calls").fetchone()[0],1)
            conn.close()

    def test_paid_actual_cost_must_be_present_and_within_reserved_cap(self):
        catalog=[{"id":"deepseek/deepseek-v4.1-flash",
            "pricing":{"prompt":"0.000000015","completion":"0.0000012"},
            "supported_parameters":["response_format"]}]
        for usage in ({"cost":"1.0"},{}):
            with tempfile.TemporaryDirectory() as td:
                conn=connect(Path(td)/"q.sqlite3");init_inbox(conn)
                with patch.dict("os.environ",{"OPENROUTER_API_KEY":"test"}):
                    with self.assertRaises(RuntimeError):
                        draft_story(conn,{"title":"title","url":"https://openai.com/news/x","text":TEXT},
                            catalog=catalog,request_fn=lambda payload,_key,u=usage: {
                                "model":payload["model"],"usage":u,"choices":[]})
                state=conn.execute("SELECT state FROM media_news_paid_calls").fetchone()["state"]
                self.assertEqual(state,"OUTCOME_UNKNOWN")
                conn.close()

    def test_article_to_persistent_script_package_is_resumable(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);workspace=root/"workspace";workspace.mkdir()
            conn=connect(root/"q.sqlite3");init_inbox(conn)
            source_id="a"*64
            ingest_items(conn,[{"source_id":source_id,"feed_id":"openai-news","title":"title",
                "url":"https://openai.com/news/example","summary":"", "published":""}])
            planner=lambda *_a,**_k:{"status":"READY","primary_model":"deepseek/deepseek-v4.1-flash","provider_allow_fallbacks":False}
            calls={"model":0,"image":0}
            def requester(*_a):
                calls["model"]+=1
                return {"model":"deepseek/deepseek-v4.1-flash","usage":{"cost":"0.0001"},"choices":[{"message":{"content":json.dumps(story(),ensure_ascii=False)}}]}
            def fake_download(_url,dest_dir,**_kwargs):
                import hashlib
                calls["image"]+=1;dest_dir.mkdir(parents=True,exist_ok=True)
                data=b"fixture-image";path=dest_dir/(hashlib.sha256(data).hexdigest()+".png");path.write_bytes(data)
                return {"file":str(path),"sha256":hashlib.sha256(data).hexdigest(),"url":"https://openai.com/image.png","bytes":len(data)}
            with patch.dict("os.environ",{"OPENROUTER_API_KEY":"test"}), patch(
                "scripts.media_news_pipeline.extract_article",return_value={"title":"title","url":"https://openai.com/news/example","text":TEXT,"description":"","images":[{"url":"https://openai.com/image.png","alt":"official image"}]}
            ), patch("scripts.media_news_pipeline.download_article_image",side_effect=fake_download):
                mission=process_source(conn,source_id,workspace,image_hosts={"openai.com"},catalog=[{"id":"deepseek/deepseek-v4.1-flash",
                    "pricing":{"prompt":"0.000000015","completion":"0.0000012"},"supported_parameters":["response_format"]}],
                    request_fn=requester,planner_fn=planner)
            saved=json.loads(mission.read_text())
            self.assertEqual(len(saved["scenes"]),3)
            self.assertTrue(saved["source_sha256"])
            self.assertTrue((mission.parent/"mission.json.gz.b64").is_file())
            checkpoint=json.loads((mission.parent/"script-generation.json").read_text())
            self.assertEqual(checkpoint["model_id"],"deepseek/deepseek-v4.1-flash")
            self.assertEqual(checkpoint["status"],"SCRIPT_READY")
            self.assertLessEqual(float(checkpoint["estimated_cost_usd"]),0.05)
            self.assertEqual(conn.execute("SELECT state FROM source_inbox").fetchone()["state"],"VOICE_PENDING")
            manifest=mission.parent/"image-candidates.json";images=json.loads(manifest.read_text())
            images["assets"][0].update(selected_for_render=True,rights_verified=True,rights_basis="licensed",rights_evidence_url="https://example.org/license",credit="OpenAI")
            manifest.write_text(json.dumps(images))
            with patch.dict("os.environ",{"OPENROUTER_API_KEY":"test"}), patch(
                "scripts.media_news_pipeline.extract_article",return_value={"title":"title","url":"https://openai.com/news/example","text":TEXT,"description":"","images":[{"url":"https://openai.com/image.png","alt":"official image"}]}
            ), patch("scripts.media_news_pipeline.download_article_image",side_effect=AssertionError("cached assets must be reused")):
                reused=process_source(conn,source_id,workspace,image_hosts={"openai.com"},catalog=[{"id":"deepseek/deepseek-v4.1-flash",
                    "pricing":{"prompt":"0.000000015","completion":"0.0000012"},"supported_parameters":["response_format"]}],
                    request_fn=lambda *_a:(_ for _ in ()).throw(AssertionError("cached script must be reused")),planner_fn=planner)
            self.assertEqual(reused,mission)
            self.assertEqual(calls,{"model":1,"image":1})
            self.assertTrue(json.loads(manifest.read_text())["assets"][0]["rights_verified"])
            conn.close()

    def test_voice_parts_are_joined_into_renderer_mono_pcm(self):
        with tempfile.TemporaryDirectory() as td:
            package=Path(td)/"media-news"/("a"*64);package.mkdir(parents=True)
            mission={"source_id":"a"*64,"source_sha256":"b"*64,
                "scenes":[{"dialogue":[{"id":"a"},{"id":"b"}]}]}
            (package/"mission.json").write_text(json.dumps(mission))
            def fake_run(command,**kwargs):
                out=package/"voice-parts";out.mkdir(exist_ok=True)
                for name in ("a.wav","b.wav"):
                    with wave.open(str(out/name),"wb") as w:
                        w.setnchannels(2);w.setsampwidth(2);w.setframerate(48000);w.writeframes(array.array("h",[100,-100]*2400).tobytes())
                (package/"timing.json").write_text(json.dumps({"total_duration":0.2,"voicevox_credit":["VOICEVOX:ずんだもん","VOICEVOX:四国めたん"],"voice_cache_hits":0,
                  "engine_version":"fixture-1","records":[{"id":"a","wav_file":"a.wav","pause_after":0.01},
                    {"id":"b","wav_file":"b.wav","pause_after":0.01}]}))
            with patch.dict("os.environ",{"VOICEVOX_CACHE_DIR":"/persistent/cache"}), patch(
                "scripts.media_news_pipeline.subprocess.run",side_effect=fake_run) as runner:
                result=synthesize_voice(package,min_seconds=0,max_seconds=1)
                self.assertFalse(result["reused"])
                self.assertEqual(runner.call_args.kwargs["env"]["VOICEVOX_CACHE_DIR"],"/persistent/cache")
                reused=synthesize_voice(package,min_seconds=0,max_seconds=1)
                self.assertTrue(reused["reused"])
                runner.assert_called_once()
                (package/"audio.wav").write_bytes(b"corrupt cached audio")
                regenerated=synthesize_voice(package,min_seconds=0,max_seconds=1)
                self.assertFalse(regenerated["reused"])
                self.assertEqual(runner.call_count,2)
            self.assertTrue((package/"mission.json.gz.b64").is_file())
            with wave.open(result["audio"],"rb") as wav:
                self.assertEqual((wav.getnchannels(),wav.getsampwidth(),wav.getframerate()),(1,2,48000))
                self.assertEqual(wav.getnframes(),2400+480+2400+480)

    def test_voice_stage_rejects_unsafe_dialogue_ids_and_symlinked_parts(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);source_id="e"*64
            package=root/"media-news"/source_id;package.mkdir(parents=True)
            mission={"source_id":source_id,"source_sha256":"f"*64,
                "scenes":[{"dialogue":[{"id":"../escape"}]}]}
            (package/"mission.json").write_text(json.dumps(mission))
            with self.assertRaisesRegex(ValueError,"path-safe"):
                synthesize_voice(package,min_seconds=0,max_seconds=1)
            mission["scenes"][0]["dialogue"][0]["id"]="safe-id"
            (package/"mission.json").write_text(json.dumps(mission))
            outside=root/"outside";outside.mkdir()
            (package/"voice-parts").symlink_to(outside,target_is_directory=True)
            with self.assertRaisesRegex(ValueError,"regular package-local paths"):
                synthesize_voice(package,min_seconds=0,max_seconds=1)

    def test_voice_failures_back_off_then_block_and_allow_manual_requeue(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);workspace=root/"workspace";workspace.mkdir()
            conn=connect(root/"queue.sqlite3");init_inbox(conn)
            source_id="c"*64
            ingest_items(conn,[{"source_id":source_id,"feed_id":"openai-news","title":"title",
                "url":"https://openai.com/news/x","summary":"","published":""}])
            conn.execute("UPDATE source_inbox SET state='VOICE_PENDING' WHERE source_id=?",(source_id,))
            package=_resolve_news_package(workspace,source_id,create=True)
            (package/"mission.json").write_text(json.dumps({"source_id":source_id,"source_sha256":"d"*64}))
            with patch("scripts.media_news_pipeline.synthesize_voice",side_effect=OSError("unavailable")), patch(
                "scripts.media_news_pipeline.shutil.disk_usage",
                return_value=SimpleNamespace(free=3 * 1024**3),
            ):
                first=_process_next(conn,workspace,min_seconds=60,max_seconds=300)
                self.assertEqual(first["status"],"VOICE_RETRY_SCHEDULED")
                self.assertEqual(first["attempts"],1)
                self.assertGreaterEqual(first["retry_after_seconds"],58)
                self.assertLessEqual(first["retry_after_seconds"],60)
                self.assertEqual(_process_next(conn,workspace,min_seconds=60,max_seconds=300)["status"],"VOICE_RETRY_WAIT")
                conn.execute("UPDATE media_news_stage_retry SET next_attempt_at=0 WHERE source_id=?",(source_id,))
                second=_process_next(conn,workspace,min_seconds=60,max_seconds=300)
                self.assertEqual(second["attempts"],2)
                self.assertGreaterEqual(second["retry_after_seconds"],298)
                self.assertLessEqual(second["retry_after_seconds"],300)
                conn.execute("UPDATE media_news_stage_retry SET next_attempt_at=0 WHERE source_id=?",(source_id,))
                third=_process_next(conn,workspace,min_seconds=60,max_seconds=300)
            self.assertEqual(third["status"],"VOICE_BLOCKED")
            self.assertEqual(conn.execute("SELECT state FROM source_inbox WHERE source_id=?",(source_id,)).fetchone()["state"],"VOICE_BLOCKED")
            requeued=_requeue_voice(conn,workspace,source_id)
            self.assertEqual(requeued["status"],"VOICE_REQUEUED")
            self.assertEqual(conn.execute("SELECT state FROM source_inbox WHERE source_id=?",(source_id,)).fetchone()["state"],"VOICE_PENDING")
            conn.close()

    def test_remote_voicevox_tunnel_accepts_loopback_and_rejects_public_hosts(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/version":
                    payload=b'"fixture-1"'
                elif self.path == "/speakers":
                    payload=json.dumps([
                        {"name":"ずんだもん","styles":[{"name":"ノーマル","id":1}]},
                        {"name":"四国めたん","styles":[{"name":"ノーマル","id":2}]},
                    ],ensure_ascii=False).encode()
                else:
                    self.send_error(404);return
                self.send_response(200);self.send_header("Content-Type","application/json")
                self.send_header("Content-Length",str(len(payload)));self.end_headers();self.wfile.write(payload)
            def log_message(self,*_args): pass

        server=HTTPServer(("127.0.0.1",0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with tempfile.TemporaryDirectory() as td:
                env={"PATH":os.environ.get("PATH","/usr/bin:/bin"),"VOICEVOX_REMOTE_TUNNEL":"1",
                    "VOICEVOX_URL":f"http://127.0.0.1:{server.server_port}","VOICEVOX_ENGINE_DIR":str(Path(td)/"absent")}
                ok=subprocess.run(["bash","scripts/with_local_voicevox.sh","--","true"],cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=10)
                self.assertEqual(ok.returncode,0,ok.stderr)
                env["VOICEVOX_URL"]="http://192.0.2.1:50021"
                rejected=subprocess.run(["bash","scripts/with_local_voicevox.sh","--","true"],cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=10)
                self.assertEqual(rejected.returncode,2)
                self.assertIn("loopback-only",rejected.stderr)
                env["VOICEVOX_URL"] = f"http://127.0.0.1:{server.server_port}"
                env["VOICEVOX_EXPECTED_VERSION"] = "different-version"
                mismatch=subprocess.run(["bash","scripts/with_local_voicevox.sh","--","true"],cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=10)
                self.assertNotEqual(mismatch.returncode,0)
                self.assertIn("expected version",mismatch.stderr)
        finally:
            server.shutdown();thread.join(timeout=2);server.server_close()


if __name__=="__main__":
    unittest.main()
