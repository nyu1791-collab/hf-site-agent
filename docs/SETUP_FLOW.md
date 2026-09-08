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
2. `confirm=CONNECT`
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
2. `confirm=SYNC`
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
2. `confirm=DESIGN`
3. `brief`: 今回検討したい機能を3000文字以内
4. `context`: 制約を6000文字以内（任意）
5. Model: `openrouter/free`
6. 実行

外部AIは反対意見・改善案・受け入れテストだけを返します。リポジトリ変更、公開、YouTube、決済は実行できません。

## 4. 中国AI分担会議（planner → critic）

設計課題を中国系の無料モデルへ分担させる読み取り専用Workflowです。Qwen系plannerが最小案を作り、DeepSeek系criticが同じ案を反対検証します。司令部が結果を確認してから、採用する変更だけをPRにします。

- [中国AI分担Workflow](https://github.com/nyu1791-collab/hf-site-agent/actions/workflows/agent-delegation.yml)
- Branch: `main`
- `confirm=DELEGATE`
- Brief: 3000文字以内（秘密値を書かない）
- Context: 6000文字以内（任意）
- Planner model: `qwen/qwen3-32b:free`
- Critic model: `deepseek/deepseek-chat-v3-0324:free`

1回の実行は2回の短いAPI呼び出し（各最大320 tokens、タイムアウト12秒、リトライ0）だけです。自動フォールバック、リポジトリ変更、デプロイ、公開、YouTube、決済、有料検索は行いません。402・429・タイムアウト時は停止します。

## 5. 費用ゲート

- Tavily引用検索は明示選択時だけ、既存の無料枠・最大10件で実行します。
- 有料検索はこの自動保守では実行しません。将来の有料経路は、事前の費用確認・上限・明示承認が揃わない限り送信しない設計にします。
- OpenRouterは`:free`モデルだけを分担Workflowで受け付け、無料クレジット不足（402）や課金要求が出た場合はフォールバックせず停止します。
- AI同士の短い会話は司令部の入力を減らせますが、APIトークンを自動的に無料にするものではありません。要約・最大トークン・呼び出し回数を固定して総量を抑えます。

## 6. 利用画面の実行順

1. 業種テンプレートを選択。
2. 必要なら「引用付きコンテンツ計画」で無料検索を明示選択。
3. コードを生成。
4. DeepSeekレビューを実行。
5. 内容を確認し、チェックを入れてからPush。
6. 次回は「前回内容を再利用」で更新。
7. 生成・計画・レビュー・更新再利用・承認Pushの回数を端末内で確認。

課金検索、有料フォールバック、YouTube投稿はこのフローにありません。
