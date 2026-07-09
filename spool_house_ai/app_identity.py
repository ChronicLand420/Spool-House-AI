from __future__ import annotations

import ctypes
import sys
from pathlib import Path


APP_DISPLAY_NAME = "Spool House Studio"
APP_ORGANIZATION_NAME = "ChronicLand420"
APP_USER_MODEL_ID = "ChronicLand420.SpoolHouseStudio"
APP_ICON_RELATIVE_PATH = Path("assets") / "spai_icon_purple.ico"
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
    return resource_path(APP_ICON_RELATIVE_PATH)


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
