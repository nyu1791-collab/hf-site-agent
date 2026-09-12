# 新タブ引継ぎ — 2026-09-12 18:44 JST

この文書は、次のChatGPTタブへそのまま貼り付けて継続作業するための要点版です。会話記憶ではなく、GitHubのcanonical policy stackを最優先のSource of Truthとして扱ってください。

## 1. 最初に必ず確認

- Repository: `nyu1791-collab/hf-site-agent`
- Working branch: `ai-army/provider-v3`
- PR: `#40`
- PR状態（保存時確認）: `OPEN / DRAFT / UNMERGED`
- 保存直前のbranch HEAD: `8b1cef499781686fdfc6f49e3760771814ac0f1c`
- 作業開始時は必ずbranch HEAD / PR状態を再確認すること。
- `main`直接Push、PR Merge、本番Deploy、Publish、Secrets変更、Durable Object変更は禁止。明示承認なしに実行しない。

## 2. Canonical read order

新タブでは外部実行や設計変更より先に以下を読む。

1. `README.md`
2. `config/current_commander_handoff.json`
3. `config/permanent_standards_manifest.json`
4. `config/ai_army_org_chart.json`
5. `docs/AI_ARMY_CANONICAL_ORGANIZATION_2026-09-12.md`
6. `config/multi_agent_operating_policy.json`
7. `config/deepseek_paid_supervisor_policy.json`
8. `config/ci_execution_policy.json`
9. `config/agent_efficiency_policy.json`
10. `config/peer_review_adopted_deltas_20260912.json`
11. `docs/MULTI_AGENT_OPERATING_STANDARD.md`
12. 動画作業なら `config/longform_video_objectives.json` / `config/longform_video_reliability_policy.json` / `docs/LONGFORM_VIDEO_RELIABILITY_PLAYBOOK.md`
13. TikTok Shopなら `config/tiktok_shop_influence_policy.json` / `docs/TIKTOK_SHOP_INFLUENCE_PLAYBOOK.md`
14. 切り抜きなら `config/authorized_clipping_monetization_policy.json` / `docs/AUTHORIZED_CLIPPING_AND_MONETIZATION_PLAYBOOK.md`

## 3. AI Armyの現在の正式階層

### Depth 0 — Top Commander
`ChatGPT Work`

役割: Mission所有、最終判断、承認境界、統合、ユーザーへの最終報告。

### Depth 1 — Executive Supervisor
`Paid DeepSeek`

有料DeepSeekはユーザー承認済みの恒久的な例外として、上司AI／調査統括AIに置く。毎callごとの再承認は不要。ただし許可範囲は supervisory scope に限定。

主用途:
- 大規模な情報収集
- 複数ソース比較
- アーキテクチャ案の大量生成・比較
- Red Team / contradiction search
- 障害原因分析
- 下位AIへのTask分解
- 下位AI結果の査読・統合
- Media戦略
- TikTok Shop戦略
- Postmortem / remediation

DeepSeekを末端Workerや大量のboilerplate codeを書くAIとして常用しない。大量コード・機械的変換・単純な定型処理はQwen/NVIDIA/Groq/Google/Free Worker/Python等へ委譲する。

### Depth 2 — Specialist / Worker
Qwen、NVIDIA、Groq、Google、OpenRouter free、GitHub coding adapter、Python/FFmpeg等。現在のprovider readinessが確認でき、専門化・並列化に測定可能な価値がある時だけ使う。

Worker-to-Workerの無制限委任は禁止。Single Writer、Task Lease、Dependency Joinを守る。

## 4. DeepSeek有料Supervisorの固定ルール

Canonical policy: `config/deepseek_paid_supervisor_policy.json`

- Rank: `EXECUTIVE_SUPERVISOR`
- Reports to: ChatGPT Work
- DeepSeekは最終決定権を持たない。
- 標準Research fan-out: **6 lanes / 3 parallel / 8 calls per Mission**
- 拡張上限: **8 lanes / 4 parallel / 12 calls**
- 拡張には明示的なMission理由、独立した調査方向、budget preflight、重複なし、期待情報利得が必要。
- 成功済みlaneは新証拠なしで再実行しない。
- 失敗lane retryは最大1回、同一root causeでの再試行禁止。方法変更が必要。
- Mission推定費用上限: **$0.50**
- Daily推定費用上限: **$3.00**
- `AUTO_TOP_UP=false`
- Generic paid fallback禁止。
- DeepSeek以外のPaid Providerは別途ユーザー明示承認が必要。
- DeepSeek自身によるrepository write / deploy / publish / merge / secret mutationは禁止。
- Paid media generationは禁止。
- 唯一のactive paid DeepSeek workflowは `.github/workflows/deepseek-supervisor-research.yml`
- Historical paid DeepSeek workflows/configsは実行権限を持たない。

## 5. Routingの正式入口

新しいpolicy-facing routingは必ず `scripts/ai_army_routing_facade.py` を入口にする。

選択肢:
1. Deterministic Tool
2. Paid DeepSeek Executive Supervisor
3. Direct Specialist Bypass
4. ChatGPT single-controller fallback

DeepSeekを全Taskに必須の有料hopとして挟まない。単純処理・狭いcoding・機械検証ではPython/Test/FFmpeg/verified specialistを直接使う。

旧 `scripts/commander_routing.py` はcompatibility layerであり、policy Source of Truthではない。

## 6. Multi-Agent運用原則

