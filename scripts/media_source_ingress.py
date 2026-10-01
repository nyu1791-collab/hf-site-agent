#!/usr/bin/env python3
"""Bounded RSS intake to a durable preparation inbox; never publishes."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sqlite3
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

from scripts.durable_media_runner import connect, enqueue_job, init_db, _load_job_spec

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "media_source_ingress_policy.json"
MAX_FEED_BYTES = 2 * 1024 * 1024
MAX_ITEMS = 100
FETCH_TIMEOUT_SECONDS = 8


def _name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _text(raw: str, limit: int) -> str:
    clean = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", raw or "", flags=re.I | re.S)
    clean = re.sub(r"<[^>]{1,512}>", " ", html.unescape(clean))
    return " ".join(clean.split())[:limit]


def _https_link(raw: str) -> str:
    parts = urlsplit((raw or "").strip())
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise ValueError("source link must be credential-free HTTPS")
    if parts.port not in (None, 443):
        raise ValueError("unsupported source link port")
    return urlunsplit(("https", parts.netloc.lower(), parts.path or "/", parts.query, ""))


def parse_feed(content: bytes, feed_id: str) -> list[dict[str, str]]:
    if len(content) > MAX_FEED_BYTES:
        raise ValueError("feed exceeds 2 MiB limit")
    text = content.decode("utf-8-sig")
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("DTD/entities are not accepted")
    root = ET.fromstring(text)
    if _name(root.tag) not in {"rss", "feed", "rdf"}:
        raise ValueError("RSS or Atom document required")
    result = []
    for entry in root.iter():
        if _name(entry.tag) not in {"item", "entry"}:
            continue
        fields: dict[str, str] = {}
        link = ""
        for child in entry:
            key = _name(child.tag)
            if key == "link":
                link = child.attrib.get("href", "") or (child.text or "").strip()
            elif key not in fields:
                # RSS descriptions may contain nested markup; Element.text alone
                # silently drops child text and the tails that follow it.
                fields[key] = "".join(child.itertext())
        link = _https_link(link or fields.get("link", ""))
        identity = (fields.get("guid") or fields.get("id") or link).strip()
        source_id = hashlib.sha256(f"{feed_id}\0{identity}".encode()).hexdigest()
        result.append({
            "source_id": source_id, "feed_id": feed_id, "title": _text(fields.get("title", ""), 500) or link,
            "url": link, "summary": _text(fields.get("description") or fields.get("summary") or fields.get("encoded", ""), 4000),
            "published": _text(fields.get("pubdate") or fields.get("published") or fields.get("updated", ""), 80),
        })
        if len(result) == MAX_ITEMS:
            break
    return result


class _SameHostRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, host: str):
        super().__init__()
        self.host = host.lower()

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parts = urlsplit(newurl)
        if (parts.scheme != "https" or (parts.hostname or "").lower() != self.host
                or parts.port not in (None, 443) or parts.username or parts.password):
            raise urllib.error.URLError("redirect left configured HTTPS host")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def load_policy() -> dict[str, Any]:
    value = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    if value.get("schema_version") != "media-source-ingress-v1":
        raise ValueError("unsupported source ingress policy")
    return value


def init_inbox(conn: sqlite3.Connection) -> None:
    init_db(conn)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS source_inbox (
      source_id TEXT PRIMARY KEY, feed_id TEXT NOT NULL, title TEXT NOT NULL,
      url TEXT NOT NULL, summary TEXT NOT NULL, published TEXT NOT NULL,
      content_trust TEXT NOT NULL DEFAULT 'UNTRUSTED_EXTERNAL',
      state TEXT NOT NULL DEFAULT 'PREPARATION_REQUIRED',
      media_job_id INTEGER REFERENCES jobs(id), created_at REAL NOT NULL, updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS source_feed_state (
      feed_id TEXT PRIMARY KEY, etag TEXT, last_modified TEXT,
      last_checked_at REAL NOT NULL, last_status TEXT NOT NULL
    );
    """)


def ingest_items(conn: sqlite3.Connection, items: list[Mapping[str, str]]) -> dict[str, int]:
    now, added = time.time(), 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        for item in items:
            cur = conn.execute("""INSERT OR IGNORE INTO source_inbox
            (source_id,feed_id,title,url,summary,published,content_trust,state,created_at,updated_at)
            VALUES(?,?,?,?,?,?,'UNTRUSTED_EXTERNAL','PREPARATION_REQUIRED',?,?)""",
            (item["source_id"], item["feed_id"], item["title"], item["url"],
             item.get("summary", ""), item.get("published", ""), now, now))
            added += cur.rowcount
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return {"added": added, "deduped": len(items) - added, "render_jobs_created": 0}


def inbox_status(conn: sqlite3.Connection, *, now: float | None = None,
                 poll_interval_seconds: int | None = None,
                 stale_after_seconds: int | None = None) -> dict[str, Any]:
    now = time.time() if now is None else float(now)
    policy = load_policy()
    interval = int(poll_interval_seconds or policy.get("poll_interval_seconds", 300))
    stale_after = int(stale_after_seconds or policy.get("stale_after_seconds", interval * 2))
    feeds = []
    for row in conn.execute("SELECT feed_id,last_checked_at,last_status FROM source_feed_state ORDER BY feed_id"):
        age = max(0, int(now - float(row["last_checked_at"])))
        stale = row["last_status"] not in {"OK", "NOT_MODIFIED"} or age > stale_after
        feeds.append({"feed_id":row["feed_id"], "last_status":row["last_status"],
            "age_seconds":age, "stale":stale})
    counts = {row[0]:row[1] for row in conn.execute("SELECT state,COUNT(*) FROM source_inbox GROUP BY state")}
    pending = conn.execute("SELECT MIN(created_at) FROM source_inbox WHERE state IN ('PREPARATION_REQUIRED','VOICE_PENDING')").fetchone()[0]
    return {"feed_freshness":feeds, "any_feed_stale":any(x["stale"] for x in feeds) or not feeds,
        "inbox_counts":counts, "oldest_unprepared_age_seconds":None if pending is None else max(0,int(now-float(pending))),
        "process_supervisor_status":"NOT_OBSERVED", "public_publish_enabled":False}


