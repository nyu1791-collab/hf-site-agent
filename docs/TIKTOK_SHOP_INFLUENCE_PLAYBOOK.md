# TikTok Shop Influence & Deep-Research Commerce Playbook

**Status:** Canonical / permanent conditional standard  
**Effective:** 2026-09-12 JST  
**Decision owner:** ChatGPT Top Commander  
**Paid specialist consulted:** DeepSeek V4.1 Flash (`deepseek-flash`)

## 1. Objective

TikTok Shop動画を「商品を褒める広告」ではなく、**短時間で好奇心→理解→信用→欲求→不安解消→行動を作る、事実ベースのミニ・ドキュメンタリー／実演コンテンツ**として設計する。

最重要原則は次の2つ。

1. **売れるための演出は強くするが、事実は弱めない。**
2. **ストーリーは証拠を魅力的に伝えるために使い、証拠の代用品にはしない。**

このレーンは `config/tiktok_shop_influence_policy.json` を機械可読の正本とする。

---

## 2. Evidence base

### TikTok Shop Japan / TikTok For Business

TikTok Shop Japanの2026-07-08「売れるショッピング動画を作るための5つのチェックリスト」は、冒頭3秒に日常的な悩み＋商品ソリューションを置き、商品を自然に導入し、主なセールスポイント、素材、サイズ、使用効果、複数の利用シーンを示し、最後に自然なCTAへ繋げる構成を推奨している。字幕も投稿前チェック項目として明記されている。

- https://seller-jp.tiktok.com/university/essay?knowledge_id=1232479651153681&lang=ja-JP

2026-05-20のTikTok Shop Japanガイドも、冒頭3秒を高コンバージョンの重要区間として扱い、単なる商品表示ではなく「シーンに特化した興味喚起」を推奨している。

- https://seller-jp.tiktok.com/university/essay?knowledge_id=1429562417137409

TikTok For Businessも、最初の数秒が重要で、強い映像、質問、共感できる一言、好奇心を刺激する導入を推奨している。

- https://ads.tiktok.com/business/en/blog/tiktok-short-video-best-practice

TikTok Shop Japanの商品選定教育は、サンプル入手性、市場性、フォロワーとの適合、収益性、ユーザーメリット、品質、動画で紹介しやすいかを商品選びの軸としている。商品詳細では価格、注文、CTR、クリエイター数、カート追加、クーポン、配送、返品、レビュー等を見るよう案内している。

- https://seller-jp.tiktok.com/university/essay?knowledge_id=1086540470748945&lang=ja-JP

2026-07-14のAIGC専用ガイドは、AI生成/AI変更コンテンツを現行ルールの範囲で認めつつ、AIGC表示、商品の実像との一致、誇張回避、重要セールスポイントの字幕強調、テンプレ使い回し回避を求めている。

- https://seller-jp.tiktok.com/university/essay?knowledge_id=6860523157653265

一方、2026-06の一般Content PolicyにはAIGCにより厳しい文言が残るため、**公開直前に最新の専用AIGC規約を再取得し、文書間で矛盾があればfail-closed**とする。

### Marketing / consumer research

ストーリーは単なる装飾ではない。2019年のJournal of Business Researchのmeta-analysisは64論文・138効果量を統合し、digital/commercial環境でnarrative transportationが説得に関係することを示している。

- https://www.sciencedirect.com/science/article/pii/S0148296318305356

2024年のonline-review meta-analysisは156研究・69,006観測を統合し、online reviewsの各要因がpurchase intentionと有意に関連し、review valenceが強い関連を示した。

- https://www.sciencedirect.com/science/article/pii/S2543925123000323

eWOM meta-analysisでも、credibility、usefulness、source credibility、argument quality、emotional trustなどが購買意図と関連している。

- https://link.springer.com/article/10.1007/s10796-019-09924-y

またconsumer reviewをstoryとして提示する研究では、story-like reviewがnarrative transportationやreflectionを高め、behavioral intentへ繋がる経路が示されている。

