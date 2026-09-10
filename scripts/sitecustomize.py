"""Repository-local Python startup bootstrap for direct script execution.

GitHub Actions invokes several modules as ``python scripts/name.py``. In that
mode Python places ``scripts/`` on ``sys.path`` but may omit the repository
root, so absolute imports such as ``from scripts.foo import ...`` fail before
any application recovery logic can run. Python imports ``sitecustomize`` during
startup when it is available on the script path; add only the repository root.

This performs no network, credential, provider, or repository mutation.
"""
from __future__ import annotations

from pathlib import Path
import sys

_REPOSITORY_ROOT = str(Path(__file__).resolve().parents[1])
if _REPOSITORY_ROOT not in sys.path:
    sys.path.insert(0, _REPOSITORY_ROOT)
