from __future__ import annotations

import ctypes
import sys
from pathlib import Path
from urllib.parse import quote


APP_DISPLAY_NAME = "Spool House Studio"
APP_ORGANIZATION_NAME = "ChronicLand420"
APP_USER_MODEL_ID = "ChronicLand420.SpoolHouseStudio"
APP_SUPPORT_URL = ""
APP_CONTACT_URL = ""
APP_CONTACT_EMAIL = ""
APP_GITHUB_URL = ""
APP_LOGO_GUI_RELATIVE_PATH = Path("assets") / "branding" / "spool_house_logo_gui.png"
APP_MARK_ICON_RELATIVE_PATH = Path("assets") / "branding" / "spool_house_icon.png"
APP_MARK_ICON_ICO_RELATIVE_PATH = Path("assets") / "branding" / "spool_house_icon.ico"
APP_WORDMARK_ICON_RELATIVE_PATH = Path("assets") / "branding" / "spool_house_wordmark_icon.png"
APP_WORDMARK_ICON_ICO_RELATIVE_PATH = Path("assets") / "branding" / "spool_house_wordmark_icon.ico"
APP_RUNTIME_ICON_RELATIVE_PATH = APP_MARK_ICON_ICO_RELATIVE_PATH
APP_BUILD_ICON_RELATIVE_PATH = APP_WORDMARK_ICON_ICO_RELATIVE_PATH
APP_SHORTCUT_ICON_RELATIVE_PATH = APP_WORDMARK_ICON_ICO_RELATIVE_PATH
APP_ICON_RELATIVE_PATH = APP_RUNTIME_ICON_RELATIVE_PATH
CONFIG_RELATIVE_PATH = Path("config") / "config.yaml"


def runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def bundled_root() -> Path | None:
    bundle_path = getattr(sys, "_MEIPASS", None)
    if not bundle_path:
        return None
    return Path(bundle_path).resolve()


def resource_path(relative_path: Path | str) -> Path:
    relative = Path(relative_path)
    candidates = [
        runtime_root() / relative,
        Path.cwd() / relative,
    ]
    bundle = bundled_root()
    if bundle is not None:
        candidates.append(bundle / relative)
    candidates.append(Path(__file__).resolve().parents[1] / relative)

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return runtime_root() / relative


def config_path() -> Path:
    return resource_path(CONFIG_RELATIVE_PATH)


def app_icon_path() -> Path:
    return app_runtime_icon_path()


def app_runtime_icon_path() -> Path:
    return resource_path(APP_RUNTIME_ICON_RELATIVE_PATH)


def app_build_icon_path() -> Path:
    return resource_path(APP_BUILD_ICON_RELATIVE_PATH)


def app_shortcut_icon_path() -> Path:
    return resource_path(APP_SHORTCUT_ICON_RELATIVE_PATH)


def app_mark_icon_path() -> Path:
    return resource_path(APP_MARK_ICON_RELATIVE_PATH)


def app_wordmark_icon_path() -> Path:
    return resource_path(APP_WORDMARK_ICON_RELATIVE_PATH)


def app_support_url() -> str:
    return APP_SUPPORT_URL.strip()


def app_contact_url() -> str:
    if APP_CONTACT_URL.strip():
        return APP_CONTACT_URL.strip()
    if APP_CONTACT_EMAIL.strip():
        subject = quote("Spool House Studio")
        email = quote(APP_CONTACT_EMAIL.strip(), safe="@._+-")
        return f"mailto:{email}?subject={subject}"
    if APP_GITHUB_URL.strip():
        return APP_GITHUB_URL.strip()
    return ""


def app_logo_gui_path() -> Path:
    return resource_path(APP_LOGO_GUI_RELATIVE_PATH)


def load_app_version() -> str:
    version_path = resource_path("VERSION")
    if not version_path.exists():
        return ""
    return version_path.read_text(encoding="utf-8").strip()


def set_windows_app_user_model_id() -> None:
    if not sys.platform.startswith("win"):
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        return
