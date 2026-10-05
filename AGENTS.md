# Startup and video task rules

Check the current repository branch and PR. On every new tab, read README.md → config/current_commander_handoff.json → config/permanent_standards_manifest.json → docs/AI_ARMY_MASTER_RULEBOOK.md.

For any request to create, edit, shorten or deliver video, also read config/media_command_read_gate.json and config/media_speed_quality_policy.json before planning. Restore these current repository versions; the speed-first delivery policy overrides historic review, polish and duration rules.

Quality 20%, speed 80%. For long-form up to 16 minutes, measure against a five-minute work target. Keep the requested video duration separate from production time: if a later user correction conflicts with an earlier duration, use the latest explicit artifact duration; fit the script/audio before rendering and never accelerate speech to force a fit. “Make it in five minutes” is a wall-clock creation target, not video runtime; keep those fields separate and preserve the latest explicit long-form duration across tabs. Preserve the approved canvas, visible Zundamon and Metan, native mouth motion and pale speaker captions. Use `scripts/render_reusable_short.py` with `config/approved_video_template.json` for a single encode; synthesize voice by paragraph/topic instead of per sentence. Log actual wall time if the target is exceeded. Do not ask routine questions, do manual visual review, wait for agent critique or correct cosmetic/micro timing differences. After one successful export, confirm a nonempty file and audio/video streams, then deliver immediately. Repair only failed or unusable output or a material factual/rights error. Keep paid generation, public publish, merge, deploy and secret boundaries unchanged.

Use 2–4 distinct, topic-matched relevant photos, product/statement screenshots or explanatory diagrams; official sources are preferred when useful per main section. Image generation is disabled. Crop source screenshots to media content and remove browser/player UI. Preserve source, credit, rights basis and claim mapping; an official source is not automatically licensed for reuse. If no suitable cleared image exists, use a short attributed text card. This visual-density rule does not add review passes. Read `config/media_speed_quality_policy.json#/visual_density_contract` and `config/media_source_policy.json`.

For scripts, use a clear heading for each main topic; state the main point first in plain language. Keep only details that help the viewer understand or decide. Explain necessary jargon once, avoid long preambles and repeated caveats, and use a brief on-screen availability label when that is enough. Follow `config/media_speed_quality_policy.json#/script_clarity_contract`. A specific improvement the user asks for is an authorized targeted revision, not unsolicited polish.


承認済みのキャラクター動画を通常の既定とする。品質2・速度8は追加の磨き込みを省く方針であり、ずんだもん・四国めたん、口パク、承認済み背景・字幕色・画面形式を削除する許可ではない。静止画のみの別形式への切替はユーザーが明示した場合に限る。タブ切替時も `config/approved_video_template.json` と `docs/VIDEO_PRODUCTION_BASELINE.md` を制作前に必読する。


関連画像を多く使う構成・実際の二人の会話・平易な解説を制作の必須入力とする。各主要章に関連写真、製品画面、スクリーンショット、説明に役立つ図解を2〜4場面使う。公式素材は優先候補であり必須の限定条件ではない。自作の説明図も使えるが、文字だけのカードや同じ画像の使い回しは画像数に含めない。生成画像は禁止。素材の出典と利用根拠を保持する。ずんだもんと四国めたんは双方が実際に発話し、説明と視聴者の質問・言い換えを分担する。キャラクター表示だけで二話者とは扱わない。見出し、要点、平易な説明、必要なら短い具体例の順で説明し、モデル名・数値・専門語の羅列を避ける。新タブでも速度ポリシーの dialogue_contract / visual_density_contract / script_clarity_contract を必読する。入力は `scripts/validate_video_content_contract.py` で書き出し前に一度確認し、任意の査読や磨き込みは増やさない。


## VM常時接続と代替操作経路（2026-10-05）

VM接続・24時間運用・障害復旧の依頼では `docs/VM_CONNECTIVITY_RECOVERY.md` を必読する。通常のDesktop CommanderがOfflineでも、private repository `nyu1791-collab/-hf-vm-control` の既存self-hosted RunnerからVMを操作できる。両方が停止した場合はGoogle Cloud Console/Cloud Shell SSHへ進む。Offline表示だけでVM停止・Gemini/VOICEVOX/Renderer故障と判断しない。

既存接続サービスはsystemdのRestart=always / RestartSec=30と毎分watchdogで復帰させ、lingerとboot enableを維持する。既存資格情報・SSH検証・ネットワーク境界を変更しない。保守停止は明示pauseで尊重する。常時オンラインは運用目標であり、VM停止・ネットワーク障害・クラウド側障害まで保証したとは報告しない。ローカルactive、クラウド側Online、実コマンド成功、独立Runner操作成功を分けて確認する。

動画は接続sessionに依存しないdurable Jobで実行し、STATEを保存する。新しい動画は `docs/VIDEO_FRESH_CONTENT_POLICY_20261005.md` に従い、土台・型・Renderer・キャラ素材のみ再利用し、情報収集・解析・台本・音声は毎回一から作る。同一ジョブの障害復帰時のみ今回のcheckpointを使う。
