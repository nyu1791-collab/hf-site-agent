# Zundamon vertical news template (55–60 seconds)

Use this profile when the user asks for a short vertical news explainer or continues an approximately one-minute video. Other future news explainers retain the separate 8–12 minute default unless the user asks for a short format.

Machine contract: `config/zundamon_news60_template.json`. This document contains the fill-in prompt. Before any video work, read the current media command gate and video admission read set from the repository.

## Fill-in inputs

- Topic and the exact question the video should answer.
- Research cutoff date and primary or official source URLs.
- Claims that need confirmation and any uncertainty that must remain visible.
- Cached rights-verified visuals that may already fit.
- Intended audience or platform, if relevant.

## Reusable prompt

テーマ: {{TOPIC}}
調査基準日: {{AS_OF_DATE}}
尺: 約60秒の縦型ニュース・解説
一次資料または公式資料: {{SOURCE_URLS}}
確認したい事実・主張: {{CLAIMS_OR_QUESTIONS}}
使える既存素材・キャッシュ: {{CACHED_ASSETS}}

次に、以下を依頼文としてそのまま使う:

> このプロジェクトの最新メディアRead Gateと動画Admissionを読み、上の入力と `config/zundamon_news60_template.json` に従って制作してください。まず一次資料で主張を固定し、55–60秒に収まる一つの台本を作ってください。各行にspeaker、voice_text、caption_text、emotion、semantic_beat_id、visual_beat、source_claim_ids、emphasis_terms、emphasis_reasonを持たせ、ずんだもんを主役にしてください。内容を分かりやすくする場合だけ四国めたんを追加してください。VOICEVOXローカル音声を生成して実測した長さに字幕を同期し、話した全文を必ず表示してください。話者色はずんだもんが緑、四国めたんがピンクです。特別な黄・赤色は基本使わず、意味を大きく変える発見・数字・重要な訂正・注意に限って、動画全体3箇所まで付けてください。話している間の口パクは音声に同期させ、無音では閉じてください。YMM4とVOICEVOXのローカル連携を優先してください。口だけでなく、意味の節目に合わせて目・眉・表情も変えますが、毎行切り替えたり、複数の大きな動きを重ねたりしないでください。顔全体の口・表情プレビューを確認してから本番レンダーし、ズレがあれば本番を止めて修正してください。ニュース画像は既存キャッシュを優先し、不足分だけ一次・公式資料を調べます。画像ごとに出典ページ、素材URL、利用条件、対応する主張を記録してください。調査・台本はひとまとまりの判断段階とし、無関係な小作業へエージェントを細分化しないでください。Admission後は独立準備を最大3レーンで並行し、失敗時は該当箇所だけ直して、完成動画を一度だけエンコードしてください。自動色強調・有料サービス・生成画像を事実の証拠として使うことは禁止です。機械検査後に映像を見直し、完成したMP4を渡してください。品質や事実確認は省かず、確認済みキャッシュを再利用して速く仕上げてください。

## Default beat plan

| Beat | Target window | Function |
|---|---:|---|
| Hook | 0–3s | Truthful question, surprising finding, or real stake |
| What changed | 3–12s | Give the verified central fact and identify evidence early |
| Why it happened | 12–27s | Explain the mechanism in plain language |
| Evidence | 27–42s | Match a real or official visual to the claim |
| Limit or caveat | 42–51s | State material uncertainty or correction when needed |
| Takeaway | 51–60s | Resolve the opening question with one useful takeaway |

These are planning windows, not a padding quota. Final timing follows the WAV measured from local VOICEVOX.

## Permanent production rules

- Keep the existing verified-asset cache first. Search only for a missing semantic visual, then verify its rights and provenance.
- Preserve complete FULL_SPOKEN_TEXT captions and the current green/pink speaker identity colors.
- Extra highlight colors start at zero. Allow at most one critical highlight per semantic beat and three for the whole short. Each highlight needs a written reason.
- For this profile, use VOICEVOX-synchronized mouth motion and a small number of script-driven facial-expression changes. Reuse the cached character shell and reaction pack; never download them per video.
- Use a full-face fixture for Zundamon and Metan when their visible mouth geometry or scale identity changes. Confirm closed, small-open, and open states before the expensive render.
- Use one attention hero per beat. When the evidence image matters, keep character movement subordinate.
- Keep the production target at 10–15 minutes as an observed goal. Run the three independent preparation lanes only after script and claim lock, reuse valid stage checkpoints, repair the smallest failing stage, and encode once.
- Keep this exception scoped to the 55–60 second Zundamon profile. The general static-turn-focus policy remains for other video profiles unless the user asks for character motion.

## Why this lip-sync route

YMM4's official release notes describe A-I-U-E-O lip sync for compatible animated-standing-picture or PSD assets, using generated timing from supported engines such as VOICEVOX; other engines may use audio analysis. VOICEVOX's official guide also documents exporting a .lab file containing phoneme timing that is useful for lip sync. Use a character asset that actually contains compatible mouth/eye states, and check the full-face fixture before rendering. See [YMM4 lip-sync support](https://manjubox.net/ymm4/release/4.49.0.0/), [VOICEVOX phoneme timing export](https://voicevox.hiroshiba.jp/how_to_use/), and [YMM4's VOICEVOX integration guide](https://manjubox.net/ymm4/faq/%E3%82%86%E3%81%A3%E3%81%8F%E3%82%8A%E3%83%9C%E3%82%A4%E3%82%B9/VOICEVOX%E3%82%92%E4%BD%BF%E7%94%A8%E3%81%99%E3%82%8B/).
