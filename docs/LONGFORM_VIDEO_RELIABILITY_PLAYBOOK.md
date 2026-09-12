# 長編AI動画制作 Reliability Playbook

**Status:** Permanent operating standard  
**Effective:** 2026-09-12 JST  
**Scope:** 4〜10分以上の日本語解説・ニュース動画を、VOICEVOXずんだもん + Python/FFmpeg を中心に、無料動画制作経路で安定生成するための恒久基準。

この文書は `docs/MEDIA_PIPELINE.md` を補強する長編動画の耐障害性・効率・再開性の正本である。候補調査メモは `docs/LONGFORM_VIDEO_RELIABILITY_RESEARCH.md` に残すが、**実運用では本Playbookを優先する。**

---

## 1. 証拠の優先順位

長編動画パイプラインを変更するときは、次の順で根拠を重く扱う。

1. **公式仕様** — FFmpeg / ffprobe / VOICEVOX / GitHub Actions 等の一次資料。
2. **現行コードで再現できる事実** — 実際の関数、workflow、ffprobe結果、exit code。
3. **成熟したOSSの実装・障害事例** — MoneyPrinterTurbo、ShortGPT等。
4. **複数AIレビューの一致** — 技術レビュー補助。公式仕様や再現可能なコード事実より上位には置かない。
5. **個人の経験談・コミュニティ投稿** — 仮説発見には使うが、それだけで恒久仕様にしない。

**AI多数決は証拠の代わりにならない。** AIが一致しても、機械検証方法がない提案は原則としてP0ルールへ昇格しない。

---

## 2. 今回参照した主要資料

### 公式

- FFmpeg Documentation — https://ffmpeg.org/ffmpeg.html
  - streamcopy (`-c copy`) はdecode/encodeを行わず高速・無劣化。filterを通す工程だけencodeする。
- FFmpeg Formats Documentation — https://ffmpeg.org/ffmpeg-formats.html
  - concat demuxer / segment muxer。concat前に互換ストリームであることを機械確認する。
- ffprobe Documentation — https://ffmpeg.org/ffprobe.html
  - container / streamの機械可読検査の正本。
- FFmpeg Filters Documentation — https://ffmpeg.org/ffmpeg-filters.html
  - subtitles/libass、audio filter等の仕様。
- VOICEVOX ENGINE — https://github.com/VOICEVOX/voicevox_engine
  - `/audio_query` → `/synthesis`、`/speakers`、engine version、speaker/style IDを明示して扱う。
- GitHub Actions — https://docs.github.com/en/actions
  - cache、artifact、concurrency、job timeoutを目的別に分離する。

### OSS / 実運用

- MoneyPrinterTurbo — https://github.com/harry0703/MoneyPrinterTurbo
  - script → audio → subtitle → materials → video の段階分離、task directory、FFmpeg readiness、素材cache、字幕復旧、再encoding削減、音声安定化。
- ShortGPT — https://github.com/RayVentura/ShortGPT
  - audio / footage / captions / asset preparationを分離し、JSON/Editing Markupを中間表現にする考え方。

### 制作者・開発者の公開運用例から採った仮説

コミュニティ事例では以下が繰り返し現れたため、公式/コード根拠と一致するものだけ採用した。

- **Project stateをJSON等で持ち、停止/再開可能にする。**
- **単一segmentだけ再生成可能にする。** 全動画を再生成しない。
- **TTS後のtime-coded transcript / 実音声時間を編集計画の基準にする。**
- **FFmpegを最終組立の決定論的レイヤーに置く。**
- 「モデル選択」より「workflow glue / orchestration」が障害点になりやすい。
- 長尺TTSは自由長なので、固定字幕slotへ無理に押し込むとdriftが蓄積しやすい。

コミュニティ情報は補助証拠であり、単独では採用根拠にしない。

---

## 3. AI Councilの実績と限界

無料Councilは課金を避けたまま複数回実行した。**有料fallback・auto top-up・動画レンダリングは0。**

### Run 34679437376

- DeepSeek R1 `:free`: endpoint廃止の404。paid siblingへは移行しなかった。
- NVIDIA Nemotron系: **SUCCESS / reported cost 0**。
- Qwen `:free`: endpoint廃止の404。paid siblingへは移行しなかった。

