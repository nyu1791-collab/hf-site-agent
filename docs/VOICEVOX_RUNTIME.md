# VOICEVOX ローカル実行手順

更新: 2026-09-30 JST

## 現在の環境で確認した状態

- 公式 VOICEVOX Engine Linux CPU 版が `/tmp/devday-voicevox/extracted/linux-cpu-x64` に展開済み。
- 取得済み公式 `engine.7z.001` SHA-256 は `bab016a966131b89bad398e7b898f8c742617dc80a7da49bf494cd07133dbc7a` と一致。
- Engine version `0.25.2`。`/version` と `/speakers` を確認し、ずんだもん（ノーマル style id 3）と四国めたん（ノーマル style id 2）を取得できた。IDはこの実行の観測値であり、制作コードでは必ず `/speakers` から動的に選ぶ。
- 短文を実際に合成し、24 kHz mono PCM WAV、4.02秒のファイルが生成されることを確認済み。
- したがって以前の「VOICEVOX Engine が見つからず起動できない」という状態説明は失効。現在は「公式CPU版あり、API起動・話者取得・実音声合成を確認済み」が正しい。
- この作業環境ではツール実行ごとにネットワーク名前空間が分離されることがある。Engineを別実行で起動し、別実行から localhost API を呼ぶと接続できない。この場合は Engine 起動と音声・動画処理を同じシェル実行内にまとめる。

## 起動と制作処理を一つの実行にまとめる

```bash
bash scripts/with_local_voicevox.sh -- python scripts/video_creation_admission.py --runtime
```

実際の台本・レンダリングコマンドを渡せば、ラッパーが Engine を起動・疎通確認し、渡した処理が終わるまでプロセスを保持する。

```bash
bash scripts/with_local_voicevox.sh -- python scripts/synthesize_longform_voicevox.py \
  --mission-b64 /path/to/mission.b64 \
  --output-dir /path/to/voice \
  --timing-out /path/to/timing.json \
  --min-seconds 480 --max-seconds 720
```

Engineのみを対話的に起動する場合は `bash scripts/with_local_voicevox.sh` を実行し、終了は Ctrl-C。Engineは `127.0.0.1:50021` だけで待ち受ける。別ホストへポートを公開しない。

## 復旧手順

1. この文書と `config/video_creation_admission_policy.json` を読む。
2. `VOICEVOX_ENGINE_DIR` が指定されていれば、その中の実行可能な `run` を確認する。未指定ならこの環境の既知パスを確認する。
3. バイナリがあり、APIが未起動なら `scripts/with_local_voicevox.sh` で起動する。既に `/version` が応答するなら再起動せず再利用する。
4. `/version`、`/speakers` の両標準話者、短い実音声合成を確認してから制作ゲートを通す。
5. エンジンが本当にない、または起動・合成に失敗したときだけ `BLOCK_BEFORE_RENDER`。無音動画・有料TTSへの置換はしない。公式配布版の取得が必要な場合も、サイズ・容量・利用規約を確認し、このローカルCPU版を優先する。

## 公式情報

- Engine source / releases: https://github.com/VOICEVOX/voicevox_engine
- Engine usage and local HTTP API: https://github.com/VOICEVOX/voicevox_engine#readme
- Engine version and voice-library terms are separate: each voice's published terms and required credit must be followed.
