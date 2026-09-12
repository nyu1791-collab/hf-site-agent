# 日本向けメディアパイプライン仕様

この文書は、Colab / Kaggle / GitHub Actions / ローカルPython環境等で動かす動画制作パイプラインの固定境界を定める。Cloudflare Workerは企画・承認・状態管理に留め、重い動画処理やGPU処理を持ち込まない。

## 固定方針

このリポジトリの動画制作では、Runway、Fal/fal.ai、Descript、VEED、HeyGen、Higgsfield等の「限定無料クレジット後に課金へ移る動画制作／編集SaaS」を使わない。接続済み・無料残高ありであっても実行経路に選ばない。

外部サービスを使う場合は、利用時点で完全無料であり、自動課金、有料Fallback、クレジット購入要求がないことを確認できるものだけに限定する。確認できない場合は停止する。

動画本体は Python / FFmpeg / ffprobe / Pillow / OpenCV / MoviePy 等のローカルまたは無料実行環境で生成する。

日本語ナレーションの標準音声は **VOICEVOX ずんだもん** とする。ユーザーが明示的に変更しない限り他の有料TTSへ切り替えない。

## 長編Reliabilityの正本

4〜10分以上の長編動画では、[LONGFORM_VIDEO_RELIABILITY_PLAYBOOK.md](./LONGFORM_VIDEO_RELIABILITY_PLAYBOOK.md) を本書と併せて必ず読む。競合する場合、長編の耐障害性・checkpoint・cache・preflight・media contractについてはPlaybookを優先する。

機械可読の固定条件は [`config/longform_video_reliability_policy.json`](../config/longform_video_reliability_policy.json) に保存する。レンダリング前には [`scripts/longform_video_preflight.py`](../scripts/longform_video_preflight.py) を使い、FFmpeg/ffprobe、encoder/filter/font、disk、Mission、禁止動画SaaS、実行時はローカルVOICEVOXずんだもんをfail-fast検査する。

重要:

- 通常実行で健康なaudio/image/Scene checkpointを破壊しない。
- `.partial` を成功済み成果物として扱わない。
- concat前に全Sceneのffprobe media contractを比較する。
- file sizeやfilenameだけでcache互換を判定しない。
- A/V driftは必ず計測するが、block閾値はfixture実測で校正するまで絶対値として固定しない。
- free AI reviewerの公開catalogが無料でも、実行直前に404/429/EMPTYへ変化し得る。paid siblingへ自動移行しない。
- `scripts/media_agent_runtime.py` に残るDescript/Fal/Runway等のlegacy/general routeは長編動画制作には適用しない。

## 長編パイプライン

1. **Research / source lock**: 公開情報を出典付きで収集し、採用する事実と表現を固定する。台本承認後はレンダリング失敗を理由に再調査・再生成しない。
2. **Rights manifest**: 入力ごとに source_type、permission_status、attribution、取得日時、SHA-256を記録する。権利が pending または blocked の素材はレンダリング対象にしない。
3. **Asset materialization**: 外部画像をレンダリング中に直接読むのではなく、事前に作業領域へ取得する。取得成功、非ゼロサイズ、画像デコード、最低寸法を確認する。失敗素材だけ代替へ切り替える。
4. **Script segmentation**: 台本を章・Scene・字幕ブロックに分ける。長編動画を1つの巨大な処理単位にしない。
5. **Zundamon narration**: VOICEVOXずんだもんで章または字幕ブロック単位にWAVを生成する。成功済みWAVをキャッシュする。
6. **Audio duration probe**: ffprobe等で各WAVの実時間を取得し、その実測値を映像尺と字幕タイミングの基準にする。文字数から秒数を推測して固定しない。
7. **Captions**: ナレーション全文を字幕でカバーする。意味の切れ目で短く分割し、タイトル／章タイトル／小見出し／本文で文字サイズ・太さ・位置を分ける。ずんだもん・重要画像との安全領域を固定する。
8. **Scene render**: Sceneごとに個別MP4を生成する。基本条件は 1080x1920 / 30fps / 同一Video Codec / 同一Audio Codec / 同一Sample Rate / 同一Pixel Format。途中Sceneは高速presetを優先する。
9. **Scene validation**: 各Sceneの存在、非ゼロサイズ、映像・音声ストリーム、解像度、尺を機械検査する。失敗Sceneのみ再試行する。
10. **Checkpoint**: 素材、音声、字幕、Sceneレンダリング、Scene検証、結合状態を保存する。失敗時は最後の正常地点から再開する。
11. **Concat**: 全Sceneが同一条件なら concat copy を優先する。必要な場合だけ最終再エンコードする。
12. **Final mechanical QA**: final.mp4 の存在、非ゼロサイズ、映像ストリーム、音声ストリーム、1080x1920、想定尺、ffprobe正常読込を確認する。
13. **Artifact handoff**: 検査を通過したら、重いAI最終目視レビューを待たずに完成動画を先に提示する。

