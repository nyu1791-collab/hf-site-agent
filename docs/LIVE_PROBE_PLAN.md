# Live probe plan

`scripts/live_probe_plan.py` produces the redacted preflight required before
any manual Provider or Modal validation. It never opens a network connection,
reads a secret, starts a compute job, changes a registry, or enables routing.

Each plan includes the provider, unresolved model source, bounded request and
token estimate, timeout, retry count, approval token, and estimated cost. Cost
is intentionally `UNKNOWN` until current provider/account evidence is supplied;
therefore `auto_execution_allowed` and every `LIVE_PROBE_*` flag remain false.

Candidate labels are now included separately from model IDs. Google evaluates
current Gemini Flash discovery with `Gemini 3.8 Flash` as a label; NVIDIA
evaluates current NIM/Build discovery with `Nemotron 3.5 Lightning 30B A3B` and
current DeepSeek labels; Groq evaluates the user-observed Qwen, GPT-OSS and
Compound labels through the current Models API; OpenRouter uses a role-scoped
current Free Worker pool. These labels never authorize a fixed model ID.

Every exact model ID must be discovered from the current provider catalog,
capability-checked, cost/quota-checked, probed, benchmarked, and explicitly
approved before it can become a candidate or active model.

The dry-run also records `endpoint`, bounded `requests`, `max_input_tokens`,
`max_output_tokens`, `free_verified`, `quota_verified`, `capabilities_tested`,
`probe_gate`, `probe_status`, and `benchmark_status`. Until a current exact ID,
zero-cost evidence, and quota evidence are supplied, the plan uses
`endpoint=null`, `free_verified=false`, `quota_verified=false`,
`probe_gate=BLOCKED_COST_UNKNOWN`, `probe_status=NOT_RUN`, and
`benchmark_status=NOT_RUN`. These fields are descriptive only and never enable
network execution.

Example:

```sh
python scripts/live_probe_plan.py \
  --capabilities \
  --missions \
  --max-candidates 1 \
  --max-missions 2 \
  --max-total-requests 24 \
  --output artifacts/live_probe_plan.json
```

The generated JSON is validated by
`schemas/live_probe_plan.schema.json`. A plan is not a Provider readiness
report and does not select a model. Exact model IDs must come from the current
Provider catalog during a separately approved manual probe.
