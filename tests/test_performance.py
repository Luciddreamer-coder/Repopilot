import tempfile
import unittest
from pathlib import Path

from app.scanner import scan_repository


class RepoPilotScanCacheTests(unittest.TestCase):
    def test_scan_cache_reuses_unchanged_state_and_invalidates_after_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "main.py"
            source.write_text("value = 1\n", encoding="utf-8")

            first = scan_repository(root)
            second = scan_repository(root)
            self.assertEqual(first.to_dict(), second.to_dict())

            source.write_text("value = 1\nvalue += 2\n", encoding="utf-8")
            third = scan_repository(root)
            self.assertEqual(third.files[0].lines, 2)
            self.assertNotEqual(first.files[0].lines, third.files[0].lines)


if __name__ == "__main__":
    unittest.main()
