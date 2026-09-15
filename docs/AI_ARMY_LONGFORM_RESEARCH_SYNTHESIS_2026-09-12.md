# AI Army / 長編動画制作 Research Synthesis — Compatibility Pointer

**Filename retained for existing Read Gate compatibility.**  
**Current effective review:** 2026-09-15 JST  
**Current authority:** `config/longform_video_reliability_policy.json`  
**Current human playbook:** `docs/LONGFORM_VIDEO_RELIABILITY_PLAYBOOK.md`

このファイルの2026-09-12時点の長い研究サマリーは、現行Ruleと重複し、6〜10分等の古い前提を将来復元する危険があるため、**現行運用Authorityから降格**した。Read Gate互換のPathは維持するが、ここから旧Ruleを復活させてはならない。

2026-09-15の再調査では、YouTube公式Retention/CTR資料、LangGraph Durable Execution、Temporal Durable Execution、AWS retry/backoff+jitter、Google Cloud retry guidance、GitHub Actions cache/artifact/concurrency/timeout、FFmpeg/ffprobe、Adobe proxy workflowを再確認した。

同EvidenceをDeepSeek Executive Supervisorへ渡し、Workflow run `34956083092` / Artifact `10391441454` で6-lane peer review + synthesisを実施した。DeepSeekはEvidenceでありAuthorityではなく、ChatGPTが一次資料・ユーザー指示・Repository Policyと照合して最終採否した。

現行結論だけをここに残す。

- 通常News/Topic Explainerは **8〜12分目安**。Paddingは禁止。
- Research + ScriptはChatGPT + DeepSeekのCompact Pair。
- Scene/Chapter first、Verified Checkpoint、Atomic promotion、Idempotency、Single Writer。
- Retry ownerは1 Layer。Transientだけbounded backoff + jitter。Multi-layer retry stormは禁止。
- Provider/Processはbounded timeout + No-progress detection + Circuit breaker。
- CacheはAccelerator、Artifact/Checkpointは復旧用。
- PartialをCompleteと報告しない。
- Final MP4はffprobe + Full decode + Fast Start + Visual rereview。
- Fixed cut/expression/chapter cadence、固定Information Density秒数、mandatory proxy、AI fanout増加をHard Lawにしない。

詳細・採用/保留/削除理由・Source URLは `docs/LONGFORM_VIDEO_RELIABILITY_PLAYBOOK.md` を読む。
