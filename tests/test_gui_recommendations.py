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


if __name__ == "__main__":
    unittest.main()
