# Provider Readiness A

## 監査の位置付け

この文書は、AI部隊 第4段階の Lane A（Qwen-labelled Provider Readiness reviewer）による、読み取り専用の静的監査記録です。ここでいうQwenは実Provider/APIではなく、担当レーンの名称です。

監査対象はローカルのforked workspaceにある次のcheckoutです。

| 項目 | 値 |
|---|---|
| Repository | `nyu1791-collab/hf-site-agent` |
| 基準main SHA（依頼記載） | `6c65d35f45539459c428b96769ef240aee8ec2f1` |
| PR | #40、Open / Draft / 未merge（依頼記載） |
| PR HEAD（依頼記載・未fetch） | `a72155b08302ee25ba592dbe474b50c70ee90c8c` |
| ローカル監査HEAD | `fb789a12e79994b23608d8c02579dd06a6423a51` |
| ローカルBranch | `ai-army/provider-v3` |
| 外部API / Modal | 呼出なし |
| Secret値 | 取得・表示・出力なし |
| Workflow | dispatchなし |

PR HEADはネットワークで再取得せず、依頼文に記載された安全基準として扱いました。したがって、この文書のコード所見はローカル監査HEADの内容に基づき、PR HEADとの差分が必要な項目は未確認としています。

## 監査した証拠

- `config/provider_registry.json`
- `config/model_registry.json`
- `scripts/provider_registry.py`
- `scripts/model_registry.py`
- `scripts/provider_controls.py`
- `scripts/free_quota.py`
- `scripts/provider_adapters.py`
- `scripts/probe_providers.py`
- `scripts/direct_api_validation.py`
- `scripts/probe_free_workers.py`
- `scripts/worker_selection.py`
- `scripts/commander_routing.py`
- `docs/DIRECT_API_VALIDATION.md`
- `docs/MODEL_REGISTRY.md`
- `docs/AI_COMMANDER_OPERATING_RULES.md`
- Provider、Adapter、Probe、Worker選択、Registry、Routingの関連テストと手動Workflow定義

## 共通判定

現在のProvider Registryは、Google / NVIDIA / Groqを `COMMANDER_PROVIDER`、OpenRouterを `WORKER_PROVIDER` として分離しています。全Providerは `enabled=false`、`activation_approved=false`、`probe_status=NOT_RUN` であり、Roleも未有効化です。Model Registryの新3CommanderはModel ID未解決、旧OpenRouter Commander Roleは `LEGACY_DISABLED` です。

Model名や外部Dashboardの表示値だけでは、現行Model、無料状態、Quota、能力を確定しません。ユーザーから提示されたGroq Dashboard値も、Repository内の検証済み証拠ではないため、本番Registryへ転記していません。

### Quota・予約・Retryの横断所見

| 項目 | Repositoryで確認できたこと | READY判定への影響 |
|---|---|---|
| RPM | `config/provider_registry.json`でOpenRouterのみ15。Google/NVIDIA/Groqは`null` | 3Providerは不明値のまま送信不可。OpenRouterの15は維持対象 |
| TPM | 全ProviderでRegistry値なし。Adapterは一部Headerを保持するだけ | Token上限・Token予約の実装証拠がなく、READY不可 |
| RPD | OpenRouterのRegistry値は900、`daily_cap=1000`。他3Providerは`null` | OpenRouterだけ既存Hard Stopを適用。他3Providerは不明値で停止 |
| TPD | 全ProviderでRegistry値なし | Token日次上限を確認できず、READY不可 |
| Concurrency | `FreeUsageLedger`のファイルロックはLedger更新を保護するが、in-flight同時実行数の上限ではない。`ProviderQuotaLedger`はProcess内`RLock`のみ | Provider横断・Runner横断のConcurrency Guardは未証明 |
| Request reservation | `FreeUsageLedger`はMission予約と送信前Request記録を持つ。`ProviderQuotaLedger`も推定Request数を予約する | Providerごとの共通予約契約ではなく、二つのLedgerが併存 |
| Token reservation | 実装・Registry項目・Probe経路で確認できず | LIVE Probe前に追加設計・検証が必要 |
| Retry-After | AdapterのError正規化で数値を読み、Ledger/Reportへ保持可能 | ProbeはRetry 0。ただし待機・再開スケジューラは別途未証明 |
| 429 / Quota exhaustion | 429、402、Auth/Permission系を停止・Circuit OPENへ分類するテストあり | 5xx、Timeout、Header欠落、並列競合を含む統合Failure Injectionは未実施 |
| Fallback | Paid Model、Paid Fallback、Generic `openrouter/free`、Cross-role置換はPolicy/Selectorで拒否 | 維持すべき安全条件。未検証結果をFallbackで補わない |
| Unknown limit | `ProviderQuotaLedger`はdailyまたはRPM不明なら`QUOTA_UNKNOWN`で予約拒否 | Safe defaultはFail Closed |

