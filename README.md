# hf-site-agent

AI Army / Provider-v3 の実験・検証リポジトリ。

## 0. 新しいチャット／タブ／AIセッションで最初に読むもの

**会話履歴をこのプロジェクトの正本にしない。** タブを変更した場合、別AIセッションから再開した場合、または長時間中断後に復旧する場合は、作業を始める前に次の順でRepository stateを復元する。

1. `README.md`
2. `config/current_commander_handoff.json`
3. `config/multi_agent_operating_policy.json`
4. `docs/MULTI_AGENT_OPERATING_STANDARD.md`
5. `config/longform_video_objectives.json`
6. `config/longform_video_reliability_policy.json`
7. `docs/LONGFORM_VIDEO_RELIABILITY_PLAYBOOK.md`
8. `docs/AI_ARMY_LONGFORM_RESEARCH_SYNTHESIS_2026-09-12.md`

恒久的な運用判断が変わった場合は、チャット内だけで終わらせずRepository側の正本も更新する。`config/current_commander_handoff.json` が新しいセッションの継続入口である。

## 動画制作の固定運用ルール

このリポジトリで長編動画を制作する場合、以下を標準ルールとして扱う。チャットや別タブで毎回説明し直す必要はない。

### 1. 外部のフリーミアム動画制作SaaSは使用しない

動画の生成・編集・字幕・音声・アップスケール等を目的として、次のような「最初だけ無料／少量無料だが、継続利用ですぐ課金へ移行する外部サービス」は使用しない。

- Runway
- Fal / fal.ai
- Descript
- VEED
- HeyGen
- Higgsfield
- その他、同種のクレジット制・従量課金制・無料枠消費型の動画制作／編集SaaS

接続済み・インストール済みであっても、動画制作の実行経路として選択しない。無料クレジットが残っていても使わない。

外部サービスを使う場合は、利用時点で **完全無料であり、自動課金・有料Fallback・クレジット購入を要求しないことが確認できるものだけ** を許可する。不明な場合は fail-closed とする。

### 2. 動画本体はローカル／無料実行系で作る

実レンダリングの標準経路は次のとおり。

- Python
- FFmpeg / ffprobe
- Pillow / OpenCV / MoviePy などのローカル処理
- Colab / Kaggle / GitHub Actions 等の無料実行枠（無料であることを確認できる場合のみ）
- 権利確認済みの無料素材、または自前生成素材

AIは企画・台本・技術レビュー・素材設計を担当し、反復的な映像処理はPython/FFmpegへ渡す。

### 3. ナレーションは「ずんだもん」で固定

日本語動画の標準ナレーションは **VOICEVOXのずんだもん** とする。ユーザーが明示的に変更を指示しない限り、外部有料TTSへ切り替えない。

- 章または字幕ブロック単位で音声生成
- 生成済みWAVはキャッシュして再利用
- 実際のWAV長を測定し、映像尺・字幕タイミングの基準にする
- 映像修正だけでVOICEVOXを再生成しない
- VOICEVOX raw WAVと48kHz正規化済みScene音声を別成果物として保持する

### 4. 長編はScene単位で生成して最後に結合

5分、10分、それ以上でも1本の巨大なFFmpeg処理へまとめない。

`Scene 01 -> Scene 02 -> ... -> Scene N -> concat -> final.mp4`

各Sceneは同一条件へ正規化する。

- 1080x1920
- 30fps
- 同一Video Codec
- 同一Audio Codec
- 同一Sample Rate
- 同一Pixel Format

成功済みSceneは再生成しない。失敗したSceneだけ再試行し、最後にconcat copyを優先する。必要な場合だけ最終再エンコードする。

### 5. Checkpoint / 再開を必須にする

各Scene／章について少なくとも以下を記録する。

- 素材取得済み
- 音声生成済み
- 字幕生成済み
- Sceneレンダリング済み
- Scene検証済み
- 最終結合済み

失敗した場合は最後の正常Checkpointから再開する。成功済みの台本、音声、画像、字幕、Sceneを削除・再生成しない。

**GitHub Actions cacheは高速化用であり、唯一のCheckpoint正本にしない。** runを跨いで残す検証済み成果物はArtifactまたは明示的Checkpoint manifestで管理する。

### 6. 素材障害を全体障害にしない

レンダリング中に外部URLを直接読み込まない。必要素材は事前取得し、サイズ・デコード・content hash・rights/statusを検査する。1素材だけ失敗した場合は代替素材に切り替え、動画全体を停止しない。

