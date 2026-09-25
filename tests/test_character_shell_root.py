from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.character_shell_root import resolve_character_shell_root


class CharacterShellRootResolverTests(unittest.TestCase):
    def _mkdir_character_root(self, root: Path) -> None:
        for name in ("Zundamon", "Metan"):
            (root / name).mkdir(parents=True, exist_ok=True)

    def test_direct_root_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._mkdir_character_root(root)
            self.assertEqual(resolve_character_shell_root(root), root.resolve())

    def test_nested_wrapper_directories_are_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            actual = root / "archive-wrapper" / "shell-assets"
            self._mkdir_character_root(actual)
            self.assertEqual(resolve_character_shell_root(root), actual.resolve())

    def test_missing_character_pair_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "wrapper" / "Zundamon").mkdir(parents=True)
            with self.assertRaises(FileNotFoundError):
                resolve_character_shell_root(root)

    def test_multiple_candidate_roots_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._mkdir_character_root(root / "a")
            self._mkdir_character_root(root / "b")
            with self.assertRaisesRegex(RuntimeError, "ambiguous character shell roots"):
                resolve_character_shell_root(root)

    def test_scan_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a" / "b").mkdir(parents=True)
            with self.assertRaisesRegex(RuntimeError, "bounded directory limit"):
                resolve_character_shell_root(root, max_scan_dirs=1)


if __name__ == "__main__":
    unittest.main()
