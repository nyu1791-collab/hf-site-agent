# Commerce + Clipping Precision Refresh — 2026-09-15

**Status:** Current official-source reconciliation  
**Decision owner:** ChatGPT Top Commander  
**Machine overlay:** `config/commerce_clipping_precision_overlay.json`

## Why this refresh exists

This refresh strengthens two areas without replacing their existing canonical playbooks:

- authorized clipping / repurposing
- shopping / commerce video, especially TikTok Shop Japan and YouTube Shopping

It also strengthens semantic recall: when the user means clipping, shopping, affiliate commerce, sponsored product content, or a combination of them, the applicable current repository rules must be restored by meaning rather than waiting for an exact policy name.

The existing domain policies stay active. This document records the 2026-09-15 evidence reconciliation and the machine overlay records the adopted deltas.

## Sources reviewed

Current primary/official sources were prioritized over creator folklore and AI reviewer opinion.

### YouTube

- YouTube channel monetization policies / reused content: https://support.google.com/youtube/answer/1311392
- Monetizable content and commercial-use rights: https://support.google.com/youtube/answer/2490020
- Paid promotion disclosure: https://support.google.com/youtube/answer/154235
- YouTube Shopping affiliate program: https://support.google.com/youtube/answer/13376398?hl=ja

### TikTok / TikTok Shop Japan

- Creator Rewards / original content: https://support.tiktok.com/ja/business-and-creator/tiktok-creator-fund-us/who-is-eligible-us
- TikTok Shop Japan Content Policy: https://seller-jp.tiktok.com/university/essay?knowledge_id=2707587427927825
- TikTok Shop Japan AIGC guidance: https://seller-jp.tiktok.com/university/essay?knowledge_id=6860523157653265&lang=ja-JP
- TikTok Shop Japan IP Policy: https://seller-jp.tiktok.com/university/essay?knowledge_id=2727520119195408&lang=en
- TikTok Shop Japan Content Authorization Tool: https://seller-jp.tiktok.com/university/essay?knowledge_id=680638764795649&lang=ja-JP
- Creator Performance Evaluation Policy: https://seller-jp.tiktok.com/university/essay?knowledge_id=2709643852121873
- Affiliate seller/product qualification: https://seller-jp.tiktok.com/university/essay?knowledge_id=2575312534718209&lang=en
- Shop Ads creator authorization / commission: https://seller-jp.tiktok.com/university/essay?knowledge_id=1192106471966480&lang=ja-JP

### Japan disclosure / consumer protection

- Consumer Affairs Agency — stealth marketing Q&A: https://www.caa.go.jp/policies/policy/representation/fair_labeling/faq/stealth_marketing/

## Reconciled clipping findings

### Retained

The existing clipping policy was fundamentally correct and is retained:

- generic unlicensed mass clipping is not the canonical business lane
- source rights and platform monetization eligibility are separate gates
- captions/crop/zoom alone do not prove substantive transformation
- ASR/alignment, candidate scoring, dedupe, semantic boundaries, reframe, captions and machine QA remain the clipping pipeline
- successful stages are checkpoints and later failures do not restart healthy earlier stages
- third-party publication remains human-approved unless separately authorized

### Strengthened

1. **Permission is not YouTube monetization approval.** YouTube explicitly treats reused-content monetization separately from copyright permission. Even a licensed clip can still fail reused-content review if the channel does not show meaningful original value.

2. **Channel-level template risk matters.** A single transformed clip can look acceptable while the overall channel still looks mass-produced or interchangeable. Therefore output diversity and channel-level similarity are part of the risk review.

3. **Commercial rights must cover all material elements.** The source owner's permission does not automatically clear embedded music, guests, game footage, logos, likeness or other third-party elements.

4. **Context integrity is a blocking quality gate.** A highlight must not reverse, materially distort, or overstate the original meaning. Sensitive/factual claims need enough pre/post context; uncertain clips are expanded or blocked rather than optimized into a misleading hook.

5. **Transformation must be auditable.** Each publishable third-party clip should retain source hash/time range, context window, original value added, commentary/analysis summary, visual changes, rights record and target platform.

6. **TikTok Creator Rewards is not inferred from a license.** Current originality/duration/program rules are rechecked separately. Sponsored, reused or licensed material is never assumed eligible merely because publication rights exist.