- https://www.sciencedirect.com/science/article/pii/S0148296314003476

したがってAI Armyは、**レビューを数だけ見せるのではなく、信頼できるレビュー群から「どんな状況で、何が良くて、何が不満だったか」を物語化して提示する**。ただし、原文にない体験や感情は追加しない。

---

## 3. Canonical purchase story

標準ファネルは以下。

```text
好奇心 / 自分事化
        ↓
理解
        ↓
証拠 / 信用
        ↓
使用場面を想像
        ↓
欲しい理由が具体化
        ↓
不安・反論の解消
        ↓
自然な購入行動
```

短尺では全ステージを均等な時間にしない。商品によってモジュールを圧縮する。

### 0–3秒: Hook

最優先区間。次のうち1つを選ぶ。

- **悩み型:** 「○○で困る人、これ知ってる？」
- **実演型:** 最も視覚的に強い動作を先に見せる
- **用途型:** 「出先で○○したい人向け」
- **質問型:** 「中国ではこのお菓子、どう見られてる？」
- **文化/歴史型:** 「これ、ただの辛いお菓子じゃない」
- **意外な事実型:** 根拠があるcounterintuitive fact
- **レビュー型:** 「レビューを100件見ると、褒められてる所がかなり偏ってた」※実際の件数のみ

Hookの役割は「買わせる」ではなく**次の数秒を見る理由を作ること**。約束した疑問は動画内で回収する。

### 3–8秒: Context

- 商品名・商品カテゴリ
- どんな場面のための商品か
- どんな人の問題を解くか

1動画1personaを優先する。誰にでも売ろうとすると訴求が薄くなる。

### 8–20秒: Demonstration / core value

TikTok Shop公式が重視する部分。

- 実物
- 使用手順
- 食感/サイズ/動作
- 複数アングル
- 使用場面

商品ページやメーカー情報で確認できない性能を動画だけで断定しない。

### Deep-dive module

商品の魅力が「機能」だけでなく「背景」にある場合に追加する。

- 歴史
- 文化背景
- 中国/現地での位置づけ
- なぜその形・味・設計なのか
- 現地での肯定/否定レビュー傾向
- 他の使い方
- 誰向け/誰には向かない

この部分がユーザー案の「深掘り動画」の中核。

### Proof / trust

証拠源を分ける。

1. TikTok Shop商品ページ
2. 公式メーカー/ブランド
3. 独立した公的・技術・報道資料
4. 現地レビューcluster
5. 個別レビュー

動画台本の各 factual sentence に `evidence_id` を付ける。

### Objection reduction

売るために弱点を隠さない。

- 辛さが強い
- 洗いにくい
- 容量が小さい
- 音が大きい
- 配送に時間がかかる
- 好みが分かれる

など、実際のレビューclusterにある反論を整理し、**「こういう人には向く / こういう人には向かない」**まで言う。これは短期CVを少し下げても信頼・返品率・長期収益に有利な可能性があるため、AI Armyはpurchase rateだけで最適化しない。

### CTA

CTAは価値と証拠を見せた後。

推奨:

- 「詳しい仕様は商品ページで確認できます」
- 「この使い方が合いそうなら商品ページへ」
- 「現在の価格・クーポンは黄色いカート側で確認」

割引・在庫・期限を言う場合は、**公開直前の最新情報**を証拠として保持する。偽の「残り○個」「あと○時間」は禁止。

---

## 4. Subtitle system

字幕は文章を全部載せるのではなく、**視線を操作するUI**として扱う。

### Hierarchy

1. **HOOK** — 最も強い一文
2. **KEY PROOF / BENEFIT** — 実証された核心
3. **NORMAL CAPTION** — 補助説明

### Rules

