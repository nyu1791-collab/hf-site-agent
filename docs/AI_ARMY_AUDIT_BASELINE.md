# AI部隊 第3次再編成・初回監査記録

監査日: 2026-09-08 UTC
Source of Truth: GitHub `main`
BASE_SHA: `6c65d35f45539459c428b96769ef240aee8ec2f1`
作業Branch: `ai-army/provider-v3`

この記録は、Provider追加前の正規mainを読み取り、変更前状態とリスクを固定するためのものです。Secret値、完全なPrompt、Provider本文、Credit残高は保存していません。

## A. 変更前のAI部隊構成

```text
ChatGPT Work
  └─ 旧OpenRouter上級Role（General / Engineering）
       └─ 既存Specialist
            └─ 既存Worker
```

Runtime、Delegation、Executor、Model Registry、Command/Report Envelope、OpenRouter処理、Free quota処理は存在しました。旧Qwen/DeepSeek等の固定IDはLegacy候補または旧Workflowに残っていました。Providerごとの直属Commander分離はありませんでした。

## B. 変更前のProvider一覧

実行経路として確認できた主ProviderはOpenRouterでした。WorkflowやSmoke処理には複数の互換Provider名が散在していましたが、Provider Registryによる一元管理はありませんでした。Google、NVIDIA、Groqの専用Adapter・Quota・Health状態は未接続です。

## C. 変更前のModel一覧

`config/model_registry.json` には16件の候補・Probe記録がありました。General / Engineering / Reserveの旧RoleにOpenRouter固定候補があり、Free Catalog Only、Authentication Failed、Paid approval required等が混在していました。正確な現行Catalogと実Endpointが一致するまでActiveにしない安全方針は存在しましたが、新3CommanderのProvider別Roleは未定義でした。

## D. Secret参照一覧（値は非表示）

RepositoryとWorkflowの環境参照を確認しました。代表的な参照名は `AI_API_KEY`、`GROQ_API_KEY`、`HF_TOKEN`、`GITHUB_TOKEN`、Worker管理用環境参照などです。値は表示・取得・ログ出力していません。初回監査では、外部バックアップ側に平文Binding候補がある可能性をリスクとして記録しましたが、削除・ローテーション・再利用は実施していません。

## E. 変更前の無料枠管理

OpenRouter向けにFree Usage Ledger、送信前予約、日次1000 requests、900 Hard Stop、15 RPM、429時停止、UTC日付切替後のProbeが実装されていました。Provider別LedgerではなくOpenRouter中心の管理で、Google/NVIDIA/Groqへ同じQuota契約を適用する構造はありませんでした。

## F. 変更前の重大障害

- CRITICAL-A: Runtimeの共有Cancellation状態により、Mission Aの取消がMission Bへ波及する可能性。
- CRITICAL-B: `idempotency_key=null`、空文字、空白を許容し、副作用の永続的な重複防止が不十分。
- CRITICAL-C: Repository・Workflow・外部バックアップにSecret候補が混在する可能性。
- Provider別Registry、Adapter、Quota/Circuit、3Commander Routing、動的Worker選定が未接続。
- Worker/関連APIのHealthはこの監査では外部変更なしのため未検証。

## G. 変更候補

Priority順に、`scripts/agent_runtime.py`、`schemas/*envelope*`、`scripts/audit_secret_safety.py`、`config/provider_registry.json`、`config/model_registry.json`、Provider Adapter/Quota/Circuit、Commander Routing、Worker Probe、関連Unit/Integration Test、検証Workflowを対象としました。

## H. 変更しない対象

Production Worker、Durable Object、既存Binding、永続データ構造、決済・Credits・収益化、公開投稿、無関係なPvP/サイト機能、Secret値・Secret名、force push、既存成果物の削除は変更していません。

## I. リスク一覧

1. Google/NVIDIA/Groqの正確なEndpoint、Model ID、Quota、無料条件が未確認です。
2. 実Provider Probe、Commanderの20〜50件Mission比較、Worker Health、P50/P95性能比較は未実施です。
3. CLIからGitHub remoteを認証取得できなかったため、ローカル検証ミラーのGit SHAは正規main SHAと同一ではありません。remote Branchの起点はGitHub APIでBASE_SHA一致を確認しました。
4. 旧Workflowは互換入口として残っており、Legacy停止・新Routing入口のActions検証を継続します。

## J. ロールバック方法

本番mainへの直接変更・Deployは行いません。作業Branchと小Commit単位を保持し、問題があれば対象PRを閉じるか、レビュー済みの対象Commitだけを通常のrevertで戻します。`git reset --hard`、force push、既存成果物・Checkpoint・データ削除は使用しません。Provider有効化前は全Role/Providerが無効・未承認なので、切替停止だけで旧経路へ戻せます。

## 変更前Baseline

| 検査 | 結果 | 備考 |
|---|---|---|
| Python syntax / JSON parse | PASS | Python 15ファイル、JSON 11ファイルを静的検査 |
| Runtime / Hierarchy / Quota / Model Registry | PASS | 既存契約テスト |
| Delegation | PASS | 一時的なOpenAI互換SDK環境のみ使用、Provider APIは未呼出 |
| Fixture / Command / Report | PASS | 親子・Schema契約を確認 |
| Secret literal audit | 実装前未実施 | 再編成Branchで追加監査を実施 |
| External Provider Probe | NOT_RUN | Secret/正確なProvider設定の承認前 |
| Worker / `/health` | NOT_RUN | 外部状態の変更なし |

既存テストは、依存SDKがない初期環境ではDelegationのImport Errorが1件ありました。安全な一時環境へ限定依存を入れた再Baselineでは既存41件がPASSしました。これはRepositoryへ依存追加した記録ではありません。
