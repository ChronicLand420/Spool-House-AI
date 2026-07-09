from __future__ import annotations

import unittest

from spool_house_ai import __version__
from spool_house_ai.app_identity import (
    APP_DISPLAY_NAME,
    APP_USER_MODEL_ID,
    app_icon_path,
    config_path,
    load_app_version,
    set_windows_app_user_model_id,
)


class AppIdentityTests(unittest.TestCase):
    def test_runtime_paths_find_required_files(self) -> None:
        self.assertTrue(config_path().exists())
        self.assertTrue(app_icon_path().exists())
        self.assertEqual(app_icon_path().suffix.lower(), ".ico")

    def test_display_identity_values_are_release_ready(self) -> None:
        self.assertEqual(APP_DISPLAY_NAME, "Spool House Studio")
        self.assertEqual(APP_USER_MODEL_ID, "ChronicLand420.SpoolHouseStudio")
        self.assertEqual(load_app_version(), "v0.1.0-alpha")
        self.assertEqual(__version__, "0.1.0-alpha")

    def test_app_user_model_id_helper_is_safe_to_call(self) -> None:
        set_windows_app_user_model_id()


if __name__ == "__main__":
    unittest.main()
