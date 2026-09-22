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
- 現在の動画品質・視聴維持・時短改善をタブ跨ぎで即復元する補助Checkpoint: `config/current_media_quality_handoff.json`
- 10〜15分目標の品質維持型メディア時短（マニフェストキャッシュ、最大3準備レーン、影響範囲修復、短尺1回エンコード、Jev typed計画）: `config/media_speed_quality_policy.json` / `scripts/media_speed_orchestrator.py`
- 2026-09-15以降の明示的な字幕配色・説明図静止・8〜12分目安のユーザー指定: `config/media_user_visual_duration_preferences.json`
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

`config/current_media_quality_handoff.json` は会話Memoryの代わりとなる現行サマリーだが、最終Authorityではない。内容が異なる場合は現行のMachine Policy・Validator・CIを優先する。メディア依頼ではBootstrap後にこのCheckpoint、`config/media_user_visual_duration_preferences.json`、`config/media_command_read_gate.json` を読み、そこから現在の詳細Policyへ展開する。

時短を求めるメディア依頼では、さらに `config/media_speed_quality_policy.json` と `scripts/media_speed_orchestrator.py` を復元する。Jevは候補プロファイルと実行形状のtyped判断だけを行い、ハッシュ、キャッシュ無効化、並列数、エンコード回数、最終JSONはPythonが決める。目標の10〜15分は過去の約40分ローカル実測から設定した観測目標であり、品質ゲートを緩める保証値ではない。

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

## メディア制作

メディア作業では、計画・素材取得・音声生成・レンダリングより前に `config/current_media_quality_handoff.json`、`config/media_user_visual_duration_preferences.json`、`config/media_command_read_gate.json` の現行版を読む。READMEへ詳細ルールを重複させない。

現在の恒久標準の要点:

- 通常の情報収集と原稿作成は **ChatGPT + DeepSeek** を1つの判断工程として扱う。調査役、原稿役、書き直し役、通常査読役を理由なく細分化せず、追加Agentは独立並列化・専門能力・リスク低減に明確な価値がある場合だけ使う。
- レンダリング、タイミング計測、エンコード、Hash、ffprobe、Decode QAなどの機械工程はDeterministic Toolを優先し、Agent数を増やさない。
- 標準VOICEVOX castは **ずんだもん + 四国めたん**。Speaker / Style IDは実行時に利用可能状態を確認する。
- ずんだもんと四国めたんは素材の生ピクセル高ではなく見た目の大きさを揃え、話者を自然に前へ・大きく見せる。拡大で字幕、説明図、安全領域を侵さない。
- 口だけを動かしてキャラ演技完了としない。目、眉、顔つき、首傾き、ポーズ、必要な聞き手リアクションを意味と感情に合わせて使い、全要素を同時に動かす過剰演出は避ける。
- 音声はSemantic Beat単位でPause・Speed・Pitch・Intonation・Emotionを設計し、長時間の平坦読みを標準にしない。
- キャラクターはIdle / Speaking / Reaction / Emphasis等の状態で控えめに動かし、長時間の完全静止立ち絵へ退行させない。
- 1 Semantic Beatにつき主役となるAttention Heroは原則1つ。Caption / Evidence / Character / SFX / Zoomを理由なく競合させない。
- 視聴維持のための構成は釣りではなく、Truthful Hook → Early Value / Evidence → Explanation / Contrast → Payoffを基本候補とし、Curiosity Gapを使う場合は動画内で回収する。
- 字幕は意味のまとまり、実フォント表示幅、測定済み音声タイミング、強調を別軸で扱い、文字数だけで機械分割しない。
- 字幕は話した内容を省略しない `FULL_SPOKEN_TEXT` 契約で全話し言葉を表示する。短い要約字幕でナレーションを置き換えず、実音声のWAV境界へ同期する。
- 字幕本文・枠は話者色を使う。ずんだもんは明るい緑、四国めたんは明るいピンク/マゼンタ。台本が指定した重要語は黄色または赤で強調し、色だけに意味を依存させず暗い縁取りと話者ラベルを併用する。
- ニュース・事実説明では、話題に意味的に合う検索済み／登録済みの権利確認済み画像を優先し、source page、asset locator、ライセンスまたはパブリックドメイン状態、scene/claim mapping、取得・確認時刻を台帳へ残す。検索結果はライセンスではない。
- 説明図・背景図の全体を意味なく上下に漂わせない。原則静止させ、必要なPointer/Highlight/Revealなど局所的で意味のある動きだけを使う。
- 今後の通常News/Topic Explainerは **8〜12分を目安** とする。ただし尺合わせのための無関係な歴史、背景説明、反復、遅い読み、低情報量Fillerは禁止。追加尺は一次情報、仕組み、影響、重要な時系列、相反する見方、不確実性、今後の論点など、その話題を本当に理解するための情報で稼ぐ。
- 口元やキャラGeometry変更時は本編前に顔全体Fixtureで確認し、口が動くだけでは合格としない。
- 変更したHigh-risk Layerは低コストPreviewで先に検査し、失敗したまま高コストFull Renderへ進めない。
- 修正は最小Stageと真の依存先だけを再生成し、字幕・説明Panel・口Anchorだけの変更で都合上Full Pipelineをやり直さない。
- Cache再利用はPolicy版、素材Hash、Character Pack、口Anchor、字幕Rule、VOICEVOX設定、出力Geometry、Dependency Hash等を含む入力Manifest一致を必要とする。
- 視覚素材は検索 → Original Source確認 → Rights確認 → 事前取得・Decode検証を基本とする。Generated Image / Generated Video Assetは現行Longform標準経路にしない。
- 第三者Free BGMはDOVA-SYNDROME / OpenTracksを優先候補とし、`config/free_audio_source_registry.json` と `config/dova_curated_bgm_catalog.json` の現行条件を守る。
- 長尺はScene / Chapter単位で `Scene -> Validate -> Checkpoint -> Join`。Monolithic Renderへ戻さない。
- Timelineは文字数推測ではなく、生成済みWAVの実時間をffprobeで測定して決める。
- Partial / Unverified SceneをConcatへ入れない。失敗時は最小失敗単位だけを再処理し、正常な成果物を保持する。
- 完成判定は最終MP4のffprobe、Video/Audio stream、Media Contract、Decode integrity、字幕Coverage等のMachine QAに加えて、代表FrameのVisual QAも通す。Decode PASSだけを見た目PASSとみなさない。
- 速さや視聴維持のために事実、権利、口元、字幕、安全領域、音量、Decode、Final Visual QAを弱めない。
- Runway、Fal/fal.ai、Descript、VEED、HeyGen、Higgsfield等のPaid/Freemium/Trial media SaaSを標準制作経路にしない。Unknown cost routeはfail-closed。

