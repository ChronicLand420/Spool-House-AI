from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from spool_house_ai import __version__
from spool_house_ai.app_identity import (
    APP_DISPLAY_NAME,
    APP_ICON_RELATIVE_PATH,
    APP_LOGO_GUI_RELATIVE_PATH,
    APP_USER_MODEL_ID,
    CONFIG_RELATIVE_PATH,
    app_logo_gui_path,
    app_icon_path,
    config_path,
    load_app_version,
    resource_path,
    set_windows_app_user_model_id,
)
from scripts.create_desktop_shortcut import _existing_icon


class AppIdentityTests(unittest.TestCase):
    def test_runtime_paths_find_required_files(self) -> None:
        self.assertTrue(config_path().exists())
        self.assertTrue(app_icon_path().exists())
        self.assertEqual(app_icon_path().suffix.lower(), ".ico")
        self.assertTrue(app_logo_gui_path().exists())
        self.assertEqual(app_logo_gui_path().suffix.lower(), ".png")

    def test_branding_asset_paths_use_spool_house_branding(self) -> None:
        self.assertEqual(APP_ICON_RELATIVE_PATH.as_posix(), "assets/branding/spool_house_icon.ico")
        self.assertEqual(APP_LOGO_GUI_RELATIVE_PATH.as_posix(), "assets/branding/spool_house_logo_gui.png")
        self.assertTrue((Path.cwd() / "assets" / "branding" / "spool_house_logo_source.png").exists())

    def test_shortcut_helper_prefers_branded_icon(self) -> None:
        self.assertEqual(
            _existing_icon(Path.cwd()),
            Path.cwd() / "assets" / "branding" / "spool_house_icon.ico",
        )

    def test_display_identity_values_are_release_ready(self) -> None:
        self.assertEqual(APP_DISPLAY_NAME, "Spool House Studio")
        self.assertEqual(APP_USER_MODEL_ID, "ChronicLand420.SpoolHouseStudio")
        self.assertEqual(load_app_version(), "v0.1.0-alpha")
        self.assertEqual(__version__, "0.1.0-alpha")

    def test_app_user_model_id_helper_is_safe_to_call(self) -> None:
        set_windows_app_user_model_id()

    def test_frozen_resource_path_prefers_exe_folder_over_current_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            app_root = temp_path / "portable_app"
            cwd_root = temp_path / "repo_cwd"
            app_config = app_root / CONFIG_RELATIVE_PATH
            cwd_config = cwd_root / CONFIG_RELATIVE_PATH
            app_config.parent.mkdir(parents=True)
            cwd_config.parent.mkdir(parents=True)
            app_config.write_text("app config\n", encoding="utf-8")
            cwd_config.write_text("cwd config\n", encoding="utf-8")

            previous_cwd = Path.cwd()
            try:
                os.chdir(cwd_root)
                with patch.object(sys, "frozen", True, create=True), patch.object(
                    sys,
                    "executable",
                    str(app_root / "Spool House Studio.exe"),
                ):
                    self.assertEqual(resource_path(CONFIG_RELATIVE_PATH).resolve(), app_config.resolve())
            finally:
                os.chdir(previous_cwd)


if __name__ == "__main__":
    unittest.main()
