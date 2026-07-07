from __future__ import annotations

import importlib
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    print("Spool House Studio environment check")
    print(f"Project root: {PROJECT_ROOT}")
    print(f"Python executable: {sys.executable}")
    print(f"Python version: {sys.version.split()[0]}")

    checks = [
        ("cv2", "cv2"),
        ("PySide6", "PySide6"),
        ("shapely", "shapely"),
        ("mapbox_earcut", "mapbox_earcut"),
    ]
    failed = False
    for label, module_name in checks:
        ok, message = _check_import(module_name)
        print(f"{label}: {message}")
        failed = failed or not ok

    ok, message = _check_config()
    print(f"config/config.yaml: {message}")
    failed = failed or not ok

    if failed:
        print("Environment check found missing or failing dependencies.")
        return 1
    print("Environment check passed.")
    return 0


def _check_import(module_name: str) -> tuple[bool, str]:
    try:
        importlib.import_module(module_name)
    except Exception as error:
        return False, f"FAILED ({error})"
    return True, "OK"


def _check_config() -> tuple[bool, str]:
    try:
        from spool_house_ai.config import load_config

        config = load_config(PROJECT_ROOT / "config" / "config.yaml")
    except Exception as error:
        return False, f"FAILED ({error})"
    return True, f"OK (input={config.input_dir}, output={config.output_dir})"


if __name__ == "__main__":
    raise SystemExit(main())