### Ledgerについての重要な境界

`scripts/free_quota.py` の `FreeUsageLedger` は、Linux上ではlock fileと一時ファイル置換により、同一共有ファイルを使うProcess間のRequest予約を保護できます。ただし、Runner間で同じ永続ファイルを共有すること自体はこのコードからは保証されません。またToken予約、in-flight同時数、Provider応答の実Quotaを扱いません。

一方、`scripts/provider_controls.py` の `ProviderQuotaLedger` はProvider単位のNamespace、Daily/RPM、Circuitを持ちますが、ファイルLockによるProcess間のread-check-write原子性はありません。`agent_executor.py` ではOpenRouter呼出しに既存Free LedgerとProvider Ledgerの双方が関与するため、どちらを正規の共有Budget台帳とするかは未統合です。この状態を `LEDGER_OK` とは判定しません。

### Probe経路についての重要な境界

- `scripts/probe_providers.py` は既定ではDry Runで、`--network`時にProviderごと1回のModel Probeを行います。Registryを変更せず、Retry 0、Fallbackなしです。ただしスクリプト単体のlive gateは`--network`だけで、`direct_api_validation.py`のような追加確認文字列はありません。
- `scripts/direct_api_validation.py` は`--network`と完全一致の`DIRECT_API_VALIDATION`を要求し、Google/NVIDIA/Groqを直接Commander候補として検査します。Discovery、Candidate、Capability、MissionのRequest予算はメモリ上の合計値で、Provider-specific Ledger、TPM/TPD、Token予約、事前のcost estimateには接続していません。
- `scripts/probe_free_workers.py` は現行CatalogからRole別候補を選び、最大4件のModel POSTをRetry 0で実行します。通常のCLI実行ではCatalog 1回、Credits前後2回、Model最大4回となり、最大7ネットワークRequestですが、`model_calls`はModel POSTだけを数えます。OpenRouter Free Usage Ledgerへの事前予約もToken予算もありません。
- `scripts/worker_selection.py` は、`:free`、価格0、Context、Role能力、Exact response model、`usage.cost=0`、Credits不変、Fallbackなしを確認した候補だけを選びます。ただし選択成功はProvider/Workerの実行許可や本番Activationではありません。

---

## Google

### KNOWN

- `google` は `COMMANDER_PROVIDER` で、AdapterはGemini Native形式です。
- Registryに公式Gemini RESTのBase URL、`/models`、`/models/{model}:generateContent`、`x-goog-api-key`方式、`GOOGLE_API_KEY`参照名があります。旧互換名として`GEMINI_API_KEY`も定義されています。
- `ROLE_GOOGLE_GENERAL_COMMANDER` はResearch、Planning、Long Context、Multimodal、Synthesis担当で、Tool Calling、Structured Output、Multimodalを要求します。
- Native AdapterはModel discovery、最低限Probe、Structured Output、Tool Calling、Command Schema、Mission Probe、Health Checkの契約を持ち、SecretをURLやReportへ入れません。
- Registryの`free_mode=true`、`free_access_type=FREE_TIER`は、利用可能性を実証した状態ではありません。