- 一字幕一アイデア
- 長い段落を避ける
- 音声の意味単位で切る
- 重要語だけ太字/色/背景強調
- 商品を字幕で隠さない
- 右側UI、下部説明/商品UIと衝突しない
- 現行TikTok UIをレンダリング前に確認する
- 固定ピクセル値を永久ルールにしない
- 重要だが未検証の数字を大文字・色強調しない
- 常時揺れる字幕や過剰なmotionは標準にしない

DeepSeekは短いcaption chunkを推奨したが、具体的な文字数は公式ルールではないため、**A/B test対象のheuristic**として扱う。

---

## 5. Cover / thumbnail / first frame

TikTokは動画再生前後の一覧表示でもcoverが判断材料になる。

標準:

- 商品または主要利用シーンが見える
- 1つの問い/メリットだけ
- 小さい表示でも読める
- spoken hookと完全コピーにせず補完関係にする
- 動画本編で回収できる内容だけ
- 偽Before/After禁止
- 価格/割引はfresh evidenceがある時だけ

例:

**辣条**
- Cover: 「中国でこれはどんなお菓子？」
- Spoken Hook: 「辛いだけだと思ったら、背景を調べると結構おもしろかった」

**Portable juicer**
- Cover: 「外で本当に使える？」
- Spoken Hook: 「持ち運びミキサーって便利そうだけど、実際どこまで出来るのか仕様を全部見ました」

---

## 6. Local review intelligence

「中国人の感想」のような表現を、民族/国籍全体の総意として生成しない。

正しい工程:

```text
Chinese/local reviews
   ↓
source + date + original text保存
   ↓
translation別保存
   ↓
spam / duplicate除去
   ↓
topic classification
   ↓
positive / mixed / negative
   ↓
cluster size + sample size
   ↓
representative examples
   ↓
script wording
```

例:

NG:
> 中国人はみんなこの味が好きです。

OK:
> 今回確認した中国語レビューでは、特に「○○」について肯定的な声が多く、一方で「○○」は好みが分かれていました。

必ず対象件数、取得元、市場、期間を内部manifestに持つ。

個別レビューを紹介する場合は個別体験として扱い、cluster consensusには昇格させない。

---

## 7. Product-page grounding

台本を書く前にTikTok Shop商品ページから最低限取得する。

- 商品名
- ブランド
- 現在価格 + timestamp
- バリエーション
- 容量/サイズ/素材/成分
- 公式に記載された機能
- 使用方法
- 配送
- 返品
- クーポン/期限
- rating/review count
- commission rate（取得可能な場合）
- 注文/CTR/creator/cartなどのmarketplace signals（取得可能な場合）

その後メーカー公式、説明書、ブランド公式へ進む。

商品ページとメーカー公式が矛盾する場合は断定せずBLOCK/ESCALATE。

---

## 8. Product auto-discovery

TikTok Shop Japan公式の商品選定項目とCreative Centerのdataをベースにする。

候補商品は以下で評価する。

### Commercial

- market momentum
- CTR / interest
- CVR / sales
- commission
- price competitiveness
- sample availability
- return / complaint risk

### Content potential

- demonstrability
- story potential
- researchability
- cultural/history depth
- local review richness
- visually surprising behavior
- multiple use scenes

### Audience

- follower fit
- clear pain point
- clear use occasion
- target persona clarity

### Risk

- policy risk
- health/safety claim burden
- unverified claims
- low review health
- creative saturation

**固定weightはまだ設定しない。** TikTok Shop公式も市場/アカウントごとのデータ確認を推奨しており、初期のweightはpilot dataで校正する。

Creative CenterのTop ProductsはPopularity、CTR、CVR、CPA、6s View Rate等を提供するが、表示値はapproximationであるため、絶対的な真実ではなく方向性signalとして使用する。

- https://ads.tiktok.com/business/creativecenter/product-category/

---

## 9. Target persona

Personaは事実ではなく**仮説**。

生成元:

- use case
- pain point
- price
- actual audience analytics
- review clusters
- category trends

例:

### 辣条

