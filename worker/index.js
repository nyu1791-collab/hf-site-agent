var __defProp = Object.defineProperty;
var __name = (target, value) => __defProp(target, "name", { value, configurable: true });

// src/index.js
var SAFE_REPOSITORY = "nyu1791-collab/hf-site-agent";
var SAFE_BRANCH = "main";
var EXTENSIONS = /* @__PURE__ */ new Set(["html", "css", "js", "json", "svg", "txt", "md"]);
var BLOCKED_SEGMENTS = /* @__PURE__ */ new Set([".git", ".github", "node_modules", "__pycache__"]);
var DEFAULT_ORIGINS = /* @__PURE__ */ new Set([
  "https://huggingface.co",
  "https://yu-179191-groq-github-site-agent.hf.space",
  "https://yu-179191-groq-github-site-agent.static.hf.space"
]);
var MAX_INSTRUCTION_CHARS = 5e3;
var MAX_TOTAL_FILE_CHARS = 65e3;
var COMMANDER_VERSION = "1.0";
var MAX_QWEN_CALLS_PER_MISSION = 2;
var MAX_EXTERNAL_MODEL_CALLS_PER_MISSION = 1;
var MAX_PARALLEL_MODEL_CALLS = 1;
var ADMIN_PASSWORD_BINDING = "WORKER_ADMIN_PASSWORD";
var HttpError = class extends Error {
  static {
    __name(this, "HttpError");
  }
  constructor(status, message, details = {}) {
    super(message);
    this.status = status;
    this.details = details;
  }
};
function requestId() {
  return crypto.randomUUID();
}
__name(requestId, "requestId");
function asInt(value, fallback, min, max) {
  const number = Number(value);
  if (!Number.isFinite(number)) return fallback;
  return Math.min(Math.max(Math.floor(number), min), max);
}
__name(asInt, "asInt");
function configuredOrigins(env) {
  const extra = [env.ALLOWED_ORIGIN, env.ALLOWED_ORIGINS].filter(Boolean).flatMap((value) => String(value).split(",")).map((value) => value.trim()).filter((value) => value.startsWith("https://"));
  return /* @__PURE__ */ new Set([...DEFAULT_ORIGINS, ...extra]);
}
__name(configuredOrigins, "configuredOrigins");
function corsHeaders(request, env) {
  const origin = request.headers.get("Origin");
  if (!origin || !configuredOrigins(env).has(origin)) return null;
  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "content-type, x-admin-password",
    "Access-Control-Max-Age": "86400",
    Vary: "Origin"
  };
}
__name(corsHeaders, "corsHeaders");
function sendJson(body, status = 200, headers = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      ...headers
    }
  });
}
__name(sendJson, "sendJson");
function fail(message, status, id, headers = {}, details = {}) {
  return sendJson({ error: message, requestId: id, ...details }, status, headers);
}
__name(fail, "fail");
function log(event, data = {}) {
  console.log(JSON.stringify({ event, ...data }));
}
__name(log, "log");
async function sameSecret(input, expected) {
  if (!input || !expected) return false;
  const encoder = new TextEncoder();
  const [inputHash, expectedHash] = await Promise.all([
    crypto.subtle.digest("SHA-256", encoder.encode(input)),
    crypto.subtle.digest("SHA-256", encoder.encode(expected))
  ]);
  const left = new Uint8Array(inputHash);
  const right = new Uint8Array(expectedHash);
  let difference = left.length ^ right.length;
  for (let index = 0; index < Math.max(left.length, right.length); index += 1) {
    difference |= (left[index] || 0) ^ (right[index] || 0);
  }
  return difference === 0;
}
__name(sameSecret, "sameSecret");
function requireConfig(env) {
  if (!env.GROQ_API_KEY || !env.GITHUB_TOKEN || !(env[ADMIN_PASSWORD_BINDING] || env.ADMIN_PASSWORD)) {
    throw new HttpError(503, "Worker\u306E\u30B7\u30FC\u30AF\u30EC\u30C3\u30C8\u8A2D\u5B9A\u304C\u4E0D\u8DB3\u3057\u3066\u3044\u307E\u3059\u3002");
  }
  if (env.GITHUB_REPOSITORY !== SAFE_REPOSITORY) {
    throw new HttpError(503, `GITHUB_REPOSITORY \u306F ${SAFE_REPOSITORY} \u306B\u8A2D\u5B9A\u3057\u3066\u304F\u3060\u3055\u3044\u3002`);
  }
  if ((env.GITHUB_BRANCH || SAFE_BRANCH) !== SAFE_BRANCH) {
    throw new HttpError(503, `GITHUB_BRANCH \u306F ${SAFE_BRANCH} \u306B\u8A2D\u5B9A\u3057\u3066\u304F\u3060\u3055\u3044\u3002`);
  }
}
__name(requireConfig, "requireConfig");
function safePath(value) {
  if (typeof value !== "string" || !value || value.startsWith("/") || value.includes("\\") || value.includes("\0")) {
    throw new Error("\u5B89\u5168\u3067\u306A\u3044\u30D5\u30A1\u30A4\u30EB\u540D\u3067\u3059\u3002");
  }
  const parts = value.split("/");
  if (parts.some((part) => !part || part === "." || part === ".." || part.startsWith(".") || BLOCKED_SEGMENTS.has(part))) {
    throw new Error("\u305D\u306E\u30D5\u30A1\u30A4\u30EB\u306E\u5834\u6240\u306F\u4F7F\u7528\u3067\u304D\u307E\u305B\u3093\u3002");
  }
  const extension = parts.at(-1).split(".").at(-1).toLowerCase();
  if (!EXTENSIONS.has(extension)) throw new Error("\u5BFE\u5FDC\u3057\u3066\u3044\u306A\u3044\u30D5\u30A1\u30A4\u30EB\u5F62\u5F0F\u3067\u3059\u3002");
  return parts.join("/");
}
__name(safePath, "safePath");
function hasBlockedContent(content) {
  const patterns = [
    /\bfetch\s*\(/i,
    /\bXMLHttpRequest\b/i,
    /\bWebSocket\b/i,
    /\bEventSource\b/i,
    /\bnavigator\.sendBeacon\b/i,
    /<\s*(iframe|object|embed)\b/i,
    /\beval\s*\(/i,
    /\bnew\s+Function\b/i,
    /\bdocument\.cookie\b/i,
    /\b(gsk|sk|ghp)_[A-Za-z0-9_-]{12,}/,
    /\bgithub_pat_[A-Za-z0-9_]{12,}/i,
    /<\s*(script|link|img|source|video|audio)\b[^>]*(src|href)\s*=\s*["']?https?:\/\//i,
    /\burl\s*\(\s*["']?https?:\/\//i
  ];
  return patterns.some((pattern) => pattern.test(content));
}
__name(hasBlockedContent, "hasBlockedContent");
function qualityReport(site) {
  const index = site.files.find((file) => file.path === "index.html");
  const warnings = [];
  if (!/<!doctype\s+html/i.test(index.content)) warnings.push("index.html \u306B <!doctype html> \u304C\u3042\u308A\u307E\u305B\u3093\u3002");
  if (!/<meta\s+name=["']viewport["']/i.test(index.content)) warnings.push("\u30B9\u30DE\u30DB\u8868\u793A\u7528viewport\u304C\u3042\u308A\u307E\u305B\u3093\u3002");
  if (!/<title>[^<]+<\/title>/i.test(index.content)) warnings.push("\u30DA\u30FC\u30B8\u30BF\u30A4\u30C8\u30EB\u304C\u3042\u308A\u307E\u305B\u3093\u3002");
  if (!site.files.some((file) => file.path.endsWith(".css"))) warnings.push("CSS\u30D5\u30A1\u30A4\u30EB\u304C\u3042\u308A\u307E\u305B\u3093\u3002");
  return {
    passed: warnings.length === 0,
    warnings,
    fileCount: site.files.length,
    totalCharacters: site.files.reduce((total, file) => total + file.content.length, 0)
  };
}
__name(qualityReport, "qualityReport");
function verifySite(candidate, env) {
  if (!candidate || !Array.isArray(candidate.files)) throw new Error("\u30E2\u30C7\u30EB\u306E\u8FD4\u7B54\u306B files \u914D\u5217\u304C\u3042\u308A\u307E\u305B\u3093\u3002");
  const maxFiles = asInt(env.MAX_FILES, 6, 2, 12);
  const maxFileChars = asInt(env.MAX_FILE_CHARS, 3e4, 1e3, 3e4);
  if (candidate.files.length < 2 || candidate.files.length > maxFiles) {
    throw new Error(`\u30D5\u30A1\u30A4\u30EB\u6570\u306F2\u301C${maxFiles}\u500B\u306B\u3057\u3066\u304F\u3060\u3055\u3044\u3002`);
  }
  const paths = /* @__PURE__ */ new Set();
  let totalCharacters = 0;
  const files = candidate.files.map((raw) => {
    const path = safePath(raw?.path);
    const content = raw?.content;
    if (typeof content !== "string" || !content.trim()) throw new Error(`${path} \u306E\u5185\u5BB9\u304C\u3042\u308A\u307E\u305B\u3093\u3002`);
    if (content.length > maxFileChars) throw new Error(`${path} \u304C\u5927\u304D\u3059\u304E\u307E\u3059\u3002`);
    if (paths.has(path)) throw new Error(`${path} \u304C\u91CD\u8907\u3057\u3066\u3044\u307E\u3059\u3002`);
    if (hasBlockedContent(content)) throw new Error(`${path} \u306B\u5916\u90E8\u901A\u4FE1\u307E\u305F\u306F\u5B89\u5168\u3067\u306A\u3044\u6A5F\u80FD\u304C\u3042\u308A\u307E\u3059\u3002`);
    paths.add(path);
    totalCharacters += content.length;
    return { path, content };
  });
  if (totalCharacters > MAX_TOTAL_FILE_CHARS) throw new Error("\u751F\u6210\u3055\u308C\u305F\u30B5\u30A4\u30C8\u304C\u5927\u304D\u3059\u304E\u307E\u3059\u3002");
  if (!paths.has("index.html")) throw new Error("index.html \u304C\u5FC5\u8981\u3067\u3059\u3002");
  if (![...paths].some((path) => path.endsWith(".css"))) throw new Error("CSS\u30D5\u30A1\u30A4\u30EB\u304C\u5FC5\u8981\u3067\u3059\u3002");
  const site = {
    title: String(candidate.title || "Generated website").trim().slice(0, 100),
    summary: String(candidate.summary || "").trim().slice(0, 300),
    plan: Array.isArray(candidate.plan) ? candidate.plan.slice(0, 6).map((step) => String(step).trim().slice(0, 180)).filter(Boolean) : [],
    files
  };
  return { site, report: qualityReport(site) };
}
__name(verifySite, "verifySite");
function modelText(content) {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) return content.map((part) => part?.text || "").join("");
  return "";
}
__name(modelText, "modelText");
function cleanText(value, max = 240) {
  return String(value || "").replace(/<[^>]*>/g, "").replace(/[\u0000-\u001f\u007f]/g, " ").replace(/\s+/g, " ").trim().slice(0, max);
}
__name(cleanText, "cleanText");
function parseSpec(content, instruction) {
  const text = modelText(content).replace(/\r\n/g, "\n").trim();
  const spec = {
    title: cleanText(instruction, 42) || "\u65B0\u3057\u3044\u30A6\u30A7\u30D6\u30B5\u30A4\u30C8",
    eyebrow: "WELCOME",
    tagline: "\u30A2\u30A4\u30C7\u30A2\u304C\u3064\u306A\u304C\u308B\u5834\u6240",
    description: cleanText(instruction, 220),
    cta: "\u8A73\u3057\u304F\u898B\u308B",
    theme: "violet",
    cards: [],
    faqs: []
  };
  for (const rawLine of text.split("\n")) {
    const line = rawLine.replace(/^[-*#\s]+/, "").trim();
    const match = line.match(/^([A-Z_]+)\s*[:：]\s*(.+)$/i);
    if (!match) continue;
    const key = match[1].toUpperCase();
    const value = match[2].trim();
    if (key === "TITLE") spec.title = cleanText(value, 60) || spec.title;
    else if (key === "EYEBROW") spec.eyebrow = cleanText(value, 32) || spec.eyebrow;
    else if (key === "TAGLINE") spec.tagline = cleanText(value, 90) || spec.tagline;
    else if (key === "DESCRIPTION") spec.description = cleanText(value, 260) || spec.description;
    else if (key === "CTA") spec.cta = cleanText(value, 30) || spec.cta;
    else if (key === "THEME" && ["violet", "ocean", "forest", "sunset", "mono"].includes(value.toLowerCase())) spec.theme = value.toLowerCase();
    else if (key === "CARD") {
      const [title, description, badge] = value.split(/[|｜]/).map((part) => cleanText(part));
      if (title) spec.cards.push({ title, description: description || "\u8A73\u3057\u3044\u60C5\u5831\u3092\u78BA\u8A8D\u3067\u304D\u307E\u3059\u3002", badge: cleanText(badge, 20) });
    } else if (key === "FAQ") {
      const [question, answer] = value.split(/[|｜]/).map((part) => cleanText(part));
      if (question) spec.faqs.push({ question, answer: answer || "\u8A73\u3057\u3044\u5185\u5BB9\u306F\u968F\u6642\u66F4\u65B0\u3057\u307E\u3059\u3002" });
    }
  }
  const fallbackCards = [
    { title: "\u898B\u3064\u3051\u308B", description: "\u5FC5\u8981\u306A\u60C5\u5831\u3078\u3059\u3050\u306B\u30A2\u30AF\u30BB\u30B9\u3067\u304D\u307E\u3059\u3002", badge: "01" },
    { title: "\u3064\u306A\u304C\u308B", description: "\u540C\u3058\u8208\u5473\u3092\u6301\u3064\u4EBA\u3068\u4EA4\u6D41\u3067\u304D\u307E\u3059\u3002", badge: "02" },
    { title: "\u697D\u3057\u3080", description: "\u65B0\u3057\u3044\u4F53\u9A13\u3084\u767A\u898B\u3092\u697D\u3057\u3081\u307E\u3059\u3002", badge: "03" }
  ];
  const fallbackFaqs = [
    { question: "\u3053\u306E\u30B5\u30A4\u30C8\u3067\u306F\u4F55\u304C\u3067\u304D\u307E\u3059\u304B\uFF1F", answer: "\u60C5\u5831\u3092\u63A2\u3057\u3001\u30B5\u30FC\u30D3\u30B9\u306E\u5185\u5BB9\u3092\u5206\u304B\u308A\u3084\u3059\u304F\u78BA\u8A8D\u3067\u304D\u307E\u3059\u3002" },
    { question: "\u30B9\u30DE\u30FC\u30C8\u30D5\u30A9\u30F3\u3067\u3082\u4F7F\u3048\u307E\u3059\u304B\uFF1F", answer: "\u30B9\u30DE\u30FC\u30C8\u30D5\u30A9\u30F3\u3001\u30BF\u30D6\u30EC\u30C3\u30C8\u3001\u30D1\u30BD\u30B3\u30F3\u306B\u5BFE\u5FDC\u3057\u3066\u3044\u307E\u3059\u3002" }
  ];
  while (spec.cards.length < 3) spec.cards.push(fallbackCards[spec.cards.length]);
  while (spec.faqs.length < 2) spec.faqs.push(fallbackFaqs[spec.faqs.length]);
  spec.cards = spec.cards.slice(0, 6);
  spec.faqs = spec.faqs.slice(0, 5);
  return spec;
}
__name(parseSpec, "parseSpec");
function escapeHtml(value) {
  return String(value || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
__name(escapeHtml, "escapeHtml");
function renderSite(spec) {
  const palettes = {
    violet: ["#8b5cf6", "#22d3ee", "#0b1020"],
    ocean: ["#0ea5e9", "#2dd4bf", "#071826"],
    forest: ["#22c55e", "#a3e635", "#07160d"],
    sunset: ["#f97316", "#ec4899", "#1d0b16"],
    mono: ["#f5f5f5", "#a3a3a3", "#111111"]
  };
  const [accent, accent2, background] = palettes[spec.theme] || palettes.violet;
  const cards = spec.cards.map((card, index) => `<article class="card"><span>${escapeHtml(card.badge || String(index + 1).padStart(2, "0"))}</span><h3>${escapeHtml(card.title)}</h3><p>${escapeHtml(card.description)}</p></article>`).join("");
  const faqs = spec.faqs.map((faq) => `<details><summary>${escapeHtml(faq.question)}</summary><p>${escapeHtml(faq.answer)}</p></details>`).join("");
  const html = `<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="description" content="${escapeHtml(spec.description)}"><title>${escapeHtml(spec.title)}</title><link rel="stylesheet" href="styles.css"></head>
<body><header><a class="logo" href="#top">${escapeHtml(spec.title)}</a><nav aria-label="\u30E1\u30A4\u30F3\u30E1\u30CB\u30E5\u30FC"><a href="#features">\u7279\u5FB4</a><a href="#faq">FAQ</a></nav></header>
<main id="top"><section class="hero"><p class="eyebrow">${escapeHtml(spec.eyebrow)}</p><h1>${escapeHtml(spec.tagline)}</h1><p class="lead">${escapeHtml(spec.description)}</p><a class="button" href="#features">${escapeHtml(spec.cta)}</a><div class="orb" aria-hidden="true"></div></section>
<section id="features"><div class="section-title"><p>FEATURES</p><h2>\u3053\u3053\u3067\u3067\u304D\u308B\u3053\u3068</h2></div><div class="grid">${cards}</div></section>
<section id="faq"><div class="section-title"><p>QUESTIONS</p><h2>\u3088\u304F\u3042\u308B\u8CEA\u554F</h2></div><div class="faq">${faqs}</div></section></main>
<footer><p>\xA9 ${(/* @__PURE__ */ new Date()).getUTCFullYear()} ${escapeHtml(spec.title)}</p></footer></body></html>`;
  const css = `:root{--a:${accent};--b:${accent2};--bg:${background};color-scheme:dark;font-family:Inter,ui-sans-serif,system-ui,-apple-system,sans-serif}*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;color:#f8fafc;background:var(--bg);line-height:1.7}a{color:inherit}header{position:sticky;top:0;z-index:10;display:flex;justify-content:space-between;align-items:center;padding:18px max(5vw,20px);background:color-mix(in srgb,var(--bg) 82%,transparent);border-bottom:1px solid #ffffff18;backdrop-filter:blur(16px)}.logo{font-weight:800;text-decoration:none}nav{display:flex;gap:20px}nav a{text-decoration:none;color:#cbd5e1}main{width:min(1120px,calc(100% - 40px));margin:auto}.hero{position:relative;overflow:hidden;min-height:70vh;display:grid;align-content:center;padding:90px 0}.eyebrow,.section-title>p{color:var(--b);font-size:.78rem;font-weight:900;letter-spacing:.2em}.hero h1{max-width:850px;margin:.1em 0;font-size:clamp(2.7rem,9vw,6.8rem);line-height:1.02;letter-spacing:-.055em}.lead{max-width:680px;color:#cbd5e1;font-size:clamp(1rem,2vw,1.2rem)}.button{width:max-content;margin-top:18px;padding:13px 22px;border-radius:999px;background:linear-gradient(120deg,var(--a),var(--b));color:#071018;font-weight:900;text-decoration:none}.orb{position:absolute;right:-8%;top:15%;width:330px;aspect-ratio:1;border-radius:50%;background:linear-gradient(135deg,var(--a),var(--b));filter:blur(80px);opacity:.3;pointer-events:none}section{padding:80px 0}.section-title h2{margin:.1em 0 .8em;font-size:clamp(2rem,5vw,3.5rem)}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}.card,details{border:1px solid #ffffff1c;border-radius:22px;background:#ffffff0b;box-shadow:0 20px 60px #0003}.card{padding:26px;transition:.2s transform,.2s border-color}.card:hover{transform:translateY(-4px);border-color:var(--a)}.card span{color:var(--b);font-weight:900}.card h3{font-size:1.35rem}.card p,details p{color:#bdc7d7}.faq{display:grid;gap:12px}details{padding:18px 22px}summary{cursor:pointer;font-weight:800}footer{padding:30px 5vw;border-top:1px solid #ffffff18;color:#94a3b8}.button:focus-visible,a:focus-visible,summary:focus-visible{outline:3px solid var(--b);outline-offset:4px}@media(max-width:760px){nav{display:none}.grid{grid-template-columns:1fr}.hero{min-height:620px;padding:60px 0}.orb{width:230px}section{padding:58px 0}}`;
  return { title: spec.title, summary: spec.description, plan: ["\u76EE\u7684\u3068\u5BFE\u8C61\u3092\u6574\u7406", "\u60C5\u5831\u69CB\u6210\u3068\u6587\u7AE0\u3092\u8A2D\u8A08", "\u30EC\u30B9\u30DD\u30F3\u30B7\u30D6\u306A\u30B5\u30A4\u30C8\u3092\u69CB\u7BC9"], files: [{ path: "index.html", content: html }, { path: "styles.css", content: css }] };
}
__name(renderSite, "renderSite");
function systemPrompt() {
  return [
    "You are a senior Japanese web strategist and copywriter.",
    "Analyze the request and return only short line-based fields, never HTML, CSS, JSON, Markdown, or explanations.",
    "Use TITLE, EYEBROW, TAGLINE, DESCRIPTION, CTA, THEME, CARD, and FAQ fields.",
    "THEME must be violet, ocean, forest, sunset, or mono.",
    "Return 3 to 6 CARD lines formatted CARD: title | description | short badge.",
    "Return 2 to 5 FAQ lines formatted FAQ: question | answer.",
    "Write natural, specific Japanese copy. Avoid vague filler and keep the whole answer under 350 tokens."
  ].join(" ");
}
__name(systemPrompt, "systemPrompt");
function groqModel(env) {
  const configured = String(env.GROQ_MODEL || "").trim();
  return !configured || configured === "qwen-2.5-coder-32b" ? "qwen/qwen3.6-27b" : configured;
}
__name(groqModel, "groqModel");
function tavilyMonthKey() {
  return (/* @__PURE__ */ new Date()).toISOString().slice(0, 7);
}
__name(tavilyMonthKey, "tavilyMonthKey");
function tavilyFreeCreditLimit(env) {
  return asInt(env.TAVILY_FREE_CREDIT_LIMIT, 1500, 0, 1500);
}
__name(tavilyFreeCreditLimit, "tavilyFreeCreditLimit");
function tavilyConfigMissing(env) {
  const missing = [];
  if (!env.TAVILY_API_KEY) missing.push("TAVILY_API_KEY");
  if (!env.TAVILY_QUOTA) missing.push("TAVILY_QUOTA");
  return missing;
}
__name(tavilyConfigMissing, "tavilyConfigMissing");
function tavilySearchEnabled(env) {
  return tavilyConfigMissing(env).length === 0;
}
__name(tavilySearchEnabled, "tavilySearchEnabled");
async function reserveTavilyCredits(env, allowPaidResearch) {
  const missing = tavilyConfigMissing(env);
  if (missing.length) throw new HttpError(503, "Web search is not configured. Missing: " + missing.join(", ") + ".");
  const month = tavilyMonthKey();
  const freeCreditLimit = tavilyFreeCreditLimit(env);
  const stub = env.TAVILY_QUOTA.get(env.TAVILY_QUOTA.idFromName(month));
  const response = await stub.fetch("https://quota.internal/reserve", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ month, freeCreditLimit, credits: 2, allowPaidResearch: allowPaidResearch === true }) });
  const data = await response.json().catch(() => ({}));
  if (response.status === 402 && data?.approvalRequired) throw new HttpError(402, "Tavily\u306E\u7121\u6599\u30AF\u30EC\u30B8\u30C3\u30C8\u3092\u4F7F\u3044\u5207\u308A\u307E\u3057\u305F\u3002\u6709\u6599\u691C\u7D22\u306F\u505C\u6B62\u4E2D\u3067\u3059\u3002", { approvalRequired: true, month });
  if (!response.ok || !Number.isFinite(Number(data?.totalCreditsUsed))) throw new HttpError(503, "\u691C\u7D22\u56DE\u6570\u306E\u78BA\u8A8D\u306B\u5931\u6557\u3057\u305F\u305F\u3081\u3001\u5B89\u5168\u306E\u305F\u3081Web\u691C\u7D22\u3092\u5B9F\u884C\u3057\u307E\u305B\u3093\u3067\u3057\u305F\u3002");
  return { month, freeCreditsUsed: Number(data.freeCreditsUsed), paidCreditsUsed: Number(data.paidCreditsUsed), paid: data.paid === true };
}
__name(reserveTavilyCredits, "reserveTavilyCredits");
async function tavilySearch(query, env, id, allowPaidResearch = false) {
  const cleanQuery = cleanText(query, 300);
  if (cleanQuery.length < 3) throw new HttpError(400, "\u691C\u7D22\u8A9E\u306F3\u301C300\u6587\u5B57\u3067\u5165\u529B\u3057\u3066\u304F\u3060\u3055\u3044\u3002");
  const quota = await reserveTavilyCredits(env, allowPaidResearch);
  const response = await fetch("https://api.tavily.com/search", { method: "POST", headers: { "content-type": "application/json", authorization: "Bearer " + env.TAVILY_API_KEY }, body: JSON.stringify({ query: cleanQuery, topic: "general", search_depth: "advanced", max_results: 10, include_answer: false, include_raw_content: false, include_images: false }) });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new HttpError(502, "Tavily\u691C\u7D22\u306B\u5931\u6557\u3057\u307E\u3057\u305F\u3002");
  const results = Array.isArray(data?.results) ? data.results.slice(0, 10).map((item) => ({ title: cleanText(item?.title, 180), url: String(item?.url || "").slice(0, 1200), content: cleanText(item?.content, 900) })).filter((item) => item.title || item.url || item.content) : [];
  log(quota.paid ? "tavily_paid_search_completed" : "tavily_free_search_completed", { requestId: id, month: quota.month, freeCreditsUsed: quota.freeCreditsUsed, paidCreditsUsed: quota.paidCreditsUsed, resultCount: results.length });
  return { query: cleanQuery, results, quota };
}
__name(tavilySearch, "tavilySearch");
var TavilyQuota = class {
  static {
    __name(this, "TavilyQuota");
  }
  constructor(state) {
    this.state = state;
  }
  async fetch(request) {
    const url = new URL(request.url);
    if (request.method !== "POST" || url.pathname !== "/reserve") return new Response("Not found.", { status: 404 });
    const input = await request.json().catch(() => ({}));
    const month = /^\\d{4}-\\d{2}$/.test(String(input?.month || "")) ? String(input.month) : "";
    const freeCreditLimit = asInt(input?.freeCreditLimit, 1500, 0, 1500);
    const credits = asInt(input?.credits, 2, 1, 2);
    if (!month) return sendJson({ error: "Invalid month." }, 400);
    const result = await this.state.storage.transaction(async (storage) => {
      const freeKey = "free-credits:" + month, paidKey = "paid-credits:" + month;
      const freeCreditsUsed = asInt(await storage.get(freeKey), 0, 0, freeCreditLimit);
      const paidCreditsUsed = asInt(await storage.get(paidKey), 0, 0, 1e8);
      if (freeCreditsUsed + credits <= freeCreditLimit) {
        const nextFree = freeCreditsUsed + credits;
        await storage.put(freeKey, nextFree);
        return { allowed: true, paid: false, freeCreditsUsed: nextFree, paidCreditsUsed, totalCreditsUsed: nextFree + paidCreditsUsed };
      }
      if (input?.allowPaidResearch !== true) return { allowed: false, approvalRequired: true, freeCreditsUsed, paidCreditsUsed, totalCreditsUsed: freeCreditsUsed + paidCreditsUsed };
      const nextPaid = paidCreditsUsed + credits;
      await storage.put(paidKey, nextPaid);
      return { allowed: true, paid: true, freeCreditsUsed, paidCreditsUsed: nextPaid, totalCreditsUsed: freeCreditsUsed + nextPaid };
    });
    return sendJson(result, result.allowed ? 200 : 402);
  }
};
var COMMANDER_SQUADS = Object.freeze({
  qwen: Object.freeze({ lead: "Qwen", purpose: "\u5B9F\u884C\u8A08\u753B\u3001\u6587\u7AE0\u3001\u30B5\u30A4\u30C8\u4ED5\u69D8\u3001\u6C7A\u5B9A\u8AD6\u7684\u306A\u5236\u4F5C\u306E\u6307\u63EE", roles: ["planner", "builder", "writer"] }),
  deepseek: Object.freeze({ lead: "DeepSeek", purpose: "\u72EC\u7ACB\u3057\u305F\u6280\u8853\u5206\u6790\u3001\u53CD\u5BFE\u610F\u898B\u3001\u5B9F\u88C5\u30EC\u30D3\u30E5\u30FC", roles: ["analyst", "reviewer", "auditor"] })
});
var SPECIALIST_REGISTRY = Object.freeze({
  web_research: Object.freeze({ squad: "qwen", requires: ["TAVILY_API_KEY", "TAVILY_QUOTA"], action: "\u5FC5\u8981\u6642\u3060\u3051\u6700\u65B0\u306E\u516C\u958B\u60C5\u5831\u3092\u53D6\u5F97", paid: "approval_required" }),
  code_review: Object.freeze({ squad: "deepseek", requires: ["HF_TOKEN", "HF_DEEPSEEK_MODEL"], action: "\u72EC\u7ACB\u3057\u305F\u30B3\u30FC\u30C9\u30FB\u8A2D\u8A08\u30EC\u30D3\u30E5\u30FC", paid: "disabled_until_explicitly_enabled" }),
  vision_review: Object.freeze({ squad: "deepseek", requires: ["HF_TOKEN", "HF_VISION_MODEL"], action: "\u753B\u9762\u30FB\u753B\u50CF\u306E\u54C1\u8CEA\u78BA\u8A8D", paid: "approval_required" }),
  image_generation: Object.freeze({ squad: "qwen", requires: ["HF_TOKEN", "HF_IMAGE_MODEL"], action: "\u753B\u50CF\u7D20\u6750\u306E\u751F\u6210", paid: "always_approval_required" }),
  video_generation: Object.freeze({ squad: "qwen", requires: ["HF_TOKEN", "HF_VIDEO_MODEL"], action: "\u77ED\u3044\u30D7\u30EC\u30D3\u30E5\u30FC\u52D5\u753B\u306E\u751F\u6210", paid: "always_approval_required" })
});
function missionMode(value, goal) {
  const requested = String(value || "").toLowerCase();
  if (["website", "research", "plan", "repository", "answer", "media"].includes(requested)) return requested;
  const text = String(goal || "").toLowerCase();
  if (/(画像|video|動画|image|media)/.test(text)) return "media";
  if (/(調べ|検索|最新|research|news)/.test(text)) return "research";
  if (/(サイト|web|html|css|ページ)/.test(text)) return "website";
  if (/(リポジトリ|github|コード確認)/.test(text)) return "repository";
  if (/(計画|設計|plan)/.test(text)) return "plan";
  return "answer";
}
__name(missionMode, "missionMode");
function specialistStatus(env, definition) {
  const missing = definition.requires.filter((name) => !env[name]);
  return { squad: definition.squad, action: definition.action, state: missing.length ? "awaiting_configuration" : "standby", missing };
}
__name(specialistStatus, "specialistStatus");
function commanderStatus(env) {
  const specialists = Object.fromEntries(Object.entries(SPECIALIST_REGISTRY).map(([name, definition]) => [name, specialistStatus(env, definition)]));
  return {
    version: COMMANDER_VERSION,
    state: "ready_for_specialists",
    commander: "owner_and_strategist",
    operatingRules: {
      maxParallelModelCalls: MAX_PARALLEL_MODEL_CALLS,
      maxQwenCallsPerMission: MAX_QWEN_CALLS_PER_MISSION,
      maxExternalModelCallsPerMission: MAX_EXTERNAL_MODEL_CALLS_PER_MISSION,
      publishRequiresExplicitConfirmation: true,
      paidCallsRequireExplicitConfirmation: true,
      unknownProviderCostDefaultsToStopped: true
    },
    squads: COMMANDER_SQUADS,
    specialists
  };
}
__name(commanderStatus, "commanderStatus");
function createMissionPlan(goal, mode, env, id) {
  const normalizedGoal = cleanText(goal, 1200);
  if (normalizedGoal.length < 3) throw new HttpError(400, "\u76EE\u7684\u306F3\u6587\u5B57\u4EE5\u4E0A\u3067\u5165\u529B\u3057\u3066\u304F\u3060\u3055\u3044\u3002");
  const selectedMode = missionMode(mode, normalizedGoal);
  const required = {
    website: ["planner", "builder", "code_review", "verifier"],
    research: ["planner", "web_research", "reviewer", "verifier"],
    plan: ["planner", "analyst", "verifier"],
    repository: ["planner", "auditor", "verifier"],
    media: ["planner", "vision_review", "image_generation", "video_generation", "verifier"],
    answer: ["planner", "verifier"]
  }[selectedMode] || ["planner", "verifier"];
  const status = commanderStatus(env);
  const waiting = [];
  for (const role of required) {
    const definition = SPECIALIST_REGISTRY[role];
    if (definition && specialistStatus(env, definition).state !== "standby") waiting.push(role);
  }
  return {
    missionId: id,
    mode: selectedMode,
    goal: normalizedGoal,
    state: waiting.length ? "awaiting_specialist_configuration" : "ready_for_execution",
    squads: [
      { squad: "qwen", assignment: required.filter((role) => !SPECIALIST_REGISTRY[role] || SPECIALIST_REGISTRY[role].squad === "qwen") },
      { squad: "deepseek", assignment: required.filter((role) => SPECIALIST_REGISTRY[role]?.squad === "deepseek") }
    ],
    pipeline: ["classify", "plan", "gather_or_build", "independent_review", "deterministic_verification", "await_owner_approval"],
    waitingFor: waiting,
    controls: status.operatingRules
  };
}
__name(createMissionPlan, "createMissionPlan");
var AGENT_TOOLS = [
  {
    type: "function",
    function: {
      name: "build_website",
      description: "Create a polished static landing page specification.",
      parameters: {
        type: "object",
        properties: {
          title: { type: "string" },
          eyebrow: { type: "string" },
          tagline: { type: "string" },
          description: { type: "string" },
          cta: { type: "string" },
          theme: { type: "string", enum: ["violet", "ocean", "forest", "sunset", "mono"] },
          cards: { type: "array", items: { type: "object", properties: { title: { type: "string" }, description: { type: "string" }, badge: { type: "string" } }, required: ["title", "description"] } },
          faqs: { type: "array", items: { type: "object", properties: { question: { type: "string" }, answer: { type: "string" } }, required: ["question", "answer"] } }
        },
        required: ["title", "tagline", "description", "theme", "cards", "faqs"]
      }
    }
  },
  {
    type: "function",
    function: {
      name: "create_plan",
      description: "Create a practical plan for a project, investigation, improvement, or multi-step task.",
      parameters: {
        type: "object",
        properties: {
          summary: { type: "string" },
          steps: { type: "array", items: { type: "string" } },
          risks: { type: "array", items: { type: "string" } },
          next_action: { type: "string" }
        },
        required: ["summary", "steps", "risks", "next_action"]
      }
    }
  },
  {
    type: "function",
    function: {
      name: "respond",
      description: "Answer, explain, analyze, summarize, brainstorm, or draft text when no external action is needed.",
      parameters: {
        type: "object",
        properties: {
          title: { type: "string" },
          answer: { type: "string" },
          next_actions: { type: "array", items: { type: "string" } }
        },
        required: ["answer"]
      }
    }
  },
  {
    type: "function",
    function: {
      name: "inspect_repository",
      description: "Read a file or directory from the configured safe GitHub repository. Never modifies files.",
      parameters: {
        type: "object",
        properties: { path: { type: "string", description: "Repository-relative path, or empty string for root." } },
        required: ["path"]
      }
    }
  },
  {
    type: "function",
    function: {
      name: "search_web",
      description: "Search public web pages for current factual information only when it is essential. This is limited to one low-cost search per user goal and never accesses private accounts.",
      parameters: {
        type: "object",
        properties: { query: { type: "string", description: "A concise public-web search query." } },
        required: ["query"]
      }
    }
  }
];
function parseToolArguments(value) {
  try {
    return JSON.parse(String(value || "{}"));
  } catch {
    return {};
  }
}
__name(parseToolArguments, "parseToolArguments");
function availableAgentTools(env) {
  return tavilySearchEnabled(env) ? AGENT_TOOLS : AGENT_TOOLS.filter((tool) => tool.function?.name !== "search_web");
}
__name(availableAgentTools, "availableAgentTools");
function agentSystemPrompt(env) {
  const researchRule = tavilySearchEnabled(env) ? "Use search_web only once when current public web evidence is essential. It returns at most 10 high-quality public sources. Never search for secrets, personal data, private accounts, or instructions to bypass safeguards." : "Web search is unavailable; never claim that you searched the web.";
  return [
    "You are a bounded autonomous Japanese AI agent.",
    "Choose the smallest suitable tool for the user's goal.",
    "Use build_website only for websites, create_plan for multi-step projects, respond for writing or analysis, and inspect_repository only when repository evidence is necessary.",
    researchRule,
    "Never claim an action happened unless a tool result confirms it.",
    "Never request, expose, infer, or repeat secrets.",
    "Never publish or modify a repository. Repository inspection is read-only.",
    "Keep answers concise, specific, and in Japanese."
  ].join(" ");
}
__name(agentSystemPrompt, "agentSystemPrompt");
function retryDelaySeconds(response, message, attempt) {
  const retryAfterHeader = Number(response?.headers?.get("retry-after"));
  if (Number.isFinite(retryAfterHeader) && retryAfterHeader > 0) return Math.ceil(retryAfterHeader);
  const hinted = retryAfterSeconds(message);
  if (hinted > 0) return hinted;
  return Math.max(1, 2 ** attempt);
}
__name(retryDelaySeconds, "retryDelaySeconds");
async function groqCompletionRequest(body, env, lane) {
  const retries = asInt(env.MAX_RATE_LIMIT_RETRIES, 1, 0, 3);
  const maxWaitSeconds = asInt(env.MAX_AUTOMATIC_RETRY_SECONDS, 3, 0, 15);
  for (let attempt = 0; ; attempt += 1) {
    let response;
    try {
      response = await fetch("https://api.groq.com/openai/v1/chat/completions", {
        method: "POST",
        headers: { "content-type": "application/json", authorization: `Bearer ${env.GROQ_API_KEY}` },
        body: JSON.stringify(body)
      });
    } catch {
      if (attempt < retries && maxWaitSeconds > 0) {
        const waitSeconds = Math.min(maxWaitSeconds, Math.max(1, 2 ** attempt));
        log("groq_network_backoff", { lane, attempt: attempt + 1, waitSeconds });
        await sleep(waitSeconds * 1e3);
        continue;
      }
      throw new HttpError(502, "Groqへの接続に失敗しました。");
    }
    const data = await response.json().catch(() => ({}));
    if (response.ok) return data;
    const providerMessage = data?.error?.message || String(response.status);
    if (response.status === 429) {
      const waitSeconds = retryDelaySeconds(response, providerMessage, attempt);
      if (attempt < retries && waitSeconds <= maxWaitSeconds) {
        const jitteredWait = Math.min(maxWaitSeconds, waitSeconds + Math.random());
        log("groq_rate_limit_backoff", {
          lane,
          attempt: attempt + 1,
          waitSeconds: Number(jitteredWait.toFixed(2))
        });
        await sleep(jitteredWait * 1e3);
        continue;
      }
      throw new HttpError(429, `Groqの無料枠が混雑しています。${waitSeconds || 30}秒後に再実行してください。`, { retryAfterSeconds: waitSeconds || 30 });
    }
    throw new HttpError(502, `Groq error: ${providerMessage}`);
  }
}
__name(groqCompletionRequest, "groqCompletionRequest");
async function groqAgentCompletion(messages, env, mode) {
  const reasoning = mode === "coding" || mode === "repository" ? "default" : "none";
  const data = await groqCompletionRequest({
    model: groqModel(env),
    temperature: reasoning === "default" ? 0.6 : 0.7,
    top_p: reasoning === "default" ? 0.95 : 0.8,
    reasoning_effort: reasoning,
    max_completion_tokens: asInt(env.AGENT_OUTPUT_TOKENS, 420, 250, 700),
    messages,
    tools: availableAgentTools(env),
    tool_choice: "auto"
  }, env, "agent");
  return data?.choices?.[0]?.message || {};
}
__name(groqAgentCompletion, "groqAgentCompletion");
async function inspectRepository(path, env) {
  const clean = String(path || "").replace(/^\/+/, "");
  if (clean.includes("..") || clean.length > 240) throw new HttpError(400, "\u5B89\u5168\u3067\u306A\u3044\u30EA\u30DD\u30B8\u30C8\u30EA\u30D1\u30B9\u3067\u3059\u3002");
  const encoded = clean.split("/").filter(Boolean).map(encodeURIComponent).join("/");
  const data = await github(env, `/contents/${encoded}?ref=${encodeURIComponent(SAFE_BRANCH)}`);
  if (Array.isArray(data)) return data.slice(0, 30).map((item) => ({ name: item.name, path: item.path, type: item.type, size: item.size }));
  if (data?.type === "file" && data?.content) {
    const binary = atob(String(data.content).replace(/\s/g, ""));
    const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
    return { path: data.path, size: data.size, content: new TextDecoder().decode(bytes).slice(0, 24e3) };
  }
  return { path: data?.path || clean, type: data?.type || "unknown" };
}
__name(inspectRepository, "inspectRepository");
async function runAgent(goal, mode, env, id, options = {}) {
  if (typeof goal !== "string" || goal.trim().length < 3 || goal.length > 6e3) throw new HttpError(400, "\u76EE\u7684\u306F3\u301C6000\u6587\u5B57\u3067\u5165\u529B\u3057\u3066\u304F\u3060\u3055\u3044\u3002");
  const messages = [{ role: "system", content: agentSystemPrompt(env) }, { role: "user", content: goal.trim() }];
  for (let step = 1; step <= 2; step += 1) {
    const message = await groqAgentCompletion(messages, env, mode);
    const call = message?.tool_calls?.[0];
    if (!call) return { type: "answer", title: "\u56DE\u7B54", answer: cleanText(message?.content || "\u56DE\u7B54\u3092\u751F\u6210\u3067\u304D\u307E\u305B\u3093\u3067\u3057\u305F\u3002", 4e3), nextActions: [], agent: { state: "completed", steps: step } };
    const name = call.function?.name;
    const args = parseToolArguments(call.function?.arguments);
    if (name === "build_website") {
      const site = verifySite(renderSite(parseSpec(Object.entries(args).flatMap(([key, value]) => Array.isArray(value) ? value.map((item) => `${key === "cards" ? "CARD" : "FAQ"}: ${Object.values(item).join(" | ")}`) : [`${key.toUpperCase()}: ${value}`]).join("\n"), goal)), env);
      return { type: "site", ...site, agent: { state: "awaiting_review", steps: step } };
    }
    if (name === "create_plan") return { type: "plan", summary: cleanText(args.summary, 1e3), steps: (args.steps || []).slice(0, 10).map((x) => cleanText(x, 300)), risks: (args.risks || []).slice(0, 8).map((x) => cleanText(x, 300)), nextAction: cleanText(args.next_action, 500), agent: { state: "completed", steps: step } };
    if (name === "respond") return { type: "answer", title: cleanText(args.title, 120), answer: cleanText(args.answer, 4e3), nextActions: (args.next_actions || []).slice(0, 8).map((x) => cleanText(x, 300)), agent: { state: "completed", steps: step } };
    if (name === "search_web" && step === 1) {
      const result = await tavilySearch(args.query, env, id, options.allowPaidResearch === true);
      messages.push(message, { role: "tool", tool_call_id: call.id, name, content: JSON.stringify(result) });
      continue;
    }
    if (name === "inspect_repository" && step === 1) {
      const result = await inspectRepository(args.path, env);
      messages.push(message, { role: "tool", tool_call_id: call.id, name, content: JSON.stringify(result) });
      continue;
    }
    throw new HttpError(422, "\u30A8\u30FC\u30B8\u30A7\u30F3\u30C8\u304C\u8A31\u53EF\u3055\u308C\u3066\u3044\u306A\u3044\u64CD\u4F5C\u3092\u9078\u629E\u3057\u307E\u3057\u305F\u3002");
  }
  throw new HttpError(422, "\u30A8\u30FC\u30B8\u30A7\u30F3\u30C8\u306E\u6700\u5927\u30B9\u30C6\u30C3\u30D7\u6570\u306B\u9054\u3057\u307E\u3057\u305F\u3002");
}
__name(runAgent, "runAgent");
function retryAfterSeconds(message) {
  const match = String(message || "").match(/try again in\s+([0-9.]+)s/i);
  return match ? Math.ceil(Number(match[1])) + 1 : 0;
}
__name(retryAfterSeconds, "retryAfterSeconds");
function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}
__name(sleep, "sleep");
async function askGroq(instruction, env) {
  const data = await groqCompletionRequest({
    model: groqModel(env),
    temperature: 0.2,
    max_completion_tokens: asInt(env.MAX_OUTPUT_TOKENS, 400, 250, 500),
    messages: [
      { role: "system", content: systemPrompt() },
      { role: "user", content: instruction }
    ]
  }, env, "site");
  return parseSpec(data?.choices?.[0]?.message?.content, instruction);
}
__name(askGroq, "askGroq");
async function generateSite(instruction, env, id, options = {}) {
  if (typeof instruction !== "string") throw new HttpError(400, "\u30B5\u30A4\u30C8\u306E\u8AAC\u660E\u3092\u5165\u529B\u3057\u3066\u304F\u3060\u3055\u3044\u3002");
  const goal = instruction.trim();
  if (goal.length < 10 || goal.length > MAX_INSTRUCTION_CHARS) {
    throw new HttpError(400, "\u30B5\u30A4\u30C8\u306E\u8AAC\u660E\u306F10\u301C5000\u6587\u5B57\u3067\u5165\u529B\u3057\u3066\u304F\u3060\u3055\u3044\u3002");
  }
  try {
    const research = options.research === true ? await tavilySearch(goal, env, id, options.allowPaidResearch === true) : null;
    const researchContext = research?.results?.length ? "\n\n\u6700\u65B0\u306E\u516C\u958B\u60C5\u5831\uFF08\u4FE1\u983C\u5EA6\u3092\u78BA\u8A8D\u3057\u3066\u53CD\u6620\u3059\u308B\u3053\u3068\uFF09\uFF1A\n" + research.results.map((item, index) => index + 1 + ". " + item.title + "\n" + item.content + "\n" + item.url).join("\n\n") : "";
    const specification = await askGroq(goal + researchContext, env);
    const candidate = renderSite(specification);
    const result = verifySite(candidate, env);
    log("site_generated", { requestId: id, fileCount: result.report.fileCount, totalCharacters: result.report.totalCharacters, researched: Boolean(research) });
    return {
      ...result,
      research: research ? { sourceCount: research.results.length, paid: research.quota.paid, freeCreditsUsed: research.quota.freeCreditsUsed, paidCreditsUsed: research.quota.paidCreditsUsed } : null,
      agent: { state: "awaiting_review", nextAction: "\u751F\u6210\u5185\u5BB9\u3092\u78BA\u8A8D\u5F8C\u3001\u4E0B\u66F8\u304D\u4F5C\u6210\u307E\u305F\u306F\u516C\u958B\u3092\u660E\u793A\u7684\u306B\u5B9F\u884C\u3057\u3066\u304F\u3060\u3055\u3044\u3002" }
    };
  } catch (cause) {
    if (cause instanceof HttpError) throw cause;
    throw new HttpError(422, `Qwen\u306E\u51FA\u529B\u3092\u5B89\u5168\u306A\u30B5\u30A4\u30C8\u3068\u3057\u3066\u78BA\u8A8D\u3067\u304D\u307E\u305B\u3093\u3067\u3057\u305F: ${cause instanceof Error ? cause.message : "\u4E0D\u660E\u306A\u30A8\u30E9\u30FC"}\u3002\u77ED\u3044\u4F9D\u983C\u6587\u3067\u300130\u79D2\u5F8C\u306B1\u56DE\u3060\u3051\u518D\u5B9F\u884C\u3057\u3066\u304F\u3060\u3055\u3044\u3002`);
  }
}
__name(generateSite, "generateSite");
function base64(text) {
  const bytes = new TextEncoder().encode(text);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}
__name(base64, "base64");
function branchPath(branch) {
  return String(branch).split("/").map((part) => encodeURIComponent(part)).join("/");
}
__name(branchPath, "branchPath");
async function github(env, path, init = {}) {
  const response = await fetch(`https://api.github.com/repos/${SAFE_REPOSITORY}${path}`, {
    ...init,
    headers: {
      accept: "application/vnd.github+json",
      authorization: `Bearer ${env.GITHUB_TOKEN}`,
      "x-github-api-version": "2022-11-28",
      ...init.headers || {}
    }
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new HttpError(502, `GitHub error: ${data?.message || response.status}`);
  return data;
}
__name(github, "github");
async function getBranchSha(env, branch) {
  const ref = await github(env, `/git/ref/heads/${branchPath(branch)}`);
  return ref.object.sha;
}
__name(getBranchSha, "getBranchSha");
async function createCommit(env, branch, site, message) {
  const parentSha = await getBranchSha(env, branch);
  const parent = await github(env, `/git/commits/${parentSha}`);
  const tree = [];
  for (const file of site.files) {
    const blob = await github(env, "/git/blobs", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ content: base64(file.content), encoding: "base64" })
    });
    tree.push({ path: file.path, mode: "100644", type: "blob", sha: blob.sha });
  }
  const nextTree = await github(env, "/git/trees", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ base_tree: parent.tree.sha, tree })
  });
  return github(env, "/git/commits", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ message: message.slice(0, 120), tree: nextTree.sha, parents: [parentSha] })
  });
}
__name(createCommit, "createCommit");
async function updateBranch(env, branch, sha) {
  return github(env, `/git/refs/heads/${branchPath(branch)}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ sha, force: false })
  });
}
__name(updateBranch, "updateBranch");
async function publish(site, env, id) {
  for (let attempt = 1; attempt <= 2; attempt += 1) {
    try {
      const commit = await createCommit(env, SAFE_BRANCH, site, `feat: publish ${site.title}`);
      await updateBranch(env, SAFE_BRANCH, commit.sha);
      log("site_published", { requestId: id, branch: SAFE_BRANCH, attempt });
      return { branch: SAFE_BRANCH, commitUrl: commit.html_url };
    } catch (cause) {
      if (attempt === 2) throw cause;
      log("publish_retry", { requestId: id, branch: SAFE_BRANCH, attempt });
    }
  }
  throw new HttpError(409, "GitHub\u30D6\u30E9\u30F3\u30C1\u306E\u66F4\u65B0\u306B\u5931\u6557\u3057\u307E\u3057\u305F\u3002");
}
__name(publish, "publish");
async function createDraft(site, env, id) {
  const baseSha = await getBranchSha(env, SAFE_BRANCH);
  const suffix = id.replaceAll("-", "").slice(0, 10);
  const draftBranch = `ai-drafts/${Date.now()}-${suffix}`;
  await github(env, "/git/refs", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ ref: `refs/heads/${draftBranch}`, sha: baseSha })
  });
  const commit = await createCommit(env, draftBranch, site, `draft: ${site.title}`);
  await updateBranch(env, draftBranch, commit.sha);
  log("draft_created", { requestId: id, branch: draftBranch });
  return { branch: draftBranch, commitUrl: commit.html_url };
}
__name(createDraft, "createDraft");
async function readPayload(request) {
  const raw = await request.text();
  if (raw.length > 15e4) throw new HttpError(413, "\u30EA\u30AF\u30A8\u30B9\u30C8\u304C\u5927\u304D\u3059\u304E\u307E\u3059\u3002");
  try {
    return JSON.parse(raw);
  } catch {
    throw new HttpError(400, "JSON\u5F62\u5F0F\u306E\u30EA\u30AF\u30A8\u30B9\u30C8\u304C\u5FC5\u8981\u3067\u3059\u3002");
  }
}
__name(readPayload, "readPayload");
async function authenticate(request, env) {
  requireConfig(env);
  const expectedPassword = env[ADMIN_PASSWORD_BINDING] || env.ADMIN_PASSWORD;
  const authenticated = await sameSecret(request.headers.get("x-admin-password"), expectedPassword);
  if (!authenticated) throw new HttpError(401, "\u64CD\u4F5C\u30D1\u30B9\u30EF\u30FC\u30C9\u304C\u9055\u3044\u307E\u3059\u3002");
}
__name(authenticate, "authenticate");
var index_default = {
  async fetch(request, env) {
    const id = requestId();
    const url = new URL(request.url);
    const headers = corsHeaders(request, env) || {};
    if (request.method === "GET" && url.pathname === "/") {
      return sendJson({
        ok: true,
        service: "groq-github-site-agent",
        message: "Backend API is running. Use the Hugging Face Space for the web interface.",
        health: "/health"
      }, 200, headers);
    }
    if (request.method === "GET" && url.pathname === "/health") {
      return sendJson({
        ok: true,
        service: "groq-github-site-agent",
        version: "2026-09-07",
        research: { enabled: tavilySearchEnabled(env), missing: tavilyConfigMissing(env), sourcesPerRequest: 10, searchDepth: "advanced", creditsPerSearch: 2, freeCreditLimit: tavilyFreeCreditLimit(env) },
        commander: { version: COMMANDER_VERSION, maxParallelModelCalls: MAX_PARALLEL_MODEL_CALLS, state: "ready_for_specialists" }
      }, 200, headers);
    }
    const cors = corsHeaders(request, env);
    if (!cors) {
      log("origin_rejected", { requestId: id, path: url.pathname });
      return fail("Origin is not allowed.", 403, id);
    }
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
    if (request.method !== "POST") return fail("POST only.", 405, id, cors);
    try {
      await authenticate(request, env);
      const payload = await readPayload(request);
      if (url.pathname === "/command/status") {
        return sendJson({ commander: commanderStatus(env), requestId: id }, 200, cors);
      }
      if (url.pathname === "/command/plan") {
        return sendJson({ mission: createMissionPlan(payload.goal, payload.mode || "auto", env, id), requestId: id }, 200, cors);
      }
      if (url.pathname === "/agent") {
        const mission = createMissionPlan(payload.goal, payload.mode || "auto", env, id);
        const result = await runAgent(payload.goal, mission.mode, env, id, { allowPaidResearch: payload.allowPaidResearch === true });
        return sendJson({ mission, ...result, requestId: id }, 200, cors);
      }
      if (url.pathname === "/generate" || url.pathname === "/run") {
        const mission = createMissionPlan(payload.instruction, payload.research === true ? "research" : "website", env, id);
        const result = await generateSite(payload.instruction, env, id, { research: payload.research === true, allowPaidResearch: payload.allowPaidResearch === true });
        const body = url.pathname === "/run" ? { status: "awaiting_approval", mission, ...result, requestId: id } : { site: result.site, report: result.report, agent: result.agent, mission, research: result.research, requestId: id };
        return sendJson(body, 200, cors);
      }
      if (url.pathname === "/validate") {
        const result = verifySite(payload.site, env);
        return sendJson({ ...result, requestId: id }, 200, cors);
      }
      if (url.pathname === "/draft") {
        const { site, report } = verifySite(payload.site, env);
        const draft = await createDraft(site, env, id);
        return sendJson({ ...draft, report, requestId: id }, 200, cors);
      }
      if (url.pathname === "/publish") {
        const { site, report } = verifySite(payload.site, env);
        const result = await publish(site, env, id);
        return sendJson({ ...result, report, requestId: id }, 200, cors);
      }
      return fail("Unknown path.", 404, id, cors);
    } catch (cause) {
      const status = cause instanceof HttpError ? cause.status : 500;
      const message = cause instanceof Error ? cause.message : "Unexpected error.";
      const details = cause instanceof HttpError ? cause.details : {};
      log("request_failed", { requestId: id, path: url.pathname, status, message });
      return fail(message, status, id, cors, details);
    }
  }
};
export {
  TavilyQuota,
  index_default as default
};
//# sourceMappingURL=index.js.map

