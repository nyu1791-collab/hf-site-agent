#!/usr/bin/env python3
"""Deterministic rights ledger used before any media publish approval.

No network, purchase, upload, deploy or publish action is performed here.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER = ROOT / "asset_rights.json"
BLOCKED_LICENSES = {
    "",
    "UNKNOWN",
    "ALL_RIGHTS_RESERVED",
    "NONCOMMERCIAL_ONLY",
    "NO_DERIVATIVES",
    "EDITORIAL_ONLY_UNVERIFIED",
}
DEFAULT_ALLOWED_LICENSES = {
    "CC0-1.0",
    "CC-BY-2.0",
    "CC-BY-3.0",
    "CC-BY-4.0",
    "CC-BY-SA-3.0",
    "CC-BY-SA-4.0",
    "PUBLIC_DOMAIN",
}


class AssetRightsError(ValueError):
    pass


def _policy_allowed_licenses(policy: Mapping[str, Any] | None) -> set[str]:
    row = policy if isinstance(policy, Mapping) else {}
    configured = row.get("allowed_licenses")
    if configured is None:
        return set(DEFAULT_ALLOWED_LICENSES)
    if not isinstance(configured, list) or not configured:
        raise AssetRightsError("allowed_licenses must be a non-empty list")
    allowed = {str(value or "").strip().upper() for value in configured if str(value or "").strip()}
    if not allowed or allowed & BLOCKED_LICENSES:
        raise AssetRightsError("allowed_licenses contains blocked or invalid values")
    return allowed


def load_ledger(path: str | Path = DEFAULT_LEDGER) -> dict[str, Any]:
    row = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(row, dict) or row.get("schema_version") != "asset-rights-v1":
        raise AssetRightsError("invalid asset rights ledger")
    if not isinstance(row.get("assets"), list):
        raise AssetRightsError("assets must be a list")
    policy = row.get("policy") if isinstance(row.get("policy"), Mapping) else {}
    if policy.get("unknown_license_publish") is not False:
        raise AssetRightsError("unknown-license publish must remain disabled")
    if policy.get("paid_stock_fallback") is not False or policy.get("auto_purchase") is not False:
        raise AssetRightsError("paid stock purchase path must remain disabled")
    _policy_allowed_licenses(policy)
    return row


def _canonical_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parts = urlsplit(text)
    if parts.scheme.lower() != "https" or not parts.netloc:
        return ""
    return urlunsplit(("https", parts.netloc.lower(), parts.path, parts.query, ""))


def _verified_timestamp(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def validate_asset(asset: Mapping[str, Any], *, policy: Mapping[str, Any] | None = None) -> list[str]:
    failures: list[str] = []
    if not str(asset.get("asset_id") or "").strip():
        failures.append("asset_id_required")
    if not _canonical_url(asset.get("source_url")):
        failures.append("source_url_required")

    license_name = str(asset.get("license") or "UNKNOWN").strip().upper()
    if license_name in BLOCKED_LICENSES:
        failures.append("license_unknown_or_blocked")
    elif license_name not in _policy_allowed_licenses(policy):
        failures.append("license_not_allowlisted")

    if asset.get("commercial_use_ok") is not True:
        failures.append("commercial_use_not_verified")
    if asset.get("attribution_required") is True and not str(asset.get("author") or "").strip():
        failures.append("author_required")
    if not _verified_timestamp(asset.get("verified_at")):
        failures.append("verified_at_required")
    if asset.get("rights_verified") is not True:
        failures.append("rights_not_verified")
    return sorted(set(failures))


def register_asset(ledger: Mapping[str, Any], asset: Mapping[str, Any]) -> dict[str, Any]:
    """Return a new ledger. Duplicate asset/source entries reuse the existing record."""
    result = deepcopy(dict(ledger))
    result.setdefault("assets", [])
    policy = result.get("policy") if isinstance(result.get("policy"), Mapping) else {}
    failures = validate_asset(asset, policy=policy)
    if failures:
        raise AssetRightsError(",".join(failures))
    normalized = deepcopy(dict(asset))
    normalized["asset_id"] = str(normalized["asset_id"])
    normalized["source_url"] = _canonical_url(normalized["source_url"])
    normalized["license"] = str(normalized.get("license") or "").strip().upper()
    for existing in result["assets"]:
        if not isinstance(existing, Mapping):
            continue
        if str(existing.get("asset_id")) == normalized["asset_id"] or _canonical_url(existing.get("source_url")) == normalized["source_url"]:
            result["last_registration"] = {
                "asset_id": str(existing.get("asset_id") or normalized["asset_id"]),
                "reused": True,
            }
            return result
    result["assets"].append(normalized)
    result["last_registration"] = {"asset_id": normalized["asset_id"], "reused": False}
    return result


def credit_lines(ledger: Mapping[str, Any], asset_ids: Sequence[str] | None = None) -> list[str]:
    wanted = {str(v) for v in asset_ids} if asset_ids is not None else None
    lines: list[str] = []
    for asset in ledger.get("assets", ()) if isinstance(ledger.get("assets"), list) else ():
        if not isinstance(asset, Mapping):
            continue
        if wanted is not None and str(asset.get("asset_id")) not in wanted:
            continue
        if asset.get("attribution_required") is True:
            lines.append(f"{asset.get('author')} — {asset.get('source_url')} — {asset.get('license')}")
    return lines


def publish_rights_gate(
    ledger: Mapping[str, Any],
    *,
    asset_ids: Sequence[str],
    deterministic_validator_passed: bool,
    independent_media_qa_passed: bool,
) -> dict[str, Any]:
    indexed = {
        str(asset.get("asset_id")): asset
        for asset in ledger.get("assets", ())
        if isinstance(asset, Mapping) and str(asset.get("asset_id") or "")
    }
    policy = ledger.get("policy") if isinstance(ledger.get("policy"), Mapping) else {}
    failures: list[str] = []
    for asset_id in asset_ids:
        row = indexed.get(str(asset_id))
        if row is None:
            failures.append(f"asset_missing:{asset_id}")
            continue
        for failure in validate_asset(row, policy=policy):
            failures.append(f"{asset_id}:{failure}")
    if not deterministic_validator_passed:
        failures.append("validator_failed")
    if not independent_media_qa_passed:
        failures.append("independent_media_qa_failed")
    return {
        "rights_gate_passed": not failures,
        "failures": sorted(set(failures)),
        "credits": credit_lines(ledger, asset_ids),
        "publish_executed": False,
        "human_publish_approval_required": True,
        "ready_for_human_publish_approval": not failures,
    }


def save_ledger(ledger: Mapping[str, Any], path: str | Path = DEFAULT_LEDGER) -> None:
    policy = ledger.get("policy") if isinstance(ledger.get("policy"), Mapping) else {}
    _policy_allowed_licenses(policy)
    for asset in ledger.get("assets", ()) if isinstance(ledger.get("assets"), list) else ():
        if not isinstance(asset, Mapping):
            raise AssetRightsError("invalid asset row")
        failures = validate_asset(asset, policy=policy)
        if failures:
            raise AssetRightsError(",".join(failures))
    Path(path).write_text(json.dumps(dict(ledger), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


__all__ = [
    "AssetRightsError",
    "credit_lines",
    "load_ledger",
    "publish_rights_gate",
    "register_asset",
    "save_ledger",
    "validate_asset",
]