### UNKNOWN

- 現行の公式Model ID、Catalog掲載、Google側の実際のFree Tier適用状態。
- RPM、TPM、RPD、TPD、Account quota、残Credit。Registryは全て未設定で、`quota_path`もありません。
- 応答Usage、Cost、Quota Headerが実Endpointで取得できるか。
- 実際のStructured Output、Tool Calling、Command Schema、画像・動画・音声・PDF能力。
- Provider固有のConcurrency上限、Token reservation、Retry-Afterの意味と安全な再開条件。

### BLOCKED

- 本フェーズは外部Provider APIを呼ばないため、Auth、Model、Probeを確認できません。
- Providerは未有効化・未Probe・Role未有効化です。
- Daily/RPMが不明なため、`ProviderQuotaLedger.reserve()`は安全側で`QUOTA_UNKNOWN`になります。
- Gemini名やユーザー指定の候補名だけで、現行Model・無料状態・Quotaを登録することは禁止です。

### SAFE_DEFAULT

- `enabled=false`、`activation_approved=false`、`probe_status=NOT_RUN`、Production routeなしを維持します。
- Quota不明時は新規Requestを拒否し、Paid Model/Fallbackへ移行しません。
- Live実行する場合も、明示された正確なModel ID、最小出力、Tool/Web Searchなし、Retry 0、短いTimeoutに限定します。
- Headerまたは公式Account情報で得たQuotaを、単なる成功応答とは分離して記録します。

### LIVE_PROBE_REQUIRED

1. 公式Catalogまたは公式Account情報で正確なModel IDとRole能力を確認する。
2. 実行前に、Discovery・最小Probe・必要Capability検査のRequest数、Token数、Cost見積り、Timeout、Retry数をDry Run表示する。Costが不明なら開始しない。
3. Auth/Model/最小応答の一回Probeで、HTTP、応答Model、Usage、Quota情報、無料条件を確認する。
4. Provider-specific request/token reservationとConcurrency Guardを経由して、CapabilityとCommander契約を検査する。
5. 失敗時の429、5xx、Timeout、Malformed JSON、Quota不明を安全なFixtureで検証する。実APIを故障させる試験は行わない。

### READY_REQUIREMENTS

`AUTH_OK`、`MODEL_AVAILABLE`、`PROBE_OK`に加えて、正確なModelとGoogleの無料条件、RPM/TPM/RPD/TPDまたは保守的な公式上限、Concurrency、Request/Token予約、Retry-After処理、Provider単位Circuit、Structured/Tool/Command/Mission Test、Secret Guard、明示承認が全て必要です。いずれかが`UNKNOWN`または`BLOCKED`なら `GOOGLE_READY=false` を維持します。

---

## NVIDIA

### KNOWN

- `nvidia` は `COMMANDER_PROVIDER` で、OpenAI-compatible Adapter経路です。
- RegistryにNVIDIA NIMのBase URL、`/models`、`/chat/completions`、Authorization方式、`NVIDIA_API_KEY`参照名があります。
- Registryの無料区分は `TRIAL_CREDITS`、Quota源はProvider AccountまたはResponse Headerです。
- `ROLE_NVIDIA_ENGINEERING_COMMANDER` はRepository、Coding、Debug、Testing、Infrastructure、Tool利用を要求し、Tool CallingとStructured Outputが必要です。
- Adapterは401/403/404/402/429、5xx、Timeout、Network、JSON不正を正規化し、`Retry-After`を読み取れます。ProbeはRegistryを自動変更しません。

### UNKNOWN

