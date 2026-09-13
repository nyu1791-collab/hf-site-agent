# Media Command Read Gate

**Status:** Enforced permanent standard  
**Effective:** 2026-09-13 JST  
**Purpose:** 動画制作・切り抜き・TikTok Shop系の指令が来た時、チャット記憶だけで制作を始めず、リポジトリの最新ノウハウ正本を必ず読み直す。

## 発火条件

完全一致キーワードではなく、**ユーザー意図**で判定する。

- **VIDEO_CREATION**: 「動画を作って」「動画制作して」「ショートを作って」「ニュース動画にして」「MP4にして」等
- **CLIPPING_REPURPOSING**: 「切り抜いて」「クリップ化」「Shorts向けに抜き出す」「再編集」「ハイライト化」等
- **TIKTOK_SHOP_COMMERCE**: 「TikTok Shop動画」「商品紹介動画」「売れる商品動画」「TikTokショップ用」等

複数意図が同時に含まれる場合は、読込セットを**加算**する。  
例: 「TikTok Shop向けに既存動画を切り抜いて商品紹介動画を作る」→ 3系統すべて読む。

## 実行前ゲート

該当指令を受けたら、以下より前に必読セットを読み終える。

1. 動画制作計画の確定
2. 外部メディアツール呼び出し
3. 素材取得
4. 音声生成
5. レンダリング
6. 公開引き渡し

会話メモや以前のタブの要約だけでは代用しない。新タブ・新セッションでも、現在のリポジトリ版を読み直す。

## 共通必読セット

- `config/current_commander_handoff.json`
- `config/permanent_standards_manifest.json`
- `docs/MEDIA_PIPELINE.md`
- `config/longform_video_objectives.json`
- `config/longform_video_reliability_policy.json`
- `docs/LONGFORM_VIDEO_RELIABILITY_PLAYBOOK.md`
- `docs/AI_ARMY_LONGFORM_RESEARCH_SYNTHESIS_2026-09-12.md`

## 動画制作時

さらに読む:

- `docs/LONGFORM_VIDEO_OBJECTIVES.md`

最低限回収するノウハウ:

- Scene/Chapter単位制作
- VOICEVOXずんだもん
- 実WAV尺を基準にしたaudio-first timeline
- ナレーション全量字幕
- 字幕safe zoneと視線設計
- 素材事前取得・decode検証
- rights manifest / provenance
- content-addressed checkpoint
- `.partial`→機械検証→atomic promotion
- 失敗Sceneだけ再試行
- 1080x1920 / 30fps / H.264 / yuv420p / AAC / 48kHz
- ffprobe + full decode QA
- Runway / Fal / Descript等の有料・限定無料動画SaaSを標準経路にしない

## 切り抜き・再編集時

さらに読む:

- `config/authorized_clipping_monetization_policy.json`
- `docs/AUTHORIZED_CLIPPING_AND_MONETIZATION_PLAYBOOK.md`
- `config/batch_media_orchestration_policy.json`
- `docs/BATCH_MEDIA_ORCHESTRATION.md`
- `docs/MEDIA_BATCH_COMMAND_CENTER.md`

最低限回収するノウハウ:

- 著作権許可とプラットフォーム収益化適格性を別ゲートにする
- 許可済み素材を標準とする
- 必要時ASR/VAD/word alignment
- multi-signal highlight scoring
- 近似候補のdedup
- 文・意味単位でboundary refinement
- subject-aware vertical reframe
- 字幕・crop・zoomだけを十分な変形とみなさない
- export前の機械QA
- 第三者素材の最終公開前はhuman approval

## TikTok Shop時

さらに読む:

- `config/tiktok_shop_influence_policy.json`
- `docs/TIKTOK_SHOP_INFLUENCE_PLAYBOOK.md`

既存・第三者素材を流用する場合は切り抜き規約も加算して読む。

最低限回収するノウハウ:

- 最初の3秒を最優先
- 1動画1persona / 1 primary purchase motive
- 好奇心→理解→証拠/信用→欲求→反論解消→透明な行動
- 商品ページを台本確定前に取得
- claim-to-evidence mapping
- 実演・複数利用シーン
- review dedup / cluster
- 向く人・向かない人を明示
- 字幕は全文表示ではなくattention UIとして設計
- coverは動画内容と一致
- 価格/クーポン/在庫/配送は公開時に再確認
- fake review / fake scarcity / unverified claim禁止
- AIGC・TikTok規約は公開直前にfresh確認

## 禁止ショートカット

- 「前タブで覚えているから読まない」
- 「接続済みだからDescript等を使う」
- 機械QA前に完成扱い
- attributionを著作権許可の代わりにする
- crop/字幕だけで十分な変形とみなす
- 一部失敗で正常な前段を全再生成
- 必要な承認なしに第三者素材を公開

機械可読の正本は `config/media_command_read_gate.json`。
