#!/usr/bin/env python3
"""Safe lazy compatibility probe for optional AI orchestration frameworks.

The registry never installs packages, calls a model/provider, uses the network,
spends money, or grants authority. It only checks whether an already-installed
framework exposes the public symbols expected by the AI Army adapter contract.
The resulting evidence is still insufficient to execute: the normal health gate
and verified FREE model route are required separately.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import json
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "framework_plugin_registry.json"
SCHEMA_VERSION = "framework-plugin-probe-v1"


class FrameworkPluginError(ValueError):
    pass


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def load_config(path: str | Path = DEFAULT_CONFIG) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != "framework-plugin-registry-v1":
        raise FrameworkPluginError("invalid framework plugin registry")
    policy = _mapping(value.get("policy"))
    for key in ("lazy_import_only", "package_install_forbidden", "network_probe_forbidden", "unknown_api_contract_blocks", "health_gate_required", "free_model_route_required", "native_fallback"):
        if policy.get(key) is not True:
            raise FrameworkPluginError(f"unsafe plugin registry policy: {key}")
    return value


def _distribution_version(name: str | None) -> str | None:
    if not name:
        return None
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def probe_plugin(
    plugin_id: str,
    *,
    config: Mapping[str, Any] | None = None,
    connector_present: bool = False,
    billing_safe_verified: bool = False,
) -> dict[str, Any]:
    cfg = dict(config or load_config())
    plugins = _mapping(cfg.get("plugins"))
    key = str(plugin_id or "").upper()
    row = _mapping(plugins.get(key))
    if not row:
        raise FrameworkPluginError(f"unknown plugin: {key}")

    if row.get("requires_external_connector_evidence") is True:
        blockers: list[str] = []
        if connector_present is not True:
            blockers.append("connector_present")
        if row.get("requires_billing_safe_evidence") is True and billing_safe_verified is not True:
            blockers.append("billing_safe_verified")
        return {
            "schema_version": SCHEMA_VERSION,
            "plugin_id": key,
            "installed": connector_present is True,
            "api_contract_verified": not blockers,
            "version": None,
            "stability": str(row.get("stability") or "UNKNOWN"),
            "execution_profile": str(row.get("execution_profile") or ""),
            "required_symbols_missing": [],
            "optional_symbols_missing": [],
            "blockers": blockers,
            "ready_for_health_shadow": not blockers,
            "network_called": False,
            "package_installed_by_probe": False,
        }

    distribution = str(row.get("distribution") or "")
    module_name = str(row.get("module") or "")
    blockers: list[str] = []
    spec = None
    try:
        spec = importlib.util.find_spec(module_name) if module_name else None
    except (ImportError, ModuleNotFoundError, ValueError):
        spec = None
    installed = spec is not None
    missing_required: list[str] = []
    missing_optional: list[str] = []
    module = None
    if not installed:
        blockers.append("framework_installed")
    else:
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:  # fail closed; never suppress into READY
            blockers.append(f"module_import:{type(exc).__name__}")

    if module is not None:
        for symbol in row.get("required_symbols", ()):
            name = str(symbol)
            if not hasattr(module, name):
                missing_required.append(name)
        for symbol in row.get("optional_symbols", ()):
            name = str(symbol)
            if not hasattr(module, name):
                missing_optional.append(name)
    elif installed:
        missing_required = [str(item) for item in row.get("required_symbols", ())]

    if missing_required:
        blockers.append("api_contract_required_symbols")

    return {
        "schema_version": SCHEMA_VERSION,
        "plugin_id": key,
        "installed": installed,
        "api_contract_verified": installed and not missing_required and module is not None,
        "version": _distribution_version(distribution),
        "stability": str(row.get("stability") or "UNKNOWN"),
        "execution_profile": str(row.get("execution_profile") or ""),
        "required_symbols_missing": missing_required,
        "optional_symbols_missing": missing_optional,
        "blockers": sorted(set(blockers)),
        "ready_for_health_shadow": installed and not blockers,
        "network_called": False,
        "package_installed_by_probe": False,
    }


def probe_all(
    *,
    config: Mapping[str, Any] | None = None,
    connector_evidence: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    cfg = dict(config or load_config())
    evidence = connector_evidence if isinstance(connector_evidence, Mapping) else {}
    results: dict[str, Any] = {}
    for plugin_id in _mapping(cfg.get("plugins")):
        row = _mapping(evidence.get(plugin_id))
        results[str(plugin_id)] = probe_plugin(
            str(plugin_id),
            config=cfg,
            connector_present=row.get("connector_present") is True,
            billing_safe_verified=row.get("billing_safe_verified") is True,
        )
    return {
        "schema_version": "framework-plugin-probe-batch-v1",
        "results": results,
        "authority": {
            "provider_call": False,
            "network_call": False,
            "package_install": False,
            "payment": False,
            "repository_write": False,
            "secret_mutation": False,
            "deploy": False,
            "publish": False,
            "merge": False,
        },
    }


def build_adapter_evidence(
    plugin_report: Mapping[str, Any],
    *,
    runtime_present: bool,
    model_route_free_verified: bool,
    framework_health_ready: bool,
    benchmark_quality: float | None = None,
) -> dict[str, Any]:
    """Produce only the compatibility portion of adapter-layer evidence."""
    return {
        "framework_installed": plugin_report.get("installed") is True,
        "runtime_present": runtime_present is True,
        "model_route_free_verified": model_route_free_verified is True,
        "framework_health_ready": framework_health_ready is True,
        "api_contract_verified": plugin_report.get("api_contract_verified") is True,
        "benchmark_quality": benchmark_quality,
        "connector_present": plugin_report.get("installed") is True,
        "paid": False,
        "paid_fallback_enabled": False,
    }


__all__ = [
    "FrameworkPluginError",
    "build_adapter_evidence",
    "load_config",
    "probe_all",
    "probe_plugin",
]
