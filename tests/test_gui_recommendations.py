from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path

from PySide6.QtWidgets import QApplication, QPushButton

from spool_house_ai.gui import MainWindow


REPO_ROOT = Path(__file__).resolve().parents[1]


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
            window.ui_preferences = replace(window.ui_preferences, use_last_selected_preset=False)
            window.add_files([REPO_ROOT / "input" / "Tanjiro.jpg"])
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
