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
- 収益化・案件・アフィリエイト・Creator Program・AI workflow service: `config/monetization_command_read_gate.json`

詳細が文書間で異なる場合は、最新の明示的ユーザー指示と安全境界を守ったうえで、現行Machine-readable Policy・Validator・CIを優先する。恒久ルールを変更する場合は会話だけで終わらせず、Machine Policy / Rulebook / Validator / CI / Read Gateの整合性を同じ変更で確認する。

## AI Army の固定境界

- ChatGPT / Work がTop Commanderかつ最終判断者。
- 有料DeepSeekは `config/deepseek_paid_supervisor_policy.json` の範囲だけで使うExecutive Supervisor。全タスクの必須hopでも大量boilerplate coderでもない。
- Deterministic ToolまたはSingle Agentで十分ならそれを優先する。
- Single Writerを維持し、同一mutable targetの並列変更にはTask Leaseを要求する。
- 最大Delegation Depthは2、1ユーザー依頼あたり最大10 Tasks。無限Swarm・無限Reflection・無限Replanは禁止。
- Machine Oracle / Schema / Test / Hash / ffprobe等をAI多数決より優先する。
- 通常Routeはfree-first。Auto Top-up、Generic Paid Fallback、Paid sibling自動置換は禁止。
- DeepSeek例外は他の有料Provider、Repository Write、main Push、PR Merge、Deploy、Publish、Secrets操作、支払い操作へ権限を拡張しない。

## メディア制作

メディア作業では、計画・素材取得・音声生成・レンダリングより前に `config/media_command_read_gate.json` の現行版を読む。READMEへ詳細ルールを重複させない。

現在の恒久標準の要点:

- 標準VOICEVOX castは **ずんだもん + 四国めたん**。Speaker / Style IDは実行時に利用可能状態を確認する。
- 音声はSemantic Beat単位でPause・Speed・Pitch・Intonation・Emotionを設計し、長時間の平坦読みを標準にしない。
- キャラクターはIdle / Speaking / Reaction / Emphasis等の状態で控えめに動かし、長時間の完全静止立ち絵へ退行させない。
- 1 Semantic Beatにつき主役となるAttention Heroは原則1つ。Caption / Evidence / Character / SFX / Zoomを理由なく競合させない。
- 視覚素材は検索 → Original Source確認 → Rights確認 → 事前取得・Decode検証を基本とする。Generated Image / Generated Video Assetは現行Longform標準経路にしない。
- 第三者Free BGMはDOVA-SYNDROME / OpenTracksを優先候補とし、`config/free_audio_source_registry.json` と `config/dova_curated_bgm_catalog.json` の現行条件を守る。
- 長尺はScene / Chapter単位で `Scene -> Validate -> Checkpoint -> Join`。Monolithic Renderへ戻さない。
- Timelineは文字数推測ではなく、生成済みWAVの実時間をffprobeで測定して決める。
- Partial / Unverified SceneをConcatへ入れない。失敗時は最小失敗単位だけを再処理し、正常な成果物を保持する。
- 完成判定は最終MP4のffprobe、Video/Audio stream、Media Contract、Decode integrity、字幕Coverage等のMachine QAを通す。
- Runway、Fal/fal.ai、Descript、VEED、HeyGen、Higgsfield等のPaid/Freemium/Trial media SaaSを標準制作経路にしない。Unknown cost routeはfail-closed。

詳細は以下をSemantic Gateから現行版で復元する。

- `config/media_audio_motion_retention_policy.json`
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
