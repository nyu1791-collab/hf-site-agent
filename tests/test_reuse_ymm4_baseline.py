import json
import tempfile
import unittest
from pathlib import Path
from scripts.reuse_ymm4_baseline import duplicate, digest


class BaselineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root/'base.ymmp'
        self.base.write_text('{"fixture":true}')
        self.asset = self.root/'expression.png'
        self.asset.write_bytes(b'fixture asset')
        self.receipt = self.root/'receipt.json'
        self.data = dict(baseline_sha256=digest(self.base), reviewer='fixture', verified_at='2026-09-30',
                         windows_preview_verified=True, mouth_sync_verified=True,
                         semantic_expressions_verified=True, caption_phrase_color_verified=True,
                         assets=[dict(path='expression.png', sha256=digest(self.asset))])
        self.receipt.write_text(json.dumps(self.data))
        self.dest = self.root/'episode.ymmp'

    def test_copy_and_reuse_preserve_baseline(self):
        self.assertEqual(duplicate(self.base,self.receipt,self.dest)['status'],'COPIED')
        self.assertEqual(duplicate(self.base,self.receipt,self.dest)['status'],'REUSED')
        self.assertEqual(self.base.read_bytes(),self.dest.read_bytes())

    def test_existing_edited_episode_is_never_overwritten(self):
        self.dest.write_text('edited')
        with self.assertRaises(FileExistsError):duplicate(self.base,self.receipt,self.dest)
        self.assertEqual(self.dest.read_text(),'edited')

    def test_changed_assets_or_unverified_template_block_copy(self):
        self.asset.write_bytes(b'changed')
        with self.assertRaises(ValueError):duplicate(self.base,self.receipt,self.dest)
        self.data['windows_preview_verified']=False
        self.receipt.write_text(json.dumps(self.data))
        with self.assertRaises(ValueError):duplicate(self.base,self.receipt,self.dest)
        self.assertFalse(self.dest.exists())

if __name__ == '__main__':unittest.main()
