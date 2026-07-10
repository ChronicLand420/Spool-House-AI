from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REVIEW_DIR = Path(tempfile.gettempdir()) / "shai_spool_house_studio_build"
DEFAULT_DIST_DIR = DEFAULT_REVIEW_DIR / "dist"
DEFAULT_WORK_DIR = DEFAULT_REVIEW_DIR / "build"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from spool_house_ai.app_identity import APP_BUILD_ICON_RELATIVE_PATH, APP_DISPLAY_NAME


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a portable Windows folder for Spool House Studio.")
    parser.add_argument("--dist-dir", type=Path, default=DEFAULT_DIST_DIR, help="Output directory for PyInstaller dist.")
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR, help="Temporary PyInstaller work directory.")
    parser.add_argument("--dry-run", action="store_true", help="Print the PyInstaller command without building.")
    args = parser.parse_args()

    if os.name != "nt":
        print("Portable Windows builds should be run on Windows.")
        return 1

    command = _pyinstaller_command(args.dist_dir, args.work_dir)
    print("Build command:")
    print(_format_command(command))

    if args.dry_run:
        return 0

    if importlib.util.find_spec("PyInstaller") is None:
        print("PyInstaller is not installed.")
        print(f"Install build dependencies with: {sys.executable} -m pip install -r requirements-build.txt")
        return 1

    args.dist_dir.mkdir(parents=True, exist_ok=True)
    args.work_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)

    app_dir = args.dist_dir / APP_DISPLAY_NAME
    _copy_release_files(app_dir)
    exe_path = app_dir / f"{APP_DISPLAY_NAME}.exe"
    print(f"Portable app folder: {app_dir}")
    print(f"Executable: {exe_path}")
    return 0


def _pyinstaller_command(dist_dir: Path, work_dir: Path) -> list[str]:
    spec_dir = work_dir / "spec"
    icon_path = PROJECT_ROOT / APP_BUILD_ICON_RELATIVE_PATH
    return [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name",
        APP_DISPLAY_NAME,
        "--icon",
        str(icon_path),
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(work_dir),
        "--specpath",
        str(spec_dir),
        str(PROJECT_ROOT / "spool_house_ai" / "gui.py"),
    ]


def _copy_release_files(app_dir: Path) -> None:
    app_dir.mkdir(parents=True, exist_ok=True)
    _copy_tree(PROJECT_ROOT / "assets", app_dir / "assets")
    (app_dir / "config").mkdir(parents=True, exist_ok=True)
    shutil.copy2(PROJECT_ROOT / "config" / "config.yaml", app_dir / "config" / "config.yaml")

    for folder_name in ("input", "output", "logs"):
        (app_dir / folder_name).mkdir(parents=True, exist_ok=True)

    readme_text = (
        "Spool House Studio Portable\n"
        "Built by ChronicLand420\n\n"
        "Run Spool House Studio.exe to launch the app.\n"
        "Generated files are written to organized per-image job folders under output/ by default.\n"
        "Use Settings -> Output Folder to choose a different output root.\n"
        "UI preferences are stored in config/ui_preferences.json after first launch.\n"
    )
    (app_dir / "README.txt").write_text(readme_text, encoding="utf-8")


def _copy_tree(source: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)


def _format_command(command: list[str]) -> str:
    return " ".join(_quote(part) for part in command)


def _quote(value: str) -> str:
    if any(char.isspace() for char in value):
        return f'"{value}"'
    return value


if __name__ == "__main__":
    raise SystemExit(main())
