# Media Command Read Gate

**Status:** Enforced permanent standard  
**Effective:** 2026-09-14 JST  
**Machine source of truth:** `config/media_command_read_gate.json`

動画制作・切り抜き・TikTok Shop系の指令では、会話メモだけで始めず、**現在HEADの必要なノウハウだけ**を復元してから実行する。目的は「全部読む」ことではなく、**不足なく・重複なく・速く読む**こと。

## Fast restore

1. 意図を `VIDEO_CREATION` / `CLIPPING_REPURPOSING` / `TIKTOK_SHOP_COMMERCE` に分類する。
2. 複合意図は加算する。Shop系切り抜きは **Shop + Clipping** を必ず両方読む。
3. 必読PathをUnionして重複除去する。
4. 独立したRepository readは最大4並列で取得してよい。
5. 同じHEAD・同じPath・同じBlob SHAを同一作業中に再度読む必要はない。HEADまたはBlobが変われば無効化する。
6. Machine PolicyをHuman Playbookより先に適用する。
7. Longform専用資料はLongform時だけ、Cross-source/Second-pass資料はClaim-bearing・Commerce・時事Fact時だけ追加する。

## Common core

全メディアで読む最小Core:

- `config/current_commander_handoff.json`
- `config/permanent_standards_manifest.json`
- `docs/AI_ARMY_MASTER_RULEBOOK.md`
- `config/multi_agent_operating_policy.json`
- `config/agent_efficiency_policy.json`
- `config/media_audio_motion_retention_policy.json`
- `config/free_audio_source_registry.json`
- `config/dova_curated_bgm_catalog.json`
- `docs/MEDIA_PIPELINE.md`

旧 `config/shortform_edit_profile.json` は廃止済みで、再利用しない。

## Video creation

通常動画では `config/media_source_policy.json` を追加する。

Longformだけ、Objectives / Reliability Policy / Playbook / Research Synthesisを追加する。ShortformやShop ClipにLongform一式を無条件ロードしない。

ニュース・時事・Fact claimを含む場合はCross-source evidence / measurement / second-pass / artifact contractsを追加する。

編集面は `config/media_audio_motion_retention_policy.json` が正本。YMM4または同等挙動、VOICEVOX、口パク・目パチ、発話開始バウンス、話者Focus、表情差分、二重縁取り字幕、13〜15文字、音声境界同期をここから復元する。

## Clipping / repurposing

必須:

- `config/authorized_clipping_monetization_policy.json`
- `docs/AUTHORIZED_CLIPPING_AND_MONETIZATION_PLAYBOOK.md`
- `config/batch_media_orchestration_policy.json`
- `docs/BATCH_MEDIA_ORCHESTRATION.md`
- `docs/MEDIA_BATCH_COMMAND_CENTER.md`
- `config/media_source_policy.json`

核となる順序は **Rights → ASR/Alignment → Candidate/Boundary/Dedup → Cut/Reframe/Caption → Machine QA**。Rightsと収益化適格性は別Gate。Trim/Cut/Concat/Reframe/Caption burn/ffprobe等はAI討論ではなく決定論的Toolを優先する。

## TikTok Shop

必須:

- `config/tiktok_shop_influence_policy.json`
- `docs/TIKTOK_SHOP_INFLUENCE_PLAYBOOK.md`
- Cross-source evidence / measurement / second-pass / artifact contracts
- `config/media_source_policy.json`

商品ページEvidenceを台本確定前に取り込み、material claimをEvidenceへ結び、価格・Coupon・在庫・配送は公開近辺で再確認する。Persona / purchase motiveは仮説として扱い、HookやCreativeは実験として測る。

**既存・第三者動画をShop用に切り抜く場合はClippingセットも必須。Shopノウハウだけ、またはClippingノウハウだけで処理してはいけない。**

## Parallel execution

速度はAgent数ではなく、独立Workの同時実行で稼ぐ。

- 独立Read-only laneは並列可。
- 独立Media JobはAdmission後、最大3並列。
- Dependencyがある工程は順序を守る。
- 同じMutable OutputにはSingle Writer。
- Research / Rights & Claim verification / Edit planning / Deterministic automation planningは独立できる場合のみ並列化する。
- Mechanical stageにPeer debateやMajority voteを使わない。
- Resource/Provider/Write contentionが出たらQuality Gateを落とさずConcurrencyを下げる。

## 禁止

- すべての短尺処理でLongform資料一式を読む
- 同じBlobを1回のGate内で何度も読む
- Shop ClipでShop/Clippingどちらか一方のKnow-howを省く
- Rights / Claim / Machine QAを速度のために省く
- 同じOutputへ複数Writerを置く
- 旧Shortform Profileへ戻す
- 検索結果をLicenseとして扱う
- 自動Paid fallback

Machine-readableな正本は `config/media_command_read_gate.json`。各専門ルールの詳細はそれぞれのMachine Policy / Playbookを参照し、この文書へ重複コピーしない。
