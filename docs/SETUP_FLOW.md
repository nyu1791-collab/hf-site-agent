# 登録・実行フロー

このプロジェクトでは、トークンの用途を混ぜません。YouTube投稿・決済・収益化設定は未接続です。

## 1. Hugging Face推論用（既存 HF_TOKEN）

Hugging Faceのトークン作成画面で次を選びます。

- Token type: Fine-grained
- Preset: **Inference**
- `Make calls to Inference Providers`: 有効
- リポジトリWrite、Jobs、Billing、Endpoint管理: 無効

作成後、値をGitHub Actions Secretの `HF_TOKEN` に登録します。これはDeepSeekレビューなど推論専用です。Spaceへのpushには使いません。

- [HFトークン作成](https://huggingface.co/settings/tokens/new?tokenType=fineGrained)
- [GitHub Actions Secrets](https://github.com/nyu1791-collab/hf-site-agent/settings/secrets/actions)
- [推論接続Workflow](https://github.com/nyu1791-collab/hf-site-agent/actions/workflows/connect-hf-inference.yml)

推論接続Workflowの入力:

1. Branch: `main`
2. `confirm`: `CONNECT`
3. 実行

## 2. Hugging Face Space書込み用（HF_SPACE_WRITE_TOKEN）

HF画面で別トークンを作成します。

- Token type: Fine-grained
- Preset: **Write**
- 対象を `yu-179191/groq-github-site-agent` のみに限定
- Spaceのファイル書込みを許可
- Full Access、CI/CD、Jobs、Billingは選ばない

作成後、GitHub Secretを **`HF_SPACE_WRITE_TOKEN`** という名前で追加します。既存の `HF_TOKEN` は編集しません。

- [GitHub Actions Secrets](https://github.com/nyu1791-collab/hf-site-agent/settings/secrets/actions)
- [Space同期Workflow](https://github.com/nyu1791-collab/hf-site-agent/actions/workflows/sync-hf-space.yml)

Space同期Workflowの入力:

1. Branch: `main`
2. `confirm`: `SYNC`
3. 実行
4. `Hugging Face frontend safety contract is live.` を確認

Write Secretが未登録のpushは、同期せず通知だけで終了します。推論用トークンでの誤書込みは行いません。

## 3. OpenRouter設計会議用（既存 AI_API_KEY）

OpenRouterのAPIキーはHugging Faceのプリセットではありません。GitHub Secret `AI_API_KEY` に登録済みのキーを使います。

- [OpenRouter API Keys](https://openrouter.ai/settings/keys)
- [プロバイダー疎通Workflow](https://github.com/nyu1791-collab/hf-site-agent/actions/workflows/check-openai-compatible-provider.yml)
- [設計会議Workflow](https://github.com/nyu1791-collab/hf-site-agent/actions/workflows/design-council.yml)

設計会議Workflowの入力:

1. Branch: `main`
2. `confirm`: `DESIGN`
3. `brief`: 今回検討したい機能を3000文字以内
4. `context`: 制約を6000文字以内（任意）
5. Model: `openrouter/free`
6. 実行

外部AIは反対意見・改善案・受け入れテストだけを返します。リポジトリ変更、公開、YouTube、決済は実行できません。

## 4. 利用画面の実行順

1. 業種テンプレートを選択。
2. 必要なら「引用付きコンテンツ計画」で無料検索を明示選択。
3. コードを生成。
4. DeepSeekレビューを実行。
5. 内容を確認し、チェックを入れてからPush。
6. 次回は「前回内容を再利用」で更新。
7. 生成・計画・レビュー・更新再利用・承認Pushの回数を端末内で確認。

課金検索、有料フォールバック、YouTube投稿はこのフローにありません。
