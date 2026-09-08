# Agent Hierarchy

## 指揮系統

唯一のRootは `chatgpt-work` です。RootがMissionを解釈し、適任の直属CommanderへCommandを発行します。Commanderは許可されたSpecialistだけを生成し、Specialistは同じTaskのOpenRouter Workerだけを限定的に生成します。WorkerからRoot、別Commander、同格Agentへの直接報告・委任はありません。

| Agent | Provider | 生成可能な直接の子 | 初期状態 |
|---|---|---|---|
| `chatgpt-work` | ChatGPT Work | Google / NVIDIA / Groq Commander | 承認主体 |
| `google-general-commander` | Google | Research / Data / Media / Planning系 | inactive |
| `nvidia-engineering-commander` | NVIDIA | Repository / Coding / Test / Debug系 | inactive |
| `groq-rapid-commander` | Groq | Summary / Classifier / JSON / Log系 | inactive |
| `<role>-specialist` | 親Commanderと同じProvider | 自分のWorker 1系統 | inactive |
| `<role>-worker` | OpenRouter | なし | inactive |

AgentSpecの `parent_agent_id` は直接の親だけを示します。`owner_agent_id` は各Taskに必須で、同じWorkerへ複数Commanderが同時に命令することを禁止します。ChildのToolとPermissionは親のScope内に限定し、Workerには `artifact_read`、`artifact_write`、`trace` だけを与えます。

## Routing原則

- Research、Planning、Long Context、MultimodalはGoogle。
- Repository、Coding、Debug、Test、InfrastructureはNVIDIA。
- 大量要約、分類、抽出、JSON変換、Log一次判定はGroq。
- 軽量実働WorkerはSpecialist経由でOpenRouter。
- 重複排除、Sort、Hash、JSON/Schema、Quota、Retry、Queue、CacheはPython。

同じTaskを3部隊へ送る多数決は行いません。必要な独立検証だけをPrimary＋Verifierの2経路に限定します。失敗した下位Agentは上位モデルへ自動昇格せず、`blocked` または `failed` を親へ返します。

## AgentSpecの安全条件

- `active=false`、`requires_explicit_approval=true` を初期値とする。
- `allowed_children` 以外の子を生成しない。
- `may_spawn_children=false` のWorkerはSpawnしない。
- `max_children`、`max_parallel`、`max_depth` は有限値にする。
- `provider_id` と `model_binding_role` はRegistry解決用で、Model IDをソースへ直書きしない。
- CommandはRead-only DraftまたはDry Runから開始し、公開・決済・Deploy・Secret/Binding変更は別承認とする。

## 状態と復旧

Command状態は `queued → running → completed / completed_with_warnings / blocked / failed / cancelled` です。Runningの取消は一時的に `cancelling` を経由します。Mission取消はそのMissionと祖先・子孫だけへ伝播し、Sibling・別Mission・保存済みArtifact・Checkpointを変更しません。

Idempotency台帳には `mission_id`、`command_id`、`idempotency_key`、`operation_type`、`payload_hash` を保存します。Timeout・Connection Error後の副作用Operationは再送せず、同じKeyでPayloadが変われば `IDEMPOTENCY_CONFLICT` で拒否します。

## 承認ゲート

1. 基盤テスト（Cancellation、Idempotency、Secret監査、Schema）を通す。
2. ProviderごとにAuth、Model、最小Probe、Quota、Healthを確認する。
3. Commander契約テストとWorker契約テストをRead-onlyで実施する。
4. 20〜50件の実Mission比較を行い、JSON率、Tool成功、Latency、Token、429、Cost、指示遵守を記録する。
5. ChatGPT Workが採用Roleと切替範囲を明示承認する。
6. 小さなPR、全Regression、Worker/health、Actionsを確認する。

完了状態は自動的に本番確定へ進まず、`AWAITING_USER_FINAL_APPROVAL` で停止します。