詳細は以下をSemantic Gateから現行版で復元する。

- `config/current_media_quality_handoff.json`
- `config/media_user_visual_duration_preferences.json`
- `config/media_character_performance_compact_orchestration_policy.json`
- `config/media_audio_motion_retention_policy.json`
- `config/media_reusable_asset_standard.json`
- `config/media_character_reaction_cache_policy.json`
- `config/zundamon_metan_production_quality_policy.json`
- `docs/ZUNDAMON_METAN_PRODUCTION_QUALITY_STANDARD.md`
- `config/batch_media_orchestration_policy.json`
- `config/cross_domain_measurement_registry.json`
- `scripts/validate_zundamon_metan_production_quality.py`
- `scripts/validate_video_retention_efficiency.py`
- `config/free_audio_source_registry.json`
- `config/dova_curated_bgm_catalog.json`
- `config/longform_video_objectives.json`
- `config/longform_video_reliability_policy.json`
- `docs/LONGFORM_VIDEO_RELIABILITY_PLAYBOOK.md`
- `docs/AI_ARMY_LONGFORM_RESEARCH_SYNTHESIS_2026-09-12.md`

## CI / Compatibility

CIの実行権限と自動fan-outは `config/ci_execution_policy.json` を正本とし、`scripts/ci_control_plane_guard.py` で検査する。旧実験コードを保持する場合でも、それだけで現行Routing権限・有料実行権限・自動発火権限を復活させてはならない。

`config/legacy_deepseek_compatibility.json` は古いDeepSeek設定を実行するためのファイルではなく、旧経路が現行Authorityへ復帰しないことを検証するための互換・回帰ガードである。削除理由がない限り履歴的なコードやテストを「古い名前」だけで削除しない。

## Hard Boundaries

明示された権限がない限り、main直接Push、PR Merge、本番Deploy、公開Publish、Secrets変更・開示、Durable Object変更、Auto Top-up、Generic Paid Fallback、不可逆な外部操作を行わない。
