# Role-based Model Registry

`config/model_registry.json` はRoleとModelを分離した台帳です。Model IDをPrompt、Workflow、Runtimeへ大量に直書きせず、`provider_id → model_binding_role → 現行Catalog/Probe` の順で解決します。

## LifecycleとDiscovery

各Model recordは `model_id`、`provider`、`role_candidates`、`capabilities`、`status`、`lifecycle`、`discovered_at`、`last_verified_at`、`probe_status`、`benchmark_status`、`cost_class`、`quota_status` を保持します。Lifecycleは `STABLE`、`GA`、`PREVIEW`、`EXPERIMENTAL`、`LEGACY`、`DEPRECATED`、`REMOVED`、`UNKNOWN` のいずれかです。

`STABLE`/`GA`だけがPrimary候補です。`PREVIEW`/`EXPERIMENTAL`は未承認の評価対象、`LEGACY`/`DEPRECATED`/`REMOVED`/`UNKNOWN`はRouting不可です。新モデルは `DISCOVERED → CAPABILITY_CHECKED → COST_CHECKED → PROBED → BENCHMARKED → CANDIDATE → EXPLICIT_APPROVAL → ACTIVE` の順で進み、発見だけでActiveにはなりません。

Googleの `Gemini 3.8 Flash`、NVIDIAの `Nemotron 3.5 Lightning 30B A3B`、Groqのユーザー提供候補、OpenRouterの現行Free Worker群は `model_discovery.provider_targets` に評価対象として記録します。Google/NVIDIAの正確なIDが未確認の候補は `model_id=null`、Groqの画面由来IDは `USER_OBSERVED_UNVERIFIED` とし、Production RegistryのPrimaryには使用しません。

旧固定Commander Roleは `LEGACY_DISABLED` を維持し、旧IDは `compatibility_model_ids` にのみ残します。旧IDを `primary_model`、`fallback_models`、`candidate_models`、Active経路へ戻すことは禁止です。

## 現行Role

| Role | Agent | Provider | 初期状態 |
|---|---|---|---|
| `ROLE_GOOGLE_GENERAL_COMMANDER` | `google-general-commander` | Google | `UNVERIFIED`, inactive |
| `ROLE_NVIDIA_ENGINEERING_COMMANDER` | `nvidia-engineering-commander` | NVIDIA | `UNVERIFIED`, inactive |
| `ROLE_GROQ_RAPID_EXECUTION_COMMANDER` | `groq-rapid-commander` | Groq | `UNVERIFIED`, inactive |
| `ROLE_GOOGLE_SPECIALIST` | Google specialists | Google | `UNVERIFIED`, inactive |
| `ROLE_NVIDIA_SPECIALIST` | NVIDIA specialists | NVIDIA | `UNVERIFIED`, inactive |
| `ROLE_GROQ_SPECIALIST` | Groq specialists | Groq | `UNVERIFIED`, inactive |
| `ROLE_OPENROUTER_WORKER` | 各 `<role>-worker` | OpenRouter | `UNVERIFIED`, inactive |

Google、NVIDIA、GroqのCommander Model IDは、現行公式Catalog、価格・Quota、能力、実Endpoint Probeが揃うまで空欄です。Gemini、DeepSeek、GPT-OSS等の名称だけからID、価格、Free状態を推測して登録しません。旧固定Roleは `LEGACY_DISABLED` として互換検査用に隔離され、新Routingから参照されません。

## Free Workerの選定

`scripts/probe_free_workers.py` は既定ではDry Runです。Catalog・Credits・Model Endpointへ接続するには、承認済みの手動Actionsから明示的に `--network` を付けます。そのうえで次の順に動きます。

1. 現行OpenRouter Catalogを読み取る。
2. `GENERAL_WORKER`、`CODING_WORKER`、`REVIEW_WORKER`、`FAST_WORKER`ごとに、正確な `:free` suffix、入力・出力価格0、必要Context、Tool/Structured Output、Role能力を確認する。
3. 各Role最大1候補、全体最大4件だけをProbeする。
4. 応答Modelが要求IDと一致し、`usage.cost=0`、Credits前後不変、`provider.allow_fallbacks=false`、Retry 0を満たす場合だけ `FREE_ACTIVE` とする。
5. Catalog掲載だけ、個別ページのFree表記、Model mismatch、429、401/403、Cost不明、Credits不明は実行可能候補にしない。

`openrouter/free` は動的RouterなのでCommander、重大判断、Deploy判断、最終Reviewには使いません。低リスクWorkerでも、現在のRole条件とProbeを満たした記録が必要です。

## Evaluation record projection

既存のv1キーとの互換性を保つため、`scripts/model_registry.py` の
`normalized_model_records()` が各Modelをv2評価レコードへ射影します。射影はRegistryを変更せず、Lifecycle、Role candidates、Capabilities、Probe、Benchmark、Cost、Quotaを含む安定した評価レコードを返します。未検証の値は推測せず、`None`、`UNKNOWN`または`NOT_RUN`のまま保持します。

## Provider別ポリシー

Provider台帳は [`config/provider_registry.json`](../config/provider_registry.json) で管理します。Google/NVIDIA/Groqは `COMMANDER_PROVIDER`、OpenRouterは `WORKER_PROVIDER` です。全Providerで初期値は `enabled=false`、`probe_status=NOT_RUN`、Paid Model/Fallback/Auto top-up=falseです。

OpenRouterだけに既存の1000 requests/day、900 Hard Stop、15 RPM、429停止、UTC日替わりの1回Probeを適用します。Google、NVIDIA、Groqへ900回ルールを流用しません。Quota APIがないProviderは無制限として扱わず、Quota不明で停止します。GroqについてはProbe応答のRate Limit Headerを許可されたQuota項目だけ記録します。

## 有効化条件

Provider/Roleの `active=true` は自動変更しません。少なくとも次を満たし、ChatGPT Workが明示承認した場合だけ切替候補になります。

- Provider Authが成功。
- 指定Modelが現行Catalogまたは公式Account情報で存在。
- Context、Multimodal、Tool Calling、Structured Output、Code能力がRole条件を満たす。
- Retry 0の最小Probeが成功し、応答Modelと指定IDが一致。
- Freeの場合は価格0、Usage Cost 0、Credits不変、Paid Fallbackなし。
- Quota、Rate Limit、Health、Circuitが安全状態。
- Commander契約、専門Mission、Worker契約を検証し、20〜50件の比較結果を保存。

Probeはレジストリを直接変更しません。成功しても `FREE_ACTIVE` の昇格、Production swap、公開、決済は別承認です。