- SPICY_SNACK_EXPLORER
- CULTURE_CURIOUS_FOODIE
- NEW_IMPORT_SNACK_EXPLORER

### Portable juicer

- BUSY_SMALL_KITCHEN_USER
- PORTABLE_GADGET_SEEKER
- WORKOUT_DRINK_USER

1本に全部詰めず、personaごとにhook/storyを変える。

---

## 10. A/B test system

最初から「正解の動画テンプレ」を作らない。

比較対象:

- hook type
- first frame
- cover text
- story arc
- proof: official spec vs review pattern
- subtitle emphasis
- persona
- CTA

KPI:

- 3s hold
- 6s view rate（取得可能時）
- completion rate
- CTR
- CTOR
- add-to-cart
- CVR
- GMV
- GPM
- commission
- return rate
- complaint / negative-review / report signals

**売れた動画だけを勝者にしない。** 返品や不満が高ければ降格する。

---

## 11. Example: imported snack / 辣条-type product

実商品の事実を取得してから次の構造へ落とす。

```text
0-3s    「中国でこれはどういうお菓子？」
3-7s    商品実物・パッケージ・食感
7-15s   商品ページの成分/容量/特徴
15-25s  歴史や文化背景（別資料で検証）
25-35s  中国語レビューcluster：よく出る肯定点と不満点
35-42s  「辛い物好き/○○食感好き向き」などfit check
42s-    商品ページへ自然なCTA
```

「現地で人気」はreview数、ランキング、売上、信頼できる報道等がなければ断定しない。

---

## 12. Example: portable juicer / gadget

```text
0-3s    実際に回転/ジュース化するvisual
3-7s    「外出先で本当に使える？」
7-15s   容量・充電・対応材料など公式仕様
15-25s  2-3 use scenes
25-35s  review cluster：便利点 + complaint
35-42s  向く人/向かない人
42s-    現在の価格/詳細は商品ページへ
```

氷対応、バッテリー持続時間、モーター性能などは必ず公式specに限定する。

---

## 13. DeepSeek review result

Paid DeepSeek research was executed under bounded staging-only conditions.

### Run 34682745114

- Product research/discovery lane: success
- Creative psychology lane: JSON truncation failure
- Successful parsed lane conservative cost estimate: `$0.00609488`
- Production/publish/deploy/merge/secret mutation: false

### Run 34682826338

Only the failed creative lane was retried.

- Creative psychology lane: success
- Successful parsed call conservative cost estimate: `$0.00190212`
- Production/publish/deploy/merge/secret mutation: false

Known successful parsed-call estimates total `$0.00799700`. This is **not a billing statement** and does not prove the full provider charge for failed/unparsed calls.

The two usable DeepSeek outputs aligned with the primary-source research on:

- first-3-second hook importance
- product-first visuals
- product-page grounding
- short/high-signal subtitles
- story + proof rather than spec dumping
- local review clustering rather than fabricated consensus
- one persona / one main motive per video
- A/B testing rather than permanent template assumptions
- rejecting fake urgency, fake reviews and unsupported claims

---

## 14. Rejected practices

Do not standardize:

- fabricated local reviews
- AI avatars claiming to be real customers
- fake stock countdowns
- stale coupon/price claims
- unsupported health/performance claims
- one review → “everyone says”
- one fixed video template for every product
- unrelated viral story that hides the actual product
- excessive subtitle animation
- optimizing only for views while returns/complaints rise

---

## 15. Implementation order

1. Product-page/official evidence collector
2. Claim-to-evidence registry
3. Local-language review collector + translator + deduper + clusterer
4. Product discovery scorer
5. Persona/angle generator
6. Hook generator with hard claim gates
7. Story composer with evidence slots
8. Subtitle/cover renderer
9. Claim & policy QA
10. A/B variant generator
11. TikTok Shop analytics ingestion
12. Weight/pattern learning from real performance

The output should eventually be a **Research Pack + Script Pack + Evidence Manifest + Video Edit Spec**, not merely a freeform script.