- 現行NIM Catalog上の正確なModel IDと可用性。
- ユーザー指示にあるDeepSeek V4 Pro 0813がNVIDIA NIMの現行Endpointで利用できるか。現在のModel RegistryにあるDeepSeek V4 Proの記録はOpenRouter側候補であり、NVIDIAの実証にはなりません。
- Trial Credit残高、Credit消費の単位、RPM、TPM、RPD、TPD、Provider Headerの実形式。
- Provider固有Concurrency、Token reservation、Retry-After、安全なCircuit回復条件。
- Repository/Coding/Debug/Test/Toolの実Mission品質。

### BLOCKED

- 外部NVIDIA API・Account情報を読み取っていないため、Auth、Model、Credit、Quota、Probeは未確認です。
- Providerは未有効化・未Probe・Role未有効化です。
- RPM/RPDが不明なため、Provider Ledgerは`QUOTA_UNKNOWN`で送信を止める設計です。
- NVIDIAのTrial Creditを「無料」とみなして、固定消費量や無制限利用を仮定することは禁止です。

### SAFE_DEFAULT

- NVIDIAを停止してもGoogle、Groq、OpenRouterのCircuitを開かないProvider単位分離を維持します。
- Trial Creditの残量が不明な間は、無料実行可能とは判定せず、Paid Fallbackも行いません。
- DeepSeek、Kimiその他のModel名を、NVIDIAの実Endpoint確認なしにRegistryへPrimaryとして昇格しません。
- Live Probeは最小のRead-only検査だけにし、CPU/GPU Job、Deploy、Repository Writeは行いません。

### LIVE_PROBE_REQUIRED

1. 公式NIM CatalogまたはAccount情報で、NVIDIA Providerに属する正確なModel ID、Context、Tool、Structured Output、Code能力を確認する。
2. Trial Credit、Rate Limit Header、Quota Errorの取得可能性を確認し、取得できない上限はUNKNOWNのまま停止する。
3. 実行前にRequest/Token/CostをDry Run表示し、CreditまたはCostが不明なら送信しない。
4. Authと最小Model ProbeをRetry 0で実施し、応答Model一致、Usage、Quota、HTTPを赤字なしのReportへ射影する。
5. 401/403/404/402/429/5xx/Timeout/Model Output InvalidのFixtureをProvider単位で検証する。

### READY_REQUIREMENTS

`AUTH_OK`、`MODEL_AVAILABLE`、`PROBE_OK`、Trial Creditまたは無料条件の検証、RPM/TPM/RPD/TPD、Concurrency、Request/Token予約、Retry-After、Circuit、Engineering Mission Test、Secret Guard、Paid Guard、明示承認が全て必要です。DeepSeek/Kimiが別Provider経路である場合は、NVIDIA READYの根拠に混ぜません。未確認なら `NVIDIA_READY=false` を維持します。

---

## Groq

### KNOWN

- `groq` は `COMMANDER_PROVIDER` で、OpenAI-compatible Adapter経路です。
- RegistryにGroq Base URL、`/models`、`/chat/completions`、Authorization方式、`GROQ_API_KEY`参照名があります。
- Registryは `FREE_PLAN` と記録していますが、現在の限度値はRPM/RPD/TPM/TPD全て未設定です。
- AdapterはRate Limit関連の許可されたHeader（limit、remaining、reset、Retry-Afterの断片）だけを抽出できます。Authorization等の任意Headerや本文はQuota Reportへコピーしません。
- `direct_api_validation.py`にはGroq候補ヒントがありますが、候補ヒントはCatalog証拠でも本番Bindingでもありません。Catalogに存在しないIDはProbe対象になりません。
- Groq Roleは高速要約、分類、抽出、JSON変換、Log一次判定、Batch前処理向けです。実際のCommander Roleは未有効化です。

### UNKNOWN

