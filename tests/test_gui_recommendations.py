from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from PIL import Image, ImageDraw
from PySide6.QtWidgets import QApplication, QCheckBox, QLabel, QPushButton

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

    def test_customer_review_surface_hides_developer_pipeline_by_default(self) -> None:
        window = MainWindow()
        try:
            label_texts = {label.text() for label in window.findChildren(QLabel)}
            self.assertIn("Review & Export", label_texts)
            self.assertIn("Artwork Queue", label_texts)
            self.assertIn("Artwork Preview", label_texts)
            self.assertIn("Print-Ready Summary", label_texts)
            self.assertNotIn("Status Log", label_texts)
            self.assertEqual(window.main_splitter.count(), 2)
            self.assertFalse(hasattr(window, "left_scroll"))
            self.assertTrue(hasattr(window, "developer_view_toggle"))
            self.assertTrue(window.developer_pipeline_widget.isHidden())
            self.assertTrue(window.review_details_section.body.isHidden())
            self.assertTrue(window.path_tools_section.body.isHidden())
            button_texts = {button.text() for button in window.findChildren(QPushButton)}
            self.assertIn("Generate Selected", button_texts)
            self.assertIn("Generate Queue", button_texts)
            self.assertIn("Open Job Folder", button_texts)
            self.assertNotIn("Show Log", button_texts)

            window.developer_view_toggle.setChecked(True)
            self.assertFalse(window.developer_pipeline_widget.isHidden())
        finally:
            window.close()

    def test_status_log_is_available_from_settings_not_bottom_bar(self) -> None:
        window = MainWindow()
        try:
            self.assertFalse(hasattr(window, "log_toggle_button"))
            self.assertFalse(hasattr(window, "log_panel"))
            window.logs.append("diagnostic message")
            window.open_settings()
            self.assertIsNotNone(window.settings_dialog)
            settings_buttons = {button.text() for button in window.settings_dialog.findChildren(QPushButton)}
            settings_labels = {label.text() for label in window.settings_dialog.findChildren(QLabel)}
            self.assertIn("Open Status Log", settings_buttons)
            self.assertNotIn("Startup log", settings_labels)

            window.settings_dialog.open_status_log_button.click()
            self.assertIsNotNone(window.status_log_dialog)
            self.assertIsNotNone(window.status_log_view)
            self.assertIn("diagnostic message", window.status_log_view.toPlainText())
        finally:
            if window.settings_dialog is not None:
                window.settings_dialog.close()
            if window.status_log_dialog is not None:
                window.status_log_dialog.close()
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
            self.assertTrue(hasattr(window, "filament_quick_group"))
            self.assertTrue(hasattr(window, "filament_solid_base"))
            self.assertTrue(hasattr(window, "filament_orca_project_3mf"))
            self.assertFalse(window.filament_quick_group.isHidden())
            self.assertEqual(window._combo_value(window.filament_detail_quality), "700000")

            ultra_index = window.filament_detail_quality.findData("1600000")
            self.assertGreaterEqual(ultra_index, 0)
            window.filament_detail_quality.setCurrentIndex(ultra_index)
            window.filament_solid_base.setChecked(True)
            window._refresh_filament_color_plan_estimate()

            config = window._config_from_controls()
            self.assertEqual(config.filament_swap_relief.max_sampled_pixels, 1600000)
            self.assertTrue(config.filament_swap_relief.solid_base_enabled)
            self.assertTrue(config.filament_swap_relief.export_orca_project_3mf)
            self.assertEqual(window.filament_plan_table.item(0, 0).text(), "Base")
            self.assertEqual(window.filament_plan_table.item(0, 5).text(), "1")
            self.assertEqual(window.filament_plan_table.item(0, 6).text(), "10")
            self.assertEqual(window.filament_plan_table.item(0, 8).text(), "10")
            self.assertEqual(window.filament_plan_table.item(1, 5).text(), "11")
            self.assertEqual(window.filament_plan_table.item(1, 6).text(), "14")
            self.assertEqual(window.filament_plan_table.item(1, 7).text(), "11")
            self.assertEqual(window.filament_plan_table.item(1, 8).text(), "4")
        finally:
            window.close()

    def test_print_safe_cleanup_uses_customer_friendly_labels(self) -> None:
        window = MainWindow()
        try:
            buttons = window.findChildren(QPushButton)
            checkboxes = window.findChildren(QCheckBox)
            visible_text = " ".join(child.text() for child in [*buttons, *checkboxes])
            self.assertIn("Print-safe cleanup", visible_text)
            self.assertIn("Use printer/nozzle defaults", visible_text)
            self.assertIn("Use Printer Defaults", visible_text)
            self.assertNotIn("Enforce minimum printable geometry", visible_text)
            self.assertNotIn("preserve_holes", visible_text)
            self.assertNotIn("preserve_internal_details", visible_text)
            self.assertNotIn("remove_isolated_islands", visible_text)
            self.assertIn("Preserve holes", visible_text)
            self.assertIn("Keep fine inner details", visible_text)
        finally:
            window.close()


    def test_printability_controls_feed_shared_config(self) -> None:
        window = MainWindow()
        try:
            self.assertTrue(hasattr(window, "printability_enabled"))
            self.assertTrue(hasattr(window, "printability_printer_aware_defaults"))
            self.assertTrue(hasattr(window, "printability_min_feature_width"))
            window.printability_printer_aware_defaults.setChecked(False)
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
            self.assertFalse(config.printability.use_printer_aware_defaults)
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

    def test_printer_aware_defaults_feed_printability_config(self) -> None:
        window = MainWindow()
        try:
            self.assertTrue(hasattr(window, "printer_nozzle_diameter"))
            self.assertTrue(hasattr(window, "printer_line_width"))
            window.printability_printer_aware_defaults.setChecked(True)
            window.printer_nozzle_diameter.setValue(0.6)
            window.printer_line_width.setValue(0.6)
            window.apply_printer_aware_defaults()

            config = window._config_from_controls()

            self.assertTrue(config.printability.use_printer_aware_defaults)
            self.assertAlmostEqual(config.printer_profile.nozzle_diameter_mm, 0.6)
            self.assertAlmostEqual(config.printer_profile.line_width_mm, 0.6)
            self.assertAlmostEqual(config.printability.minimum_feature_width_mm, 1.2)
            self.assertAlmostEqual(config.printability.minimum_segment_length_mm, 2.25)
            self.assertAlmostEqual(config.printability.maximum_mergeable_gap_mm, 0.9)
            self.assertFalse(window.printability_min_feature_width.isEnabled())
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
