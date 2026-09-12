# 長編AI動画制作・耐障害性リサーチ（審議前の候補知見）

更新日: 2026-09-12 JST

> この文書は、公式資料・実運用OSS・公開障害事例から得た「採用候補」を集めた一次調査メモである。  
> **ここに書かれた候補を無条件で本番仕様にしない。** DeepSeek / NVIDIA / Qwen の独立レビューと、現行コードとの照合を行い、採用判断後に `docs/MEDIA_PIPELINE.md` / 長編Reliability Playbookへ昇格する。

## 0. 固定境界

- 動画そのものの制作はまだ開始しない。
- Runway / Fal / Descript / VEED / HeyGen / Higgsfield 等の「少量無料後に課金へ移る動画制作SaaS」は使わない。
- 外部サービスは、完全無料・自動課金なし・有料Fallbackなしを確認できる場合だけ使用可能。
- 標準音声はローカル VOICEVOX ずんだもん。
- 機械処理は Python / FFmpeg / ffprobe を中心にする。
- main直接Push、PR Merge、Deploy、Publish、Secrets変更・表示は禁止。

---

## 1. 調査した一次資料・実運用資料

### 公式一次資料

1. FFmpeg Documentation — Streamcopy / transcoding
   - https://ffmpeg.org/ffmpeg.html
   - `-c copy` はdecode/encodeをせず高速・無劣化。filterが必要な箇所だけencodeする。
2. FFmpeg Formats Documentation — concat demuxer / segment muxer
   - https://ffmpeg.org/ffmpeg-formats.html
   - concat demuxerは同一互換ストリームを順番に読み込める。
   - segment muxerは `.ffconcat` リスト出力が可能。
   - segment_timeはkeyframe条件により分割時刻が厳密でない場合がある。
3. ffprobe Documentation
   - https://ffmpeg.org/ffprobe.html
   - stream/containerの機械可読検査を行える。
4. FFmpeg Filters Documentation
   - https://ffmpeg.org/ffmpeg-filters.html
   - 字幕/libass、音声loudness等はfilter段階で扱う。
5. VOICEVOX ENGINE
   - https://github.com/VOICEVOX/voicevox_engine
   - `/audio_query` → `/synthesis`、`/speakers`によるstyle id確認。
   - 標準出力は24kHz系のため最終パイプラインで48kHzへ正規化する設計が有効。
   - `/cancellable_synthesis` は切断時に計算資源を解放するが実験的機能。
6. GitHub Actions Documentation
   - https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency
   - https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching
   - https://docs.github.com/en/actions/how-tos/manage-workflow-runs/download-workflow-artifacts
   - concurrency / cancel-in-progress / cache key / artifactsを用途別に使い分ける。

### 実運用OSS・公開障害例

1. MoneyPrinterTurbo
   - https://github.com/harry0703/MoneyPrinterTurbo
   - pipeline stageを `script -> terms -> audio -> subtitle -> materials -> video` と分離し、stage単位停止・task directory・明確なexit codeを採用。
   - 最近の改善でもFFmpeg readiness、素材キャッシュ、字幕復旧、再encoding削減、音声安定性が重点項目。
2. ShortGPT
   - https://github.com/RayVentura/ShortGPT
   - 長尺用Engineでaudio / footage / caption timing / asset preparationを分離。
   - JSON/Editing Markupにより編集計画を宣言的なblockへ分ける。
3. MoneyPrinterTurbo公開Issue群
   - FFmpeg結合が終了しない、外部素材の404/403、壊れた素材、環境依存FFmpeg、字幕/映像の時系列ずれ、音声ノイズ、retryが誤って成功扱いになる等の実運用障害を確認。

---

## 2. 現行リポジトリで確認できた重要ギャップ

### P0-A: 通常レンダラーが正常キャッシュを破棄する

`scripts/render_zundamon_news_longform.py` の `reset_workdirs()` は開始時に `audio / images / poses / composites / clips / scene_ass` を削除して作り直す。

これは「失敗しても成功済みScene/WAV/画像を残す」という長編固定方針と矛盾する。

**候補対策**
- 通常実行では削除しない。
- `--clean` のような明示操作だけ完全初期化を許可。
- run固有directory + content-addressed cacheへ分離。

### P0-B: resume経路と現行base APIに不整合疑い

`scripts/resume_zundamon_news_longform.py` は `base.make_scene()` / `base.write_ass()` を呼ぶが、現行baseでは `render_scene()` / `write_scene_ass()` / `write_global_ass()` が中心。

**候補対策**
- Resumeをbaseのprivate/旧APIへ依存させない。
- 共通stable libraryを1個だけ持ち、通常/robust/resume/recoveryから同じ関数を呼ぶ。
- CIで「resume module import + representative checkpoint restore」の契約テストを必須化。

### P1-C: reuse判定がサイズ中心で、内容互換性を証明していない

存在・最低ファイルサイズだけでは、古い台本・別VOICEVOX設定・別codecの成果物を誤再利用しうる。

**候補対策**
- input hash + renderer version + toolchain versionでreuseを決める。