- Multi-Agentは多いほど良いわけではない。
- DefaultはSingle AgentまたはDeterministic Tool。
- 並列化は、独立workstream・専門分離・verification・context isolation・長時間checkpointに価値がある時だけ。
- 厳密な逐次reasoningを人数増加のためにfan-outしない。
- Machine oracle（schema validator、test、hash、ffprobe等）がある時はAI多数決より優先。
- Single Writerを維持。
- 同じmutable targetへのparallel write禁止。
- healthy outputをdownstream failureで破棄しない。
- checkpointから最小失敗単位のみresume。
- bounded retry / bounded replan / circuit breakerを維持。
- Failure classとtermination reasonを記録する。
- Multi-Agentの昇格はSingle-controller baselineとのshadow measurementを通す。

重要実装:
- `scripts/agent_architecture_admission.py`
- `scripts/agent_efficiency_eval.py`
- `scripts/ai_army_routing_facade.py`
- `scripts/validate_permanent_ai_army_state.py`
- `.github/workflows/canonical-ai-army-consistency.yml`

## 7. 最新の査読・改善状態

DeepSeek full-system peer reviewは8ドメインをカバー済み。ChatGPTが一次資料と照合して採用・却下を分離済み。

採用した重要点:
- ChatGPTはTop Commanderのまま。
- Paid DeepSeekはExecutive Supervisor。
- Agent数増加より中央管理・Single Writer・Machine Oracle・Checkpoint・測定を優先。
- DeepSeek computeは調査、設計、査読、Red Team、障害診断、統合に集中。
- Stable shared prefix / prefix hash / artifact refsで重複contextを削減。
- 成功lane再実行禁止、same-root retry禁止。
- 効果がないMulti-Agent構成はdefaultへ昇格しない。

CIで次を確認済み:
- sequential taskを無理にMulti-Agent化しない
- parallel researchではcentral managerを選べる
- single writer contract
- worse treatmentをpromoteしない
- DeepSeek supervisor runner contract
- paid workflowがsingle-mission scoped

## 8. 長尺動画の恒久ルール

最優先目標は「再生可能な完成MP4を作ること」。報告だけで終わらない。

- 標準音声: `VOICEVOX ずんだもん`
- 実装: Python + FFmpeg + ffprobe
- 1080×1920 / 30fpsへScene単位でnormalize
- monolithic huge render禁止
- Scene/Chapterごとにrenderして最後にconcat
- 成功済みScene、音声、画像をcache/checkpointし再生成しない
- 字幕/動画timingは実WAV durationを基準にする
- 外部画像取得はrender前に完了・検証
- 画像1枚の失敗で全動画を止めない
- `assets_ready -> voice_ready -> subtitles_ready -> scene_rendered -> scene_validated -> final_concat_done`
- 最終確認: MP4存在、size>0、video/audio stream、1080×1920、duration、ffprobe、可能ならfull decode

禁止メディアSaaS:
`Runway / Fal / fal.ai / Descript / VEED / HeyGen / Higgsfield` および同種の少量無料→すぐ有料のcredit/subscriptionサービス。

完全無料routeであることを実行時確認できない外部video serviceはfail closed。

Paid DeepSeekは動画生成サービスではなく、技術分析・調査・設計・査読にのみ使う。

## 9. TikTok Shop運用の重要点

- 商品を最初の3秒から明確にする。
- 1動画につきPrimary Personaは1つ、Primary Purchase Motiveも1つ。
- Storytellingはevidence gradeを上げない。
- Product page / manufacturer / independent evidence / review clusterを分離。
- Claim-to-Evidence mappingをpublication blocking gateにする。
- Price / coupon / stock / shipping / return policy等はtimestamp＋TTL/expiryを持たせ、publish直前に再確認。
- Local reviewは原文保持、翻訳分離、重複排除、topic clustering、sample size記録。
- 個別レビューを国民全体の意見に一般化しない。
- Fake review / fake urgency / unverified numeric claimは禁止。
- AIGC policyはpublish時に再確認し、必要なDisclosureを行う。
- Winner判定は売上だけでなくreturn / complaint / policy guardrailを見る。
- A/B testは原則1 primary variable、事前登録。

## 10. 切り抜き・再利用動画

- Generic無断切り抜きは採用しない。
- Source ownerまたは明示的なcommercial licenseが必要。
- Attributionはpermissionの代わりにならない。
- Platform monetization eligibilityとcopyright permissionは別々に確認。
- Rights manifestをSourceごとに持つ。
- Third-party publicationはHuman approval gateを残す。
- Fake engagement automation禁止。

## 11. 金銭・安全の固定境界

Paid DeepSeek Supervisor以外は原則free-only。

禁止:
- 自動チャージ
- Generic paid fallback
- 無断のpaid provider利用
- main direct push
- PR merge
- production deploy
- public publish
- secret表示・取得・変更
- Durable Object変更
- 不可逆外部操作

## 12. 次タブの実行方針

新タブではユーザーに同じ説明を再度求めない。まずGitHub canonical stateを読み、現在HEAD/PRを確認してから続行する。

ユーザーはChatGPTがTop Commanderとして計画・分解・判断し、必要ならDeepSeek上司・Qwen/NVIDIA等へ指示を出すことを望んでいる。ユーザー自身に下位AIへの指示を書かせない。

作業原則:
`現状確認 -> 最小変更 -> 検証 -> 実測 -> 必要なら昇格 -> 保存`

効果が測れない追加Agent・Framework・Policyは増やさない。既存の良い構成を壊さず、測定で改善したものだけ恒久化する。
