from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from scripts.build_character_full_face_fixture import build_fixture


class CharacterFullFaceFixtureTests(unittest.TestCase):
    def _png(self, path: Path, *, size=(120, 160), box=(30, 40, 90, 150), fill=(80, 180, 120, 255)) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        im = Image.new("RGBA", size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(im)
        draw.rectangle(box, fill=fill)
        im.save(path)

    def _pack(self, root: Path, *, bad_mouth_size: bool = False) -> Path:
        pack = root / "pack"
        characters = {}
        for character in ("Zundamon", "Metan"):
            categories = {"full_body": [], "mouth": [], "eyes": [], "brows": []}
            base = pack / "normalized" / character / "All.png"
            self._png(base)
            categories["full_body"].append({"normalized_path": str(base.relative_to(pack))})
            for index in range(3):
                path = pack / "normalized" / character / "口" / f"mouth_{index}.png"
                size = (80, 100) if bad_mouth_size and character == "Zundamon" and index == 0 else (120, 160)
                self._png(path, size=size, box=(52, 92, min(75, size[0]-1), min(112, size[1]-1)), fill=(220, 80, 100, 210))
                categories["mouth"].append({"normalized_path": str(path.relative_to(pack))})
            for category, folder, count in (("eyes", "目", 2), ("brows", "眉", 2)):
                for index in range(count):
                    path = pack / "normalized" / character / folder / f"{category}_{index}.png"
                    self._png(path, box=(44, 60 + index * 3, 78, 70 + index * 3), fill=(40, 40, 40, 180))
                    categories[category].append({"normalized_path": str(path.relative_to(pack))})
            characters[character] = {"png_count": 8, "categories": categories}
        pack.mkdir(parents=True, exist_ok=True)
        (pack / "inventory.json").write_text(
            json.dumps({
                "schema": "zm-reaction-pack-inventory-v1",
                "pack_revision": "test",
                "characters": characters,
                "generated_symbols": [],
            }),
            encoding="utf-8",
        )
        return pack

    def test_builds_full_face_contact_sheet_for_both_characters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = self._pack(root)
            out = root / "fixture"
            result = build_fixture(pack, out)
            self.assertEqual(result["gate"], "PASS")
            self.assertTrue((out / "full_face_mouth_contact_sheet.png").is_file())
            self.assertTrue((out / "full_face_fixture.json").is_file())
            for character in ("Zundamon", "Metan"):
                row = result["characters"][character]
                self.assertEqual(row["mouth_variants_checked"], 3)
                self.assertEqual(row["base_canvas"], [120, 160])

    def test_rejects_overlay_canvas_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = self._pack(root, bad_mouth_size=True)
            with self.assertRaisesRegex(ValueError, "overlay canvas mismatch"):
                build_fixture(pack, root / "fixture")

    def test_rejects_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = self._pack(root)
            data = json.loads((pack / "inventory.json").read_text(encoding="utf-8"))
            data["characters"]["Zundamon"]["mouth"][0:0] = []
            data["characters"]["Zundamon"]["categories"]["mouth"][0]["normalized_path"] = "../outside.png"
            (root / "outside.png").write_bytes(b"not-used")
            (pack / "inventory.json").write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "escapes pack root"):
                build_fixture(pack, root / "fixture")


if __name__ == "__main__":
    unittest.main()