### Run 34679637856

- NVIDIA Nemotron Ultra: **SUCCESS / reported cost 0**。
- Dots: EMPTY。
- Ling: free endpoint廃止の404。

### Run 34679770909

- NVIDIA Ultra: EMPTY。
- Gemma 4 `:free`: upstream shared pool 429。
- Nex N2.5 Pro `:free`: EMPTY / reported cost 0。
- **success_count=0**。複数AI合意は成立しなかった。

### Councilから確実に採れること

独立した2回の成功NVIDIAレビューは、公式資料・現行コード監査と同じ方向を指した。

- destructive resetはP0。
- stale resume APIはP0。
- file size中心のreuse判定は弱い。
- `latest` tagは再現性を落とす。
- hardcoded historical artifact/runは一般的なcheckpoint復旧にならない。
- per-process / per-scene watchdogが必要。
- content-addressed cache、atomic output commit、preflight、strict media contract、generic restoreを優先。

ただし、**「複数ベンダーAIが合意した」とは記録しない。** 無料endpointは変動が大きく、実行直前のavailability/price/rate-limit検査が必須であること自体を今回の重要知見とする。

---

## 4. 永続する非交渉ルール

### 4.1 動画制作経路

- 標準は **VOICEVOXずんだもん + Python + FFmpeg + ffprobe**。
- Runway / Fal / Descript / VEED / HeyGen / Higgsfield等の限定無料後に課金へ移る動画制作SaaSは、接続済み・無料残高ありでも長編制作経路に選ばない。
- `scripts/media_agent_runtime.py` に残るDescript/Fal/Runway分岐は**legacy/general media route**として扱い、長編動画では無効。長編側のpolicy guardを優先する。
- 完全無料がfresh evidenceで確認できない外部経路はfail closed。
- free endpointが404/429/EMPTYになっても、同名paid sibling、random paid-capable router、auto top-upへ移行しない。

### 4.2 再生成の禁止

- Scene Nの失敗でScene 1..N-1を再生成しない。
- 映像だけ変更したとき、正常VOICEVOX WAVを再生成しない。
- 1画像の失敗で全動画を失敗させない。権利・安全の問題を除き、Scene単位fallbackを使う。
- 成功済みcheckpointを後段の失敗で削除しない。
- 完全初期化は明示的`clean`操作だけに限定する。

### 4.3 時間の正本

- 文字数から音声秒数を決めない。
- **生成済みWAVのffprobe実測duration**を字幕・Scene尺の正本にする。
- subtitle timestampは単調増加、negative 0、意図しないoverlap 0。
- 最終字幕endとnarration endの差を計測する。

---

## 5. 現行コードで確認済みのP0/P1

### P0-1: destructive reset

現行base rendererの `reset_workdirs()` は通常開始時に `audio / images / poses / composites / clips / scene_ass` を削除する。

**採用方針:** 通常実行はnon-destructive。`--clean` 等の明示操作だけ全消去を許可する。

### P0-2: stale resume contract

`resume_zundamon_news_longform.py` が旧API名 `make_scene()` / `write_ass()` を期待する一方、現行baseは `render_scene()` / `write_scene_ass()` / `write_global_ass()` を中心にしている。

**採用方針:** normal / robust / resume / recoveryが同じstable libraryを呼ぶ。CIでcheckpoint restore contract testを持つ。

### P0-3: prohibited video SaaS route remains in generic runtime

一般media runtimeにはDescript/Fal/Runway選択ロジックが残る。

**採用方針:** 長編動画では機械可読policyで禁止し、longform実行前guardで拒否する。将来generic runtimeを整理するときも、長編policyを弱めない。

### P1-1: weak cache identity

存在・最低file sizeだけでは、古い台本、別VOICEVOX設定、別codec成果物を誤reuseできる。

**採用方針:** content-addressed key + manifest一致が必要。

### P1-2: unpinned toolchain

`voicevox/voicevox_engine:cpu-latest` は同じMissionでも将来内容が変わりうる。

**採用方針:** 検証済みtag/digestへpinし、FFmpeg/ffprobe/VOICEVOX versionをmanifestへ保存する。

### P1-3: hardcoded resume source

特定historical run/artifactに固定したresumeは一般復旧ではない。