### P1-D: Job全体timeoutはあるがScene単位timeoutが弱い

FFmpeg結合/encodeがハングするとJob timeoutまで資源を保持する可能性がある。

**候補対策**
- subprocess単位timeout。
- Scene timeout / TTS timeout / asset timeoutを別設定。
- timeout後のpartial fileは採用しない。

### P1-E: `voicevox_engine:cpu-latest` が再現性を弱める

同じMissionでも後日`latest`の内容が変わる可能性がある。

**候補対策**
- 検証済みversion/tag/digestを固定。
- FFmpeg/ffprobe/VOICEVOX versionをrender manifestに残す。

### P1-F: Resume Workflowが過去run IDを固定

再開workflowが特定artifact/runに固定されると一般的なcheckpoint復旧にならない。

**候補対策**
- 明示`checkpoint_artifact_id/run_id` input、またはcompatibility manifestで選択。
- 「最新だから採用」ではなくMission hash一致で採用。

---

## 3. 採用候補: Content-addressed checkpoint

各成果物を「名前」ではなく入力内容で識別する。

### Voice cache key

`SHA256(text + speaker_uuid/style_id + speedScale + intonationScale + volumeScale + outputSamplingRate + VOICEVOX_engine_version)`

### Visual cache key

`SHA256(downloaded_bytes + normalization_version + target_geometry)`

URLだけでは同じURLの中身が変更され得るため、最終的にはbytes SHA-256を正本にする。

### Subtitle cache key

`SHA256(scene_text + subtitle_chunks + style_contract_version + measured_audio_durations)`

### Scene cache key

`SHA256(visual_hash + pose_hash + audio_hash + ass_hash + renderer_version + ffmpeg_contract)`

### Final concat key

`SHA256(ordered_scene_hashes + concat_contract_version)`

**原則:** key不一致の成果物を再利用しない。key一致の正常成果物は後段失敗で削除しない。

---

## 4. 採用候補: Atomic Scene Commit

1. `scene_07.partial.mp4` へ出力。
2. ffmpeg終了codeを確認。
3. ffprobeでVideo/Audio/解像度/fps/codec/pix_fmt/sample rate/channels/durationを検査。
4. expected audio durationとのdriftを検査。
5. 全PASS後だけatomic renameで `scene_07.mp4` に昇格。
6. `.partial` はcheckpointとして扱わない。

これにより中断・disk full・killされた壊れたMP4を「成功済み」と誤認しない。

---

## 5. 採用候補: Scene Media Contract

concat copy前に全Sceneで最低限次を一致確認する。

- video codec
- width / height = 1080 / 1920
- fps（r_frame_rate + avg_frame_rate）
- pix_fmt = yuv420p
- profile / level（必要範囲）
- time_base / start_timeの異常
- audio codec = AAC
- sample_rate = 48000
- channels / channel_layout = stereo
- stream count
- duration > 0

**重要:** 拡張子が同じだけではconcat互換と見なさない。

---

## 6. 採用候補: A/V drift budget

レンダリング成功だけでは品質保証にならない。長尺では微小な差がScene数に応じて蓄積し得る。

候補目標:
- Scene audio-video absolute drift: <= 80 ms（初期目標。実測で調整）
- Final cumulative drift: <= 150 ms
- subtitle last-end と narration end の差: <= 100 ms
- negative / overlapping / non-monotonic subtitle timestamp: 0件

文字数推定ではなく、VOICEVOX生成WAVのffprobe実測durationを正本にする。

---

## 7. 採用候補: Timeline manifestを編集の正本にする

`timeline_manifest.json` の例:

```json
{
  "mission_hash": "...",
  "renderer_version": "...",
  "toolchain": {"ffmpeg": "...", "voicevox": "..."},
  "scenes": [
    {
      "id": "scene_01",
      "narration_hash": "...",
      "visual_hash": "...",
      "subtitle_hash": "...",
      "start": 0.0,
      "end": 21.384,
      "validated": true
    }
  ]
}
```

ShortGPTのJSON/Editing Markup型の考え方と同様、AIの自然言語指示と機械実行を直接結びつけず、宣言的な中間表現を1枚挟む。

これにより「後半で使う画像が冒頭に出る」等の時系列取り違えを機械的に検出しやすくする。

---

## 8. 採用候補: Toolchain preflight

動画生成を開始する前に以下をfail-fast検査する。

- `ffmpeg` 実体path/version
- `ffprobe` 実体path/version
- `libx264` encoder
- `aac` encoder
- `ass/subtitles` filter + libass
- Noto CJK指定fontの存在
- free disk容量
- output directory write可否
- VOICEVOX `/version`
- `/speakers` 内のずんだもん style id
- Mission JSON schema
- expected scene count

Missing dependencyをScene 11で初めて知る構成にしない。

---

## 9. 採用候補: Retry分類

同じ失敗を無条件で繰り返さない。

### Retry可（例）
- 一時的network timeout
- HTTP 429/5xx（ただし外部完全無料経路のみ、上限あり）
- temporary CDN download interruption

### Method change / fallback
- image decoderが形式非対応 → PNG normalize
- 画像取得404/403 → scene単位placeholder/別承認素材

