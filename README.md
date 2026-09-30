
**動画制作は品質2・速度8。** `config/media_speed_quality_policy.json#/speed_first_delivery` を新しいタブの制作前に必読する。通常のプレビュー・目視確認・追加査読・微細な修正・品質向上を省き、書き出し成功・非空の出力・音声/映像streamが揃えば即時提出する。既存の型と素材を再利用し、独立準備のみ最大3レーンで並列化する。

# hf-site-agent

AI Army / Provider-v3 の実験・検証リポジトリ。

## Source of Truth と新セッション復元

会話履歴や古いhandoffをこのプロジェクトの正本にしない。作業開始時は現在の `ai-army/provider-v3` HEAD と PR #40 の状態を確認し、次の4ファイルをBootstrapとしてこの順に読む。

1. `README.md`
2. `config/current_commander_handoff.json`
3. `config/permanent_standards_manifest.json`
4. `docs/AI_ARMY_MASTER_RULEBOOK.md`

この4ファイルは全ルールの複製ではない。詳細は `config/permanent_standards_manifest.json` から、依頼の意味に応じたSemantic Gateを解決する。

- 動画・音声・字幕・キャラクター・BGM・SFX・画像素材・切り抜き・TikTok Shopメディア: `config/media_command_read_gate.json`
- VOICEVOXの現在のローカル実行状態・起動・復旧手順: `docs/VOICEVOX_RUNTIME.md` / `scripts/with_local_voicevox.sh`
- 現行動画設定をタブ跨ぎで復元するCheckpoint: `config/current_media_quality_handoff.json`
- 品質20％・速度80％、5分は未計測目標、即時提出と「話題ごとの見出し・平易な言葉・要点優先」: `config/media_speed_quality_policy.json`
- 既存テンプレートや口パク等のスタイル詳細は、ユーザーがその形式を指定した場合だけ読む。通常動画の必須条件にはしない。
- 収益化・案件・アフィリエイト・Creator Program・AI workflow service: `config/monetization_command_read_gate.json`

### Semantic know-how recall

Repositoryへ保存してあるKnow-howは「置いてあるだけ」にしない。Bootstrap完了後、**各ユーザー依頼を文字列一致ではなく意味で分類し、該当するGate / Policy / Playbookの現行版を実際に読み直してから計画する。** 一つの依頼に複数の意味がある場合は最初の1分類で止めず、必要なRead Setを和集合で復元する。

- `動画制作`、`コンテンツを作る`、`これを動画にする`のような包括表現は、周辺文脈から VIDEO_CREATION / LONGFORM / CLIPPING_REPURPOSING / TIKTOK_SHOP_COMMERCE を意味分類する。ユーザーがPolicy名や「切り抜き」「Shop」という完全一致語を言わなくても、実質的にその作業なら該当Know-howを復元する。
- 他人または既存素材を短く再編集する、ハイライト化する、再利用する、縦動画へ展開する意味ならAuthorized ClippingのRights / Originality / ASR / Alignment / Dedup / Reframe / QAを読む。
- 商品を紹介して売る、購入へつなげる、Shop動画、商品訴求、レビューや価格を扱う意味ならTikTok Shop / CommerceのEvidence / Claim / Freshness / Persona / Funnel / Experiment know-howを読む。収益・コミッション・案件・Affiliateまで含む場合は `config/monetization_command_read_gate.json` も加算する。
- `動画で稼ぐ`、`商品動画を収益化`、`切り抜きで収益化`のような複合意図ではMedia GateとMonetization Gateの両方を読む。片方だけで済ませない。
- 動画以外でも、収益化・案件・Affiliate・Membership・Creator Program・AI workflow service・Lead generation・Licensing・Productizationの意味ならMonetization Gateを復元する。
- AI Army / Agent / Provider / Model / Routing / CI / Failure recovery / Efficiency / DeepSeek運用の意味ならPermanent ManifestからOrg Chart、Multi-agent Policy、DeepSeek Supervisor Policy、CI Control Plane、Efficiency / Recovery系の現行Authorityを復元する。
- ニュース、商品Claim、Platform Program、数値、実験、ROI、Evidenceの正確性が重要な依頼ではCross-source Evidence、Measurement、Second-pass、Artifact Contract、Platform Evidenceを必要に応じて復元する。