**採用方針:** `mission_hash + artifact manifest compatibility` で復旧元を選ぶ。

### P1-4: process watchdog不足

Job timeoutだけでは1つのFFmpeg/TTS processが長時間資源を保持する。

**採用方針:** asset / TTS / scene encode / concatごとにbounded timeoutを持つ。

---

## 6. Content-addressed checkpoint contract

### Voice key

```text
SHA256(
  normalized_text
  + speaker_uuid/style_id
  + speedScale/intonationScale/volumeScale
  + outputSamplingRate
  + VOICEVOX_engine_version_or_digest
  + voice_contract_version
)
```

### Visual key

```text
SHA256(
  downloaded_bytes_sha256
  + normalization_version
  + crop/fit/geometry_contract
  + color/pixel_contract
)
```

URLだけをcache keyにしない。同じURLの内容が変わる可能性がある。

### Subtitle key

```text
SHA256(
  narration_text
  + subtitle_chunks
  + measured_chunk_durations
  + style_contract_version
  + font_identity
)
```

### Scene key

```text
SHA256(
  visual_hash
  + pose_hash
  + audio_hash
  + subtitle_hash
  + scene_layout_contract
  + renderer_version
  + ffmpeg_media_contract
)
```

### Final concat key

```text
SHA256(ordered_scene_hashes + concat_contract_version)
```

**key不一致をreuseしない。key一致かつ機械QA済み成果物は再生成しない。**

---

## 7. Atomic Scene Commit

各Sceneは次の順でのみ「成功済み」に昇格する。

1. `scene_XX.partial.mp4` へ書く。
2. subprocess exit code = 0。
3. file exists / size > 0。
4. ffprobeでstream/containerを読む。
5. Scene Media Contractを検査。
6. audio/video duration差とsubtitle endを計測。
7. 全PASS後だけatomic renameで `scene_XX.mp4` にする。
8. `scene_XX.mp4` のSHA-256と検査結果をmanifestへ記録。

`.partial` はcheckpointとして絶対に採用しない。

---

## 8. Scene Media Contract

concat copy前に全Sceneで最低限次を比較する。

### Video

- `codec_name`
- `codec_tag_string`（必要に応じて）
- `profile`
- `width = 1080`
- `height = 1920`
- `pix_fmt = yuv420p`
- `r_frame_rate`
- `avg_frame_rate`
- `time_base`
- `start_time` の異常有無
- stream count

### Audio

- `codec_name = aac`
- `sample_rate = 48000`
- `channels = 2`
- `channel_layout` が期待値と一致
- `time_base`
- stream count

### Container / duration

- duration > 0
- ffprobe parse成功
- expected scene durationとの乖離を記録

**同じ `.mp4` 拡張子であるだけではconcat互換と判定しない。**

---

## 9. A/V・字幕同期の扱い

過去の候補値 `Scene <= 80ms / Final <= 150ms` は、複数AIレビューでも「根拠を実測で校正すべき」と判断された。

したがって恒久ルールは次のとおり。

- **driftは必ず測る。**
- 初期値を絶対基準として固定しない。
- 代表fixtureで、人間が違和感を感じ始める範囲と実測値を集めてblock/warn thresholdを校正する。
- threshold変更はpolicy versionを上げ、過去renderと比較可能にする。
- 長尺ではScene単位driftだけでなく**累積drift**を記録する。

これにより「根拠のない厳しすぎる閾値で正常動画を止める」「緩すぎて字幕ズレを通す」の両方を避ける。

---

## 10. Timeline Manifest = 編集の機械的正本

自然言語の指示を直接FFmpeg commandへ変換しない。必ず中間manifestへ落とす。

```json
{
  "mission_hash": "sha256:...",
  "renderer_version": "...",
  "toolchain": {
    "ffmpeg": "...",
    "ffprobe": "...",
    "voicevox": "..."
  },
  "scenes": [
    {
      "id": "scene_01",
      "narration_hash": "...",
      "visual_hash": "...",
      "subtitle_hash": "...",
      "scene_hash": "...",
      "start": 0.0,
      "end": 21.384,
      "validated": true
    }
  ]
}
```

目的:
- Scene順序取り違えを検出。
- 後半素材が冒頭へ出る等のtime-line汚染を防止。
- 変更されたSceneだけinvalidate。
- provenanceと復旧判断を同一manifestで行う。

