# AI Army — Authorized Clipping & Monetization Playbook

**Status:** Canonical / permanent conditional standard  
**Effective:** 2026-09-12 JST  
**Decision owner:** ChatGPT Top Commander  
**Paid research specialist:** DeepSeek V4.1 Flash (`deepseek-flash`)

## 1. Final adoption decision

AI Armyに「切り抜き」を取り入れる。ただし採用対象は **無断切り抜き量産** ではなく、以下に限定する。

> **AUTHORIZED_CREATOR_REPURPOSING_AND_OFFICIAL_CLIPPING**

つまり、配信者・ポッドキャスター・ウェビナー主催者・企業など、素材の権利者本人、または明示的な商用利用許諾を得た相手のコンテンツを、AIで見どころ抽出・再編集・字幕化・縦動画化し、最終承認を経て納品／公開する事業である。

Genericな第三者ライブ配信の無断切り抜き、転載、字幕だけを足した量産は恒久レーンとして不採用とする。

## 2. Evidence and DeepSeek consultation

2026-09-12の調査ではYouTube/TikTokの公式方針、日本市場のShopping情報、オープンソースの切り抜き/ASRツールを確認したうえで、DeepSeek V4.1 Flashを2レーン並列で実行した。

- GitHub Actions run: `34682071530`
- Model: `deepseek-flash`
- Paid calls: 2
- Successful calls: 2
- Conservative peak-cost estimate: `$0.00693572`
- Production change: false
- Publish: false
- Deploy: false
- Merge: false
- Secret mutation: false

DeepSeekの2レーンは独立して、次の同じ結論に到達した。

1. Genericな無断切り抜きは恒久収益化レーンにしない。
2. Creator-owned / written-license / official clippingは条件付きで有望。
3. Platform monetization eligibility と copyright/commercial-use permission は別々に判定する。
4. 字幕・Crop・ZoomだけをTransformation扱いしない。
5. Third-party素材の公開はHuman approvalを残す。
6. Direct service feeを広告収益より先に置く。

Top Commanderは公式資料との照合後、この結論を採用した。

## 3. Revenue model — 広告収益だけに依存しない

優先順は以下とする。

1. **月額制作費 / Retainer** — 例: 月20本、月40本などの制作契約
2. **基本料金 + 成果報酬** — 視聴・CV・売上など明確なKPIに連動
3. **公認チャンネルのRevenue Share** — 書面契約とplatform eligibility確認後
4. **TikTok Shop / YouTube Shopping / Affiliate** — オリジナル・権利クリア素材で商品導線を作る
5. **Platform ad revenue** — 最後の追加収益として扱う

AI Armyの強みは、広告単価に賭けることではなく、**1時間の配信から複数の販売可能なコンテンツ成果物を安価に生産すること**にある。

## 4. Canonical clipping pipeline

```text
Rights / License Gate
       ↓
Authorized source ingest
       ↓
Audio + video + chat feature extraction
       ↓
Local ASR / alignment
       ↓
Multi-signal highlight scoring
       ↓
Candidate diversity + dedupe
       ↓
Boundary refinement
       ↓
Rough cut
       ↓
9:16 reframe / subject tracking
       ↓
Caption draft
       ↓
Originality / transformation layer
       ↓
Rights + duplicate + technical QA
       ↓
Human approval
       ↓
Export package
       ↓
Approved publishing handoff
       ↓
Analytics / copyright claim feedback
```

既存の長編動画規約と同様、FFmpeg中に外部ネットワーク素材を直接参照しない。取得素材・字幕・音声・中間成果物はmanifest/hashで追跡し、成功済み工程を下流失敗で破壊しない。

## 5. Highlight scoring

LLM一発判定にしない。複数Signalを組み合わせる。

- Chat density / 急増率 / z-score
- 「草」「www」「!?」「えぐい」等のreaction/emotion signal
- TranscriptとChatのsemantic overlap
- 音声Energy、笑い、叫び、無音からの変化
- Transcript上のPunchline、結論、意外性、論争点、学び
- Motion / scene change
- 配信者ごとの固有keyword、ゲームEvent、商品名
- 過去Analyticsから学習したRetention/CV特徴

候補は平滑化・Peak detectionを行い、近接重複を除外する。LLMは上位候補の意味評価・Hook案・文脈理解に使い、権利Gateを上書きする権限は持たない。

## 6. Recommended low-cost toolchain

- Python
- FFmpeg / ffprobe
- faster-whisper または WhisperX
- OpenCV
- Auto-Editor（必要な場合のみ）
- Local metadata / manifest / hash store
- AI Army specialists for semantic ranking, copy, review, analytics

既存方針どおりRunway / Fal / Descript / VEED / HeyGen / Higgsfield等のfreemium media SaaSは標準経路に入れない。

## 7. Mandatory rights gate

公開前にSourceごとにrights recordを持つ。

最低項目:

