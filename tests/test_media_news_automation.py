from __future__ import annotations

import json
import tempfile
import unittest
import wave
import array
from pathlib import Path
from unittest.mock import patch

from scripts.durable_media_runner import connect
from scripts.media_news_pipeline import draft_story, extract_article, process_source, select_render_assets, synthesize_voice, validate_story
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
    def test_source_daemon_one_shot_polls_once_and_releases_lock(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/"queue.sqlite3"
            with patch("scripts.media_source_daemon.poll_once",return_value={"feeds":[]} ) as poll:
                self.assertEqual(run_source_daemon(db,interval=900,once=True),0)
                self.assertEqual(run_source_daemon(db,interval=900,once=True),0)
            self.assertEqual(poll.call_count,2)

    def test_render_assets_require_selection_rights_basis_and_credit(self):
        assets=[{"id":str(i),"downloaded":True,"selected_for_render":True,
                 "rights_verified":True,"rights_basis":"CC BY 4.0","credit":"Author"} for i in range(6)]
        self.assertEqual(len(select_render_assets({"assets":assets},3)),6)
        for field,value in (("rights_verified",False),("rights_basis",""),("credit","")):
            broken=[dict(x) for x in assets];broken[0][field]=value
            with self.assertRaises(RuntimeError): select_render_assets({"assets":broken},3)
        with self.assertRaises(RuntimeError): select_render_assets({"assets":assets[:5]},3)

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

    def test_story_call_uses_exact_free_model_no_fallback_and_zero_cost(self):
        with tempfile.TemporaryDirectory() as td:
            conn=connect(Path(td)/"q.sqlite3");init_inbox(conn)
            call={}
            def planner(_task,_catalog,**kwargs):
                return {"status":"READY","primary_model":"vendor/news:free","provider_allow_fallbacks":False}
            def requester(payload,key):
                call.update(payload=payload,key_seen=bool(key))
                return {"model":"vendor/news:free","choices":[{"message":{"content":json.dumps(story(),ensure_ascii=False)}}],"usage":{"cost":0}}
            with patch.dict("os.environ",{"OPENROUTER_API_KEY":"test-secret-never-logged"}):
                result,model=draft_story(conn,{"title":"title","url":"https://openai.com/news/x","text":TEXT},
                    catalog=[{"id":"vendor/news:free"}],planner_fn=planner,request_fn=requester)
            self.assertEqual(model,"vendor/news:free")
            self.assertEqual(len(result["scenes"]),3)
            self.assertTrue(call["key_seen"])
            self.assertIs(call["payload"]["provider"]["allow_fallbacks"],False)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM media_news_model_calls").fetchone()[0],1)
            conn.close()

    def test_nonzero_or_unknown_usage_cost_blocks_story(self):
        with tempfile.TemporaryDirectory() as td:
            conn=connect(Path(td)/"q.sqlite3");init_inbox(conn)
            def planner(*_a,**_k): return {"status":"READY","primary_model":"vendor/news:free","provider_allow_fallbacks":False}
            for usage in ({"cost":0.01},{}):
                with patch.dict("os.environ",{"OPENROUTER_API_KEY":"test"}):
                    with self.assertRaises(RuntimeError):
                        draft_story(conn,{"title":"title","url":"https://openai.com/news/x","text":TEXT},
                          catalog=[{}],planner_fn=planner,request_fn=lambda *_a, u=usage: {"model":"vendor/news:free","usage":u,"choices":[]})
            conn.close()

    def test_article_to_persistent_script_package_is_resumable(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);workspace=root/"workspace";workspace.mkdir()
            conn=connect(root/"q.sqlite3");init_inbox(conn)
            source_id="source-1"
            ingest_items(conn,[{"source_id":source_id,"feed_id":"openai-news","title":"title",
                "url":"https://openai.com/news/example","summary":"", "published":""}])
            planner=lambda *_a,**_k:{"status":"READY","primary_model":"vendor/news:free","provider_allow_fallbacks":False}
            requester=lambda *_a:{"model":"vendor/news:free","usage":{"cost":0},"choices":[{"message":{"content":json.dumps(story(),ensure_ascii=False)}}]}
            with patch.dict("os.environ",{"OPENROUTER_API_KEY":"test"}), patch(
                "scripts.media_news_pipeline.extract_article",return_value={"title":"title","url":"https://openai.com/news/example","text":TEXT,"description":"","images":[]}
            ):
                mission=process_source(conn,source_id,workspace,image_hosts={"openai.com"},catalog=[{}],request_fn=requester,planner_fn=planner)
            saved=json.loads(mission.read_text())
            self.assertEqual(len(saved["scenes"]),3)
            self.assertTrue((mission.parent/"mission.json.gz.b64").is_file())
            self.assertEqual(conn.execute("SELECT state FROM source_inbox").fetchone()["state"],"VOICE_PENDING")
            conn.close()

    def test_voice_parts_are_joined_into_renderer_mono_pcm(self):
        with tempfile.TemporaryDirectory() as td:
            package=Path(td);(package/"mission.json.gz.b64").write_text("fixture")
            def fake_run(command,**kwargs):
                out=package/"voice-parts";out.mkdir(exist_ok=True)
                for name in ("a.wav","b.wav"):
                    with wave.open(str(out/name),"wb") as w:
                        w.setnchannels(2);w.setsampwidth(2);w.setframerate(48000);w.writeframes(array.array("h",[100,-100]*2400).tobytes())
                (package/"timing.json").write_text(json.dumps({"total_duration":0.2,"voicevox_credit":["VOICEVOX:ずんだもん","VOICEVOX:四国めたん"],"voice_cache_hits":0,
                  "records":[{"wav_file":"a.wav","pause_after":0.01},{"wav_file":"b.wav","pause_after":0.01}]}))
            with patch("scripts.media_news_pipeline.subprocess.run",side_effect=fake_run):
                result=synthesize_voice(package,min_seconds=0,max_seconds=1)
            with wave.open(result["audio"],"rb") as wav:
                self.assertEqual((wav.getnchannels(),wav.getsampwidth(),wav.getframerate()),(1,2,48000))
                self.assertEqual(wav.getnframes(),2400+480+2400+480)


if __name__=="__main__":
    unittest.main()