---

## 11. Preflight — Scene 1より前に壊す

動画renderを始める前に最低限を確認する。

- `ffmpeg` executable path / version
- `ffprobe` executable path / version
- `libx264` encoder
- `aac` encoder
- `ass` / `subtitles` filter と libass
- Noto CJK等の指定font
- output directory write可否
- free disk capacity
- VOICEVOX `/version`
- `/speakers` に「ずんだもん」と期待styleが存在
- Mission JSON parse / schema / scene count
- longform policyの禁止サービスがrouteに含まれない

Missing dependencyをScene 8やScene 11で初めて発見する設計は禁止。

---

## 12. Retry taxonomy

### 同じ方法で再試行可能

- 一時的network timeout
- 429 / 5xx（**完全無料かつfresh evidenceがある経路のみ**）
- 一時的download interruption

同じ方法のretryはbounded。無限retry禁止。

### 方法を変更して再試行

- CDNがAVIF等を返しstill-image inputと不整合 → PNG normalize
- 1画像だけ404/403 → Scene単位placeholder / 別の承認済み素材
- encoder compatibility mismatch → Sceneを正規media contractへ再encode

### retryしない

- Mission/schema不正
- subtitle full coverage不一致
- disk不足
- required font/filter/encoder不在
- cache/mission hash mismatch
- rights blocked
- positive cost / paid transition / auto top-up要求

retry exhausted後は必ずfailure。最後に`sleep`しただけでsuccess扱いしない。

---

## 13. Resource governor

長編では過剰並列が速いとは限らない。

- metadata / asset probe: 小さな並列可。
- asset download: bounded parallelism。
- VOICEVOX CPU synthesis: bounded parallelism。
- FFmpeg Scene encode: **最初は1〜2並列以下を基準にbenchmarkして決める**。
- concat / final QA: single writer。
- disk free bytesをScene開始前に再確認。
- checkpoint済み成果物は残し、temp / `.partial` だけ安全に掃除する。

並列数は固定の「正解」にせず、runner CPU/RAM/disk IOのbenchmarkから決める。

---

## 14. GitHub Actions — Cache / Artifact / Concurrency

### Cache

content hashで安全に再利用できるもの向け。

- dependency cache
- immutable tool/model cache
- content-addressed voice/asset cache（サイズ・retention設計を満たす場合）

### Artifact

run固有のcheckpoint/evidence受け渡し向け。

- timeline manifest
- Scene MP4
- validation JSON
- failure packet
- final.mp4

### Concurrency

- 長時間renderは、健康なcheckpointを失わないため基本 `cancel-in-progress: false`。
- staleな軽量検証やreviewは用途別に判断。
- workflow名/mission hashを含むgroupにし、無関係runを誤cancelしない。
- Scene matrixを使う場合、一Scene失敗で他の正常Sceneまでcancelする `fail-fast` は避ける方向で設計する。

---

## 15. Toolchain provenance

再現可能な動画には、素材だけでなくtoolchainのprovenanceが必要。

Manifestへ記録:

- git commit SHA
- renderer version
- FFmpeg version
- ffprobe version
- VOICEVOX engine version / container digest
- Zundamon speaker UUID / style ID
- font identity
- OS / runner image（可能な範囲）
- mission hash
- policy version

`latest` tagはproduction-quality renderでは原則禁止。検証済みversion/digestへpinする。

---

## 16. 無料AI reviewerのfreshnessルール

今回、公開ページでfreeと確認したendpointでも、数分以内に404/429/EMPTYが起きた。

恒久ルール:

1. exact model IDが `:free` である。
2. 実行直前にcatalog/priceを確認。
3. request後にusage/cost evidenceが取れる場合はpositive costを拒否。
4. 404でpaid siblingを案内されても自動移行しない。
5. 429でprovider routingを案内されても、cost/routeが不明なら自動route変更しない。
6. EMPTYは成功レビューとして数えない。
7. **複数AI合意と呼ぶには2つ以上の独立成功結果が必要。**
8. 2つ未満なら、公式資料/コード監査を正本にし、AI回答は補助意見としてだけ残す。

---

## 17. 新しいReliability SLO

以下を長編動画基盤の目標とする。

