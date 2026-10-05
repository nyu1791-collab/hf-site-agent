# Framework Adapter Layer — Reviewed Architecture (2026-09-11)

## Decision

Keep the existing AI Army V4 runtime as the only authoritative control plane. AutoGen, CrewAI, LangGraph, Microsoft Agent Framework and GitHub Copilot must remain replaceable execution adapters behind the existing task/result contracts. No external framework may become the source of truth for permissions, budgets, free-only policy, model eligibility, task ownership, repository-write authority, secrets, deploy, publish, payment or final integration.

The existing `command_envelope.schema.json` and `report_envelope.schema.json` remain the stable boundary/ABI. Framework-specific objects are temporary internal representations only. Every framework result must normalize back to the native result contract before it can affect scheduler state.

## Why this structure is preferred

The project already has bounded delegation, DAG scheduling, dependency join, cancellation isolation, idempotency, stale-response prevention, checkpoint/recovery, provider circuit isolation, result confidence checks and single-writer ownership. Replacing that control plane with a third-party agent framework would duplicate logic and create conflicting authorities.

Frameworks are therefore treated as specialist execution engines:

- Native V4: default path, final authority, routing, hard-boundary enforcement and final integration.
- LangGraph: durable/stateful subgraphs, long-running workflows, checkpoint/resume, human review and recovery-heavy workflows.
- Microsoft Agent Framework: preferred Microsoft-family adapter for new multi-agent/workflow integration, middleware, session state, agent interoperability and longer-term migration away from AutoGen-specific dependencies.
- AutoGen: bounded council/debate/adversarial-review adapter and compatibility path; useful for multi-agent discussion, but not the long-term primary Microsoft orchestration dependency.
- CrewAI: newsroom, research, media pre-production, content pipelines and role-oriented crews where a small number of agents can own adjacent stages.
- GitHub Copilot: repository-scoped coding, test repair, codebase analysis and refactor proposals only. It is not a general AI Army commander and must never auto-select without connector/billing-safe evidence.

## Selection policy

Native V4 remains the default when the task does not explicitly require a framework capability. External adapters may auto-select only for LOW/MEDIUM risk work, only when the adapter is installed/connected, its public API contract is verified, the framework health gate is READY, the model route is verified free, and a trusted executor hook is present.

A framework may own several adjacent stages if it remains the strongest verified option. Do not split one mission into many micro-agents merely because multiple frameworks are available. Introduce a second framework only for a real capability gap, independent review, clear parallel speedup, fault isolation or a hard-boundary separation. Keep at most two primary external frameworks per mission unless the native commander explicitly approves a different plan.

External frameworks may never execute hard-boundary actions. Merge, deploy, publish, secret mutation, payment, production activation, paid-model activation and new paid-provider enablement always stop before the external adapter and return to the native commander/human approval boundary.

## Evidence and promotion

Adapter readiness is not inferred from package presence alone. It requires three independent evidence classes:

1. Compatibility evidence: framework installed/connector present and expected public symbols/API contract verified.
2. Health evidence: recent shadow observations, success rate, deterministic-validator pass rate, quality, latency and circuit-breaker state.
3. Model-route evidence: exact model/route is verified zero-cost for the current execution path; paid fallback and auto top-up are disabled.

Promotion must remain fail-closed. A few lucky successes must not promote an adapter. Use minimum shadow samples plus Wilson lower bounds and quality comparison versus the native baseline. Stale health data must not count as fresh readiness.

## Provider/model separation

Framework selection and model/provider selection are separate decisions. Choosing LangGraph, CrewAI or AutoGen must not grant that framework permission to choose an unverified paid model. The native provider router supplies the already-approved binding to the adapter. The adapter can execute the workflow but cannot silently substitute another model, provider or billing route.

This is especially important for GitHub Copilot: it is enabled only as an explicit repository/coding connector when entitlement and billing safety are verified. It is never a fallback for a failed free model.

## Media/news production mapping

For short-form news/media missions, prefer a consolidated pipeline rather than many agents:

