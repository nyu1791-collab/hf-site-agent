let generatedSite = null;
let latestReview = null;
const config = window.SITE_AGENT_CONFIG || {};
const $ = id => document.getElementById(id);
function status(message) { $("status").textContent = message; }
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
async function generate() {
  const button = $("generate");
  try {
    button.disabled = true;
    status($("research").checked ? "最新情報を確認しながら生成中…" : "Groqでコードを生成中…");
    const data = await call("/generate", { instruction: $("instruction").value, research: $("research").checked });
    showSite(data.site);
    const note = data.research ? "（Web情報 " + data.research.sourceCount + "件を確認。無料使用 " + data.research.freeCreditsUsed + "、有料使用 " + data.research.paidCreditsUsed + " クレジット）" : "";
    status("生成しました" + note + "。内容を確認してからPushしてください。");
  } catch (error) {
    status("生成できませんでした: " + error.message);
  } finally { button.disabled = false; }
}
$("generate").addEventListener("click", () => generate());

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