- 現行Free Plan/APIでの正確なModel IDと、対象ModelごとのRPM、RPD、TPM、TPD。
- Dashboardで提示された外部確認値のRepository上の証跡、適用期間、Account/Model単位の差異。
- Headerのremaining requests、remaining tokens、reset requests、reset tokensが実Probeで返るか、またその単位。
- Provider側Concurrency、Token reservation、429時のRetry-Afterと再開条件。
- 無料状態、Cost、Structured Output/Tool Calling、Bulk Missionの実測値。

### BLOCKED

- 本フェーズではGroq APIを呼ばず、外部Dashboard値を本番値として登録していません。
- Providerは未有効化・未Probe・Role未有効化です。
- `ProviderQuotaLedger`はRequestの日次/RPMしか扱わず、GroqのToken HeaderをBudget/TPM/TPDへ反映しません。
- Headerを読み取れることだけでは、Rate Limit Guardが実装済み・検証済みとは言えません。

### SAFE_DEFAULT

- RPM/TPM/RPD/TPDはUNKNOWNのまま保持し、外部確認値を最大値として使いません。
- Provider固有の保守的な内部Soft Limit、Request/Token予約、Concurrency上限が実装・テストされるまで、新規Groq RequestをREADY経路へ流しません。
- 429はCircuitをGroqだけOPENにし、Retry stormや他Provider停止を起こさない設計にします。Retry-Afterは記録し、無限Retryしません。
- 大量処理はPythonで重複除去・分割・上限計算を先に行い、Groqへ無制限Batchを渡しません。

### LIVE_PROBE_REQUIRED

1. 公式Groq Catalog/Accountと対象Modelを確認し、Dashboard値とは別に実APIの現在値を取得する。
2. 実行前にCatalog/Model ProbeのRequest数、Token数、Cost、Timeout、Retry数、内部Soft LimitをDry Run表示する。CostまたはQuotaがUNKNOWNなら停止する。
3. 最小1回のExact Model Probeで応答Model、Usage、Cost/free、Rate Limit全Header、Latency、JSON妥当性を記録する。
4. Header値をRPM/TPM/RPD/TPDの各制御へ変換し、Request/Token reservationとConcurrency Guardを通す。
5. 429、Retry-After、Quota exhaustion、Header欠落、5xx、Timeout、Malformed JSON、重複RequestをMock/Fixtureで検証する。

### READY_REQUIREMENTS

`AUTH_OK`、`MODEL_AVAILABLE`、`PROBE_OK`、実Headerまたは公式Account根拠によるRate Limit Policy、内部Soft Limit、TPM/TPDを含むToken reservation、Concurrency、Retry-After、Provider Circuit、Groq Mission Test、Secret Guard、Paid Guard、明示承認が必要です。Dashboard値だけ、候補名だけ、Header抽出だけでは不十分であり、条件が一つでも不明なら `GROQ_READY=false` を維持します。

---

## OpenRouter

### KNOWN

- `openrouter` は `WORKER_PROVIDER` であり、ChatGPT Work直属Commanderにはなりません。
- RegistryにOpenRouter Base URL、`/models`、`/chat/completions`、Authorization方式、`OPENROUTER_API_KEY`参照名、旧互換名`AI_API_KEY`があります。
- 既存のOpenRouter専用値として、`daily_cap=1000`、`hard_stop=900`、`emergency_reserve=100`、`rpm_limit=15`がRegistryにあります。これらをGoogle/NVIDIA/Groqへ流用しません。
- Model RegistryはWorker Roleを動的Catalog選定にし、固定Free IDや`openrouter/free`をCommander候補にしていません。
- `worker_selection.py`は、正確な`:free` suffix、入力/出力価格0、必要Context、Role能力、Structured Output/Tool条件、Exact response model、`usage.cost=0`、Credits不変、Fallbackなしを要求します。
- `probe_free_workers.py`はCatalogからRole別候補を選び、最大4件のExact Model POSTをRetry 0、`provider.allow_fallbacks=false`で実施する設計です。Catalog掲載だけで`FREE_ACTIVE`にはしません。
- Repository内の直近記録では、OpenRouter exact free endpoint probeが401で停止し、Credits確認もできず、`FREE_ACTIVE`への昇格を行っていません。

