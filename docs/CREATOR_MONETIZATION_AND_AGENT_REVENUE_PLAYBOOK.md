# Creator Monetization & Agent Revenue Playbook

**Status:** Permanent standard companion  
**Effective:** 2026-09-13 JST  
**Machine policy:** `config/creator_monetization_policy.json`  
**Versioned platform evidence:** `config/platform_program_evidence.json`  
**Read gate:** `config/monetization_command_read_gate.json`

## Objective

The AI Army is not a mass-posting bot. Its monetization role is to increase human originality, research quality, production throughput, analytics quality and commercial execution while keeping contracts, account actions, claims, disclosures and publication under the required human approval gates.

The portfolio has two main engines:

1. **Near-term cash engine:** off-platform B2B services that do not require a large owned audience first.
2. **Compounding media engine:** original YouTube/Instagram/TikTok/Facebook/X content that later unlocks ads/rewards, brand deals, affiliate commissions and memberships.

A third layer—memberships, subscriptions and premium content—becomes attractive once the audience has enough trust and repeat demand.

## Priority 1: earn before a large audience exists

### Short-form creative / UGC production service

Offer a bounded package to brands, agencies and SMEs: product/research brief, hook and script variants, shot list, captions, deterministic edit/QA, claim and rights checks, plus a post-campaign performance summary. Reuse the existing media, claim-provenance and rights standards rather than creating a weaker client-service pipeline.

The AI Army may prepare research, concepts, scripts, variants, QA and reporting. A human approves scope, price, claims, rights, final deliverables and any send/publish action.

### Analytics, localization and social-operations service

Package retention diagnosis, content-performance summaries, subtitle/localization/transcreation, content calendars and draft social posts. This is particularly suitable for recurring monthly retainers because much of the repetitive research and reporting can be automated without automating the final public action.

### What not to sell

Do not sell fake engagement, bot followers, mass comments, fake reviews, scraped/copyright-infringing clip farms, undisclosed advertising, guaranteed view/revenue claims or automated spam outreach. Those are not growth strategies; they create platform and reputation risk.

## Priority 2: official creator marketplaces and brand deals

Prefer official or opt-in marketplace surfaces over cold mass outreach:

- YouTube Creator Partnerships in Japan for eligible YPP creators.
- Instagram Creator Marketplace in Japan and partnership ads.
- TikTok One / Creator Marketplace, subject to current regional and account eligibility.

The AI Army can perform eligibility pre-checks, brand-fit research, portfolio and pitch drafts, deliverable tracking and campaign reporting. It cannot autonomously accept a contract, commit a rate, send large-scale unsolicited pitches or publish campaign content.

Client-facing rate cards and campaign reports use first-party observed metrics or clearly cited evidence. Modeled economics must be labelled **modeled**, never presented as achieved results.

## Priority 3: affiliate commerce beyond TikTok Shop

### YouTube Shopping affiliate — Japan

As of 2026-09-13, YouTube officially lists Japan among supported countries. Eligible creators can tag products and earn merchant-set commissions. Returns can reverse commissions and payment can lag substantially, so gross commission is not net revenue.

### Instagram affiliate product tags — Japan

Meta announced in June 2026 that creators in Japan can use affiliate links/product tags and earn commissions. Treat per-account availability and exact commission mechanics as volatile evidence and re-check before execution.

### Domestic affiliate networks

Amazon Associates Japan, Rakuten, ValueCommerce, A8, もしも and similar external networks may be useful experiments when their current terms, merchant categories, disclosure requirements and attribution windows fit the content. Their presence in this playbook is **not an endorsement** and exact fees/eligibility must be reverified before use.

For all affiliate lanes, measure click-through, conversion, reversals/refunds, net commission and payout lag. Never optimize only gross sales.

## Priority 4: recurring fan revenue

Potential lanes include YouTube memberships/fan funding, Instagram subscriptions, X Subscriptions and, when audience fit exists, TikTok Series or off-platform memberships/digital products.

Recurring revenue is promoted only when the paid value proposition is real. Automated filler, recycled public posts behind a paywall or engagement manipulation are rejected. Measure MRR, ARPPU, churn, production burden and member retention.

## Platform payout lanes are secondary, not the whole business

### YouTube YPP

Current 2026 ad/Premium thresholds and the already-announced 2027 threshold change are stored in `config/platform_program_evidence.json`. Early fan-funding/Shopping eligibility is separate. Do not hardcode remembered thresholds into plans without rereading current official evidence.

YouTube clarified in 2025 that repetitive or mass-produced inauthentic content is not eligible for monetization. AI assistance is therefore used to increase originality and quality—not to generate superficially varied bulk uploads.

### TikTok Creator Rewards

Treat Creator Rewards as a secondary revenue stream for eligible original, high-quality longer videos. Current thresholds, qualified-view mechanics and account eligibility are checked in TikTok Studio before planning around the program.

### X Original Content Rewards

Legacy Creator Revenue Sharing retired on 2026-09-07; Original Content Rewards began rollout on 2026-09-08. Japan is currently listed as supported. The current program explicitly excludes content created or posted using automated means. Therefore the AI Army may research, draft, analyze and QA, but monetized X content requires genuine human authorship/review and a non-automated publication path.

### Facebook Content Monetization / Creator Fast Track

Facebook Content Monetization is a real upside lane, but current access is invite/eligibility dependent. Creator Fast Track is primarily relevant to creators already established on other platforms. Recheck account and market eligibility before counting either in a revenue plan.

Facebook also explicitly favors original content and can deprioritize/demonetize low-value reuploads, minor edits, captions/speed changes and reactions that add no substantive value.

## Distribution-only channels

A platform can still be valuable without a verified direct payout. Threads or Pinterest-style distribution can be tested as audience acquisition or commerce routing, but reach is not revenue. A distribution experiment must identify the downstream monetized destination and attribution metric before it is called a monetization lane.

## Japan disclosure and claims

Commercial relationships must be clear when required. Japan's stealth-marketing rules under the Act against Unjustifiable Premiums and Misleading Representations have applied since 2023-10-01. Platform-specific paid-partnership or affiliate labels are also followed where applicable.

Product, numeric and volatile commercial claims use the repository's claim-evidence ledger. Price, coupon, stock, shipping, eligibility and platform-program facts are rechecked at the action/publish handoff. No fabricated sales, conversions, reach or testimonials are permitted.

## Measurement

Every revenue lane distinguishes **observed** from **modeled** economics. Useful portfolio metrics include:

- observed gross and net revenue;
- gross margin;
- time to first revenue;
- human review hours and rework rate;
- repeat-client rate;
- MRR, ARPPU and churn;
- affiliate commission reversal/refund rate and payout lag;
- platform-dependency share;
- disclosure compliance and claim/policy incidents.

Do not compare platform RPM, views, reach or engagement metrics as if definitions were identical. Normalize only when a documented mapping exists.

## Approval boundary

Research, ranking, internal analysis, drafts and QA can be automated. The following remain gated: sending a pitch/outreach, applying to a program, accepting or negotiating a binding contract/rate, account/payment setup, public publication, payout reconciliation and identity/secret handling.

## Cross-tab behavior

On every new tab/session, monetization tasks reread the current repository versions of the permanent manifest, this playbook, the machine policy, platform evidence and the monetization command read gate. Conversation memory is not a substitute. If a platform changes a program tomorrow, current official evidence overrides this 2026-09-13 snapshot and the registry is updated instead of preserving stale folklore.
