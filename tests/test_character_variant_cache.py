from pathlib import Path
import hashlib
import tempfile
import unittest
from unittest import mock
from PIL import Image

from scripts import render_reusable_short as renderer


class CharacterVariantCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.character = self.root / "Zundamon"
        self.cache = self.root / "cache"
        fixed, mouths, expressions = renderer.character_layers("Zundamon")
        for name in dict.fromkeys(fixed + mouths + sum(expressions.values(), [])):
            path = self.character / name
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGBA", (64, 64), (40, 120, 60, 128)).save(path)

    def test_warm_cache_avoids_all_layer_composition(self):
        expected, key = renderer.character_variants(self.character, "Zundamon", self.cache)
        fixed, mouths, expressions = renderer.character_layers("Zundamon")
        paths = sorted(set(fixed + mouths + sum(expressions.values(), [])))
        legacy_key = hashlib.sha256(b'character-native-v1' + b''.join(
            name.encode() + (self.character / name).read_bytes() for name in paths)).hexdigest()
        self.assertEqual(key, legacy_key, "existing successful caches must remain compatible")
        with mock.patch.object(renderer, "layer", side_effect=AssertionError("no repeated composition")):
            actual, restored_key = renderer.character_variants(self.character, "Zundamon", self.cache)
        self.assertEqual(key, restored_key)
        self.assertEqual(len(actual), 9)
        for variant in actual:
            self.assertEqual(actual[variant].tobytes(), expected[variant].tobytes())

    def test_one_corrupt_variant_is_repaired_without_replacing_other_variants(self):
        _, key = renderer.character_variants(self.character, "Zundamon", self.cache)
        folder = self.cache / "character-cache" / key
        healthy = folder / "HAPPY-1.png"
        before = healthy.stat().st_mtime_ns, healthy.read_bytes()
        corrupt = folder / "NORMAL-0.png"
        corrupt.write_bytes(b"partial PNG")
        actual, _ = renderer.character_variants(self.character, "Zundamon", self.cache)
        self.assertEqual(actual["NORMAL", 0].size, (64, 64))
        self.assertEqual((healthy.stat().st_mtime_ns, healthy.read_bytes()), before)
        self.assertEqual(list(folder.glob(".character-*")), [])

    def test_wrong_sized_cached_image_is_rebuilt(self):
        _, key = renderer.character_variants(self.character, "Zundamon", self.cache)
        path = self.cache / "character-cache" / key / "NORMAL-0.png"
        Image.new("RGBA", (8, 8)).save(path)
        actual, _ = renderer.character_variants(self.character, "Zundamon", self.cache)
        self.assertEqual(actual["NORMAL", 0].size, (64, 64))

    def test_interrupted_png_save_leaves_existing_file_untouched(self):
        path = self.root / "prior.png"
        path.write_bytes(b"prior successful bytes")
        with mock.patch.object(renderer.os, "replace", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                renderer._atomic_character_png(path, Image.new("RGBA", (64, 64)))
        self.assertEqual(path.read_bytes(), b"prior successful bytes")
        self.assertEqual(list(self.root.glob(".character-*")), [])


if __name__ == "__main__":
    unittest.main()