### UNKNOWN

- 現行CatalogのGeneral/Coding/Review/Fast Worker、価格、Context、Tool、Structured Output、Exact Endpoint応答。
- 現在のCredits状態、free endpointの実Cost、Credits前後不変性。
- OpenRouterのProvider/Model別RPM、TPM、RPD、TPD、Concurrency、Retry-After。RegistryのTPM/TPDは未設定です。
- Worker ProbeをOpenRouter Free Usage Ledgerへ事前予約する仕組み。通常CLIではCatalog 1、Credits 2、Model最大4の最大7 Requestを発行しますが、既存Ledgerへ予約しません。
- `FreeUsageLedger`と`ProviderQuotaLedger`を複数Runnerで同一共有状態として運用できるか。ローカルファイルがRunner間共有になることはWorkflowから保証されません。
- 手元checkoutの`probe-free-models.yml`および一部旧Workflowが旧Secret参照名`AI_API_KEY`を使っている点が、依頼記載の現在PR HEADで解消済みか。

### BLOCKED

- 本フェーズではCatalog、Credits、Model Endpointのいずれにも接続していません。
- Provider/Worker Roleは未有効化・未Probeです。直近のRepository記録も401であり、成功根拠にはできません。
- OpenRouterの15 RPM/900 Hard StopはRequest保護であり、Token上限・Provider Header・Worker Probeの予約を代替しません。
- Worker Probeを単独でNetwork実行すると、全体のRequest予算を事前予約・表示せずにCredits確認と最大4 Model POSTを行うため、第4段階のLive Probe条件を満たしません。
- `openrouter/free`、Paid Model、他Providerへの自動Fallback、Commander昇格は不可です。

### SAFE_DEFAULT

- `enabled=false`、`activation_approved=false`、`probe_status=NOT_RUN`、Worker Role inactiveを維持します。
- 既存の1000/day、900 Hard Stop、100予約、15 RPM、429時停止、日付切替後のProbe方針はOpenRouter専用として保持します。
- Exact free endpoint、Cost 0、Credits不変、Fallbackなし、応答Model一致を満たさない候補は選択しません。
- Workerは必ずSpecialist配下に限定し、直接Commanderにせず、Executionは明示承認済みの別段階まで禁止します。

### LIVE_PROBE_REQUIRED

1. 現行Catalogを読み取り、Roleごとの候補と必要能力を決める。固定候補や古いWeb紹介を根拠にしない。
2. 実行前にCatalog、Credits前後、Exact Model POSTを含む全Request数（最大7）、入力/出力Token、Cost、Timeout、Retry数をDry Run表示する。Cost/Credits不明なら開始しない。
3. Provider別の共有Request/Token reservation、15 RPM、900 Hard Stop、Concurrency上限を確認してから送信する。
4. 最大4件のExact ProbeをRetry 0で実行し、応答Model一致、`usage.cost=0`、Credits不変、Fallbackなし、Header、HTTP、Latencyを記録する。
5. Worker RoleごとのStructured Output/Tool/Context検査、401/403/404/429/402、Credits不明、Cost非ゼロ、Model mismatch、重複Requestを安全なFixtureで検証する。

### READY_REQUIREMENTS

`AUTH_OK`、Catalog上の`MODEL_AVAILABLE`、Exact `PROBE_OK`、`FREE_ACTIVE`条件、OpenRouter専用Quota、Request/Token reservation、共有Ledger、Concurrency、Retry-After/429停止、Worker Role/Tool Scope、Provider Circuit、Failure Injection、Secret Guard、明示承認が全て必要です。Token/TPM/TPDまたは共有Ledgerが不明な間は `OPENROUTER_WORKERS_READY=false` を維持します。

