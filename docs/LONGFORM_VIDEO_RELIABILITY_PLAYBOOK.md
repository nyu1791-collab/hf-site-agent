# 長編AI動画制作 Reliability / Retention Playbook

**Status:** Permanent operating standard  
**Effective:** 2026-09-15 JST  
**Machine authority:** `config/longform_video_reliability_policy.json`  
**Objectives:** `config/longform_video_objectives.json`

## 1. 目的

通常のニュース・話題解説は **8〜12分を目安**にする。ただし尺を埋めるための無関係な歴史、反復、遅い読み、低情報量の背景説明は禁止する。追加時間は、その話題を理解するための事実、仕組み、影響、重要な時系列、不確実性、相反する見方、今後の注目点で稼ぐ。

同時に、長尺制作を「最後まで1回で走り切る処理」にしない。AI/API/runner/FFmpeg/素材取得のどこかが止まっても、正常な台本・音声・素材・Sceneを保持し、**最後に検証済みのCheckpointから最小単位で再開**する。

## 2. 2026-09-15に再調査した主要根拠

一次・公式資料を優先し、AIの多数決を根拠にしない。

- YouTube Help — Measure key moments for audience retention: https://support.google.com/youtube/answer/9314415
  - Intro retention、Top moments、Spikes、Dipsを実データで見る。似た長さの動画との比較を重視し、後半の強い場面を前へ移す場合も物語上の整合性を崩さない。
- YouTube Help — Impressions / CTR guidance: https://support.google.com/youtube/answer/7628154
  - タイトル・サムネの約束を冒頭で満たす。CTRだけを単独最適化せず、視聴時間・Retentionと組み合わせる。
- LangGraph official docs — Durable execution: https://docs.langchain.com/oss/python/langgraph/durable-execution
  - Persistence / checkpoint / replay-safe execution / side-effect isolationを耐障害設計の参考にする。LangGraph採用自体を義務化しない。
- Temporal official docs — Durable execution: https://docs.temporal.io/
  - 実行状態を永続化し、process/infrastructure failure後に再開する設計、Activity retry、idempotencyを参考にする。Temporal採用自体を義務化しない。
- AWS Builders Library — Timeouts, retries, and backoff with jitter: https://aws.amazon.com/builders-library/timeouts-retries-and-backoff-with-jitter/
  - Retryは負荷を増幅しうる。Timeout、bounded retry、exponential backoff + jitter、idempotencyを使い、複数Layerでの重複Retryを避ける。
- Google Cloud — Retry strategy: https://cloud.google.com/storage/docs/retry-strategy
  - 一時的な失敗だけをbounded exponential backoffで再試行し、恒久エラーは繰り返さない。
- GitHub Actions docs — concurrency / cache / artifacts / timeout: https://docs.github.com/en/actions
  - Cacheは再生成可能な高速化、Artifactは検証済み成果物・Run間受け渡し。重複Run、job timeout、cross-run resumeを明示的に扱う。
- FFmpeg / ffprobe official docs: https://ffmpeg.org/ffmpeg.html , https://ffmpeg.org/ffmpeg-formats.html , https://ffmpeg.org/ffprobe.html
  - Scene単位のMedia Contractを検査し、互換時だけconcat/streamcopy。壊れたSceneだけを隔離・再生成する。
- Adobe Premiere Pro official proxy guidance: https://helpx.adobe.com/premiere-pro/using/proxy-workflow.html
  - Proxyは重い素材の編集負荷を下げる手段。全Sceneで必須にはせず、Resource/Visual riskが高い場合に限定する。

## 3. DeepSeek Executive Supervisor 査読

2026-09-15、上記Evidenceと現行RepositoryをDeepSeek Working Executive Supervisorへ渡し、6 laneで独立査読後に統合した。

- Workflow run: `34956083092` — SUCCESS
- Artifact: `10391441454`
- Digest: `sha256:03024ae2bfce4553b5ee56a6a260d956ef37d338802c78e8cd4b6f2e36f4ecc5`
- DeepSeekはEvidenceでありAuthorityではない。最終採否はChatGPTが一次資料・現行Policy・ユーザー指示と照合して決めた。

### 採用した指摘

1. 古い **6〜10分** を削除し、8〜12分目安へ統一する。
2. 8〜12分はPadding quotaにしない。Information Density Gateとセットにする。
3. 調査・原稿はChatGPT + DeepSeekのCompact Pairへ集約し、機械工程はDeterministic Toolへ戻す。
4. `state manifest + verified checkpoint + idempotency` をResumeの前提にする。
5. Parallel writerにはLease / TTL / heartbeatを持たせ、stale writerを拒否する。
6. Retry ownerを1 Layerへ寄せ、runner + SDK + providerの多重Retry stormを禁止する。
7. Provider/API/FFmpegの無反応をJob timeoutまで放置せず、No-progress detector + bounded timeout + circuit breakerで最小Unitを停止する。
8. `COMPLETE / PARTIAL / DEFERRED_RETRYABLE / FAILED_PERMANENT` を区別し、PartialをCompleteと報告しない。
9. Final MP4はDecodeだけでなく **Fast Start (`moov` before `mdat`)** もDelivery Gateにする。
10. Proxy/previewはRisk-triggered。全Sceneへ機械的に追加しない。

### Hard Lawへ昇格しなかった提案