7. **TikTok Shop repost authorization is scoped evidence, not a universal license.** The current Content Authorization Tool can record creator-to-creator permission to repost TikTok Shop content, including an authorization end date. When applicable, use this platform evidence rather than an informal assumption, record its scope/expiry, and do not extend it to paid-media use or other platforms unless that separate permission exists.

## Reconciled shopping / commerce findings

### Retained

The current TikTok Shop policy already had strong foundations and they remain:

- product-page ingest before final script
- claim-to-evidence mapping
- fresh price/coupon/stock/shipping facts
- one primary persona and purchase motive
- review deduplication and clustering
- no fake reviews, fake scarcity or unverified claims
- GMV funnel measurement with returns/refunds/complaints as guardrails
- publish-time current platform policy snapshot

### Strengthened

1. **Generic shopping is not automatically TikTok Shop.** First resolve the target platform by meaning/context. TikTok-specific hook, UI, Shop and AIGC rules must not be silently applied as universal commerce law. YouTube Shopping uses current YouTube program evidence instead.

2. **Japan commercial disclosure becomes a blocking gate when applicable.** Consumer Affairs Agency guidance emphasizes whether the commercial nature is clear from the overall presentation. A disclosure hidden only in a reply or remote description is not considered a safe default. Because viewers can enter a video midstream, commercial nature should remain clear throughout the video when the relationship requires disclosure. Platform paid-promotion/affiliate labels are used when current platform rules require them.

3. **TikTok Shop exact listing/product match is mandatory.** Visuals, spoken claims and captions must match the actual listing or another verified source. Fictitious listings and unsupported/restricted products are blocked.

4. **Japan localization is current platform evidence, not a universal rule.** TikTok Shop Japan content must comply with current Japan-market localization requirements. These must be rechecked rather than copied to other platforms.

5. **Low-information static Shop videos are a quality risk.** TikTok Shop's current content policy treats non-interactive/static low-information content as low quality. Product-only+BGM or templated narration is therefore not the default.

6. **AIGC has a live policy contradiction.** The June 2026 general TikTok Shop Content Policy contains broad language prohibiting AIGC, while the July 2026 dedicated Japan AIGC guidance describes conditions under which AIGC can be used, including labeling, fidelity and anti-template requirements. The system must not silently choose the more permissive interpretation. If AIGC is used, both current sources are re-fetched at publish time; an unresolved conflict blocks publish handoff.

7. **Paid-media reuse is a separate permission.** TikTok Shop's Shop Ads authorization can broaden seller use of creator videos and may use different commission rates. The AI Army must not auto-enable mass ad authorization. Scope, term, rate and revocation are recorded, and new/broad paid-media use remains human-approved.

8. **Affiliate qualification is versioned.** Seller/product qualification and commission availability are rechecked near execution. A previous qualifying state is not permanent evidence.

## Shop + clipping intersection

A product-selling clip made from existing or third-party source media must satisfy both families at the same time:

`source rights + embedded rights + transformation/originality + context integrity + product claim evidence + commercial fact freshness + disclosure + platform policy + technical QA + human publish approval`

Neither permission chain substitutes for the other:

- brand/seller permission does not clear third-party source-media rights
- source-media permission does not validate product claims, discounts or disclosures
- platform repost authorization applies only to its recorded scope/term and does not silently expand to another platform
- organic posting permission does not automatically grant paid-media/whitelisting/ad-use permission

## Rejected shortcuts

The following were specifically rejected or demoted after reconciliation:

- permission = monetization eligibility
- one disclosure at the very start is always enough in Japan
- disclosure only in a reply or buried description
- TikTok first-3-seconds guidance as universal cross-platform law
- TikTok Shop rules automatically applied to YouTube Shopping
- licensed clip automatically qualifies for TikTok Creator Rewards
- crop/zoom/captions prove transformation
- platform repost permission automatically extends to paid media or another platform
- mass template clipping/shop videos as the default scaling model
- automatically enabling broad paid-media reuse
- choosing the permissive AIGC interpretation when current platform documents conflict

## Recall contract

This knowledge is not complete merely because the files exist. For every new task:

1. classify the user's meaning and surrounding context
2. resolve all applicable current read sets
3. read current repository versions
4. union overlapping domains such as Shop + Clipping + Monetization
5. apply current official platform/legal evidence where volatile
6. validate before side effects

Same-head/same-blob read caching is allowed within an active task. A changed branch HEAD or changed blob invalidates that shortcut.
