from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from PIL import Image, ImageDraw
from PySide6.QtWidgets import QApplication, QPushButton

from spool_house_ai.gui import MainWindow


REPO_ROOT = Path(__file__).resolve().parents[1]


def _save_gui_recommendation_fixture(path: Path) -> None:
    image = Image.new("RGB", (220, 180), "white")
    draw = ImageDraw.Draw(image)
    for x in range(20, 200, 14):
        draw.line((x, 20, x, 160), fill="black", width=1)
    for y in range(25, 160, 14):
        draw.line((20, y, 200, y), fill="black", width=1)
    draw.ellipse((74, 48, 146, 120), outline="black", width=2)
    image.save(path)


class GuiRecommendationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_recommendation_panel_exists_and_generic_slicer_button_is_removed(self) -> None:
        window = MainWindow()
        try:
            button_texts = {button.text() for button in window.findChildren(QPushButton)}
            self.assertNotIn("Open in Slicer", button_texts)
            self.assertIn("Open STL", button_texts)
            self.assertIn("Open 3MF", button_texts)
            self.assertTrue(hasattr(window, "recommendation_summary"))
            self.assertTrue(hasattr(window, "apply_recommendation_button"))
        finally:
            window.close()

    def test_apply_recommendation_updates_preset_and_finished_thickness(self) -> None:
        window = MainWindow()
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                image_path = Path(temp_dir) / "gui_recommendation_fixture.png"
                _save_gui_recommendation_fixture(image_path)
                window.ui_preferences = replace(window.ui_preferences, use_last_selected_preset=False)
                window.add_files([image_path])
                recommendation = window.current_recommendation
                self.assertIsNotNone(recommendation)
                self.assertTrue(recommendation.available)
                window.apply_artwork_recommendation()
                self.assertEqual(window._combo_value(window.cleanup_preset), recommendation.recommended_preset)
                self.assertGreaterEqual(window._current_finished_thickness_mm(), 2.0)

                default_index = window.cleanup_preset.findData("default")
                if recommendation.recommended_preset != "default":
                    window.cleanup_preset.setCurrentIndex(default_index)
                    self.assertFalse(window._recommendation_matches_current(recommendation))
        finally:
            window.close()

    def test_filament_relief_quality_and_solid_base_controls_feed_config(self) -> None:
        window = MainWindow()
        try:
            product_index = window.product_mode.findData("filament_swap_relief")
            self.assertGreaterEqual(product_index, 0)
            window.product_mode.setCurrentIndex(product_index)

            self.assertTrue(hasattr(window, "filament_detail_quality"))
            self.assertTrue(hasattr(window, "filament_solid_base"))
            self.assertEqual(window._combo_value(window.filament_detail_quality), "700000")

            ultra_index = window.filament_detail_quality.findData("1600000")
            self.assertGreaterEqual(ultra_index, 0)
            window.filament_detail_quality.setCurrentIndex(ultra_index)
            window.filament_solid_base.setChecked(True)

            config = window._config_from_controls()
            self.assertEqual(config.filament_swap_relief.max_sampled_pixels, 1600000)
            self.assertTrue(config.filament_swap_relief.solid_base_enabled)
        finally:
            window.close()

    def test_printability_controls_feed_shared_config(self) -> None:
        window = MainWindow()
        try:
            self.assertTrue(hasattr(window, "printability_enabled"))
            self.assertTrue(hasattr(window, "printability_min_feature_width"))
            window.printability_enabled.setChecked(False)
            window.printability_min_feature_width.setValue(1.2)
            window.printability_min_segment_length.setValue(2.4)
            window.printability_min_island_area.setValue(3.6)
            window.printability_min_connection_width.setValue(1.0)
            window.printability_max_mergeable_gap.setValue(0.4)
            window.printability_min_hole_area.setValue(1.8)
            window.printability_min_component_dimension.setValue(0.9)

            config = window._config_from_controls()

            self.assertFalse(config.printability.enforce_minimum_printable_geometry)
            self.assertAlmostEqual(config.printability.minimum_feature_width_mm, 1.2)
            self.assertAlmostEqual(config.printability.minimum_segment_length_mm, 2.4)
            self.assertAlmostEqual(config.printability.minimum_island_area_mm2, 3.6)
            self.assertAlmostEqual(config.printability.minimum_connection_width_mm, 1.0)
            self.assertAlmostEqual(config.printability.maximum_mergeable_gap_mm, 0.4)
            self.assertAlmostEqual(config.printability.minimum_hole_area_mm2, 1.8)
            self.assertAlmostEqual(config.printability.minimum_component_dimension_mm, 0.9)
            self.assertEqual(config.stl.printability, config.printability)
            self.assertEqual(config.filament_swap_relief.printability, config.printability)
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
