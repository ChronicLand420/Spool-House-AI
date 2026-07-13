from __future__ import annotations

import unittest

from dataclasses import replace
from pathlib import Path

from scripts.run_quality_matrix import PRESETS
from spool_house_ai.config import apply_cleanup_preset, load_config, normalize_cleanup_preset
from spool_house_ai.gui import ACCENT_COLOR_OPTIONS, VISIBLE_CLEANUP_PRESETS, VISIBLE_PRODUCT_MODES


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
                "Preserve Floating Islands",
            ],
        )
        self.assertIn("clean_logo", values)
        self.assertIn("line_art", values)
        self.assertIn("preserve_floating_islands", values)
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
        self.assertEqual(normalize_cleanup_preset("Preserve Floating Islands"), "preserve_floating_islands")
        self.assertEqual(normalize_cleanup_preset("not-a-real-preset"), "default")

    def test_preserve_floating_islands_preset_uses_supported_fields(self) -> None:
        config = load_config(Path("config/config.yaml")).silhouette
        preset = apply_cleanup_preset(replace(config, cleanup_preset="preserve_floating_islands"))

        self.assertEqual(preset.cleanup_preset, "preserve_floating_islands")
        self.assertFalse(preset.remove_small_islands)
        self.assertEqual(preset.min_island_area_px, 0)
        self.assertEqual(preset.min_contour_area, 0)
        self.assertEqual(preset.simplify_tolerance, 0)
        self.assertTrue(preset.preserve_internal_details)
        self.assertFalse(preset.contour_smoothing_enabled)
        self.assertFalse(preset.straight_line_cleanup_enabled)
        self.assertFalse(preset.curve_fit_enabled)

    def test_orange_accent_uses_spool_house_label_with_legacy_value(self) -> None:
        self.assertIn(("Spool House Orange", "orange"), ACCENT_COLOR_OPTIONS)
        labels = [label for label, _value in ACCENT_COLOR_OPTIONS]
        self.assertNotIn("Fire Orange", labels)

    def test_filament_swap_relief_is_visible_product_mode(self) -> None:
        self.assertIn(("Filament Swap Relief", "filament_swap_relief"), VISIBLE_PRODUCT_MODES)
        labels = [label for label, _value in VISIBLE_PRODUCT_MODES]
        self.assertIn("Line Art", [label for label, _value in VISIBLE_CLEANUP_PRESETS])
        self.assertNotIn("Logo Clean", labels)


if __name__ == "__main__":
    unittest.main()
