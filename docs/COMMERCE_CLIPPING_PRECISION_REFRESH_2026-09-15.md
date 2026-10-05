# Commerce + Clipping Precision Refresh — 2026-09-15

**Status:** Current official-source reconciliation / second review complete  
**Decision owner:** ChatGPT Top Commander  
**Machine overlay:** `config/commerce_clipping_precision_overlay.json`

## Purpose

This refresh strengthens authorized clipping / repurposing and shopping / commerce media without replacing their existing canonical policies. The base authorities remain `config/authorized_clipping_monetization_policy.json`, `docs/AUTHORIZED_CLIPPING_AND_MONETIZATION_PLAYBOOK.md`, `config/tiktok_shop_influence_policy.json`, and `docs/TIKTOK_SHOP_INFLUENCE_PLAYBOOK.md`.

The overlay is additive. Existing rights, originality, claim, platform, disclosure, approval and technical-QA gates remain active. Saved know-how is incomplete unless a semantically matching request actually reaches the current policy through the media / monetization read gates.

## Research method

Current official / primary sources were prioritized over creator folklore, community tips and AI reviewer opinion. Platform-specific observations are not generalized into cross-platform laws.

The second review specifically checked permission vs monetization eligibility, context integrity and transformation, product/listing match, claim integrity across all surfaces, low-engagement and unoriginal Shop content, product authenticity, paid-media rights vs organic-posting rights, Japan commercial disclosure, YouTube Shopping auto-tag accuracy, and whether numeric platform advice belongs in policy or only in experimentation.

## Official sources reviewed

### YouTube

- Channel monetization / reused and inauthentic content: https://support.google.com/youtube/answer/1311392
- Commercial-use rights: https://support.google.com/youtube/answer/2490020
- Paid-promotion disclosure: https://support.google.com/youtube/answer/154235
- YouTube Shopping Affiliate Program: https://support.google.com/youtube/answer/13376398?hl=ja
- Shopping product auto-tagging: https://support.google.com/youtube/answer/17046000?hl=ja
- Shopping Affiliate tips / experiment evidence: https://support.google.com/youtube/answer/15814303?hl=ja

### TikTok / TikTok Shop Japan

- Creator Rewards / originality: https://support.tiktok.com/
- TikTok Shop Content Policy: https://seller-jp.tiktok.com/university/essay?knowledge_id=2707587427927825
- Irrelevant Promotional Content: https://seller-jp.tiktok.com/university/essay?knowledge_id=2715915867866881
- Misleading Content: https://seller-jp.tiktok.com/university/essay?knowledge_id=2498606331332368
- Low-Engagement Content: https://seller-jp.tiktok.com/university/essay?knowledge_id=600891219576592&lang=en
- Unoriginal / Pre-recorded Content: https://seller-jp.tiktok.com/university/essay?knowledge_id=2714558917691153&lang=en
- AIGC guidance: https://seller-jp.tiktok.com/university/essay?knowledge_id=6860523157653265&lang=ja-JP
- IP policy: https://seller-jp.tiktok.com/university/essay?knowledge_id=2727520119195408&lang=en
- Counterfeit / Knockoff policy: https://seller-jp.tiktok.com/university/essay?knowledge_id=2727550621255440
- Content Authorization Tool: https://seller-jp.tiktok.com/university/essay?knowledge_id=680638764795649&lang=ja-JP
- Creator Performance Evaluation: https://seller-jp.tiktok.com/university/essay?knowledge_id=2709643852121873
- Affiliate seller/product qualification: https://seller-jp.tiktok.com/university/essay?knowledge_id=2575312534718209&lang=en
- Shop Ads creator authorization / commission: https://seller-jp.tiktok.com/university/essay?knowledge_id=1192106471966480&lang=ja-JP
- Video Quality Content Guidelines: https://seller-jp.tiktok.com/university/essay?knowledge_id=5514075966703376

### Japan disclosure

- Consumer Affairs Agency — stealth marketing Q&A: https://www.caa.go.jp/policies/policy/representation/fair_labeling/faq/stealth_marketing/

## Clipping findings retained

- Generic unlicensed mass clipping is not the canonical business lane.
- Source rights and platform monetization eligibility are separate gates.
- Attribution alone is not permission.
- Source-owner permission does not automatically clear embedded music, guests, game footage, logos, likeness or other third-party rights.
- Captions / crop / zoom alone do not prove substantive transformation.
- ASR / alignment, multi-signal candidate scoring, dedupe, semantic boundaries, reframe, captions and machine QA remain the deterministic clipping pipeline.
- Successful stages are checkpoints; later failure does not restart healthy earlier stages.
- Third-party publication remains human-approved unless separately authorized.

## Clipping precision strengthened

### Permission is not monetization approval

YouTube reused-content review is separate from copyright permission and is evaluated at channel level. A licensed clip therefore still needs clearly recognizable original value and must not collapse into repetitive / interchangeable output.

### Context integrity is blocking

A highlight must not reverse, materially distort or overstate the source. Factual or sensitive claims require enough pre/post context. If context is uncertain, expand the clip or block it rather than optimizing an inaccurate hook.

### Transformation is auditable

Each publishable third-party clip retains source hash, source time range, selected pre/post context, original value added, commentary / analysis summary, visual changes, target platform, rights record, and edit-policy version.

### Shop authorization is scoped, not universal

A TikTok Shop content-authorization record is useful permission evidence when applicable, but its scope and end date are recorded. It does not silently become paid-media permission, another-platform permission, Creator Rewards eligibility, or proof of substantive originality.

## Shopping / commerce findings retained