ユーザーに「前に保存したファイル名」や同じ仕様をもう一度言わせることを前提にしない。低コストで判断できる曖昧さなら関連Read Setを少し広めに復元するが、毎回Repository全体を無差別に読むこともしない。**Semantic Recallは `保存 → 意味判定 → 現行Repository再読 → 適用` までを1セットとする。** 同一HEAD・同一Blobを同一タスク内ですでに読んでいる場合だけ、安全なRead Cache再利用を許容する。

`config/current_media_quality_handoff.json` は現行の短い継続サマリーで、最終Authorityではない。動画タスクでは `config/media_command_read_gate.json#/speed_first_delivery_override/read_set` の12ファイルを通常の読込セットとして使う。長尺の制作方針は明示された尺に必要な資料だけを追加し、YMM4資料はYMM4を指定された場合だけ読む。VOICEVOXの起動・復旧、55〜60秒ニュース短尺、プラットフォーム別の書き出し、既存素材の切り抜き、商品訴求、現在の事実確認も、実際に該当するときだけ資料を追加する。通常動画はJevを必須にしない。

詳細が文書間で異なる場合は、最新の明示的ユーザー指示と安全境界を守ったうえで、現行Machine-readable Policy・Validator・CIを優先する。恒久ルールを変更する場合は会話だけで終わらせず、Machine Policy / Rulebook / Validator / CI / Read Gateの整合性を同じ変更で確認する。

## AI Army の固定境界

- ChatGPT / Work がTop Commanderかつ最終判断者。
- 有料DeepSeekは `config/deepseek_paid_supervisor_policy.json` の範囲だけで使うExecutive Supervisor。全タスクの必須hopでも大量boilerplate coderでもない。
- DeepSeekはWorking Managerとして、調査、Evidence Triage、台本/レポート草案、Task Packaging、下位作業の割当設計・査読、関連する低リスク事務作業まで担当できる。ただし明確に速く正確なDeterministic Toolを置き換えない。
- Deterministic ToolまたはSingle Agentで十分ならそれを優先する。
- Single Writerを維持し、同一mutable targetの並列変更にはTask Leaseを要求する。
- 最大Delegation Depthは2、1ユーザー依頼あたり最大10 Tasks。無限Swarm・無限Reflection・無限Replanは禁止。
- Machine Oracle / Schema / Test / Hash / ffprobe等をAI多数決より優先する。
- 通常Routeはfree-first。Auto Top-up、Generic Paid Fallback、Paid sibling自動置換は禁止。
- DeepSeek例外は他の有料Provider、Repository Write、main Push、PR Merge、Deploy、Publish、Secrets操作、支払い操作へ権限を拡張しない。

## 動画制作

新しいタブでは動画作業前に `config/media_speed_quality_policy.json#/speed_first_delivery` と `#/script_clarity_contract` を読み、品質20％・速度80％で運用する。見出しは話題ごとに分けて内容が一目で分かる言葉にし、台本は要点から始める。一般的な言葉を使い、専門語は必要な場合だけ一度説明する。理解や判断に影響しない細部、長い前置き、同じ注意の繰り返しは省く。確認待ち・通常の目視レビュー・追加査読・微細な修正を省き、非空の出力と音声/映像streamを一度確認したら即提出する。

既存テンプレート、音声、素材を再利用し、最大3つの独立した準備だけ並列化する。約5分は未計測の目安。詳しいルールの二重記載はせず、`config/media_command_read_gate.json` と上記の速度ポリシーを正本とする。

## CI / Compatibility

CIの実行権限と自動fan-outは `config/ci_execution_policy.json` を正本とし、`scripts/ci_control_plane_guard.py` で検査する。旧実験コードを保持する場合でも、それだけで現行Routing権限・有料実行権限・自動発火権限を復活させてはならない。

`config/legacy_deepseek_compatibility.json` は古いDeepSeek設定を実行するためのファイルではなく、旧経路が現行Authorityへ復帰しないことを検証するための互換・回帰ガードである。削除理由がない限り履歴的なコードやテストを「古い名前」だけで削除しない。

## Hard Boundaries

明示された権限がない限り、main直接Push、PR Merge、本番Deploy、公開Publish、Secrets変更・開示、Durable Object変更、Auto Top-up、Generic Paid Fallback、不可逆な外部操作を行わない。
