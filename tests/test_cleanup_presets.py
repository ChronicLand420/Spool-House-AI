from __future__ import annotations

import unittest

from scripts.run_quality_matrix import PRESETS
from spool_house_ai.config import normalize_cleanup_preset
from spool_house_ai.gui import VISIBLE_CLEANUP_PRESETS


class CleanupPresetListTests(unittest.TestCase):
    def test_gui_visible_preset_list_uses_canonical_clean_logo(self) -> None:
        labels = [label for label, _value in VISIBLE_CLEANUP_PRESETS]
        values = [value for _label, value in VISIBLE_CLEANUP_PRESETS]

        self.assertEqual(
            labels,
            [
                "Default",
                "Clean Logo",
                "Detail Preserving",
                "Drip / Graffiti",
                "Splatter / Rough",
                "Line Art",
            ],
        )
        self.assertIn("clean_logo", values)
        self.assertIn("line_art", values)
        self.assertNotIn("logo_clean", values)

    def test_quality_matrix_excludes_legacy_logo_clean_alias(self) -> None:
        self.assertEqual(
            PRESETS,
            ("default", "clean_logo", "detail_preserving", "drip_logo", "splatter_logo", "line_art"),
        )
        self.assertNotIn("logo_clean", PRESETS)

    def test_cleanup_preset_normalization_keeps_legacy_alias_safe(self) -> None:
        self.assertEqual(normalize_cleanup_preset("logo_clean"), "clean_logo")
        self.assertEqual(normalize_cleanup_preset("Logo Clean"), "clean_logo")
        self.assertEqual(normalize_cleanup_preset("Line Art"), "line_art")
        self.assertEqual(normalize_cleanup_preset("sneaker"), "line_art")
        self.assertEqual(normalize_cleanup_preset("not-a-real-preset"), "default")


if __name__ == "__main__":
    unittest.main()
