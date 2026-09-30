import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from scripts import prepare_character_reaction_pack as pack


class PackReuseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.output = self.root / 'pack'
        for char, count in [('Zundamon', 95), ('Metan', 65)]:
            names = ['All.png', '口/0.png', '口/1.png', '口/2.png',
                     '目/0.png', '目/1.png', '眉/0.png', '眉/1.png']
            names += [f'other/{i}.png' for i in range(count-len(names))]
            for name in names:
                p = self.source / char / name
                p.parent.mkdir(parents=True, exist_ok=True)
                Image.new('RGBA', (8, 8), (100, 180, 200, 255)).save(p)

    def test_warm_hit_does_not_preprocess_again(self):
        self.assertEqual(pack.build_pack(self.source, self.output)['status'], 'BUILT')
        with patch.object(pack, '_normalize_png', side_effect=AssertionError('rebuilt')):
            self.assertEqual(pack.build_pack(self.source, self.output)['status'], 'CACHE_HIT')

    def test_missing_corrupt_or_modified_inventory_is_not_a_hit(self):
        for damage in ('missing', 'corrupt', 'inventory'):
            pack.build_pack(self.source, self.output)
            target = self.output / 'normalized/Zundamon/All.png'
            if damage == 'missing': target.unlink()
            elif damage == 'corrupt': target.write_bytes(b'broken')
            else: (self.output / 'inventory.json').write_text('{}')
            identity = pack._source_identity(self.source, None)
            self.assertIsNone(pack._existing_hit(self.output, identity))

    def test_receipt_cannot_hide_changed_extracted_source(self):
        receipt = self.root / 'receipt.json'
        receipt.write_text(json.dumps({'sha256':'unchanged'}))
        pack.build_pack(self.source, self.output, receipt)
        Image.new('RGBA', (8, 8), (240, 100, 100, 255)).save(self.source / 'Zundamon/All.png')
        self.assertEqual(pack.build_pack(self.source, self.output, receipt)['status'], 'BUILT')

    def test_failed_rebuild_preserves_existing_pack(self):
        pack.build_pack(self.source, self.output)
        previous = (self.output / 'pack_manifest.json').read_bytes()
        Image.new('RGBA', (8, 8), (240, 100, 100, 255)).save(self.source / 'Zundamon/All.png')
        with patch.object(pack, '_normalize_png', side_effect=RuntimeError('failed')):
            with self.assertRaises(RuntimeError): pack.build_pack(self.source, self.output)
        self.assertEqual((self.output / 'pack_manifest.json').read_bytes(), previous)

    def test_missing_required_mouth_category_blocks_pack(self):
        for p in (self.source / 'Metan/口').glob('*.png'):
            p.rename(self.source / 'Metan/other' / ('missing-mouth-'+p.name))
        with self.assertRaises(ValueError): pack.build_pack(self.source, self.output)

if __name__ == '__main__': unittest.main()
