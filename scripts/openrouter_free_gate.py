#!/usr/bin/env python3
"""Shared fail-closed admission gate for all OpenRouter inference requests.

OpenRouter is permanently free-only in this project. A request is admitted only
when the requested model is either an explicit exact ':free' model ID or the
current OpenRouter catalog proves both prompt and completion prices are zero.
Unknown, stale, malformed, generic-router, and non-zero price states are blocked
before inference.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Mapping, Sequence
import urllib.request

CATALOG_URL = "https://openrouter.ai/api/v1/models"
DEFAULT_CATALOG_TTL_SECONDS = 300
GENERIC_FREE_ROUTER = "openrouter/free"


class OpenRouterFreeGateError(RuntimeError):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class OpenRouterFreeDecision:
    allowed: bool
    reason: str
    model: str
    verified_at: float
    prompt_price: str | None
    completion_price: str | None
    evidence_source: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _decimal(value: Any) -> Decimal | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _catalog_rows(payload: Any) -> list[dict[str, Any]]:
    rows = payload.get("data") if isinstance(payload, Mapping) else payload
    if not isinstance(rows, list):
        raise OpenRouterFreeGateError("BLOCKED_UNVERIFIED_PRICE", "OpenRouter catalog shape is invalid")
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def _default_cache_path() -> Path:
    configured = os.environ.get("OPENROUTER_CATALOG_CACHE", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".cache" / "hf-site-agent" / "openrouter-models.json"


def load_openrouter_catalog(
    *,
    api_key: str = "",
    cache_path: Path | None = None,
    ttl_seconds: int = DEFAULT_CATALOG_TTL_SECONDS,
    timeout_seconds: float = 10.0,
    now: float | None = None,
    opener=urllib.request.urlopen,
) -> tuple[list[dict[str, Any]], float, str]:
    """Return a fresh catalog; expired cache is never used after fetch failure."""
    current = float(time.time() if now is None else now)
    ttl = max(1, int(ttl_seconds))
    path = cache_path or _default_cache_path()
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        fetched_at = float(cached.get("fetched_at"))
        rows = _catalog_rows(cached.get("models"))
        if current - fetched_at <= ttl:
            return rows, fetched_at, "TTL_CACHE"
    except Exception:
        pass

    headers = {"Accept": "application/json", "User-Agent": "hf-site-agent-openrouter-free-gate/1.0"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    request = urllib.request.Request(CATALOG_URL, headers=headers, method="GET")
    try:
        with opener(request, timeout=timeout_seconds) as response:
            raw = response.read(4_000_000).decode("utf-8")
            payload = json.loads(raw)
            rows = _catalog_rows(payload)
    except OpenRouterFreeGateError:
        raise
    except Exception as exc:
        raise OpenRouterFreeGateError(
            "BLOCKED_UNVERIFIED_PRICE",
            "OpenRouter catalog is unavailable; stale or unknown price evidence cannot authorize a request",
        ) from exc

    fetched_at = current
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(
            {"fetched_at": fetched_at, "models": rows},
            ensure_ascii=False,
            separators=(",", ":"),
        ) + "\n"
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
            tmp.write(encoded)
            tmp_path = Path(tmp.name)
        os.replace(tmp_path, path)
    except Exception:
        # Fresh in-memory evidence is sufficient; cache persistence is only an optimization.
        pass
    return rows, fetched_at, "LIVE_CATALOG"


def decide_openrouter_free_model(
    model: str,
    catalog: Sequence[Mapping[str, Any]] | None,
    *,
    verified_at: float | None = None,
) -> OpenRouterFreeDecision:
    requested = str(model or "").strip()
    stamp = float(time.time() if verified_at is None else verified_at)
    if not requested:
        return OpenRouterFreeDecision(False, "BLOCKED_UNVERIFIED_PRICE", requested, stamp, None, None, "NONE")
    if requested == GENERIC_FREE_ROUTER:
        return OpenRouterFreeDecision(
            False,
            "BLOCKED_UNVERIFIED_PRICE",
            requested,
            stamp,
            None,
            None,
            "GENERIC_ROUTER_DOES_NOT_BIND_AN_EXACT_MODEL",
        )

    row = None
    if catalog is not None:
        for item in catalog:
            if isinstance(item, Mapping) and str(item.get("id") or "").strip() == requested:
                row = item
                break
    pricing = row.get("pricing") if isinstance(row, Mapping) and isinstance(row.get("pricing"), Mapping) else None
    prompt = _decimal(pricing.get("prompt")) if pricing is not None else None
    completion = _decimal(pricing.get("completion")) if pricing is not None else None

    # An exact :free ID is explicit provider-side free-route evidence. If the
    # current catalog contradicts it with non-zero prices, block.
    if requested.endswith(":free"):
        if prompt is not None and completion is not None and (prompt != 0 or completion != 0):
            return OpenRouterFreeDecision(
                False,
                "BLOCKED_PAID_OPENROUTER",
                requested,
                stamp,
                str(prompt),
                str(completion),
                "CATALOG_NONZERO_CONTRADICTION",
            )
        return OpenRouterFreeDecision(
            True,
            "FREE_CONFIRMED",
            requested,
            stamp,
            None if prompt is None else str(prompt),
            None if completion is None else str(completion),
            "EXPLICIT_EXACT_FREE_MODEL_ID" if row is None else "EXPLICIT_FREE_ID_PLUS_CATALOG",
        )

    if row is None or prompt is None or completion is None:
        return OpenRouterFreeDecision(
            False,
            "BLOCKED_UNVERIFIED_PRICE",
            requested,
            stamp,
            None if prompt is None else str(prompt),
            None if completion is None else str(completion),
            "CATALOG_MISSING_OR_PRICE_UNKNOWN",
        )
    if prompt == 0 and completion == 0:
        return OpenRouterFreeDecision(
            True,
            "FREE_CONFIRMED",
            requested,
            stamp,
            "0",
            "0",
            "CATALOG_ZERO_PRICE",
        )
    return OpenRouterFreeDecision(
        False,
        "BLOCKED_PAID_OPENROUTER",
        requested,
        stamp,
        str(prompt),
        str(completion),
        "CATALOG_NONZERO_PRICE",
    )


def assert_openrouter_free_model(
    model: str,
    *,
    catalog: Sequence[Mapping[str, Any]] | None = None,
    api_key: str = "",
    cache_path: Path | None = None,
    ttl_seconds: int = DEFAULT_CATALOG_TTL_SECONDS,
    now: float | None = None,
) -> dict[str, Any]:
    requested = str(model or "").strip()
    # Exact :free IDs can be admitted without a catalog round trip. Unknown
    # IDs require fresh/current catalog evidence.
    if catalog is None and requested.endswith(":free") and requested != GENERIC_FREE_ROUTER:
        decision = decide_openrouter_free_model(requested, None, verified_at=now)
    else:
        if catalog is None:
            rows, fetched_at, _ = load_openrouter_catalog(
                api_key=api_key,
                cache_path=cache_path,
                ttl_seconds=ttl_seconds,
                now=now,
            )
        else:
            rows = [dict(row) for row in catalog if isinstance(row, Mapping)]
            fetched_at = float(time.time() if now is None else now)
        decision = decide_openrouter_free_model(requested, rows, verified_at=fetched_at)
    if not decision.allowed:
        raise OpenRouterFreeGateError(decision.reason, decision.reason)
    return decision.to_dict()


__all__ = [
    "CATALOG_URL",
    "DEFAULT_CATALOG_TTL_SECONDS",
    "OpenRouterFreeDecision",
    "OpenRouterFreeGateError",
    "assert_openrouter_free_model",
    "decide_openrouter_free_model",
    "load_openrouter_catalog",
]