---

## READY判定の現状

| Flag | 判定 | 主な理由 |
|---|---:|---|
| `GOOGLE_READY` | `false` | Auth、Model、Quota、Probe、Reservation、Capability未確認 |
| `NVIDIA_READY` | `false` | Auth、NIM Model、Trial Credit、Quota、Probe未確認 |
| `GROQ_READY` | `false` | Dashboard値未採用、Rate Header未Probe、Token Guard未接続 |
| `OPENROUTER_WORKERS_READY` | `false` | Exact free Probe未実行、直近401記録、Worker Probeが共有Ledger未接続 |

### LIVE_PROBE_REQUIREDの共通事前条件

Live Probeを実行する場合は、Providerごとに次を別個に承認・記録します。今回の監査では全て未実行です。

```text
estimated_requests
estimated_input_tokens
estimated_output_tokens
estimated_cost
provider
exact_model
timeout
max_retries
reservation_store
concurrency_limit
```

`estimated_cost`、Quota、共有Ledger、Exact ModelのいずれかがUNKNOWNなら、自動実行もREADY昇格も行いません。Provider障害はProvider単位で停止し、他Providerを巻き込みません。

## Lane Aの結論

Provider境界、Role/Model分離、Paid Guard、未検証候補をActiveにしない方針、OpenRouter専用Hard Stop、基本的なError分類とDry Run入口は確認できました。一方、4つのQuota軸、Provider別Token/Request予約、Concurrency、Runner横断の共有Ledger、Probe全Requestの事前見積り、Retry-Afterを含む実動制御、Failure InjectionはREADYに必要な証拠が不足しています。

したがって、現時点でいずれのProviderもREADYへ昇格させず、外部Dashboard値やModel名をRegistryへ追加せず、Live Probe直前の安全停止を維持します。

`LIVE_PROBE_GOOGLE=false`

`LIVE_PROBE_NVIDIA=false`

`LIVE_PROBE_GROQ=false`

`LIVE_PROBE_OPENROUTER=false`

`PRODUCTION_ROUTING_CHANGED=false`

`AWAITING_USER_FINAL_APPROVAL`

## Commander follow-up after the Lane A snapshot

## External dashboard observations supplied by the user

The following values are recorded as account-dashboard observations only;
they are not Provider Registry limits, are not API-verified in this phase,
and are not used to authorize requests:

| Model label | RPM | RPD | TPM | TPD |
|---|---:|---:|---:|---:|
| `qwen/qwen3.6-27b` | 30 | 1000 | 8000 | 200000 |
| `qwen/qwen3.8-27b` | 30 | 1000 | 8000 | 200000 |
| `openai/gpt-oss-120b` | 30 | 1000 | 8000 | 200000 |
| `groq/compound` | 30 | 250 | 70000 | UNKNOWN |
| `groq/compound-mini` | 30 | 250 | 70000 | UNKNOWN |

The observation timestamp, account scope, plan revision, and API-header
mapping are unknown. No internal safety factor or soft limit was promoted
from these values; the correct current policy remains fail-closed until
official/API evidence is captured.

The following bounded changes were made after this read-only snapshot and
were tested locally without network access:

- `ProviderQuotaLedger` now takes a POSIX file lock and reloads the provider
  namespace inside the lock before each summary, reservation, result, or
  recovery operation. This closes the local multi-process race.
- `ProviderQuotaLedger.durability_status()` explicitly reports that
  cross-runner durability is not proven; it therefore cannot make a Provider
  READY.
- The runtime now rejects duplicate or out-of-order `response_version` values
  per `(mission_id, command_id)`.
- OpenAI-compatible probes now reject empty choices and empty messages as
  `MODEL_OUTPUT_INVALID`.

These follow-up changes improve local safety coverage but do not change any
Provider activation flag or authorize a Live Probe.
