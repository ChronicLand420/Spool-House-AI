from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from spool_house_ai.app_identity import APP_SHORTCUT_ICON_RELATIVE_PATH


SHORTCUT_NAME = "Spool House Studio.lnk"


def main() -> None:
    if os.name != "nt":
        raise SystemExit("Desktop shortcut creation is only supported on Windows.")

    repo_root = Path(__file__).resolve().parents[1]
    desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
    desktop.mkdir(parents=True, exist_ok=True)
    shortcut_path = desktop / SHORTCUT_NAME

    target_path = _python_launcher(repo_root)
    arguments = "-m spool_house_ai.gui"
    icon_path = _existing_icon(repo_root)

    try:
        _create_shortcut_with_pywin32(shortcut_path, target_path, arguments, repo_root, icon_path)
    except ImportError:
        _create_shortcut_with_powershell(shortcut_path, target_path, arguments, repo_root, icon_path)

    print(f"Created shortcut: {shortcut_path}")
    print(f"Target: {target_path}")
    print(f"Arguments: {arguments}")
    print(f"Working directory: {repo_root}")


def _python_launcher(repo_root: Path) -> Path:
    venv_pythonw = repo_root / ".venv" / "Scripts" / "pythonw.exe"
    if venv_pythonw.exists():
        return venv_pythonw

    venv_python = repo_root / ".venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        return venv_python

    return Path(sys.executable)


def _existing_icon(repo_root: Path) -> Path | None:
    for relative_path in (
        APP_SHORTCUT_ICON_RELATIVE_PATH,
        "assets/branding/spool_house_wordmark_icon.ico",
        "assets/branding/spool_house_icon.ico",
        "assets/spai_icon_purple.ico",
        "assets/spool_house_ai.ico",
        "assets/icon.ico",
        "spool_house_ai.ico",
    ):
        icon_path = repo_root / relative_path
        if icon_path.exists():
            return icon_path
    return None


def _create_shortcut_with_pywin32(
    shortcut_path: Path,
    target_path: Path,
    arguments: str,
    working_directory: Path,
    icon_path: Path | None,
) -> None:
    import win32com.client

    shell = win32com.client.Dispatch("WScript.Shell")
    shortcut = shell.CreateShortcut(str(shortcut_path))
    shortcut.TargetPath = str(target_path)
    shortcut.Arguments = arguments
    shortcut.WorkingDirectory = str(working_directory)
    if icon_path is not None:
        shortcut.IconLocation = str(icon_path)
    shortcut.Save()


def _create_shortcut_with_powershell(
    shortcut_path: Path,
    target_path: Path,
    arguments: str,
    working_directory: Path,
    icon_path: Path | None,
) -> None:
    lines = [
        "$shell = New-Object -ComObject WScript.Shell",
        f"$shortcut = $shell.CreateShortcut({_ps_quote(str(shortcut_path))})",
        f"$shortcut.TargetPath = {_ps_quote(str(target_path))}",
        f"$shortcut.Arguments = {_ps_quote(arguments)}",
        f"$shortcut.WorkingDirectory = {_ps_quote(str(working_directory))}",
    ]
    if icon_path is not None:
        lines.append(f"$shortcut.IconLocation = {_ps_quote(str(icon_path))}")
    lines.append("$shortcut.Save()")

    subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            "; ".join(lines),
        ],
        check=True,
    )


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


if __name__ == "__main__":
    main()