- 権利者または正式な署名者
- 対象Channel / stream / VOD / asset
- 二次編集可否
- 商用利用可否
- 公開可能Platform
- Territory
- Term
- 収益分配条件
- Revocation / takedown条件
- BGM、ゲーム映像、Guest、画像等のembedded rights確認

`credit`、`出典表記`、`概要欄にURL` は商用利用許諾の代替にならない。

## 8. Transformation / originality gate

### YouTube

字幕、Crop、Zoom、効果音だけでは十分と仮定しない。少なくとも一つ以上の明確なOriginal valueを追加する。

- 独自解説
- 分析
- 前後Context
- Original narration
- 新しいStory構成
- Original graphics / data explanation

さらにChannel全体でnear-identical clipを量産しない。

### TikTok

Creator Rewardsを狙う場合はOriginalityを別途厳格に判定する。第三者から許諾を得た映像だからといってCreator RewardsのOriginal判定を通るとは仮定しない。Rewardsを狙う動画は現行要件に応じて1分以上等を再確認する。

切り抜きのTikTok活用では、Creator Rewardsだけでなく、client fee、lead generation、TikTok Shop、affiliateを主要収益モデルとして評価する。

## 9. Automation boundary

**自動化してよい:** Rights Gate通過後のingest、ASR、alignment、highlight scoring、rough cut、reframe draft、caption draft、metadata draft、duplicate QA、technical QA、analytics collection。

**Human approvalを残す:** 初回Licenseの確認、第三者clipの最終選択、最終commentary/context、センシティブ内容、収益化ON、公開、copyright claim/dispute対応。

将来、十分な実績・低claim率・承認されたautopublish policyができた場合に限り、低リスクClient-owned素材の公開自動化を別途審査する。

## 10. Kill switches

以下を検知したら自動停止し、公開Queueを進めない。

- license missing / expired / revoked
- unresolved Content ID / copyright claim
- strike / reused-content / inauthentic-content warning
- near-duplicate量産
- transformation不足
- unverified BGM / game / guest / embedded rights
- fake engagement / engagement manipulation
- platform policy change
- unknown or positive unapproved media-service cost

## 11. AI Army monetization portfolio — recommended order

| Rank | Lane | Revenue speed | Automation fit | Rights / platform risk | Recommendation |
|---:|---|---|---|---|---|
| 1 | Creator-owned short-form repurposing service | Fast | Very high | Low | **Start first** |
| 2 | Authorized official clipping for streamers / VTubers | Fast–medium | Very high | Medium-low with license | **Adopt** |
| 3 | Original TikTok Shop / YouTube Shopping content studio | Fast–medium | High | Medium | **Pilot after rights template** |
| 4 | B2B webinar / podcast repurposing retainer | Medium | Very high | Low | **Adopt** |
| 5 | Local-business short-video / lead-gen production | Fast | Medium-high | Low | **Good cash-flow lane** |
| 6 | Original AI/news/explainer shorts | Medium–slow | High | Medium | **Controlled editorial lane** |
| 7 | Digital product / course funnel from original expertise | Slow–medium | Medium-high | Low | **High-margin long-term lane** |
| — | Generic unlicensed clipping | Unstable | High | Very high | **Reject** |

## 12. Best first commercial offer

最初に売るサービスは、複雑な自社メディア事業よりも次の形がよい。

> **「あなたの2時間配信を、AIで10〜20本のShorts/TikTok候補へ変換。見どころ抽出、字幕、縦動画化、タイトル案、投稿文案まで作成。最終公開は本人承認。」**

この形ならClientが素材の権利者であるため権利構造が明確で、YPP/TikTok Rewards到達前でも**制作費そのものを売上**にできる。AI Armyの自動化率が上がるほど粗利が改善する。

## 13. Growth path

Serviceで実績を作り、次の順で製品化する。

`Managed Service → Creator Portal → License Ledger → Automated Candidate Review → Analytics Learning → White-label / SaaS`

SaaS化を先にせず、まず実顧客の承認データ・修正理由・Retention・CV・claim率を集める。これらがHighlight ScorerとQAの教師データになる。

## 14. Evidence sources

- YouTube channel monetization / reused content policy  
  https://support.google.com/youtube/answer/1311392?hl=ja
- YouTube commercial-use rights guidance  
  https://support.google.com/youtube/answer/2490020?hl=ja
- YouTube Shopping affiliate program  
  https://support.google.com/youtube/answer/13376398?hl=JA
- TikTok Creator Rewards support / terms  
  https://support.tiktok.com/
- TikTok Shop Japan one-year update  
  https://newsroom.tiktok.com/tiktok-shop-japan-1st-year-anniversary?lang=ja-JP
- livestream-highlight-clipper  
  https://github.com/Cbhhhh211/livestream-highlight-clipper
- WhisperX  
  https://github.com/m-bain/whisperX
- Auto-Editor  
  https://github.com/WyattBlue/auto-editor

This playbook does not treat platform policy as legal advice. When a license, fair-use/quotation, neighboring rights, music rights, or personality/likeness issue is ambiguous, publishing remains blocked until the rights position is resolved.
