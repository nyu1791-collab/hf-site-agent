#!/usr/bin/env python3
"""Build and compare redacted Provider model-catalog snapshots.

This module is deliberately offline.  It accepts an already-fetched public
catalog, keeps only bounded model metadata, and never stores credentials,
account details, raw responses, or prompt content.  A new or changed model is
never promoted automatically; callers must pass the result through the
existing capability, cost, quota, probe, benchmark, and approval gates.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

PROVIDERS = frozenset(("google", "nvidia", "groq", "openrouter"))
SNAPSHOT_SCHEMA_VERSION = "provider-catalog-snapshot-v1"
LIFECYCLE_VALUES = frozenset(
    ("STABLE", "GA", "PREVIEW", "EXPERIMENTAL", "LEGACY", "DEPRECATED", "REMOVED", "UNKNOWN")
)
PRICING_CLASSES = frozenset(("FREE_CATALOG_ONLY", "PAID", "UNKNOWN"))
MODEL_ID_RE = re.compile(r"^[A-Za-z0-9._:/-]{1,200}$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ZERO_PRICES = frozenset(("0", "0.0", "0.00"))


class CatalogSnapshotError(ValueError):
    """Raised when a snapshot is invalid or unsafe to persist."""


def _safe_text(value: Any, *, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > limit or any(ord(char) < 32 for char in text):
        return None
    return text


def _model_id(entry: Mapping[str, Any]) -> str | None:
    value = _safe_text(entry.get("id", entry.get("model_id")), limit=200)
    return value if value and MODEL_ID_RE.fullmatch(value) else None


def _lifecycle(entry: Mapping[str, Any]) -> str:
    raw = _safe_text(entry.get("lifecycle", entry.get("model_lifecycle")), limit=32)
    if raw:
        lifecycle = raw.upper()
        if lifecycle in LIFECYCLE_VALUES:
            return lifecycle
    status = (_safe_text(entry.get("status"), limit=32) or "").lower()
    if status in {"removed", "retired"}:
        return "REMOVED"
    if status == "deprecated":
        return "DEPRECATED"
    return "UNKNOWN"


def _capabilities(entry: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("capabilities", "capability_tags", "supported_parameters"):
        raw = entry.get(key)
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, Sequence) or isinstance(raw, (bytes, bytearray)):
            continue
        for item in raw:
            text = _safe_text(item, limit=80)
            if text:
                normalized = text.lower()
                if normalized not in values:
                    values.append(normalized)
    architecture = entry.get("architecture")
    if isinstance(architecture, Mapping):
        modality = _safe_text(architecture.get("modality"), limit=80)
        if modality and modality.lower() not in values:
            values.append(modality.lower())
    return sorted(values)[:64]


def _context_length(entry: Mapping[str, Any]) -> int | None:
    value = entry.get("context_length")
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 10**9:
        return value
    return None


def _pricing_class(entry: Mapping[str, Any]) -> str:
    pricing = entry.get("pricing")
    if not isinstance(pricing, Mapping):
        return "UNKNOWN"
    prompt = str(pricing.get("prompt", "")).strip()
    completion = str(pricing.get("completion", "")).strip()
    if prompt in _ZERO_PRICES and completion in _ZERO_PRICES:
        model_id = _model_id(entry) or ""
        return "FREE_CATALOG_ONLY" if model_id.endswith(":free") else "UNKNOWN"
    if prompt or completion:
        if prompt in _ZERO_PRICES and completion in _ZERO_PRICES:
            return "UNKNOWN"
        return "PAID"
    return "UNKNOWN"


def _quota_class(entry: Mapping[str, Any]) -> str:
    value = _safe_text(entry.get("quota_class"), limit=80)
    return value or "UNKNOWN"


def _record(entry: Mapping[str, Any]) -> dict[str, Any] | None:
    model_id = _model_id(entry)
    if not model_id:
        return None
    return {
        "model_id": model_id,
        "display_name": _safe_text(entry.get("display_name", entry.get("name")), limit=240),
        "lifecycle": _lifecycle(entry),
        "capabilities": _capabilities(entry),
        "context_length": _context_length(entry),
        "pricing_class": _pricing_class(entry),
        "quota_class": _quota_class(entry),
    }


def _canonical_body(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": snapshot["schema_version"],
        "provider_id": snapshot["provider_id"],
        "discovered_at": snapshot["discovered_at"],
        "invalid_model_entries": snapshot["invalid_model_entries"],
        "duplicate_model_entries": snapshot["duplicate_model_entries"],
        "models": snapshot["models"],
    }


def _snapshot_hash(snapshot: Mapping[str, Any]) -> str:
    encoded = json.dumps(_canonical_body(snapshot), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def build_snapshot(
    provider_id: str,
    entries: Sequence[Mapping[str, Any]],
    *,
    discovered_at: str | None = None,
) -> dict[str, Any]:
    """Return a redacted public-catalog snapshot without network access."""
    if provider_id not in PROVIDERS:
        raise CatalogSnapshotError("unknown provider")
    timestamp = discovered_at or datetime.now(timezone.utc).isoformat()
    if not isinstance(timestamp, str) or not timestamp.strip():
        raise CatalogSnapshotError("discovered_at is required")
    models: list[dict[str, Any]] = []
    seen: set[str] = set()
    invalid = 0
    duplicates = 0
    for entry in entries:
        if not isinstance(entry, Mapping):
            invalid += 1
            continue
        model = _record(entry)
        if model is None:
            invalid += 1
            continue
        if model["model_id"] in seen:
            duplicates += 1
            continue
        seen.add(model["model_id"])
        models.append(model)
    models.sort(key=lambda item: item["model_id"])
    snapshot: dict[str, Any] = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "provider_id": provider_id,
        "discovered_at": timestamp,
        "invalid_model_entries": invalid,
        "duplicate_model_entries": duplicates,
        "models": models,
    }
    snapshot["snapshot_sha256"] = _snapshot_hash(snapshot)
    validate_snapshot(snapshot)
    return snapshot


def validate_snapshot(snapshot: Mapping[str, Any]) -> None:
    if snapshot.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise CatalogSnapshotError("unsupported snapshot schema")
    if snapshot.get("provider_id") not in PROVIDERS:
        raise CatalogSnapshotError("invalid snapshot provider")
    if not _safe_text(snapshot.get("discovered_at"), limit=80):
        raise CatalogSnapshotError("snapshot timestamp is missing")
    if not isinstance(snapshot.get("invalid_model_entries"), int) or snapshot["invalid_model_entries"] < 0:
        raise CatalogSnapshotError("invalid entry count")
    if not isinstance(snapshot.get("duplicate_model_entries"), int) or snapshot["duplicate_model_entries"] < 0:
        raise CatalogSnapshotError("invalid duplicate count")
    models = snapshot.get("models")
    if not isinstance(models, list):
        raise CatalogSnapshotError("models must be a list")
    seen: set[str] = set()
    for model in models:
        if not isinstance(model, Mapping):
            raise CatalogSnapshotError("model record must be an object")
        model_id = model.get("model_id")
        if not isinstance(model_id, str) or not MODEL_ID_RE.fullmatch(model_id) or model_id in seen:
            raise CatalogSnapshotError("invalid or duplicate model ID")
        seen.add(model_id)
        if model.get("display_name") is not None and not _safe_text(model.get("display_name"), limit=240):
            raise CatalogSnapshotError("invalid display name")
        if model.get("lifecycle") not in LIFECYCLE_VALUES:
            raise CatalogSnapshotError("invalid lifecycle")
        if not isinstance(model.get("capabilities"), list) or any(
            not isinstance(item, str) or not _safe_text(item, limit=80) for item in model["capabilities"]
        ):
            raise CatalogSnapshotError("invalid capabilities")
        context = model.get("context_length")
        if context is not None and (not isinstance(context, int) or isinstance(context, bool) or context < 0):
            raise CatalogSnapshotError("invalid context length")
        if model.get("pricing_class") not in PRICING_CLASSES:
            raise CatalogSnapshotError("invalid pricing class")
        if not isinstance(model.get("quota_class"), str) or not _safe_text(model["quota_class"], limit=80):
            raise CatalogSnapshotError("invalid quota class")
    digest = snapshot.get("snapshot_sha256")
    if not isinstance(digest, str) or not HASH_RE.fullmatch(digest) or digest != _snapshot_hash(snapshot):
        raise CatalogSnapshotError("snapshot hash mismatch")


def save_snapshot(path: str | Path, snapshot: Mapping[str, Any]) -> None:
    validate_snapshot(snapshot)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)


def load_snapshot(path: str | Path) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise CatalogSnapshotError("snapshot could not be read") from exc
    if not isinstance(payload, Mapping):
        raise CatalogSnapshotError("snapshot must be an object")
    validate_snapshot(payload)
    return dict(payload)


def _change_action(events: Sequence[str]) -> str:
    if "MODEL_REMOVED" in events or "MODEL_DEPRECATED" in events:
        return "BLOCK_ROUTING"
    if "PRICING_CHANGED" in events:
        return "FAIL_CLOSED_REVALIDATE"
    if "MODEL_ADDED" in events:
        return "DISCOVERED_NO_AUTO_ACTIVATION"
    if "UNKNOWN_CHANGE" in events:
        return "BLOCK_ROUTING"
    if events:
        return "REVALIDATE_BEFORE_ROUTING"
    return "UNCHANGED"


def compare_snapshots(previous: Mapping[str, Any], current: Mapping[str, Any]) -> dict[str, Any]:
    """Return safe drift events; never mutates either snapshot or a registry."""
    validate_snapshot(previous)
    validate_snapshot(current)
    if previous["provider_id"] != current["provider_id"]:
        return {
            "schema_version": "provider-catalog-drift-v1",
            "provider_id": current["provider_id"],
            "events": ["UNKNOWN_CHANGE"],
            "models": [],
            "auto_activation_allowed": False,
            "safe_to_route": False,
            "fail_closed": True,
        }
    old = {item["model_id"]: item for item in previous["models"]}
    new = {item["model_id"]: item for item in current["models"]}
    changes: list[dict[str, Any]] = []
    events: set[str] = set()
    for model_id in sorted(set(old) | set(new)):
        model_events: list[str] = []
        if model_id not in old:
            model_events.append("MODEL_ADDED")
        elif model_id not in new:
            model_events.append("MODEL_REMOVED")
        else:
            before = old[model_id]
            after = new[model_id]
            if before["display_name"] != after["display_name"]:
                model_events.append("MODEL_RENAMED")
            if before["lifecycle"] != after["lifecycle"]:
                if after["lifecycle"] == "REMOVED":
                    model_events.append("MODEL_REMOVED")
                elif after["lifecycle"] == "DEPRECATED":
                    model_events.append("MODEL_DEPRECATED")
                else:
                    model_events.append("UNKNOWN_CHANGE")
            if before["capabilities"] != after["capabilities"]:
                model_events.append("CAPABILITY_CHANGED")
            if before["pricing_class"] != after["pricing_class"]:
                model_events.append("PRICING_CHANGED")
            if before["quota_class"] != after["quota_class"]:
                model_events.append("UNKNOWN_CHANGE")
        if model_events:
            unique_events = sorted(set(model_events))
            events.update(unique_events)
            changes.append({
                "model_id": model_id,
                "events": unique_events,
                "routing_action": _change_action(unique_events),
            })
    unsafe_events = {"MODEL_REMOVED", "MODEL_DEPRECATED", "PRICING_CHANGED", "UNKNOWN_CHANGE"}
    return {
        "schema_version": "provider-catalog-drift-v1",
        "provider_id": current["provider_id"],
        "events": sorted(events),
        "models": changes,
        "auto_activation_allowed": False,
        "safe_to_route": not bool(events & unsafe_events),
        "fail_closed": bool(events & unsafe_events),
    }


__all__ = [
    "CatalogSnapshotError",
    "PROVIDERS",
    "SNAPSHOT_SCHEMA_VERSION",
    "build_snapshot",
    "compare_snapshots",
    "load_snapshot",
    "save_snapshot",
    "validate_snapshot",
]
