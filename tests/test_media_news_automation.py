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
from scripts.media_news_pipeline import ArticleSourceBlocked, DailyMediaCapReached, OpenRouterRequestError, PaidMediaAlreadyAttempted, PaidMediaBalanceBlocked, PaidMediaBudgetExceeded, PaidMediaMonthlyCapReached, PaidMediaPreflightUnavailable, PIPELINE_POLICY, _paid_reserved_cost_this_month, _pipeline_lock, _process_next, _requeue_voice, _resolve_news_package, _reserve_call, _reserve_paid_call, _paid_calls_used_today, _resume_paid_provider, _rss_summary_article, _validate_existing_package, _review_story_free, draft_story, extract_article, process_source, select_render_assets, synthesize_voice, validate_story, _post_chat, _post_deepseek_chat
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
            with patch.dict("os.environ",{"DEEPSEEK_API_KEY":"test"}), patch(
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
                _post_chat({"model":"fixture/model:free"}, "hidden")
        self.assertNotIn("PRIVATE_TOKEN_SHOULD_NOT_LEAK", str(caught.exception))

    def test_openrouter_free_error_does_not_block_direct_deepseek_route(self):
        self.assertFalse(PIPELINE_POLICY["openrouter_routing"]["paid_fallback_allowed"])
        self.assertEqual(PIPELINE_POLICY["paid_script_generation"]["base_url"],"https://api.deepseek.com")

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

    def test_deepseek_artificial_daily_cap_is_removed(self):
        paid=PIPELINE_POLICY["paid_script_generation"]
        self.assertIsNone(paid["artificial_daily_cap_usd"])
        self.assertIsNone(paid["artificial_daily_call_cap"])
        self.assertIsNone(paid["artificial_monthly_cap_usd"])
        with tempfile.TemporaryDirectory() as td:
            conn=connect(Path(td)/"q.sqlite3");init_inbox(conn)
            from decimal import Decimal
            for i in range(25):
                _reserve_paid_call(conn,str(i),Decimal("1.0"),"deepseek-flash",Decimal("0.0000003"),Decimal("0.0000012"))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM media_news_paid_calls").fetchone()[0],25)
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

    def test_deepseek_balance_block_preserves_queue_policy(self):
        self.assertEqual(PIPELINE_POLICY["paid_script_generation"]["balance_exhaustion_state"],"BLOCKED_BALANCE")

    def test_deepseek_artificial_monthly_cap_is_removed(self):
        paid=PIPELINE_POLICY["paid_script_generation"]
        self.assertIsNone(paid["artificial_monthly_cap_usd"])
        self.assertIsNone(paid["artificial_per_call_cap_usd"])

    def test_openrouter_policy_is_free_only(self):
        route=PIPELINE_POLICY["openrouter_routing"]
        self.assertTrue(route["free_only"])
        self.assertFalse(route["paid_models_allowed"])
        self.assertFalse(route["paid_fallback_allowed"])
        self.assertEqual(PIPELINE_POLICY["primary_script_route"],"OPENROUTER_FREE_THEN_DEEPSEEK_OFFICIAL")

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
        pipeline=json.loads((root/"config/media_news_pipeline_policy.json").read_text())
        self.assertEqual(source_policy["poll_interval_seconds"],300)
        self.assertEqual(pipeline["poll_interval_seconds"],300)
        self.assertEqual(pipeline["paid_script_generation"]["provider"],"deepseek_official")
        self.assertEqual(pipeline["paid_script_generation"]["base_url"],"https://api.deepseek.com")
        self.assertEqual(pipeline["paid_script_generation"]["model"],"deepseek-flash")
        self.assertIsNone(pipeline["paid_script_generation"]["artificial_daily_cap_usd"])
        self.assertFalse(pipeline["paid_script_generation"]["automatic_top_up"])
        self.assertTrue(pipeline["openrouter_routing"]["free_only"])
        self.assertFalse(pipeline["openrouter_routing"]["paid_models_allowed"])
        self.assertFalse(pipeline["openrouter_routing"]["paid_fallback_allowed"])

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
                "scripts.media_news_pipeline.process_source",
                side_effect=ArticleSourceBlocked(
                    "article page could not be fetched and RSS summary is too short"
                ),
            ) as process_source, patch(
                "scripts.media_news_pipeline.shutil.disk_usage",
                return_value=SimpleNamespace(free=3*1024**3),
            ):
                result=_process_next(conn,root/"workspace",min_seconds=60,max_seconds=300)
            self.assertEqual(result["status"],"ARTICLE_SOURCE_BLOCKED")
            self.assertEqual(result["source_id"],deepmind_id)
            self.assertEqual(result["reason"],"ARTICLE_PAGE_FETCH_FAILED_RSS_SUMMARY_TOO_SHORT")
            self.assertFalse(result["request_sent"])
            self.assertEqual(process_source.call_args.args[1],deepmind_id)
            blocked=json.loads((root/"workspace"/"media-news"/deepmind_id/"blocked.json").read_text())
            self.assertEqual(blocked["reason_code"],result["reason"])
            self.assertFalse(blocked["request_sent"])
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

    def test_openrouter_free_script_success(self):
        with tempfile.TemporaryDirectory() as td:
            conn=connect(Path(td)/"q.sqlite3");init_inbox(conn);call={}
            catalog=[{"id":"fixture/model:free","pricing":{"prompt":"0","completion":"0"}}]
            def requester(payload,key):
                call.update(payload=payload,key=key)
                return {"model":"fixture/model:free","choices":[{"message":{"content":json.dumps(story(),ensure_ascii=False)}}],"usage":{"cost":"0","prompt_tokens":50,"completion_tokens":60}}
            with patch.dict("os.environ",{"OPENROUTER_API_KEY":"openrouter-secret"},clear=True):
                result,model=draft_story(conn,{"title":"title","url":"https://openai.com/news/x","text":TEXT},
                    free_catalog=catalog,request_fn=requester,
                    planner_fn=lambda *_a,**_k:{"status":"READY","primary_model":"fixture/model:free","provider_allow_fallbacks":False})
            self.assertEqual(model,"fixture/model:free")
            self.assertIs(call["payload"]["provider"]["allow_fallbacks"],False)
            self.assertNotIn("openrouter-secret",json.dumps(call["payload"]))
            conn.close()

    def test_openrouter_paid_model_is_rejected_before_network_request(self):
        with self.assertRaisesRegex(ValueError,"paid and unverified"):
            _post_chat({"model":"deepseek/deepseek-v4.1-flash"},"key")

    def test_deepseek_official_result_is_not_requested_twice(self):
        from scripts.media_news_pipeline import DEEPSEEK_PAID_MODEL
        with tempfile.TemporaryDirectory() as td:
            conn=connect(Path(td)/"q.sqlite3");init_inbox(conn);calls=[]
            def free_request(payload,_key):
                raise OpenRouterRequestError("OpenRouter request failed with HTTP 429 (RATE_LIMIT)")
            def direct(payload,_key):
                calls.append(payload["model"])
                return {"model":DEEPSEEK_PAID_MODEL,"choices":[{"message":{"content":json.dumps(story(),ensure_ascii=False)}}],
                    "usage":{"prompt_tokens":300,"completion_tokens":120,"prompt_tokens_details":{"cached_tokens":20}}}
            article={"title":"title","url":"https://openai.com/news/x","text":TEXT}
            free=[{"id":"fixture/model:free","pricing":{"prompt":"0","completion":"0"}}]
            plan=lambda *_a,**_k:{"status":"READY","primary_model":"fixture/model:free","provider_allow_fallbacks":False}
            with patch.dict("os.environ",{"OPENROUTER_API_KEY":"or","DEEPSEEK_API_KEY":"ds"}):
                first=draft_story(conn,article,free_catalog=free,planner_fn=plan,request_fn=free_request,deepseek_request_fn=direct)
                with self.assertRaises(PaidMediaAlreadyAttempted):
                    draft_story(conn,article,free_catalog=free,planner_fn=plan,request_fn=free_request,deepseek_request_fn=direct)
            self.assertEqual(first[1],DEEPSEEK_PAID_MODEL)
            self.assertEqual(calls,[DEEPSEEK_PAID_MODEL])
            row=conn.execute("SELECT provider,actual_cost_usd,retry_count FROM media_news_usage_events").fetchone()
            self.assertEqual(row["provider"],"deepseek_official");self.assertIsNone(row["actual_cost_usd"])
            conn.close()


    def test_deepseek_transport_is_official_direct_and_does_not_log_key(self):
        captured={}
        class JsonResponse:
            def __enter__(self): return self
            def __exit__(self,*_args): return None
            def read(self,_limit=-1): return json.dumps({"model":"deepseek-flash","choices":[],"usage":{}}).encode()
        def send(request,timeout):
            captured["url"]=request.full_url
            captured["authorization"]=request.get_header("Authorization")
            captured["body"]=request.data
            captured["timeout"]=timeout
            return JsonResponse()
        test_key="fixture"
        with patch("scripts.media_news_pipeline.urllib.request.urlopen",side_effect=send):
            _post_deepseek_chat({"model":"deepseek-flash","messages":[]},test_key)
        self.assertEqual(captured["url"],"https://api.deepseek.com/chat/completions")
        self.assertEqual(captured["authorization"].split()[0],"Bearer")
        self.assertNotIn(test_key.encode(),captured["body"])

    def test_openrouter_generic_free_route_requires_zero_catalog_price(self):
        from scripts.media_news_pipeline import _resolve_free_script_model
        free_catalog=[{"id":"openrouter/free","pricing":{"prompt":"0","completion":"0"}}]
        plan=lambda *_a,**_k:{"status":"READY","primary_model":"openrouter/free","provider_allow_fallbacks":False}
        self.assertEqual(_resolve_free_script_model(free_catalog,plan)[0],"openrouter/free")
        with self.assertRaises(PaidMediaPreflightUnavailable):
            _resolve_free_script_model([{"id":"openrouter/free","pricing":{"prompt":"0.001","completion":"0"}}],plan)

    def test_deepseek_balance_error_is_blocked_and_queue_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            conn=connect(Path(td)/"q.sqlite3");init_inbox(conn)
            article={"title":"title","url":"https://openai.com/news/x","text":TEXT}
            free=[{"id":"fixture/model:free","pricing":{"prompt":"0","completion":"0"}}]
            plan=lambda *_a,**_k:{"status":"READY","primary_model":"fixture/model:free","provider_allow_fallbacks":False}
            with patch.dict("os.environ",{"OPENROUTER_API_KEY":"or","DEEPSEEK_API_KEY":"ds"}):
                with self.assertRaises(PaidMediaBalanceBlocked):
                    draft_story(conn,article,free_catalog=free,planner_fn=plan,
                        request_fn=lambda *_a: (_ for _ in ()).throw(OpenRouterRequestError("free unavailable")),
                        deepseek_request_fn=lambda *_a: (_ for _ in ()).throw(PaidMediaBalanceBlocked("balance")))
            row=conn.execute("SELECT state FROM media_news_paid_calls").fetchone()
            self.assertEqual(row["state"],"BLOCKED_BALANCE")
            telemetry=conn.execute("SELECT provider,status FROM media_news_usage_events").fetchone()
            self.assertEqual((telemetry["provider"],telemetry["status"]),("deepseek_official","BLOCKED_BALANCE"))
            self.assertNotIn("ds",json.dumps([dict(row) for row in conn.execute("SELECT * FROM media_news_usage_events")]))
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

    def test_deepseek_usage_estimate_is_logged_without_claiming_actual_billing(self):
        from scripts.media_news_pipeline import _deepseek_peak_cost
        estimate,ratio=_deepseek_peak_cost({"prompt_tokens":1000,"completion_tokens":100,"prompt_tokens_details":{"cached_tokens":200}})
        self.assertGreater(estimate,0)
        self.assertEqual(ratio,__import__("decimal").Decimal("0.2"))

    def test_article_to_persistent_script_package_is_resumable(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);workspace=root/"workspace";workspace.mkdir()
            conn=connect(root/"q.sqlite3");init_inbox(conn)
            source_id="a"*64
            ingest_items(conn,[{"source_id":source_id,"feed_id":"openai-news","title":"title",
                "url":"https://openai.com/news/example","summary":"", "published":""}])
            planner=lambda *_a,**_k:{"status":"READY","primary_model":"fixture/model:free","provider_allow_fallbacks":False}
            calls={"model":0,"image":0}
            def requester(*_a):
                calls["model"]+=1
                raise OpenRouterRequestError("OpenRouter request failed with HTTP 429 (RATE_LIMIT)")
            def deepseek_requester(payload,_key):
                return {"model":"deepseek-flash","usage":{"prompt_tokens":200,"completion_tokens":100},"choices":[{"message":{"content":json.dumps(story(),ensure_ascii=False)}}]}
            def fake_download(_url,dest_dir,**_kwargs):
                import hashlib
                calls["image"]+=1;dest_dir.mkdir(parents=True,exist_ok=True)
                data=b"fixture-image";path=dest_dir/(hashlib.sha256(data).hexdigest()+".png");path.write_bytes(data)
                return {"file":str(path),"sha256":hashlib.sha256(data).hexdigest(),"url":"https://openai.com/image.png","bytes":len(data)}
            with patch.dict("os.environ",{"OPENROUTER_API_KEY":"test","DEEPSEEK_API_KEY":"deepseek-test"}), patch(
                "scripts.media_news_pipeline.extract_article",return_value={"title":"title","url":"https://openai.com/news/example","text":TEXT,"description":"","images":[{"url":"https://openai.com/image.png","alt":"official image"}]}
            ), patch("scripts.media_news_pipeline.download_article_image",side_effect=fake_download):
                mission=process_source(conn,source_id,workspace,image_hosts={"openai.com"},catalog=[{"id":"deepseek/deepseek-v4.1-flash",
                    "pricing":{"prompt":"0.000000015","completion":"0.0000012"},"supported_parameters":["response_format"]}],
                    request_fn=requester,planner_fn=planner,deepseek_request_fn=deepseek_requester,
                    free_catalog=[{"id":"fixture/model:free","pricing":{"prompt":"0","completion":"0"}}])
            saved=json.loads(mission.read_text())
            self.assertEqual(len(saved["scenes"]),3)
            self.assertTrue(saved["source_sha256"])
            self.assertTrue((mission.parent/"mission.json.gz.b64").is_file())
            checkpoint=json.loads((mission.parent/"script-generation.json").read_text())
            self.assertEqual(checkpoint["model_id"],"deepseek-flash")
            self.assertEqual(checkpoint["status"],"SCRIPT_READY")
            self.assertLessEqual(float(checkpoint["estimated_cost_usd"]),0.05)
            self.assertEqual(conn.execute("SELECT state FROM source_inbox").fetchone()["state"],"VOICE_PENDING")
            manifest=mission.parent/"image-candidates.json";images=json.loads(manifest.read_text())
            images["assets"][0].update(selected_for_render=True,rights_verified=True,rights_basis="licensed",rights_evidence_url="https://example.org/license",credit="OpenAI")
            manifest.write_text(json.dumps(images))
            with patch.dict("os.environ",{"OPENROUTER_API_KEY":"test","DEEPSEEK_API_KEY":"deepseek-test"}), patch(
                "scripts.media_news_pipeline.extract_article",return_value={"title":"title","url":"https://openai.com/news/example","text":TEXT,"description":"","images":[{"url":"https://openai.com/image.png","alt":"official image"}]}
            ), patch("scripts.media_news_pipeline.download_article_image",side_effect=AssertionError("cached assets must be reused")):
                reused=process_source(conn,source_id,workspace,image_hosts={"openai.com"},catalog=[{"id":"deepseek/deepseek-v4.1-flash",
                    "pricing":{"prompt":"0.000000015","completion":"0.0000012"},"supported_parameters":["response_format"]}],
                    request_fn=lambda *_a:(_ for _ in ()).throw(AssertionError("cached script must be reused")),planner_fn=planner,
                    free_catalog=[{"id":"fixture/model:free","pricing":{"prompt":"0","completion":"0"}}],
                    deepseek_request_fn=lambda *_a:(_ for _ in ()).throw(AssertionError("cached script must be reused")))
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