## Checkpoint状態

最低限、各Scene/章について次を保持する。

```text
assets_ready
voice_ready
subtitles_ready
scene_rendered
scene_validated
final_concat_done
```

成功済み成果物は後段の失敗で削除しない。

### 再実行禁止の例

映像レンダリングだけ失敗した場合、以下をやり直さない。

- 台本生成
- 情報収集
- VOICEVOX音声生成
- 正常取得済み画像の再取得
- 成功済みSceneの再レンダリング

失敗工程より前の正常成果物を再利用する。

## ずんだもん運用

- 音声はVOICEVOXずんだもんを標準とする。
- 立ち絵を使う場合、画面内の基本位置は固定する。
- 通常、笑顔、驚き、困惑、解説、考える、強調等の公式立ち絵を章単位で切り替える。
- 1枚だけを長時間表示し続けない。
- 立ち絵と字幕が重ならない安全領域を固定する。

## Scene失敗時の処理

- Sceneごとに個別タイムアウトを設定する。
- 再試行回数には上限を設ける。
- 無限リトライは禁止。
- 同一原因で連続失敗した場合は、再試行を続けずDeepSeek/NVIDIA/Qwen等の技術レビュー担当へエラー情報を渡す。
- 修正後も成功済みScene・WAV・字幕・画像は再利用する。

## AI分業

- **ChatGPT**: 全体統括、工程設計、タスク分解、採用判断、最終統合、完成動画の受け渡し。
- **DeepSeek**: 難しい原因分析、長尺動画構成レビュー、FFmpeg／レンダリング問題分析、修正案。
- **NVIDIA/Qwen等**: コードレビュー、個別エラー解析、字幕／音声同期、素材確認、構成レビュー。
- **Python/FFmpeg**: 実際の機械処理。

同じ仕事を複数AIへ重複させず、異なる専門役割に分ける。

## 有料DeepSeekの例外

ユーザーが明示承認した有料DeepSeekは、技術分析・設計・レビューのために利用できる。ただし、既存の予算・呼び出し上限、秘密値非表示、STAGING_ONLY、repository_write=false、deploy=false、publish=false、no auto top-up、no generic paid fallback等のガードは維持する。

この例外を、Runway/Fal/Descript等の動画制作SaaSへの課金許可として解釈してはならない。

## 初期のShorts構成

| 時間 | 役割 | 例 |
| --- | --- | --- |
| 0–2秒 | フック | 「9割が最初に間違える点」 |
| 2–6秒 | 課題 | 誰の何が困るか |
| 6–18秒 | 実演 | 一つの手順・比較・検証 |
| 18–26秒 | 反転 | 失敗例、例外、意外な結果 |
| 26–30秒 | 行動 | 保存、次回予告、質問 |

数値は固定ルールではなく、利用者が入力した視聴データで更新する。人気の断定やバズの保証は行わない。

## 費用・公開ゲート

- 動画制作系は無料ローカル処理を標準にする。
- 外部動画制作／編集SaaSの無料クレジットを消費しない。
- 完全無料か確認できない外部サービスは呼ばない。
- 有料DeepSeekは技術参謀の例外であり、既存の費用ガード内に限定する。
- 自動チャージは禁止。
- 本番Deploy、無断Publish、PR Merge、秘密値表示は禁止。
- 権利確認、内容レビュー、明示承認が必要な公開処理は承認前に実行しない。

データ形式の正本は [media_pipeline_plan.schema.json](../schemas/media_pipeline_plan.schema.json)。
