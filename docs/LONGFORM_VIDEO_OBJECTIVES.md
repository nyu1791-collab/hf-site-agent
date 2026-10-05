# 長編AI動画制作 Objectives

**Status:** Enforced standard  
**Effective:** 2026-09-15 JST  
**Machine source of truth:** `config/longform_video_objectives.json`  
**Reliability:** `config/longform_video_reliability_policy.json`  
**Playbook:** `docs/LONGFORM_VIDEO_RELIABILITY_PLAYBOOK.md`

## 最上位目標

**8〜12分を通常目安としつつ、無関係な歴史・反復・遅い読み・低情報量Fillerで尺を埋めず、途中障害が起きても正常成果物を壊さず再開できる完成MP4を届ける。**

8〜12分はPadding quotaではない。内容が薄い場合は引き延ばさない。追加尺は確認済み事実、仕組み、影響、重要な時系列、不確実性、相反する見方、今後の注目点、理解に必要な定義で作る。

## AI構成

通常の情報収集と原稿作成は **ChatGPT + DeepSeek** を1つの判断Stageとして扱う。調査AI・原稿AI・書き直しAI・通常査読AIを理由なく増やさない。Script Lock後のVOICEVOX、ffprobe timing、字幕組立、Render、Hash、Decode QA等はDeterministic Toolを優先する。

## Completion Gate

- Final MP4 exists / size > 0
- ffprobe parse PASS
- video/audio stream contract PASS
- 期待尺内、または短縮/延長理由を記録
- 必須SceneがすべてVerified
- 字幕coverage = 100%
- Full decode error = 0
- Fast Start (`moov` before `mdat`)
- Representative Visual QA PASS
- 元依頼と現行Policyを再読したRereview PASS

## Recovery Goal

- 後段失敗による正常Scene再生成 = 0
- 映像だけの変更による正常VOICEVOX再生成 = 0
- Isolated Scene failureによるFull Pipeline restart = 0
- 成功Checkpoint削除 = 0
- `.partial`をVerified Sceneとして採用 = 禁止
- CacheだけをDurable Checkpointとして採用 = 禁止
- ResumeはVerified state manifestから行う
- Retryは1 Layerが所有し、Multi-layer retry stormを作らない
- Permanent error retry = 0
- Provider/Processの無反応はNo-progress detectorとbounded timeoutで止める

## Render Goal

Scene/Chapter単位で `Render -> Validate -> Checkpoint -> Join`。Monolithic longform encodeへ戻さない。Media Contract一致時だけstreamcopy concatを優先し、不一致は最小SceneだけNormalizeする。Proxy/低解像度PreviewはResource/Visual riskが高い場合に限定する。

## Retention Goal

YouTube Analyticsが取得できる場合のみ、Intro retention、AVD、Average % Viewed、Top moments、Dips、SpikesをScene/Chapter/Semantic Beatへ紐付ける。似た長さ・Formatを比較し、CTR単独や単発Viral動画から恒久Ruleを作らない。AnalyticsがなければUNKNOWN/UNAVAILABLEと記録し、生成しない。

## Continuous Improvement

観測する主要値は、Stage wall time、cache hit、reused/rerendered Scene、wasted encode seconds、checkpoint recovery、duplicate work、provider stall、retry class、circuit breaker、lease contention、p50/p95 Scene render、Disk/RAM、A/V drift、Decode/Fast Start、字幕coverage。

数値Thresholdは一次資料または自分たちのFixture/実測がない限りHard Lawにしない。効果のないRuleは追加し続けず、削除・降格する。
