#!/usr/bin/env python3
"""Resolve the real Zundamon/Metan shell root inside an extracted asset tree.

The registered shell archive may contain one or more wrapper directories.  This
resolver accepts the extraction directory, searches only local directories, and
returns a root only when exactly one directory directly contains both required
character directories.  Missing or ambiguous layouts fail closed.
"""
from __future__ import annotations

import argparse
from pathlib import Path

CHARACTERS = ("Zundamon", "Metan")
DEFAULT_MAX_SCAN_DIRS = 4096


def _is_character_root(path: Path) -> bool:
    return all((path / character).is_dir() for character in CHARACTERS)


def resolve_character_shell_root(
    shell_root: Path,
    *,
    max_scan_dirs: int = DEFAULT_MAX_SCAN_DIRS,
) -> Path:
    """Return the unique directory directly containing both character folders.

    Symlinked directories are not traversed, which keeps resolution within the
    materialized extraction tree and prevents cycles.  The scan is bounded so a
    malformed archive cannot cause unbounded directory traversal.
    """
    root = shell_root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"shell root not found: {root}")
    if max_scan_dirs < 1:
        raise ValueError("max_scan_dirs must be positive")

    if _is_character_root(root):
        return root

    candidates: list[Path] = []
    stack = [root]
    scanned = 0

    while stack:
        current = stack.pop()
        scanned += 1
        if scanned > max_scan_dirs:
            raise RuntimeError(
                f"character shell scan exceeded bounded directory limit: {max_scan_dirs}"
            )

        try:
            children = sorted(
                (
                    child
                    for child in current.iterdir()
                    if child.is_dir() and not child.is_symlink()
                ),
                key=lambda path: path.name,
                reverse=True,
            )
        except OSError as exc:
            raise RuntimeError(f"failed to inspect extracted shell tree: {current}") from exc

        for child in children:
            if _is_character_root(child):
                candidates.append(child.resolve())
            stack.append(child)

    unique = sorted({candidate for candidate in candidates}, key=lambda path: path.as_posix())
    if not unique:
        required = ", ".join(CHARACTERS)
        raise FileNotFoundError(
            f"no character shell root contains all required directories ({required}) under {root}"
        )
    if len(unique) != 1:
        relative = [
            candidate.relative_to(root).as_posix()
            if candidate.is_relative_to(root)
            else candidate.as_posix()
            for candidate in unique
        ]
        raise RuntimeError(
            "ambiguous character shell roots; refusing to guess: " + ", ".join(relative)
        )
    return unique[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("shell_root", type=Path)
    parser.add_argument("--max-scan-dirs", type=int, default=DEFAULT_MAX_SCAN_DIRS)
    args = parser.parse_args()
    print(resolve_character_shell_root(args.shell_root, max_scan_dirs=args.max_scan_dirs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
