
**動画制作は品質2・速度8。** `config/media_speed_quality_policy.json` を新しいタブの制作前に必読する。16分以内の長尺は5分の作業時間目標で計測し、既製の公式発表画像・製品画面・発表文の引用画像を使う。画像生成は使わない。長尺も承認済みのずんだもん・四国めたんの型で一括書き出しし、段落単位の音声合成と素材準備を独立並列化する。出力が使える状態ならすぐ提出し、必須の事実・権利確認は守る.

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
- 品質20％・速度80％、16分長尺の5分作業目標、公式画像のみ・画像生成禁止、即時提出と「話題ごとの見出し・平易な言葉・要点優先」: `config/media_speed_quality_policy.json`
- 承認済みの型と口パク設定は `config/approved_video_template.json` と `docs/VIDEO_PRODUCTION_BASELINE.md` を毎回の新タブで必読し、通常動画でも維持する。
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

`config/current_media_quality_handoff.json` は現行の短い継続サマリーで、最終Authorityではない。動画タスクでは `config/media_command_read_gate.json#/speed_first_delivery_override/read_set` の必須ファイルを通常の読込セットとして使う。長尺の制作方針は明示された尺に必要な資料だけを追加し、YMM4資料はYMM4を指定された場合だけ読む。VOICEVOXの起動・復旧、55〜60秒ニュース短尺、プラットフォーム別の書き出し、既存素材の切り抜き、商品訴求、現在の事実確認も、実際に該当するときだけ資料を追加する。通常動画はJevを必須にしない。

詳細が文書間で異なる場合は、最新の明示的ユーザー指示と安全境界を守ったうえで、現行Machine-readable Policy・Validator・CIを優先する。恒久ルールを変更する場合は会話だけで終わらせず、Machine Policy / Rulebook / Validator / CI / Read Gateの整合性を同じ変更で確認する。

## 常駐監視・ニュース動画の自動準備

VPS運用の手順は `docs/VPS_MEDIA_NEWS_AUTOMATION.md`、実装詳細は `docs/DURABLE_MEDIA_AUTOMATION.md` を正本とする。`scripts/media_source_daemon.py` とsystemd user timerは5分ごとに公式RSSをSQLite inboxへ取得し、対象記事があれば1件ずつ台本・ローカルVOICEVOX音声・画像候補まで進める。台本用の有料APIは、ユーザーが指定したOpenRouterの `deepseek/deepseek-v4.1-flash` のみで、1回0.05 USD、1日予約0.10 USD、UTC月0.50 USD、1日5回を上限とする。ChatGPT API、別有料モデルへのフォールバック、自動トップアップは不要。画像の権利確認前に停止し、公開は無効。2026-10-02のユーザー提供画面は `Linger=yes` とtimer `active` を示すが、PR版のVPS導入、再起動後の有効性、RSS/API/VOICEVOX/MP4の成功は未確認。

記事RSS監視・ニュース動画自動化では新タブ開始時に `config/media_command_read_gate.json` の `if_user_requests_article_rss_or_resident_news_video_automation` を解決し、列挙された現行Policy・Runner・systemd設定を読む。画像は自動取得後も権利未確認で停止し、選択した全画像に利用根拠・根拠URL・クレジットが揃うまでレンダーしない。ステータス確認ではfeedの鮮度と未処理キューの滞留時間を表示する。公開投稿は別境界で扱い、成功状態は `READY_TO_PUBLISH` で止める。

## AI Army の固定境界

- ChatGPT / Work がTop Commanderかつ最終判断者。
- 有料DeepSeekは `config/deepseek_paid_supervisor_policy.json` のExecutive Supervisor範囲と、ユーザーが許可した常駐公式RSS台本の正確なDeepSeek V4.1 Flashルートに限る。後者はテキスト台本専用で、固定費用上限は `config/media_news_pipeline_policy.json#/paid_script_generation` に定義する。
- DeepSeekはWorking Managerとして、調査、Evidence Triage、台本/レポート草案、Task Packaging、下位作業の割当設計・査読、関連する低リスク事務作業まで担当できる。ただし明確に速く正確なDeterministic Toolを置き換えない。
- Deterministic ToolまたはSingle Agentで十分ならそれを優先する。
- Single Writerを維持し、同一mutable targetの並列変更にはTask Leaseを要求する。
- 最大Delegation Depthは2、1ユーザー依頼あたり最大10 Tasks。無限Swarm・無限Reflection・無限Replanは禁止。
- Machine Oracle / Schema / Test / Hash / ffprobe等をAI多数決より優先する。
- APIモデルは有料という理由だけで一律禁止しない。追加候補は config/paid_agent_route_eligibility_policy.json に基づき、DeepSeek V4.1 Flashより低い実タスク総費用と、必要機能を満たしたうえでの実測優位が確認できた場合だけ選択する。
- Auto Top-up、Generic Paid Fallback、Paid sibling自動置換、有料の動画/画像生成・TTS・編集は引き続き禁止。例外は正確なDeepSeek V4.1 FlashによるRSS台本テキストだけ。Vertex AIは現在不要・無効。
- VPSの永続稼働状態はライブホスト確認が必要で、リポジトリの設定だけを根拠に稼働中と報告しない。Publish先・認証・対象範囲の証拠が揃うまで公開処理を停止する。

