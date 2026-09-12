# 長編AI動画制作 Objectives

**Status:** Enforced standard  
**Machine source of truth:** `config/longform_video_objectives.json`  
**Reliability rules:** `config/longform_video_reliability_policy.json`  
**Playbook:** `docs/LONGFORM_VIDEO_RELIABILITY_PLAYBOOK.md`

## 最上位目標

**完成した再生可能な動画を、正常な中間成果物を壊さず、最小再作業でユーザーへ届ける。**

分析レポート、原因説明、AI同士の議論はこの目標より下位である。完成MP4が存在せず機械Gateを通っていない状態を「動画制作完了」と呼ばない。

## Hard completion gate

完成扱いには最低限以下を満たす。

- MP4が存在しsize > 0。
- ffprobeで正常に読める。
- video stream = 1以上、audio stream = 1以上。
- 1080x1920 / 30fps / yuv420p / H.264 / AAC / 48kHz。
- Missionで定義した期待duration範囲内。
- 必須Sceneが揃っている。
- Narrationに対する字幕coverage = 100%。

## Recovery goals

- 後段Sceneが失敗しても、それ以前の正常Scene再生成 = **0**。
- 映像だけ変えた場合の正常VOICEVOX音声再生成 = **0**。
- 1 Scene障害による全Pipeline再実行 = **0**。
- 後段失敗による成功Checkpoint削除 = **0**。
- `.partial`を成功Checkpointとして採用 = **禁止**。
- 最後の互換Checkpointからresumeする。

## Asset goals

- FFmpeg render中の外部画像取得 = **禁止**。
- Assetは事前download・decode検査・content hash化する。
- 1素材の失敗で全動画を落とさない。
- fallbackはAsset/Scene単位に隔離する。
- URLだけをcache identityにしない。

## Timeline goals

- 時間の正本は生成済みWAVのffprobe実測値。
- 文字数から音声時間を推定しない。
- subtitle timestampは単調増加。
- negative timestamp = **0**。
- 意図しないsubtitle overlap = **0**。
- Scene / FinalのA/V driftを必ず測る。
- driftのblocking thresholdはFixtureで校正してから固定する。

## Render goals

- 長編全体を1回の巨大encodeで作らない。
- Scene/Chapter単位でencodeする。
- concat前にMedia Contractを統一する。
- 互換時はconcat stream copyを優先する。
- final re-encodeは必要な場合だけ。
- Scene単位timeoutを持つ。
- concat writerは1つ。
- benchmark evidenceなしのScene encode並列数は最大2、default 1。

FFmpeg公式ではstream copyはdecode/encodeを行わないため高速かつ無劣化であり、concat demuxerは互換入力の連結に使える。これをScene-first設計の技術根拠とする。

## Retry goals

- 無限retry = **禁止**。
- 同一方式の追加retryは最大1回。
- Scene総attemptは最大3回。
- 同じroot causeが続いたら方法変更またはReplanへ進む。
- 有料移行・cost不明経路をretry先に使わない。

## Cost / route goals

以下はすべて **0** を維持する。

- paid video SaaS execution
- limited free creditを前提にしたvideo SaaS execution
- auto top-up
- paid fallback
- paid sibling substitution
- unknown-cost route execution

無料AI Reviewerを使う場合もexact model/route/free evidenceを実行直前に確認する。

## Delivery goal

Machine gate PASS後は、長い説明より先に完成動画を提示する。Creative qualityの最終判断はユーザーが行い、機械側は存在・再生可能性・A/V stream・resolution・duration等の最低条件に集中する。

## Continuous improvement

速度やdriftに根拠のない数値目標を置かない。まずbaselineを計測し、Fixtureとlast-known-goodを比較してからthresholdを昇格させる。

必須観測値:

- voice / visual / scene cache hit rate
- reused / rerendered scene ratio
- wasted encode seconds
- checkpoint restore seconds
- total render wall time
- p50/p95 scene render time
- peak disk bytes
- asset fallback count
- external request count
- retry count by root cause
- final A/V drift

これらのHard Goalは `.github/workflows/longform-video-preflight.yml` でも静的に検証し、目標の重要値が意図せず緩和された場合はCIを失敗させる。