- Product-page ingest before final script.
- Claim-to-evidence mapping.
- Publish-time refresh for volatile facts.
- One primary persona and purchase motive.
- Review dedupe / clustering.
- No fake reviews, fake scarcity or unsupported claims.
- Returns / refunds / complaints remain sales guardrails.
- Current platform-policy snapshot before publication.
- Platform-specific rules are not universal commerce law.

## Shopping precision strengthened

### Claim-surface integrity

Evidence control now covers every material claim-bearing surface: `cover + title + script + caption + voice/audio + metadata + CTA + visual demonstration`.

No surface may state or imply a claim stronger than the current listing or verified evidence. A hook or cover cannot exaggerate merely because the detailed script later becomes more careful.

### TikTok Shop product focus and exact listing match

Shopping content must clearly show, introduce and explain the linked product. Showing one product while linking another, hiding the product in the background, or allowing unrelated entertainment to dominate the product pitch is blocked or reworked.

### Low-engagement / templated output

Current TikTok Shop guidance identifies many near-identical videos, similar templates across multiple accounts, product/hands-only presentation with no meaningful personal presence, and excessive AI voiceover with little substantive information or commentary as quality risks.

The durable rule is **not** “AI voice is always banned.” AI voice, an avatar or a template alone does not constitute substantive original value. The content still needs real product evidence, useful commentary / demonstration and creative variation, subject to the current AIGC policy.

### Authorized Shop clip is not automatically original

Rights permission and originality are separate. An authorized / licensed Shop clip still needs original commerce commentary or creative value, correct product focus and exact listing match. Detection-evasion edits never count as originality.

### Product authenticity

The current Japan counterfeit / knockoff policy is explicit. Product admission therefore includes authenticity screening for brand-sensitive categories.

- counterfeit / knockoff: `BLOCK`
- suspected counterfeit or authenticity that cannot be verified: `BLOCK_UNTIL_RESOLVED`

### YouTube Shopping auto-tags are proposals, not proof

YouTube states that automatic product tagging can occasionally be wrong and should be manually reviewed. Therefore auto-tag is a candidate / workflow acceleration only; manual product-match verification is required before publish, and incorrect tags must be removed or corrected.

### Numeric platform guidance stays experimental

YouTube reports a product-click uplift in one Shopping experiment for tags plus description links. TikTok Shop education also reports “30 seconds+” and “5+ shopping videos/week” observations. These are retained only as platform-specific experiment evidence / test hypotheses. They are not guaranteed uplifts, universal generation rules, cross-platform laws, or replacements for account-level analytics.

Real account analytics and valid experiments outrank generic platform education heuristics.

## Japan disclosure

When the commercial relationship is one that requires disclosure, clarity is judged from the overall presentation. Do not rely on a hidden reply, a distant / buried description, or a single intro disclosure as an automatic safe harbor. For video, viewers may join after the opening, so the presentation should remain sufficiently clear as commercial content when applicable. Current platform labels and legal requirements are rechecked at publish time.

Claim truth and disclosure remain separate blocking gates: a true claim can still have a disclosure problem, and a clear disclosure does not make a false claim acceptable.

## AIGC conflict handling

The existing fail-closed rule remains. If current TikTok Shop general content policy and dedicated AIGC guidance conflict, fetch both current sources at publish time, do not silently choose the more permissive interpretation, and block AIGC publish handoff while a material conflict remains unresolved.

## Shop + clipping union

A product-selling clip made from existing or third-party media must pass the union:

`source rights + embedded rights + transformation/originality + context integrity + product claim evidence + claim-surface integrity + product authenticity + Shop originality/product focus + commercial-fact freshness + commercial disclosure + platform-policy snapshot + technical QA + human publish approval`

No permission chain substitutes for another.

## Explicitly rejected / demoted shortcuts

- permission = monetization eligibility
- authorization = originality
- crop / zoom / captions = substantive transformation
- auto product tag = verified product match
- AI voice alone = substantive original value
- counterfeit / knockoff promotion
- claim in cover/title/audio stronger than its evidence
- disclosure only in reply or buried description
- one intro disclosure is always sufficient
- TikTok first-3-seconds guidance as universal platform law
- TikTok “30 seconds+” or “5 posts/week” as universal mandatory rule
- TikTok Shop rules automatically applied to YouTube Shopping
- licensed clip automatically qualifies for TikTok Creator Rewards
- mass-template clipping / Shop output as the default scaling model
- automatically enabling broad paid-media reuse
- choosing the permissive AIGC rule when current official sources conflict

## Semantic recall contract

For every new task:

1. classify the user's meaning and surrounding context;
2. resolve every applicable current read set;
3. read current repository versions;
4. union overlapping domains such as Shop + Clipping + Monetization;
5. apply current official platform/legal evidence where volatile;
6. validate before side effects.

Saved files are not operational knowledge unless the relevant semantic gate can actually reach them. Same-head / same-blob caching is allowed inside the active task; a changed HEAD or blob invalidates that shortcut.

## Post-save audit

After integration, re-check that media and monetization gates reach the overlay, Shop + clipping resolves both domains, base policies remain active, the official source snapshot is preserved, rights / claim / disclosure / authenticity gates remain blocking, numeric platform heuristics remain hypotheses rather than hard laws, the validator passes, and GitHub CI is checked before completion is reported.

### Integration verification trace

The first integrated policy commit passed the dedicated semantic-recall / commerce-clipping validator, including all 21 required primary-source records and the strengthened Shop + clipping union. The full consistency workflow then correctly blocked on an unrelated unregistered `push` trigger in a one-off longform build carrier. That carrier was retained but changed to manual-only execution rather than weakening or bypassing the CI control-plane rule. Final completion may only be reported after the resulting HEAD is checked again by both the canonical consistency and hierarchical runtime gates.
