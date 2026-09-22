# 日本向けメディアパイプライン仕様

この文書はメディア制作の**簡潔な入口**である。詳細Ruleをここへ重複させず、現行Machine Policyへ展開する。最終目標は「AIが作業したこと」ではなく、**ユーザーが実際に再生でき、見やすく、内容を理解できる完成MP4を受け取ること**。

## 作業前に読む正本

1. `config/current_media_quality_handoff.json`
2. `config/media_user_visual_duration_preferences.json`
3. `config/media_command_read_gate.json`
4. Intentに応じてRead Gateが要求するMachine Policy

長尺では必ず以下も読む。

- `config/longform_video_objectives.json`
- `config/longform_video_reliability_policy.json`
- `docs/LONGFORM_VIDEO_OBJECTIVES.md`
- `docs/LONGFORM_VIDEO_RELIABILITY_PLAYBOOK.md`

競合時は、最新の明示的ユーザー指示と安全境界を守ったうえで現行Machine Policy / Validator / CIを優先する。

## 標準制作経路

- 調査・原稿: **ChatGPT + DeepSeek** のCompact Pairを1つの判断Stageとして使う。
- 音声: VOICEVOX local、標準castは **ずんだもん + 四国めたん**。
- 動画生成前に `config/video_creation_admission_policy.json` を復元し、`scripts/video_creation_admission.py --runtime` を通す。ローカルVOICEVOXまたはずんだもんが利用できない場合はレンダリングを停止し、無音動画へフォールバックしない。
- 機械制作: Python / FFmpeg / ffprobe / Pillow / OpenCV / ASS等。
- Rendering / timing / hashing / decode QA等に不要なAgentを増やさない。
- Runway / Fal / Descript / VEED / HeyGen / Higgsfield等のPaid/Freemium/Trial経路を標準制作にしない。Unknown costはBLOCK。

## 素材

検索結果は発見手段でありLicenseではない。Original Source、Rights、Attribution、取得日時、Content Hashを確認してからMaterializeする。外部素材はRender前に取得・Decode検証し、FFmpeg中のNetwork fetchを標準にしない。1素材の失敗で無関係なSceneを再生成しない。

Generated image/video assetは現行標準経路にしない。

## 長尺の標準

通常News/Topic Explainerは **8〜12分目安**。ただしPadding quotaではない。無関係な歴史、反復、遅い読み、低情報量Fillerは禁止。尺は事実、仕組み、影響、重要な時系列、不確実性、相反する見方、今後の注目点、必要な定義で作る。

長尺全体を1回の巨大Encodeにしない。

`Research+Script -> Source/Claim Lock -> Asset/Right Lock -> Voice -> Measured Timing -> Caption -> Scene Render -> Scene Validate -> Checkpoint -> Concat -> Mechanical QA -> Visual Rereview -> Handoff`

Scene/Chapter単位で `Render -> Validate -> Checkpoint -> Join`。成功済みAudio/Asset/Sceneを後段失敗で破壊しない。`.partial` はVerified Sceneではない。

## Timeline / Caption

Timelineは文字数推測ではなく生成済みWAVのffprobe実時間を正本にする。字幕は `FULL_SPOKEN_TEXT` 契約で話し言葉を省略せず、Semantic Chunk、Rendered Width、Audio Timing、Emphasisを分けて扱い、Narration coverage 100%を維持する。短い要約字幕を音声字幕の代わりにしてはならない。

字幕本文と枠は話者色で分ける。ずんだもんは明るい緑、四国めたんは明るいピンク/マゼンタ。重要語は黄色または赤で強調し、暗い内縁・話者ラベル・文言を併用する。大きな黒ベタ字幕箱を標準にせず、必要なら細い暗色内縁等でContrastを確保する。

ニュースや事実説明の背景は、話題に意味的に合う検索済み／登録済みの権利確認済み画像を優先する。source page、asset locator、ライセンス／パブリックドメイン状態、scene/claim mapping、取得・確認時刻を台帳に残し、検索結果そのものを許諾とみなさない。

## Character / Diagram

ずんだもん・四国めたんは見た目の大きさをNormalizeし、Active Speakerを自然に大きく・前へ出す。表情は口だけではなく目、眉、顔、Pose、Head Tilt、Listener Reaction等をSemantic Beatで使う。固定の「N文ごとにExpression変更」は使わない。

説明図・背景図の全体を上下に漂わせない。基本は静止。Pointer / Highlight / Reveal等、説明に意味のある局所Motionだけ使う。

## 止まらないための基本

詳細はLongform Reliability PolicyをAuthorityとする。

- Explicit state manifest
- Content-addressed verified checkpoints
- Atomic scene promotion
- Single Writer + Lease/TTL/heartbeat
- Replay-safe idempotent stage
- Bounded provider/process timeout
- No-progress detection
- Provider circuit breaker
- Retry ownerは1 Layer
- Retryable transientだけbounded exponential backoff + jitter
- Permanent errorはRetryしない
- CacheはAccelerator、Artifact/Checkpointは復旧用
- PartialをCompleteと報告しない

## Final Delivery Gate

- ffprobe parse
- Video/Audio Media Contract
- Full decode
- Subtitle coverage
- Fast Start (`moov` before `mdat`)
- Representative Visual QA
- Character / Caption / Evidence Safe Zone
- 意図しないBackground Diagram vertical driftなし
- 元依頼と現行PolicyのRereview

Decode PASSだけをVisual PASSとみなさない。Candidate完成直後に即納せず、一度見直してから渡す。


## チャット配信中断への耐性

ChatGPTアプリ側の応答ストリームは、長時間の動画生成Jobそのものの実行基盤として扱わない。長時間処理はRepositoryとDurable Runner上へ先に固定し、チャット表示が切れてもJob・Checkpoint・Artifactが残る設計にする。

- 実行前にMission / Source Lock / Policy version / request hashをRepositoryへ永続化する。
- Runnerはチャット接続から独立して継続し、`cancel-in-progress: false`を標準とする。
- VOICEVOX音声など高コストStageはRender前にVerified checkpointとしてArtifact化する。
- 各Stageはstate manifestへ `PENDING / RUNNING / VERIFIED / BLOCKED / FAILED_RETRYABLE / FAILED_PERMANENT / COMPLETE` を記録する。
- 再接続時は会話文から再開せず、最新HEAD、PR状態、request state、workflow run、verified artifactsを読み直す。
- 同一Root CauseをEvidenceなしで再試行せず、壊れたStageだけを再実行する。
- 応答ストリームの中断だけを動画生成失敗と判定しない。一方、Playable artifactが無い状態を完成扱いもしない。

Machine authority: `config/session_stream_resilience_policy.json`
