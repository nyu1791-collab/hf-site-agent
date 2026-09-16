# AI Army Canonical Organization — 2026-09-12

This document is the human-readable companion to the machine policies. The machine source of truth is the repository policy stack referenced by `config/permanent_standards_manifest.json` and `config/current_commander_handoff.json`.

## 1. Canonical hierarchy

### Top Commander — ChatGPT Work
Owns the Mission, authorization boundary, cross-domain integration, final adjudication, and user delivery. No subordinate model can override this layer.

### Executive Supervisor — paid DeepSeek
Paid DeepSeek is persistently pre-authorized only for the supervisory scope in `config/deepseek_paid_supervisor_policy.json`. It is intentionally **not** the default bottom Worker and **not** a boilerplate code factory.

Primary work:
- broad research and source comparison
- architecture alternatives and design review
- red-team and contradiction search
- incident/root-cause analysis
- task decomposition
- review and synthesis of subordinate work
- media and TikTok Shop strategy review
- postmortem and remediation planning

Normal research fan-out is bounded: 6 lanes, 3 parallel calls, 8 calls per Mission. Expansion is bounded and requires a reason and budget pass. ChatGPT performs final adjudication.

### Specialist / Worker execution corps
Qwen, NVIDIA, Groq, Google, OpenRouter-free workers and deterministic tools may execute bounded specialist work only when current capability/availability evidence permits it.

They do not gain final policy authority and do not gain permission to use paid fallback.

## 2. Direct specialist bypass is intentional

DeepSeek is a senior supervisor, but it is **not** a mandatory paid hop for every task.

For narrow execution with clear boundaries—such as a targeted coding task with a current verified provider route—ChatGPT may route directly to the specialist corps. This reduces unnecessary latency, token usage, coordination overhead, and paid calls.

This bypass is not a peer command structure. ChatGPT remains the owner and final adjudicator. The bypass cannot authorize payment, deployment, publication, merge, secret changes, or generic paid fallback.

## 3. Canonical routing entry point

All new policy-facing routing decisions should enter through:

`scripts/ai_army_routing_facade.py`

It selects among:
1. deterministic-tool path
2. paid DeepSeek Executive Supervisor path
3. direct specialist bypass
4. ChatGPT single-controller fallback

`scripts/commander_routing.py` remains a compatibility execution layer for legacy direct-provider routing and is not the policy source of truth.

## 4. Architecture admission and measurement

Multi-Agent is not automatically better. The default is the simplest architecture that can satisfy the Mission.

Use centralized Multi-Agent only when independent workstreams, specialist separation, verification value, context isolation, or checkpointed long-running work can justify coordination cost.

Do not fan out strict sequential reasoning simply to increase agent count. Deterministic validation belongs to machine oracles when available.

Architecture changes remain shadow-measured against a single-controller baseline using `config/agent_efficiency_policy.json` and `scripts/agent_efficiency_eval.py` before becoming broad routing defaults.

## 5. Single Writer and bounded autonomy

- One mutable target has one writer/lease owner.
- Worker-to-worker unbounded delegation is prohibited.
- Retries and replans are bounded.
- Same root-cause retry without new evidence or a method change is prohibited.
- Healthy outputs are preserved across downstream failures.
- Checkpoints are authoritative recovery state; provider cache is only an accelerator.
- Machine validation beats model majority vote where a reliable oracle exists.

## 6. Paid DeepSeek execution boundary

The only canonical active paid DeepSeek research workflow is:

`.github/workflows/deepseek-supervisor-research.yml`

It processes one explicit Mission JSON under `missions/deepseek-supervisor/`, enforces mission and daily estimated-cost caps, and cannot write the repository, deploy, publish, merge, mutate secrets, top up credit, or use generic paid fallback.

Historical paid engineering/specialist experiment workflows are retired from the active workflow directory. Their old configs/scripts may remain only as regression/history evidence and never authorize execution.

## 7. Cost policy

Default routes remain free-only. Paid DeepSeek is the one persistent paid exception for the authorized supervisory scope. This does not authorize other paid providers.

- no auto top-up
- no generic paid fallback
- no silent paid sibling substitution
- unknown entitlement/cost route blocks
- mission budget and daily estimated-cost budget remain enforced

## 8. Permanent consistency gate

`scripts/validate_permanent_ai_army_state.py` must pass. CI checks that:
- the canonical router and policy references agree
- DeepSeek is an Executive Supervisor, not an active legacy engineering commander
- old paid DeepSeek workflows are not active
- the canonical supervisor workflow exists
- ChatGPT retains final authority
- payment/deploy/publish/merge/secret safety boundaries remain intact

Any future change to organization, paid scope, or routing must update the canonical policy stack and pass this consistency gate before being treated as permanent.
