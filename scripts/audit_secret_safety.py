#!/usr/bin/env python3
"""Detect high-confidence secret literals without printing their values.

This is a static safety gate. Environment references, documented secret names,
and ordinary code-level variable references are allowed; literal credentials,
direct secret output, and secret-like plain-text bindings are reported only by
path, line, and classification.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
from typing import Iterable


SECRET_NAME = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|password|private[_-]?key|authorization|bearer|secret)"
)
SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(?:api[_-]?key|access[_-]?token|password|private[_-]?key|authorization|bearer|secret)"
    r"\b\s*[:=]\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s,;})\]]+))"
)
SECRET_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"[Bb]earer\s+[A-Za-z0-9._~+/=-]{12,}"
    r"|(?:sk|gsk|ghp|github_pat|sk-or-v1)[_-][A-Za-z0-9._~+/=-]{12,}"
    r"|hf_[A-Za-z0-9]{12,}"
    r")"
)
SECRET_OUTPUT = re.compile(r"(?i)\b(?:echo|printf|print|console\.(?:log|error))\b.*\$\{\{\s*secrets\.")
PLAIN_TEXT = re.compile(r"(?i)[\"'](?:type|kind)[\"']\s*:\s*[\"']plain[_-]?text[\"']")
IDENTIFIER_REFERENCE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
CODE_EXPRESSION_REFERENCE = re.compile(
    r"^(?:str|bool|bytes|os\.getenv|os\.environ\.get|[A-Za-z_][A-Za-z0-9_]*\.get)\("
)
PLACEHOLDER_WORDS = frozenset({"example", "dummy", "placeholder", "sample", "test", "redacted", "replaced"})


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    kind: str


def _is_placeholder(value: str) -> bool:
    stripped = value.strip()
    lowered = stripped.lower()
    if not lowered or lowered.startswith(("${", "$(", "$", "<", "os.", "process.", "env.", "secrets.")):
        return True
    if lowered in {"none", "null", "true", "false", "re.compile", "https://"}:
        return True
    if any(word in lowered for word in PLACEHOLDER_WORDS):
        return True
    if re.fullmatch(r"[A-Z][A-Z0-9_]{5,}", stripped):
        return True
    if IDENTIFIER_REFERENCE.fullmatch(stripped) and not SECRET_TOKEN.search(stripped):
        return True
    # Assignment scanners also encounter normal expressions such as
    # ``api_key = str(secret_map.get(name) or "")``. Treat a bounded set of
    # ordinary lookup/cast expressions as references, while credential-shaped
    # token literals remain independently caught by SECRET_TOKEN.
    if CODE_EXPRESSION_REFERENCE.match(stripped) and not SECRET_TOKEN.search(stripped):
        return True
    if "abcdefghijklmnop" in lowered or "0123456789" in lowered:
        return True
    return False


def _scan_text(path: Path, relative_path: str, text: str) -> list[Finding]:
    findings: list[Finding] = []
    lines = text.splitlines()
    has_secret_plain_binding = bool(PLAIN_TEXT.search(text) and SECRET_NAME.search(text))
    for number, line in enumerate(lines, 1):
        if SECRET_OUTPUT.search(line):
            findings.append(Finding(relative_path, number, "secret_output"))
        if has_secret_plain_binding and re.search(r"(?i)[\"'](?:value|text)[\"']\s*:\s*[\"'](?!\$|<)[^\"']{8,}[\"']", line):
            findings.append(Finding(relative_path, number, "plaintext_secret_binding"))
        for match in SECRET_ASSIGNMENT.finditer(line):
            value = next((item for item in match.groups() if item is not None), "")
            if not _is_placeholder(value) and (len(value) >= 12 or SECRET_TOKEN.search(value)):
                findings.append(Finding(relative_path, number, "secret_literal"))
        if SECRET_TOKEN.search(line):
            token = SECRET_TOKEN.search(line)
            value = token.group(0) if token else ""
            if not _is_placeholder(value):
                findings.append(Finding(relative_path, number, "secret_token"))
    return findings


def scan_paths(root: Path, paths: Iterable[Path] | None = None) -> list[Finding]:
    selected = paths if paths is not None else root.rglob("*")
    findings: list[Finding] = []
    for path in selected:
        if not path.is_file() or ".git" in path.parts or path.name == "audit_secret_safety.py":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        findings.extend(_scan_text(path, str(path.relative_to(root)), text))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    root = args.root.resolve()
    findings = scan_paths(root)
    for finding in findings:
        print(
            f"SECRET_CANDIDATE_FOUND=true path={finding.path} "
            f"line={finding.line} type={finding.kind} rotation_recommended=true"
        )
    print(f"SECRET_NEW_LEAKS={len(findings)}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
