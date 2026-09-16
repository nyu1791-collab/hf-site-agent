# Direct API Validation — Manual Diagnostic Reference

**Status:** manual/read-only Provider diagnostic. This document does not define the current AI Army hierarchy or activate routing.

この経路は、Google / NVIDIA / Groq等の外部Providerについて、Catalog、Exact Model ID、Auth/Health、Quota/Cost、Capabilityを**手動かつboundedに評価するための互換診断**です。検証中はProvider/Model Registryを変更せず、本番Routing・Deploy・Publish・Secrets変更を行いません。

現在の指揮系統・実行権限・Paid exceptionは `config/current_commander_handoff.json`、`config/permanent_standards_manifest.json`、`docs/AI_ARMY_MASTER_RULEBOOK.md`、`scripts/ai_army_routing_facade.py` と関連Machine Policyを優先します。この診断で高評価になったProvider/Modelも、それだけでCommander、Specialist、ACTIVE、Production routeには昇格しません。

## ゲート順

1. Current Provider Catalog / account evidenceからExact Model IDとEndpointを確認する。
2. Quota、Credit、Rate Limit、Free/zero-cost条件、Paid transition有無を確認する。不明値を無制限・無料とみなさない。
3. Catalogに実在する候補だけを、bounded request / token / timeout / retry条件で最小Probeする。
4. Probe応答Model、Usage、Cost/Quota evidenceを検証する。
5. 必要なStructured Output、Tool Calling、Schema、Mission能力を対象範囲だけ確認する。
6. Machine-readable reportを保存し、現在のPolicy Gateで別途adjudicateする。

Unknown cost、Quota不明、Paid transition、401/403/402、Credit exhaustion等はFail Closedです。429/5xx/timeoutも無限Retryや別の有料Modelへの自動Fallback理由にはしません。

## 実行境界

実装が要求する明示的なnetwork flag / confirmation token / request ceilingを省略しません。CLIやWorkflowの現在の引数・上限はコードをSource of Truthとして確認し、この文書へ固定値を恒久複製しません。

Secretは環境参照からのみ読み、値を標準出力、JSON、Artifact、Commitへ出しません。候補Modelを入力できる場合でも、current catalog/evidenceで確認できないIDは実行候補にしません。

## Free access / readiness

Free/zero-cost判定はProviderごとの現在のofficial/account evidenceを使用します。`FREE_TIER`、`FREE_PLAN`、`FREE_ENDPOINT`、`:free`等の分類名や過去の成功記録だけで現在のCost=0を断定しません。

次の概念を分離します。

- Model discovered
- Exact route verified
- Capability verified
- Zero-cost/free eligibility verified
- Quota safe
- Probe passed
- Candidate for a task
- Current routing authorization
- Production activation

前段の成功は後段を自動許可しません。

## 結果の扱い

Direct API Validationの成果物は**診断Evidence**です。Registry mutation、Provider activation、Paid fallback、Production routing、Deploy、Publish、Secret mutationを自動実行しません。ChatGPT / Workが現行Policy・Validator・CIと合わせて最終判断します。

外部ProviderのModel名、料金、Quota、Rate Limit、Endpointは変化するため、古い結果はfresh evidenceの代わりになりません。新しい実行前に再確認します。