- Research/claim collection: strongest verified research-capable model.
- Script + subtitle draft: one strong generalist may own both to avoid context loss.
- Fact/claim review: use an independent provider/model family when available.
- Edit planning: multimodal/video-understanding model when verified free; otherwise deterministic scene plan.
- TTS/ASR: open-source/local models or a verified free route.
- Final media assembly: deterministic FFmpeg/local tooling.
- Final semantic review: independent multimodal reviewer where verified free.

For the current news-video style, do not generate new decorative AI images by default. Search for relevant real photographs or footage and use only assets with clear reuse rights or a source/license that can be attributed. If no reusable real asset is available, keep the existing approved visual cards/backgrounds rather than generating a fake documentary-looking image.

## Real-photo sourcing policy

The asset collector should search public/reusable sources first (for example Wikimedia Commons or other sources exposing clear reuse licenses). Each selected asset must retain: source URL, creator/agency, license, attribution text, retrieved date and the claim/scene it supports.

Do not copy a news agency photo merely because it appears in search results. Reuters/AP/Getty/editorial images should be treated as non-reusable unless the specific asset carries a separate license that allows reuse. Search results are discovery only; the rights record of the underlying asset decides whether it can enter the video.

Prefer 3–8 relevant real photos for a ~60-second short, reuse them with crops, slow pan/zoom, overlays and captions, and avoid expensive video generation unless a verified free route materially improves the result.

## Framework-specific reviewed guidance

### LangGraph
Use for stateful or long-running subgraphs, checkpoint/resume, HITL and complex branching. Do not wrap simple one-shot tasks in LangGraph; that adds state machinery without value.

### Microsoft Agent Framework
Treat as the preferred Microsoft adapter for new work. Its workflow, middleware, persistence/HITL and migration path make it more suitable for long-term integration than expanding AutoGen-specific coupling. Keep it behind the same native envelope and free-route gates.

### AutoGen
Retain for compatibility, councils, adversarial review, debugging discussion and experiments. Avoid making experimental graph features required for readiness. Do not allow group-chat behavior to bypass bounded delegation or request limits.

### CrewAI
Use where role-based crews and flows are a natural fit: newsroom research, script preparation, content/media preproduction and bounded business workflows. Prefer a small crew and consolidated ownership; do not create one agent per trivial step.

### GitHub Copilot
Use only for code/repository missions. Connector presence, entitlement/billing safety and explicit task preference are mandatory. No automatic general-purpose fallback.

## Required invariants

The following must remain true after every adapter change:

- `FREE_ONLY_MODE=true` / free-only default remains enabled.
- Generic paid fallback is disabled.
- Auto top-up is disabled.
- External frameworks cannot mutate secrets, deploy, publish, merge, pay, activate production or receive repository-write authority.
- Native scheduler remains authoritative.
- Single-writer final integration remains authoritative.
- External framework failure is isolated; it must not corrupt native scheduler state or stop unrelated providers/tasks.
- Every external result is normalized and validated before commit.
- No framework is promoted to READY from package presence or self-reported quality alone.
- Final media publishing remains a separate explicit approval/action boundary.

## Implementation status reviewed

The branch already contains the adapter control plane, plugin registry, framework health/shadow benchmark, anti-fragmentation policy, parallel batch support and dedicated tests/CI. This document is the reviewed architecture contract for those modules; future framework integrations must conform to it rather than replacing the native runtime.

Relevant modules:

- `config/framework_adapter_layer.json`
- `config/framework_plugin_registry.json`
- `scripts/framework_adapter_layer.py`
- `scripts/framework_plugin_registry.py`
- `scripts/framework_adapter_health.py`
- `scripts/framework_consolidation_policy.py`
- `scripts/parallel_framework_batch.py`
- `tests/test_framework_adapter_layer.py`
- `.github/workflows/verify-framework-adapter-layer.yml`

## Next implementation order

1. Keep current adapter-layer CI green.
2. Add/maintain real executor hooks one at a time, starting with the framework whose verified free route and capability best match the mission.
3. Shadow benchmark each adapter against Native V4 before auto-selection.
4. Prefer Microsoft Agent Framework for new Microsoft-family orchestration; keep AutoGen as a compatibility/council adapter.
5. Add the reusable-real-photo collector/rights manifest to the media pipeline before further news-video automation.
6. Only after these gates pass, allow the router to auto-select external adapters for bounded LOW/MEDIUM-risk tasks.