def poll_feed(conn: sqlite3.Connection, feed: Mapping[str, Any]) -> dict[str, Any]:
    feed_id, url = str(feed.get("feed_id") or ""), str(feed.get("url") or "")
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    allowed = {str(x).lower() for x in feed.get("allowed_hosts", [])}
    if not feed_id or parts.scheme != "https" or host not in allowed or parts.port not in (None, 443):
        raise ValueError("feed must use a configured HTTPS host")
    previous = conn.execute("SELECT etag,last_modified FROM source_feed_state WHERE feed_id=?", (feed_id,)).fetchone()
    headers = {"User-Agent": "hf-site-agent-source-ingress/1.0",
               "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"}
    if previous and previous["etag"]:
        headers["If-None-Match"] = previous["etag"]
    if previous and previous["last_modified"]:
        headers["If-Modified-Since"] = previous["last_modified"]
    request = urllib.request.Request(url, headers=headers)
    opener = urllib.request.build_opener(_SameHostRedirect(host))
    checked = time.time()
    try:
        with opener.open(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
            content_type = response.headers.get_content_type().lower()
            if content_type not in {"application/rss+xml", "application/atom+xml", "application/xml", "text/xml"}:
                raise ValueError("feed response must be XML")
            items = parse_feed(response.read(MAX_FEED_BYTES + 1), feed_id)
            result = ingest_items(conn, items)
            conn.execute("""INSERT INTO source_feed_state(feed_id,etag,last_modified,last_checked_at,last_status)
            VALUES(?,?,?,?, 'OK') ON CONFLICT(feed_id) DO UPDATE SET etag=excluded.etag,
            last_modified=excluded.last_modified,last_checked_at=excluded.last_checked_at,last_status='OK'""",
            (feed_id, response.headers.get("ETag"), response.headers.get("Last-Modified"), checked))
            return {"feed_id": feed_id, "status": "OK", **result}
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            conn.execute("UPDATE source_feed_state SET last_checked_at=?,last_status='NOT_MODIFIED' WHERE feed_id=?", (checked, feed_id))
            return {"feed_id": feed_id, "status": "NOT_MODIFIED", "added": 0, "deduped": 0, "render_jobs_created": 0}
        code = f"HTTP_{exc.code}"
    except (OSError, ValueError, ET.ParseError, UnicodeError, urllib.error.URLError) as exc:
        code = type(exc).__name__
    conn.execute("""INSERT INTO source_feed_state(feed_id,last_checked_at,last_status)
    VALUES(?,?,'FEED_ERROR') ON CONFLICT(feed_id) DO UPDATE SET
    last_checked_at=excluded.last_checked_at,last_status='FEED_ERROR'""", (feed_id, checked))
    return {"feed_id": feed_id, "status": "FEED_ERROR", "error_code": code}


def promote_prepared_job(conn: sqlite3.Connection, *, source_id: str,
                         job_spec: Mapping[str, Any], workspace: Path) -> dict[str, Any]:
    if job_spec.get("source_id") != source_id:
        raise ValueError("job source_id must match inbox item")
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT media_job_id FROM source_inbox WHERE source_id=?", (source_id,)).fetchone()
        if row is None:
            raise ValueError("unknown inbox source_id")
        if row["media_job_id"] is not None:
            result = {"status": "DEDUPED", "job_id": int(row["media_job_id"])}
        else:
            result = enqueue_job(conn, workspace=workspace, kind=str(job_spec.get("kind") or ""),
                source_id=source_id, payload=job_spec.get("payload") or {},
                priority=int(job_spec.get("priority", 0)), max_attempts=int(job_spec.get("max_attempts", 2)))
            conn.execute("UPDATE source_inbox SET state='ENQUEUED',media_job_id=?,updated_at=? WHERE source_id=?",
                         (result["job_id"], time.time(), source_id))
        conn.execute("COMMIT")
        return result
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    poll = commands.add_parser("poll")
    poll.add_argument("--feed-id")
    prep = commands.add_parser("prepare")
    prep.add_argument("--source-id", required=True)
    prep.add_argument("--job", type=Path, required=True)
    prep.add_argument("--workspace", type=Path, required=True)
    commands.add_parser("status")
    args = parser.parse_args()
    conn = connect(args.db)
    try:
        init_inbox(conn)
        if args.command == "poll":
            feeds = load_policy().get("feeds", [])
            if args.feed_id:
                feeds = [x for x in feeds if x.get("feed_id") == args.feed_id]
            result = [poll_feed(conn, feed) for feed in feeds]
            print(json.dumps({"feeds": result, "daemon_enabled": False, "publish_enabled": False}, sort_keys=True))
            return 0 if all(x["status"] in {"OK", "NOT_MODIFIED"} for x in result) else 2
        if args.command == "prepare":
            result = promote_prepared_job(conn, source_id=args.source_id,
                job_spec=_load_job_spec(args.job), workspace=args.workspace.resolve())
        else:
            result = inbox_status(conn)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
