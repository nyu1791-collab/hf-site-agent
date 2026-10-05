#!/usr/bin/env python3
"""Machine-readable provider/fallback policy lookup.

This module does not call providers. It centralizes whether a task has any
approved cross-provider fallback. Unknown tasks fail closed.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

ROOT=Path(__file__).resolve().parents[1]
DEFAULT_MATRIX=ROOT/"config/ai_army_provider_route_matrix.json"
BLOCKED="BLOCKED_NO_APPROVED_FALLBACK"


def load_matrix(path:Path=DEFAULT_MATRIX)->dict[str,Any]:
    value=json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value,dict):
        raise ValueError("provider route matrix must be an object")
    return value


def route_for_task(task_type:str,matrix:Mapping[str,Any]|None=None)->dict[str,Any]:
    active=dict(matrix or load_matrix())
    key=str(task_type or "").strip().upper()
    for row in active.get("task_routes",[]) if isinstance(active.get("task_routes"),list) else []:
        if isinstance(row,Mapping) and str(row.get("task_type") or "").upper()==key:
            return dict(row)
    return {
        "task_type":key,
        "status":"BLOCKED",
        "reason":str(active.get("default_unmatched_action") or BLOCKED),
        "fallback_provider":None,
        "fallback_model":None,
        "execution_allowed":False,
    }


def approved_fallback(task_type:str,matrix:Mapping[str,Any]|None=None)->dict[str,Any]:
    route=route_for_task(task_type,matrix)
    if route.get("status")=="BLOCKED":
        return route
    provider=route.get("fallback_provider")
    if provider is None:
        return {
            "task_type":route.get("task_type"),
            "status":"BLOCKED",
            "reason":route.get("no_fallback_action") or BLOCKED,
            "fallback_provider":None,
            "fallback_model":None,
            "execution_allowed":False,
        }
    if str(provider).lower()=="openrouter":
        return {
            "task_type":route.get("task_type"),
            "status":"BLOCKED",
            "reason":"BLOCKED_PAID_OPENROUTER",
            "fallback_provider":None,
            "fallback_model":None,
            "execution_allowed":False,
        }
    return {
        "task_type":route.get("task_type"),
        "status":"READY",
        "reason":"APPROVED_FALLBACK",
        "fallback_provider":provider,
        "fallback_model":route.get("fallback_model"),
        "execution_allowed":True,
    }


__all__=["BLOCKED","approved_fallback","load_matrix","route_for_task"]
