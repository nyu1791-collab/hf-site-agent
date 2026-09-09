# Direct API Validation

このPhaseは、Google / NVIDIA / Groqを直属Commander候補として実証するための手動・読取専用検証です。検証中はProvider/Model Registryを変更せず、`enabled=false`、`activation_approved=false`、本番Routing未接続を維持します。OpenRouterはCommander候補に含めず、既存の専用Free Worker Probeで別管理します。

## ゲート順

1. Providerの公式Model Catalogを1回だけ認証付きで取得し、Health/Authを同時に確認する。
2. Quota・Credit・Rate Limitを確認する。不明なQuotaを無制限とは扱わない。
3. Catalogに実在する候補だけを、正確なModel IDで1回ずつ最小Probeする。
4. `PROBE_OK`、Usage parse、無料アクセスの証拠が揃った候補だけをCapability Testへ進める。
5. Structured Output、Tool Calling、Command Schemaを確認する。
6. 共通6 MissionとProvider固有Missionを実行し、100点の比較表を作る。
7. Hard Failがなく、採点条件を満たす候補だけを`COMMANDER_CANDIDATE`、最終選抜候補を`COMMANDER_SELECTED`としてレポートする。

Probeは1回限り・Retry 0です。401/403/429/402、Credit枯渇、Provider停止を検知したら同じProviderへの追加候補Probeを止めます。実行は`--network`と完全一致する`--confirm DIRECT_API_VALIDATION`の両方が必要です。

## 実行例

デフォルトは通信しないdry-runです。

```bash
python scripts/direct_api_validation.py \
  --network \
  --confirm DIRECT_API_VALIDATION \
  --capabilities \
  --missions \
  --output artifacts/direct_api_report.json
```

必要なSecretは環境変数から読むだけです。値は標準出力、JSON、Artifact、Commitへ出しません。候補Modelは環境変数で明示できますが、Catalogに存在しないIDはProbeしません。

```text
GOOGLE_API_KEY
NVIDIA_API_KEY
GROQ_API_KEY
```

`GOOGLE_CANDIDATE_MODELS`、`NVIDIA_CANDIDATE_MODELS`、`GROQ_CANDIDATE_MODELS`はカンマ区切りで指定できます。未指定時も候補ヒントをCatalogとの完全一致に使うだけで、CatalogにないIDを採用しません。

## Free accessの分類

Provider Registryでは、`FREE_TIER`、`FREE_PLAN`、`TRIAL_CREDITS`、`FREE_ENDPOINT`、`PAID`、`UNKNOWN`を区別します。`UNKNOWN`と`PAID`は無料検証を通過できません。GoogleのQuota情報、NVIDIAのCredit/Quota情報、GroqのRate Limit Headerが取得できない場合は、成功応答だけで`FREE_ACCESS_CONFIRMED`にしません。

## 公式Source

- Google AI for Developers: <https://ai.google.dev/gemini-api/docs/models>
- Google Generate Content API: <https://ai.google.dev/api/generate-content>
- NVIDIA Build / NIM: <https://build.nvidia.com/models>
- GroqCloud Models: <https://console.groq.com/docs/models>
- GroqCloud Rate Limits: <https://console.groq.com/docs/rate-limits>
- OpenRouter Models API（Worker専用）: <https://openrouter.ai/api/v1/models>

## 停止状態

レポートの最終状態は常に`DIRECT_API_VALIDATED_AWAITING_ACTIVATION`です。Commander選抜が成功しても自動有効化・本番Routing・Deploy・Publishは行いません。OpenRouter Worker検証が別途成功し、利用者が承認した後にのみActivation Phaseを開始します。
