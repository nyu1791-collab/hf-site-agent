let generatedSite = null;
let latestReview = null;
let reusePending = false;
const config = window.SITE_AGENT_CONFIG || {};
const METRICS_KEY = "site-agent.metrics.v1";
const TEMPLATES = Object.freeze({
  local_business: "小規模店舗・サービスの案内サイト。対象顧客、営業時間、料金、FAQ、予約への導線を明確にし、スマホ優先で作る。",
  game_community: "ゲーム・コミュニティの更新サイト。攻略情報、イベント、更新履歴、参加導線を整理し、初心者にも読みやすくする。",
  creator: "個人制作者の作品紹介サイト。プロフィール、代表作品、実績、問い合わせ導線、定期更新欄をシンプルにまとめる。"
});
const $ = id => document.getElementById(id);
function status(message) { $("status").textContent = message; }
function loadLocalState() {
  try {
    const value = JSON.parse(localStorage.getItem(METRICS_KEY) || "{}");
    return {
      generationCount: Number(value.generationCount) || 0,
      reviewCount: Number(value.reviewCount) || 0,
      planCount: Number(value.planCount) || 0,
      updateReuseCount: Number(value.updateReuseCount) || 0,
      approvalCount: Number(value.approvalCount) || 0,
      lastInstruction: typeof value.lastInstruction === "string" ? value.lastInstruction.slice(0, 5000) : "",
      lastTemplate: typeof value.lastTemplate === "string" ? value.lastTemplate : "",
      lastUsedAt: typeof value.lastUsedAt === "string" ? value.lastUsedAt : ""
    };
  } catch {
    return { generationCount: 0, reviewCount: 0, planCount: 0, updateReuseCount: 0, approvalCount: 0, lastInstruction: "", lastTemplate: "", lastUsedAt: "" };
  }
}
let localState = loadLocalState();
function saveLocalState() {
  try { localStorage.setItem(METRICS_KEY, JSON.stringify(localState)); } catch { /* local metrics are optional */ }
}
function recordMetric(name) {
  localState[name] = (Number(localState[name]) || 0) + 1;
  localState.lastUsedAt = new Date().toISOString();
  saveLocalState();
  renderMetrics();
}
function renderMetrics() {
  const root = $("metrics");
  if (!root) return;
  root.textContent = "この端末のみ: 生成 " + localState.generationCount +
    "回 / レビュー " + localState.reviewCount +
    "回 / 計画 " + localState.planCount +
    "回 / 更新再利用 " + localState.updateReuseCount +
    "回 / 承認Push " + localState.approvalCount + "回";
}
function rememberLastInstruction() {
  localState.lastInstruction = $("instruction").value.trim().slice(0, 5000);
  localState.lastTemplate = $("template")?.value || "";
  saveLocalState();
}
async function call(path, payload) {
  if (!config.WORKER_URL || config.WORKER_URL.includes("CHANGE-ME")) throw new Error("config.jsにWorker URLを設定してください。");
  const password = $("password").value;
  if (!password) throw new Error("操作パスワードを入力してください。");
  const response = await fetch(config.WORKER_URL.replace(/\/$/, "") + path, { method: "POST", headers: { "content-type": "application/json", "x-admin-password": password }, body: JSON.stringify(payload) });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) { const error = new Error(data.error || "通信に失敗しました。"); error.details = data; throw error; }
  return data;
}
function showSite(site) {
  generatedSite = site; $("site-title").textContent = site.title; $("site-summary").textContent = site.summary;
  const files = $("files"); files.replaceChildren();
  for (const file of site.files) {
    const article = document.createElement("article"), title = document.createElement("h3"), code = document.createElement("pre");
    article.className = "file"; title.textContent = file.path; code.textContent = file.content; article.append(title, code); files.append(article);
  }
  $("confirmed").checked = false; $("result").hidden = false;
}
function renderPlan(content) {
  const root = $("plan-output");
  root.replaceChildren();
  const title = document.createElement("h3");
  title.textContent = "コンテンツ計画";
  root.append(title);
  const summary = document.createElement("p");
  summary.textContent = "状態: " + content.state + " / 引用: " + (content.citations?.length || 0) + "件";
  root.append(summary);
  const steps = document.createElement("ol");
  for (const step of content.steps || []) {
    const item = document.createElement("li");
    item.textContent = step.stage + ": " + step.action;
    steps.append(item);
  }
  root.append(steps);
  const citations = document.createElement("ul");
  for (const citation of content.citations || []) {
    try {
      const url = new URL(citation.url);
      if (!["http:", "https:"].includes(url.protocol)) continue;
      const item = document.createElement("li");
      const link = document.createElement("a");
      link.href = url.toString(); link.target = "_blank"; link.rel = "noreferrer noopener"; link.textContent = citation.title || url.hostname;
      item.append(link);
      if (citation.snippet) item.append(document.createTextNode(" — " + citation.snippet));
      citations.append(item);
    } catch { /* unsafe citation is omitted */ }
  }
  if (citations.children.length) {
    const heading = document.createElement("p"); heading.textContent = "参照元（未信頼データ。本文へ自動公開しません）"; root.append(heading, citations);
  } else {
    const note = document.createElement("p"); note.textContent = "引用は未取得です。無料検索を使う場合だけチェックを入れて再実行してください。"; root.append(note);
  }
  root.hidden = false;
}
async function buildPlan() {
  const button = $("plan");
  try {
    const goal = $("instruction").value.trim();
    if (goal.length < 3) throw new Error("計画の目的を3文字以上で入力してください。");
    button.disabled = true;
    status($("plan-research").checked ? "無料検索で引用付き計画を作成中…" : "計画を作成中…");
    const data = await call("/command/content-plan", { goal, research: $("plan-research").checked });
    renderPlan(data.content);
    recordMetric("planCount");
    rememberLastInstruction();
    status("計画を作成しました。引用は確認後に利用してください。");
  } catch (error) {
    status("計画を作成できませんでした: " + error.message);
  } finally { button.disabled = false; }
}
async function generate() {
  const button = $("generate");
  try {
    const instruction = $("instruction").value.trim();
    if (instruction.length < 3) throw new Error("作りたい内容を3文字以上で入力してください。");
    button.disabled = true;
    status($("research").checked ? "最新情報を確認しながら生成中…" : "Groqでコードを生成中…");
    const data = await call("/generate", { instruction, research: $("research").checked });
    showSite(data.site);
    recordMetric("generationCount");
    if (reusePending) { recordMetric("updateReuseCount"); reusePending = false; }
    rememberLastInstruction();
    const note = data.research ? "（Web情報 " + data.research.sourceCount + "件を確認。無料使用 " + data.research.freeCreditsUsed + "、有料使用 " + data.research.paidCreditsUsed + " クレジット）" : "";
    status("生成しました" + note + "。内容を確認してからレビュー・Pushしてください。");
  } catch (error) {
    status("生成できませんでした: " + error.message);
  } finally { button.disabled = false; }
}
$("template").addEventListener("change", () => {
  const value = TEMPLATES[$("template").value];
  if (!value) return;
  const current = $("instruction").value.trim();
  $("instruction").value = current ? current + "\n\n" + value : value;
  rememberLastInstruction();
  status("テンプレートを入力欄へ追加しました。固有情報を追記してください。");
});
$("plan").addEventListener("click", () => buildPlan());
$("generate").addEventListener("click", () => generate());
$("reuse").addEventListener("click", () => {
  if (!localState.lastInstruction) { status("再利用できる前回内容がありません。まず1回生成してください。"); return; }
  $("instruction").value = localState.lastInstruction;
  if ($("template")) $("template").value = localState.lastTemplate;
  reusePending = true;
  status("前回の内容を読み込みました。変更点を追記してから生成してください。");
});
$("clear-metrics").addEventListener("click", () => {
  localState = { generationCount: 0, reviewCount: 0, planCount: 0, updateReuseCount: 0, approvalCount: 0, lastInstruction: "", lastTemplate: "", lastUsedAt: "" };
  try { localStorage.removeItem(METRICS_KEY); } catch {}
  renderMetrics();
  status("この端末の利用指標と再利用内容を消去しました。");
});
$("review").addEventListener("click", async () => {
  const button = $("review");
  try {
    if (!generatedSite) throw new Error("先にコードを生成してください。");
    button.disabled = true;
    status("DeepSeekでレビュー中…");
    const source = generatedSite.files.map(file => file.path + "\n" + file.content).join("\n\n");
    const data = await call("/command/review", { text: source });
    latestReview = data.review;
    $("review-output").textContent = latestReview.review;
    $("review-output").hidden = false;
    recordMetric("reviewCount");
    status("DeepSeekレビューが完了しました。内容を確認してからPushしてください。");
  } catch (error) {
    status("レビューできませんでした: " + error.message);
  } finally {
    button.disabled = false;
  }
});
$("publish").addEventListener("click", async () => {
  const button = $("publish");
  try {
    if (!generatedSite) throw new Error("先にコードを生成してください。");
    if (!$("confirmed").checked) throw new Error("内容確認のチェックを入れてください。");
    button.disabled = true; status("GitHubへ保存中…");
    const data = await call("/publish", { site: generatedSite, confirmPublish: true });
    recordMetric("approvalCount");
    status("Pushしました: " + data.commitUrl);
  } catch (error) { status("Pushできませんでした: " + error.message); }
  finally { button.disabled = false; }
});
function showCommanderStatus(data) {
  const root = $("commander-status");
  root.replaceChildren();
  const headline = document.createElement("p");
  headline.textContent = "司令部: " + (data.commander?.state || "不明");
  root.append(headline);
  const list = document.createElement("ul");
  for (const [name, item] of Object.entries(data.commander?.specialists || {})) {
    const row = document.createElement("li");
    row.textContent = name + " — " + item.state + (item.missing?.length ? "（未登録: " + item.missing.join(", ") + "）" : "");
    list.append(row);
  }
  root.append(list);
  root.hidden = false;
}
$("check-commander").addEventListener("click", async () => {
  const button = $("check-commander");
  try {
    button.disabled = true;
    status("司令部の準備状況を確認中…");
    const data = await call("/command/status", {});
    showCommanderStatus(data);
    status("部隊の準備状況を表示しました。");
  } catch (error) {
    status("司令部の確認に失敗しました: " + error.message);
  } finally {
    button.disabled = false;
  }
});
renderMetrics();
