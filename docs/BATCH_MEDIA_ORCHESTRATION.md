# Bounded Batch Media Orchestration

**Machine source of truth:** `config/batch_media_orchestration_policy.json`  
**Current policy:** v2

独立した動画・切り抜きJobは、Rights・Resource・Write-scopeのAdmissionを通過したら**人工的に1本ずつ待たせない**。`NORMAL` ではReady Job数に応じて最大3並列、`DEGRADED` は最大1、`PAUSED` は0。4本目以降は次WaveへQueueする。

## Fast path

次をすべて満たす場合だけ、`min(ready independent jobs, 3)` をすぐ開始する。

- JobごとのRights確認済み
- Mutable write scopeが互いに独立
- CPU / Memory / Disk / Provider slotが確保済み
- 未解決の共有Mutable dependencyがない
- Backpressure stateが`NORMAL`

Resource不足、Provider pressure、Write contentionが出たら即座に並列度を下げる。**Rights / Claim / Machine QAを速度のために緩めない。**

## What to parallelize

Job単位の並列化が基本。PreproductionでもDependency-freeなら以下を重ねてよい。

- Source / Fact research
- Rights / Claim verification
- Edit / Caption planning
- Deterministic automation planning

ただし同じMutable OutputにはSingle Writerを1つだけ置く。Dependencyがある工程はJoin後に進める。

## What not to parallelize with agents

Trim、Cut、Concat、Reframe execution、Caption burn、Audio normalization、Hashing、Manifest commit、ffprobe/decode QAは決定論的Toolを優先する。Mechanical stageにPeer debateやMajority voteを増やさない。

Optional AIはHighlight candidate ranking、Hook/Payoff review、Caption language cleanup、Metadata variants、Analytics triage等の判断部分だけ。1 ClipあたりJudgmental verifierは最大1。

## Reliability

- Per-job lease
- Lease token on commit
- Compare-and-swap base hash
- Atomic manifest promotion
- Stale writer reject
- Partial outputはCommitted扱いしない
- Verified checkpointを保持
- 失敗した最小単位だけ再開
- 1 Job失敗で無関係JobをCancelしない
- Transient providerのみ最大2 retry
- Same root causeをEvidenceなしで繰り返さない

## Measurement

最大3は安全上限であり「常に3を使え」という意味ではない。Parallel 1 baselineとのLatency / QA / Cost比較を継続し、Rights bypassとStale writeは0件を維持する。3超への拡張は別Policy変更とShadow/Soak evidenceが必要。

## Runtime

- Policy: `config/batch_media_orchestration_policy.json`
- Scheduler: `scripts/batch_media_scheduler.py`
- Command center: `scripts/media_batch_command_center.py`
- Job schema: `schemas/batch_media_job.schema.json`
- Tests: `tests/test_batch_media_scheduler.py`

RollbackはConcurrencyを1または0へ落とすだけにし、正常CheckpointやCommitted outputを破棄しない。
