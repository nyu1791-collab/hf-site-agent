# Role-based model registry

\`config/model_registry.json\` centralizes model metadata and role mapping. Code selects a role first, then an exact primary or same-role free fallback; model IDs are not scattered through prompts or workflows.

## Current commander slots

| Role | Commander ID | Exact primary slot | Current status |
|---|---|---|---|
| \`ROLE_GENERAL_COMMANDER\` | \`glm-general-commander\` | \`z-ai/glm-5.3-flash:free\` | \`FREE_CATALOG_ONLY\`; inactive until the one-shot API probe passes |
| \`ROLE_ENGINEERING_COMMANDER\` | \`deepseek-engineering-commander\` | \`deepseek/deepseek-v4-flash:free\` | \`FREE_CATALOG_ONLY\`; inactive until the one-shot API probe passes |
| \`ROLE_RESERVE_COMMANDER\` | \`minimax-reserve-commander\` | \`minimax/minimax-m3:free\` | \`FREE_CATALOG_ONLY\`; never started automatically |

The live catalog currently shows the ordinary paid records for GLM, DeepSeek V4 Flash, and MiniMax M3 while the exact \`:free\` IDs are not listed. The registry records this as an API/catalog inconsistency instead of silently substituting the paid IDs. Direct endpoint verification is performed by \`scripts/probe_free_models.py\`: exactly one request per requested ID, no \`models\` array, no retries, \`provider.allow_fallbacks=false\`, minimal output, usage cost, and redacted credits before/after comparison.

Free candidates are resolved only when the endpoint probe reports \`FREE_ACTIVE\`, the exact response model matches, \`usage.cost=0\`, credits are unchanged, and the role's required tool/structured-output features pass. Until then, ordinary runs stay blocked and make zero model calls.

## Free quota protection

- \`FREE_ONLY_MODE=true\`, paid model/fallback/web-search/auto-top-up are false.
- Daily local ledger: 1000 requests; hard stop at 900, preserving 100 emergency requests.
- Zones: GREEN 0–799, YELLOW 800–849, ORANGE 850–899, RED 900+.
- One shared limiter is capped at 15 requests/minute (below the 20 RPM provider limit).
- Mission reservations are checked before fan-out; each request is counted before sending.
- A free 429 opens the circuit and returns \`queued_free_quota\`; it is never retried.
- At a new UTC date, one probe is required before reopening.
- \`openrouter/free\` is not a commander candidate.

## Safety rules

- \`allow_paid_models\` and \`allow_paid_fallback\` are false.
- Cross-role fallback is disabled; only same-role free reserve candidates may be considered.
- Legacy IDs (old Qwen, old DeepSeek, GPT-4o, Gemini 1.5 Flash, and similar) stay in the \`legacy\` quarantine list and cannot be selected.
- \`scripts/preflight_openrouter_models.py\` performs a read-only check and emits a blocked packet when a role is inactive, a model is stale, a paid candidate is requested, or no same-role free candidate is available.
- \`scripts/model_registry.py\` provides a read-only watcher. It does not change active roles or secrets.
- \`scripts/probe_free_models.py\` never logs the API key, credit balances, or provider response bodies.

Model activation, provider changes, paid use, or production swaps remain decisions of \`chatgpt-work\` and require a separate reviewed change. Monitoring uses only the public [OpenRouter model catalog](https://openrouter.ai/api/v1/models).