## 動画制作

新しいタブでは動画作業前に `config/media_speed_quality_policy.json` と `config/media_source_policy.json` を読み、品質20％・速度80％で運用する。 尺の指定と制作時間目標を混同せず、過去の長さ指定と食い違う場合は最新の明示的な動画尺を採用する。台本・音声を尺に合わせてから書き出し、音声の早回しで合わせない。「5分で作る」は制作所要時間の目標で、動画尺ではない。動画尺は直近の明示指定を引き継ぎ、縦横・背景・字幕色・キャラクターは承認済みの型を既定とする。この再発防止ルールは新しいタブでも同じRead Gate経由で必ず復元する。16分までの長尺は5分の作業時間目標を測定する。長尺は `scripts/render_reusable_short.py` で承認済みのキャラクター・口パク・字幕と音声を一度に書き出し、各大見出しに関連する写真・製品画面・スクリーンショット・説明図を2〜4場面使う。画像生成は禁止。発表ページの必要箇所だけを切り取り、ブラウザ・再生UIを映さない。公式発表や公式コメントの画面を優先するが、公式サイト掲載だけでは再利用権があるとはみなさない。画像がない場合だけ、短い出典付き文字カードを使う。段落単位で音声を合成し、文ごとの多数の合成呼出しを避ける。見出しと平易な台本、権利記録、事実確認は維持し、任意の装飾や確認待ちは省いて提出する。

既存テンプレート、音声、素材を再利用し、最大3つの独立した準備だけ並列化する。約5分は未計測の目安。詳しいルールの二重記載はせず、`config/media_command_read_gate.json` と上記の速度ポリシーを正本とする。

## CI / Compatibility

CIの実行権限と自動fan-outは `config/ci_execution_policy.json` を正本とし、`scripts/ci_control_plane_guard.py` で検査する。旧実験コードを保持する場合でも、それだけで現行Routing権限・有料実行権限・自動発火権限を復活させてはならない。

`config/legacy_deepseek_compatibility.json` は古いDeepSeek設定を実行するためのファイルではなく、旧経路が現行Authorityへ復帰しないことを検証するための互換・回帰ガードである。削除理由がない限り履歴的なコードやテストを「古い名前」だけで削除しない。

## Hard Boundaries

明示された権限がない限り、main直接Push、PR Merge、本番Deploy、公開Publish、Secrets変更・開示、Durable Object変更、Auto Top-up、Generic Paid Fallback、不可逆な外部操作を行わない。


承認済みのキャラクター動画を通常の既定とする。品質2・速度8は追加の磨き込みを省く方針であり、ずんだもん・四国めたん、口パク、承認済み背景・字幕色・画面形式を削除する許可ではない。静止画のみの別形式への切替はユーザーが明示した場合に限る。タブ切替時も `config/approved_video_template.json` と `docs/VIDEO_PRODUCTION_BASELINE.md` を制作前に必読する。


関連画像を多く使う構成・実際の二人の会話・平易な解説を制作の必須入力とする。各主要章に関連写真、製品画面、スクリーンショット、説明に役立つ図解を2〜4場面使う。公式素材は優先候補であり必須の限定条件ではない。自作の説明図も使えるが、文字だけのカードや同じ画像の使い回しは画像数に含めない。生成画像は禁止。素材の出典と利用根拠を保持する。ずんだもんと四国めたんは双方が実際に発話し、説明と視聴者の質問・言い換えを分担する。キャラクター表示だけで二話者とは扱わない。見出し、要点、平易な説明、必要なら短い具体例の順で説明し、モデル名・数値・専門語の羅列を避ける。新タブでも速度ポリシーの dialogue_contract / visual_density_contract / script_clarity_contract を必読する。入力は `scripts/validate_video_content_contract.py` で書き出し前に一度確認し、任意の査読や磨き込みは増やさない。