1. **Healthy work preservation:** Scene Nで失敗した際、Scene 1..N-1の正常成果物の再生成 = 0。
2. **Silent success:** 空/壊れたMP4、音声欠落、ffprobe不能をsuccess扱い = 0。
3. **Cache correctness:** hash/manifest不一致成果物のreuse = 0。
4. **Subtitle coverage:** narrationに対する字幕coverage = 100%。
5. **Scene contract:** concat対象Sceneのmedia contract pass = 100%。
6. **Failure isolation:** 1素材障害が無関係Sceneを破壊 = 0。
7. **Recovery fixture:** 任意の1Sceneを人工失敗させ、成功済みSceneを再encodeせず復旧できるCI testを保持。
8. **Paid video SaaS:** 使用・課金 = 0。
9. **Auto paid transition:** 0。
10. **Delivery readiness:** final mechanical QA PASS後、不要な重いAIレビューをdelivery blockerにしない。

A/V driftのblock値は、fixture実測後にpolicyへ昇格するまでは**測定必須・閾値暫定**とする。

---

## 18. Efficiency KPI

Reliabilityとは分けて測る。

- voice cache hit rate
- visual cache hit rate
- scene cache hit rate
- rerendered_scene_count / total_scene_count
- reused_scene_count / total_scene_count
- wasted_encode_seconds
- checkpoint_restore_seconds
- total_render_wall_seconds
- p50 / p95 scene render seconds
- peak disk bytes
- max parallel encodes
- visual fallback count
- external request count

**cache hit rateはSLOではなくKPI。** 新規動画ではhit 0%でも正常だからである。

---

## 19. 実装優先順位 Top 10

1. destructive `reset_workdirs()` をnon-destructive defaultへ変更。
2. normal/robust/resume/recoveryのstable shared API化。
3. content-addressed manifest + cache identity。
4. `.partial -> ffprobe -> atomic rename` Scene commit。
5. strict ffprobe Scene Media Contract。
6. static/runtime Preflight。
7. VOICEVOX / FFmpeg toolchain pin + provenance。
8. generic checkpoint restore（hardcoded historical run排除）。
9. per-process timeout + retry taxonomy + structured failure packet。
10. synthetic recovery CI fixture + KPI collection。

この順は「品質向上」より先に**再実行コストと壊れ方を小さくする**ための順番である。

---

## 20. 採用しない設計

- 1本の巨大FFmpeg commandで長編全体を作る。
- 失敗時に台本・音声・全Sceneを最初から再生成する。
- URL / filename / file sizeだけでcache互換と判定する。
- `.partial` を成功済みとして扱う。
- `latest` toolchainをproductionの再現性基準にする。
- random free-model routerを「exact reviewer」として扱う。
- free endpoint消失時にpaid siblingへ自動移行する。
- AIが「多分大丈夫」と言ったことをffprobe/testの代わりにする。
- final mechanical QA後に、目的のない重いAIレビューでユーザーへのdeliveryを遅らせる。

---

## 21. 最小堅牢アーキテクチャ

```text
ChatGPT Commander
  │
  ├─ Research / Script / Rights lock
  │
  ├─ timeline_manifest.json
  │
  ├─ Asset materializer + validator + per-asset fallback
  │
  ├─ VOICEVOX Zundamon
  │    └─ content-addressed WAV cache + actual duration probe
  │
  ├─ Subtitle builder
  │    └─ full-coverage + monotonic-timestamp checks
  │
  ├─ Scene renderer N
  │    └─ .partial -> ffprobe contract -> atomic commit
  │
  ├─ Checkpoint manifest
  │
  ├─ concat copy
  │
  └─ Final mechanical QA -> artifact handoff
```

AI specialist reviewは失敗原因の分析と設計レビューへ使う。FFmpeg/VOICEVOXの決定論的機械処理をAIチャットに置き換えない。

---

## 22. 変更管理

このPlaybookを弱める変更は、少なくとも次を伴う。

- 変更理由
- 公式/再現可能な根拠
- 影響するSLO/KPI
- rollback方法
- 新旧policy version
- synthetic recovery test結果

Runway/Fal/Descript等の動画制作SaaSを再許可する変更、paid fallback、auto top-up、本番Publish/Deploy/PR Mergeは、通常の技術改善として勝手に変更してはならない。
