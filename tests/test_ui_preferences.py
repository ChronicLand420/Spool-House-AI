from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from spool_house_ai.ui_preferences import (
    UiPreferences,
    default_ui_preferences,
    load_ui_preferences,
    save_ui_preferences,
    ui_preferences_from_mapping,
)


class UiPreferencesTests(unittest.TestCase):
    def test_missing_file_uses_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            prefs = load_ui_preferences(Path(temp_dir) / "missing.json")
            self.assertEqual(prefs, default_ui_preferences())

    def test_save_and_load_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "ui_preferences.json"
            expected = UiPreferences(
                appearance_theme="light",
                accent_color="green",
                ui_density="compact",
                preview_size="large",
                startup_log_behavior="expanded",
                open_output_folder_after_generation=True,
                show_job_summary_after_generation=True,
                use_last_selected_preset=False,
                last_cleanup_preset="drip_logo",
                output_folder=str(Path(temp_dir) / "custom_output"),
                preferred_slicer="orca",
                orca_executable_path=str(Path(temp_dir) / "orca-slicer.exe"),
                bambu_executable_path=str(Path(temp_dir) / "bambu-studio.exe"),
                prefer_generic_3mf=False,
            )
            save_ui_preferences(path, expected)
            self.assertEqual(load_ui_preferences(path), expected)

    def test_invalid_values_fall_back_to_defaults(self) -> None:
        prefs = ui_preferences_from_mapping(
            {
                "appearance_theme": "infrared",
                "accent_color": "laser",
                "ui_density": "tiny",
                "preview_size": "billboard",
                "startup_log_behavior": "sometimes",
                "open_output_folder_after_generation": "yes",
                "show_job_summary_after_generation": 1,
                "use_last_selected_preset": None,
                "last_cleanup_preset": 123,
                "output_folder": 456,
                "preferred_slicer": "laser-cutter",
                "orca_executable_path": 123,
                "bambu_executable_path": None,
                "prefer_generic_3mf": "yes",
            }
        )
        self.assertEqual(prefs, default_ui_preferences())

    def test_corrupt_json_uses_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "ui_preferences.json"
            path.write_text("{bad json", encoding="utf-8")
            self.assertEqual(load_ui_preferences(path), default_ui_preferences())

    def test_unknown_values_do_not_replace_valid_values(self) -> None:
        defaults = default_ui_preferences()
        prefs = ui_preferences_from_mapping(
            {
                "appearance_theme": "light",
                "accent_color": "nope",
                "ui_density": "compact",
                "preview_size": "medium",
                "startup_log_behavior": "expanded",
                "output_folder": str(Path(tempfile.gettempdir()) / "spool_house_outputs"),
            }
        )
        self.assertEqual(prefs.appearance_theme, "light")
        self.assertEqual(prefs.accent_color, defaults.accent_color)
        self.assertEqual(prefs.ui_density, "compact")
        self.assertEqual(prefs.preview_size, "medium")
        self.assertEqual(prefs.startup_log_behavior, "expanded")
        self.assertTrue(prefs.output_folder.endswith("spool_house_outputs"))

    def test_legacy_logo_clean_last_preset_maps_to_clean_logo(self) -> None:
        prefs = ui_preferences_from_mapping({"last_cleanup_preset": "logo_clean"})
        self.assertEqual(prefs.last_cleanup_preset, "clean_logo")

    def test_legacy_orange_accent_value_still_loads(self) -> None:
        prefs = ui_preferences_from_mapping({"accent_color": "orange"})
        self.assertEqual(prefs.accent_color, "orange")

    def test_slicer_preferences_default_and_normalize(self) -> None:
        defaults = default_ui_preferences()
        self.assertEqual(defaults.preferred_slicer, "system_default")
        self.assertTrue(defaults.prefer_generic_3mf)
        prefs = ui_preferences_from_mapping(
            {
                "preferred_slicer": "Bambu Studio",
                "orca_executable_path": r"C:\Tools\OrcaSlicer\orca-slicer.exe",
                "bambu_executable_path": r"C:\Tools\Bambu Studio\bambu-studio.exe",
                "prefer_generic_3mf": False,
            }
        )
        self.assertEqual(prefs.preferred_slicer, "bambu")
        self.assertEqual(prefs.orca_executable_path, r"C:\Tools\OrcaSlicer\orca-slicer.exe")
        self.assertEqual(prefs.bambu_executable_path, r"C:\Tools\Bambu Studio\bambu-studio.exe")
        self.assertFalse(prefs.prefer_generic_3mf)


if __name__ == "__main__":
    unittest.main()
