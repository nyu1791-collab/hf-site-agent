# Role-based model registry

`config/model_registry.json` centralizes model metadata and role mapping. Code selects a role first, then a primary or same-role fallback; model IDs are not scattered through prompts or workflows.

## Current roles

| Role | Commander ID | Primary candidate | Current status |
|---|---|---|---|
| `ROLE_GENERAL_COMMANDER` | `glm-general-commander` | `z-ai/glm-5.3-flash` | Catalog-observed paid candidate; inactive until explicit approval |
| `ROLE_ENGINEERING_COMMANDER` | `deepseek-engineering-commander` | `deepseek/deepseek-v4-flash-0731` | Catalog-observed paid candidate; inactive until explicit approval |

Free candidates are recorded as same-role fallbacks and are resolved only when they are listed in the live catalog, priced at zero, marked free in the registry, and the role has been explicitly activated. The current registry keeps both commander roles inactive, so normal runs perform a read-only catalog check and make zero model calls.

## Safety rules

- `allow_paid_models` and `allow_generic_free_router` are false.
- `openrouter/free` is never a commander candidate.
- Cross-role fallback is disabled.
- Legacy IDs (old Qwen, old DeepSeek, GPT-4o, Gemini 1.5 Flash, and similar) stay in the `legacy` quarantine list and cannot be selected.
- `scripts/preflight_openrouter_models.py` performs a read-only check and emits a blocked packet when a role is inactive, a model is stale, a paid candidate is requested, or no same-role free candidate is available.
- `scripts/model_registry.py` provides a read-only watcher. It does not change active roles or secrets.

Model activation, provider changes, paid use, or production swaps remain decisions of `chatgpt-work` and require a separate reviewed change. Monitoring uses only the public [OpenRouter model catalog](https://openrouter.ai/api/v1/models).