- 「30秒ごとに必ず新情報」など固定時間のInformation Density数値。
- Chapter promiseの固定テンプレート。
- Open loopを必ず使うこと。
- 固定Recap間隔。
- 固定Cut/Scene/Expression cadence。
- DeepSeekが提案したLease contentionやretry amplificationの普遍的な数値閾値。

これらはPlatformやChannel実測が揃うまでExperiment扱いにする。

## 4. 8〜12分を引き延ばさず作る

Script Lock前に、各Sectionが少なくとも次のどれかを増やしているか確認する。

- 新しい確認済み事実
- 仕組み・因果の理解
- 視聴者/企業/社会への影響
- 理解に必要な重要時系列
- 不確実性・反対解釈・限界
- 今後何を見れば状況が変わるか
- 初見視聴者に必要な定義

同じ結論の言い換えだけ、無関係な歴史、冗長な人物紹介、Slow narrationだけで尺を増やすSectionは削る。内容が8分に足りないなら、無理に12分へ伸ばさず、より密度の高い動画または素材量のあるテーマを選ぶ。

## 5. Retention設計

固定の「N秒ごとにCut」は使わない。Title/Thumbnail/Coverの約束を冒頭で早く満たし、Evidenceを不自然に遅らせない。SectionはSemantic Promise → Evidence / Explanation → Payoffのまとまりとして設計できるが、固定秒数にはしない。

公開後にAnalyticsがある場合だけ、Intro retention、AVD、Average % Viewed、Top moments、Dips、SpikesをScene/Chapter/Semantic Beatへ紐付ける。似た長さ・Formatで比較し、単発Viral動画から恒久Ruleを作らない。CTR単独勝ちも採用しない。

## 6. 止まらないためのExecution Contract

### State Manifest

各Missionは最低限、`mission_hash / policy_versions / stage_states / artifact_hashes / media_contract / attempt_counts / failure_class / last_progress_at / lease_owner / lease_expiry / termination_reason` を持つ。

### Atomic promotion

Sceneは `.partial.mp4` へ生成し、process exit=0、ffprobe parse、Media Contract、duration、subtitle coverageを確認した後だけatomic renameしてVerified Sceneへ昇格する。`.partial` はConcatへ入れない。

### Retry ownership

Retryは1つのOrchestration Layerが所有する。SDKが内部Retryするならその回数を把握し、上位Layerと掛け算にならないようにする。一時的Network/429/5xxだけbounded backoff + jitter。Schema error、rights block、disk不足、missing tool、cost transition等はRetryしない。

### No-progress / Circuit breaker

Progressは「Stage完了」「新しいVerified artifact」「heartbeat」「provider response completion」「ffmpeg progress」等で判定する。一定時間Progressがなければ、そのUnitだけ停止しCheckpointを保持してFailure Classを付ける。同じProvider/Route/Root Causeが続く場合はCircuitを開き、CooldownまたはFresh health evidenceまで再突入しない。

固定Timeout秒数はProvider/Sceneの実測なしにHardcodeしない。

## 7. Scene-first / Media Contract

長尺全体を1回の巨大Encodeにしない。Scene/Chapter単位でRender → Validate → Checkpoint。Concat前にcodec/profile/pix_fmt/fps/timebase/audio codec/sample rate/channel layout等を比較する。互換時だけstreamcopyし、不一致なら最小のSceneだけNormalizeして再検証する。

VOICEVOXは生成済みWAVのffprobe実時間をTimelineの正本にする。映像だけの修正で正常なVOICEVOX音声を再生成しない。

## 8. GitHub Actions / Cache / Artifact

- Cache: 失っても再生成できる高速化データ。
- Artifact / explicit checkpoint store: Runを跨いで復旧に使う検証済み成果物。
- Cache hitだけを「成功Checkpoint」とみなさない。
- Job/Process/Provider callはbounded timeout。
- 同一Mutable outputのWriterは1つ。並列化時はLeaseを使う。
- Disk/RAM圧迫時はQuality Gateを削らずParallelismを落とす。

## 9. Final Delivery Gate

完成候補は少なくとも以下を通す。

- ffprobe parse PASS
- Video/Audio stream contract PASS
- 字幕coverage 100%
- Full decode PASS
- Fast Start: `moov` before `mdat`
- 代表FrameのVisual QA
- 字幕/キャラ/説明図のSafe Zone
- 説明図の意図しない上下Driftなし
- 8〜12分目安、または短縮/延長の明示理由
- 元のユーザー依頼と現行Policyを再読して最終確認

Decode PASSだけでVisual PASSとはしない。Candidate完成直後に即納せず、一度Rereviewしてから渡す。

## 10. 削除・降格した旧ルール

- 6〜10分を現行DefaultとするRule → **削除**
- Expressionを1〜2文ごとに変える固定Cadence → **長尺Authorityから削除**。Semantic Beat優先。
- 2文を超えてExpression変更がないだけでHard Fail → **削除対象**
- Retry everything → **禁止**
- Runner/SDK/ProviderのMulti-layer retry → **禁止**
- CacheをDurable Checkpointとして扱う → **禁止**
- 全Scene mandatory proxy → **降格**
- AIを増やせば速いという前提 → **禁止**。Coordination overhead込みで判断する。

このPlaybookに今後ルールを追加する場合も、既存Ruleと重複するだけなら追加せず、既存Authorityを更新する。
