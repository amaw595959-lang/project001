import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from resource_policy import disk_threshold_gib


class ResourcePolicyTests(unittest.TestCase):
    def test_standard_profile_preserves_twenty_gib(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(disk_threshold_gib("standard"), 20.0)

    def test_codespaces_profile_uses_provisional_ten_gib(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(disk_threshold_gib("codespaces"), 10.0)

    def test_codespaces_threshold_can_be_increased(self):
        with patch.dict(os.environ, {"P001_CODESPACES_MIN_FREE_DISK_GIB": "12.5"}):
            self.assertEqual(disk_threshold_gib("codespaces"), 12.5)

    def test_codespaces_threshold_rejects_unsafe_values(self):
        for value in ("9", "20.1", "not-a-number"):
            with self.subTest(value=value), patch.dict(
                os.environ, {"P001_CODESPACES_MIN_FREE_DISK_GIB": value}
            ):
                with self.assertRaises(ValueError):
                    disk_threshold_gib("codespaces")

    def test_unknown_profile_fails_closed(self):
        with self.assertRaises(ValueError):
            disk_threshold_gib("unknown")


if __name__ == "__main__":
    unittest.main()