### 7. 字幕とずんだもん表示

- ナレーション全文を字幕でカバーする
- 長文を短い読みやすいブロックへ分割する
- タイトル／章タイトル／本文で文字サイズ・太さ・位置を分ける
- 重要画像やずんだもん立ち絵と字幕が重ならない安全領域を固定する
- ずんだもんの画面位置は基本固定し、章ごとに表情・公式立ち絵を切り替えて単調さを抑える
- 字幕の正本はNarration Manifestとし、特定TTS provider内部stateへ結合しない

### 8. AIの分業

- **ChatGPT**: 最高司令部。全体設計、工程分解、統合、最終成果物の受け渡し
- **DeepSeek**: 難しい技術レビュー、FFmpeg／レンダリング原因分析、長尺構成レビュー、修正案
- **NVIDIA / Qwen等**: コードレビュー、エラー解析、字幕／音声同期、素材確認、構成レビュー等の専門担当
- **Python / FFmpeg**: 実際の機械処理

同じ仕事を複数AIへ重複発注せず、役割を分ける。Multi-Agentを使う前にSingle-Agent baselineで十分でないか確認し、並列mutationではTask lease / single writerを守る。

### 9. 有料DeepSeekの扱い

ユーザーが明示承認した **有料DeepSeek** は、その承認Scope内の技術分析・設計・レビュー用途の例外として利用できる。ただし、この許可はRunway/Fal/Descript等の有料・フリーミアム動画制作サービスへは波及しない。

DeepSeek利用も既存の予算上限・呼び出し上限・秘密値非表示・STAGING_ONLY・repository_write=false・deploy=false・publish=false等のガードを維持する。新しいMissionで有料実行の承認が継続しているか不明な場合はfail-closedとする。

### 10. 完成判定

重い最終目視レビューを必須にしない。最低限、以下を機械的に通過すれば完成候補とする。

- MP4が存在する
- ファイルサイズが0ではない
- 映像ストリームがある
- 音声ストリームがある
- 1080x1920
- 想定尺の範囲内
- ffprobeで正常読込可能
- 可能な範囲でdecode smoke testを通過し、decode errorを0にする

完成後は分析報告より先に、実際に再生できる動画をユーザーへ提示する。

## 組織AIの固定運用ルール

- Top Commanderは1つ。最終統合責任を保持する。
- Agent数そのものを性能指標にしない。
- 明確に独立したWorkstreamだけを並列化する。
- 同じmutable targetへ複数Writerを置かない。
- 並列mutationにはTask lease / ownershipを要求する。
- Context全文を全Agentへ配らず、必要Context + Artifact referenceだけを渡す。
- 重大成果物はPrimary + Independent Verifierまたはmachine oracleで検査する。
- Debate / majority voteは標準経路ではない。
- Agent loopは必ずturn/request/token/time/circuit等の停止条件を持つ。
- Paid / secret / merge / deploy / publish / irreversible actionはblocking guardrail + Human Gateを維持する。
- Frameworkはon-demand。Native runtimeを正本とし、LangGraphをstateful graphの第一候補、CrewAI/AutoGen/Copilot系は限定Adapterとする。
- A2Aはremote cross-vendor境界が必要な場合だけ検討し、内部通信の必須規格にはしない。

## 詳細仕様

- [Current Commander Handoff](config/current_commander_handoff.json)
- [Multi-Agent Operating Policy](config/multi_agent_operating_policy.json)
- [Multi-Agent Operating Standard](docs/MULTI_AGENT_OPERATING_STANDARD.md)
- [Long-form Video Objectives](config/longform_video_objectives.json)
- [Long-form Video Reliability Policy](config/longform_video_reliability_policy.json)
- [Long-form Video Reliability Playbook](docs/LONGFORM_VIDEO_RELIABILITY_PLAYBOOK.md)
- [AI Army / Long-form Research Synthesis](docs/AI_ARMY_LONGFORM_RESEARCH_SYNTHESIS_2026-09-12.md)
- [Media Agent Army](docs/MEDIA_AGENT_ARMY.md)
- [日本向けメディアパイプライン](docs/MEDIA_PIPELINE.md)

## 変更禁止境界

動画制作・組織AI改善のために main 直接Push、PR Merge、本番Deploy、無断Publish、秘密値表示・秘密値変更を行わない。
