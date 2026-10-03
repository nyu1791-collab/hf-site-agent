from __future__ import annotations

import unittest
from datetime import datetime, timezone

from scripts.tiktok_shop_claim_gate import evaluate


NOW = datetime(2026, 9, 12, 8, 30, tzinfo=timezone.utc)


class TikTokShopClaimGateTest(unittest.TestCase):
    def test_grounded_fresh_claims_pass(self) -> None:
        manifest = {
            "manifest_id": "ci-pass",
            "market": "JP",
            "product_url": "https://example.invalid/product",
            "product_snapshot_hash": "0123456789abcdef",
            "policy_snapshot_hash": "fedcba9876543210",
            "policy_rechecked_at": "2026-09-12T08:00:00Z",
            "aigc_disclosure_required": True,
            "claims": [
                {
                    "claim_id": "spec-1",
                    "text": "容量は500ml",
                    "claim_type": "PRODUCT_SPEC",
                    "evidence_refs": ["product-page:snapshot#capacity"],
                    "status": "VERIFIED",
                    "retrieved_at": "2026-09-12T07:50:00Z",
                    "expires_at": None,
                    "source_tier": "PRODUCT_PAGE",
                    "sample_size": None,
                    "original_language_retained": None,
                    "market_scope": "JP",
                    "notes": "",
                },
                {
                    "claim_id": "price-1",
                    "text": "現在表示価格は1000円",
                    "claim_type": "PRICE",
                    "evidence_refs": ["product-page:snapshot#price"],
                    "status": "VERIFIED",
                    "retrieved_at": "2026-09-12T07:50:00Z",
                    "expires_at": "2026-09-12T10:00:00Z",
                    "source_tier": "PRODUCT_PAGE",
                    "sample_size": None,
                    "original_language_retained": None,
                    "market_scope": "JP",
                    "notes": "volatile",
                },
                {
                    "claim_id": "review-1",
                    "text": "今回確認した中国語レビューでは辛さを評価する声が複数あった",
                    "claim_type": "LOCAL_REVIEW_PATTERN",
                    "evidence_refs": ["reviews:sample-1"],
                    "status": "VERIFIED",
                    "retrieved_at": "2026-09-12T07:30:00Z",
                    "expires_at": None,
                    "source_tier": "LOCAL_REVIEW_SAMPLE",
                    "sample_size": 12,
                    "original_language_retained": True,
                    "market_scope": "CN-language reviews sampled for this product",
                    "notes": "scope-limited",
                },
            ],
        }
        report = evaluate(manifest, now=NOW, max_policy_age_hours=2)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["block_count"], 0)

    def test_expired_unsupported_price_claim_blocks(self) -> None:
        manifest = {
            "manifest_id": "ci-block",
            "market": "JP",
            "product_url": "https://example.invalid/product",
            "product_snapshot_hash": "0123456789abcdef",
            "policy_snapshot_hash": "fedcba9876543210",
            "policy_rechecked_at": "2026-09-12T08:00:00Z",
            "claims": [
                {
                    "claim_id": "price-bad",
                    "text": "今だけ1000円",
                    "claim_type": "PRICE",
                    "evidence_refs": [],
                    "status": "VERIFIED",
                    "retrieved_at": "2026-09-12T06:00:00Z",
                    "expires_at": "2026-09-12T07:00:00Z",
                    "source_tier": "NONE",
                    "sample_size": None,
                    "original_language_retained": None,
                    "market_scope": "JP",
                    "notes": "",
                }
            ],
        }
        report = evaluate(manifest, now=NOW)
        self.assertEqual(report["status"], "BLOCK")
        reasons = {row["reason"] for row in report["blocks"]}
        self.assertIn("evidence_refs required for factual claim", reasons)
        self.assertIn("volatile PRICE claim expired before publication", reasons)


if __name__ == "__main__":
    unittest.main()
