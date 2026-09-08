#!/usr/bin/env node
/**
 * AI部隊・安全運用監査
 *
 * 読み取り専用の点検スクリプトです。秘密値を表示せず、
 * Workerソースの安全ガードと公開healthを確認します。
 *
 * 例:
 *   node worker/ai-ops-audit.mjs
 *   WORKER_URL=https://... ADMIN_PASSWORD=... node worker/ai-ops-audit.mjs
 */

import { readFile } from "node:fs/promises";
import process from "node:process";

const workerUrl = (process.env.WORKER_URL || "https://groq-github-site-agent.n-yu1791.workers.dev").replace(/\/$/, "");
const origin = process.env.WORKER_ORIGIN || "https://yu-179191-groq-github-site-agent.hf.space";
const timeoutMs = Math.min(Math.max(Number(process.env.AUDIT_TIMEOUT_MS || 15000), 3000), 30000);

function fail(message) {
  console.error(`[audit:fail] ${message}`);
  process.exitCode = 1;
}

async function fetchJson(url, init = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, { ...init, signal: controller.signal });
    const text = await response.text();
    let body = {};
    try { body = text ? JSON.parse(text) : {}; } catch { body = { invalidJson: true }; }
    return { response, body };
  } catch (error) {
    throw new Error(error?.name === "AbortError" ? "公開エンドポイントがタイムアウトしました。" : "公開エンドポイントへ接続できませんでした。");
  } finally {
    clearTimeout(timer);
  }
}

const source = await readFile(new URL("./index.js", import.meta.url), "utf8");
const requiredSource = [
  "function safeErrorDetails",
  "function safeResearchUrl",
  "BEGIN_UNTRUSTED_SOURCES",
  "END_UNTRUSTED_SOURCES",
  "async function fetchWithTimeout",
  "content-type は application/json",
  "payload.confirmPublish !== true",
  "HF_INFERENCE_ENABLED=true"
];
for (const marker of requiredSource) {
  if (!source.includes(marker)) fail(`安全ガードが見つかりません: ${marker}`);
}

for (const forbidden of [
  "Groq error: ${providerMessage}",
  "GitHub error: ${data?.message",
  "url: String(item?.url || '').slice"
]) {
  if (source.includes(forbidden)) fail(`外部エラーまたは未検証URLの直接露出が残っています: ${forbidden}`);
}

try {
  const { response, body } = await fetchJson(`${workerUrl}/health`);
  if (!response.ok || body.ok !== true || body.service !== "groq-github-site-agent") {
    fail(`Worker /health が正常ではありません (HTTP ${response.status})`);
  } else {
    console.log(`[audit:ok] Worker /health: HTTP ${response.status}, version=${body.version || "unknown"}`);
    console.log(`[audit:ok] research=${body.research?.enabled ? "enabled" : "disabled"}, hf_review=${body.inference?.codeReview?.enabled ? "enabled" : "disabled"}`);
  }
} catch (error) {
  fail(error.message);
}

if (process.env.ADMIN_PASSWORD) {
  try {
    const { response, body } = await fetchJson(`${workerUrl}/command/content-plan`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "origin": origin,
        "x-admin-password": process.env.ADMIN_PASSWORD
      },
      body: JSON.stringify({ goal: "安全運用監査" })
    });
    const safe = body?.content?.controls;
    if (!response.ok || body?.content?.pipeline?.paidOperations !== "disabled" ||
        safe?.youtubeUpload !== "not_connected" ||
        safe?.publicPublish !== "explicit_confirmation_required") {
      fail(`認証付き安全ゲートが期待値と異なります (HTTP ${response.status})`);
    } else {
      console.log("[audit:ok] 認証付き安全ゲート: 課金無効・YouTube未接続・公開確認必須");
    }
  } catch (error) {
    fail(error.message);
  }
} else {
  console.log("[audit:info] ADMIN_PASSWORD未設定のため、認証付き検査はスキップしました。");
}

if (process.exitCode) {
  console.error("[audit] 修正後に再実行してください。秘密値そのものは出力しません。");
} else {
  console.log("[audit:ok] AI部隊の読み取り専用監査が完了しました。");
}