### Retry不可
- JSON/schema不正
- subtitle coverage mismatch
- codec contract不一致
- disk不足
- font/filter/encoder不在
- Mission hash mismatch
- positive cost / paid transition

retry exhausted後は必ずnon-zero失敗。最後の`sleep`等で成功扱いにしてはいけない。

---

## 10. 採用候補: Disk / RAM / parallelism governor

長編では「並列にすれば速い」とは限らない。FFmpeg encodeを過剰並列にするとRAM・disk IO・CPUが競合する。

候補:
- asset download / metadata probe: 小さな並列
- VOICEVOX CPU synthesis: bounded parallelism
- FFmpeg scene encode: runner CPU数に応じた低並列（初期1-2）
- concat/final QA: 単一writer
- Sceneごとのestimated bytesとfree diskを監視
- checkpoint済み成果物を残し、`.partial` / tempのみ安全に削除

GitHub Actions matrixを使う場合は`strategy.max-parallel`と`fail-fast`を明示する。Scene独立性を保つなら、一Scene失敗で他Sceneをキャンセルしない方針を検討する。

---

## 11. 採用候補: GitHub Actionsの役割分離

### Cache

immutableまたはcontent hashで識別できる再利用物向け。例: VOICEVOXモデル/依存cache、content-addressed voice cache。

### Artifact

runのcheckpoint、Scene成果物、manifest、failure evidenceの受け渡し向け。

### Concurrency

- 高コストな長編render: checkpointを失う可能性があるため原則`cancel-in-progress: false`
- 軽量validation / stale review: 必要なら`true`
- workflow名を含むgroupにし、無関係workflowを誤cancelしない。

---

## 12. 採用候補: Audio品質contract

- VOICEVOX生成物の実測durationを基準にする。
- Scene audioは48kHz / stereoへ統一。
- AAC bitrateを固定。
- clipping / peak / silence異常を軽量検査する。
- loudness正規化を行う場合は、目的値と処理versionをmanifestへ記録し、毎回不必要な再encodeをしない。
- 明示的なポーズが必要なら「句読点から推定」せず、timeline上のsilence eventとして扱う。

---

## 13. 採用候補: Subtitle品質contract

- narration全文coverage = 1.0
- 1字幕2行以内を基本
- 表示幅/安全領域を固定
- timestampは単調増加
- negative timestamp 0
- unintended overlap 0
- last subtitle endとaudio endのdriftを検査
- 固有名詞はVOICEVOX読みと表示文字列を分離可能にする
- fontをOS任せにせず、使用font family/fileをmanifestへ記録

---

## 14. 新しい目標案（Council審議対象）

### Reliability SLO

1. **再開率:** Scene Nで故障してもScene 1..N-1の再生成 = 0。
2. **Failure blast radius:** 1 Sceneの素材失敗で全動画失敗 = 0（権利/安全に関わる場合を除く）。
3. **Silent success:** 壊れた/空成果物を成功扱い = 0。
4. **Cache correctness:** hash不一致成果物の再利用 = 0。
5. **A/V sync:** final cumulative drift <= 150ms（暫定）。
6. **Subtitle coverage:** 100%。
7. **Deterministic contract:** 全Sceneが同一media contractをPASSしてからconcat。
8. **Recovery test:** 人工的に1 Sceneを失敗させ、成功済み成果物を再利用して復旧できるCI fixtureを持つ。
9. **Cost:** 動画制作SaaS課金 0円、自動課金 0、有料fallback 0。
10. **Delivery readiness:** final ffprobe機械QA PASS後は重いAI最終目視を待たない。

### Efficiency KPI

- voice cache hit rate
- visual cache hit rate
- scene cache hit rate
- rerendered_scene_count / total_scene_count
- wasted_encode_seconds
- checkpoint_restore_seconds
- total_render_wall_seconds
- max_parallel_encodes
- peak disk bytes
- fallback_visual_count

---

## 15. Councilへ問う論点

1. 上記候補のうちP0として即採用すべきものは何か。
2. `reset_workdirs()`の破壊的初期化をどう置換するか。
3. stale resume APIをどう一本化するか。
4. hash keyに不足フィールドはないか。
5. concat copyの互換contractは十分か。
6. drift SLO 80ms/150msは初期基準として妥当か。
7. GitHub Actions cache/artifact/concurrencyの分担は妥当か。
8. VOICEVOX version/style/parameter cache keyは十分か。
9. 長編で最も起きやすい「成功扱いなのに品質が壊れる」問題は何か。
10. まず実装するTop 10と、その検証方法を順位付きで出すこと。

---

## 16. 審議後の昇格ルール

Councilの意見はAIの回答であり、それ単独を事実とはみなさない。

採用条件:

1. 公式資料または再現可能なOSS実装/障害例と整合する。
2. 現行コードに適用可能。
3. 無料動画制作方針を破らない。
4. 成功済み成果物を壊さない。
5. 新たな単一障害点を増やさない。
6. 機械検証方法がある。

この条件を通ったものだけ、恒久仕様へ追加する。
