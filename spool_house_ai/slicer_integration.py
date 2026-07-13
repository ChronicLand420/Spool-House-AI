from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

from spool_house_ai.output_paths import JobOutputPaths


OPEN_IN_SLICER_LABEL = "Open in Slicer"
PREFERRED_SLICERS = {"system_default", "orca", "bambu"}
SLICER_LABELS = {
    "system_default": "System default",
    "orca": "OrcaSlicer",
    "bambu": "Bambu Studio",
}
SLICER_EXECUTABLE_NAMES = {
    "orca": "orca-slicer.exe",
    "bambu": "bambu-studio.exe",
}


@dataclass(frozen=True)
class SlicerInputSelection:
    path: Path | None
    file_format: str
    used_generic_3mf: bool
    fallback_used: bool
    message: str
    error: str = ""

    @property
    def success(self) -> bool:
        return self.path is not None and not self.error


@dataclass(frozen=True)
class SlicerDiscoveryResult:
    slicer: str
    found: bool
    executable_path: Path | None
    discovery_method: str
    validation_result: str
    warning: str = ""


@dataclass(frozen=True)
class SlicerLaunchPlan:
    preferred_slicer: str
    display_name: str
    selected_file: Path | None
    file_format: str
    executable_path: Path | None
    arguments: list[str]
    message: str
    error: str = ""

    @property
    def can_launch(self) -> bool:
        return self.selected_file is not None and not self.error


@dataclass(frozen=True)
class SlicerLaunchResult:
    launched: bool
    message: str
    error: str = ""


@dataclass(frozen=True)
class SlicerInfoResult:
    slicer: str
    supported: bool
    command: list[str]
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    success: bool
    message: str


def normalize_preferred_slicer(value: str | None) -> str:
    normalized = str(value or "system_default").strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "default": "system_default",
        "system": "system_default",
        "orca_slicer": "orca",
        "orcaslicer": "orca",
        "bambu_studio": "bambu",
        "bambustudio": "bambu",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in PREFERRED_SLICERS else "system_default"


def select_slicer_input(
    paths: JobOutputPaths | None,
    job_status: dict | None,
    *,
    prefer_generic_3mf: bool = True,
) -> SlicerInputSelection:
    if paths is None:
        return SlicerInputSelection(None, "", False, False, "", "No successful output is selected.")
    generic_ok = _validated_generic_3mf_available(paths, job_status)
    stl_ok = _successful_stl_available(paths, job_status)

    if prefer_generic_3mf and generic_ok:
        return SlicerInputSelection(
            paths.generic_3mf_path,
            "3mf",
            True,
            False,
            "Opening validated generic 3MF.",
        )
    if stl_ok:
        message = "Opening STL."
        fallback = False
        if prefer_generic_3mf and not generic_ok:
            message = "Generic 3MF unavailable; opening STL instead."
            fallback = True
        return SlicerInputSelection(paths.stl_path, "stl", False, fallback, message)
    if generic_ok:
        return SlicerInputSelection(
            paths.generic_3mf_path,
            "3mf",
            True,
            False,
            "Opening validated generic 3MF; STL was unavailable.",
        )
    return SlicerInputSelection(
        None,
        "",
        False,
        False,
        "",
        "No successful STL or validated generic 3MF output exists for this job.",
    )


def discover_slicer(
    slicer: str,
    *,
    configured_path: str = "",
    path_lookup: Callable[[str], str | None] | None = None,
    common_paths: Sequence[Path] | None = None,
    registry_reader: Callable[[str], Iterable[Path]] | None = None,
    home: Path | None = None,
) -> SlicerDiscoveryResult:
    slicer = normalize_preferred_slicer(slicer)
    if slicer == "system_default":
        return SlicerDiscoveryResult(
            slicer=slicer,
            found=True,
            executable_path=None,
            discovery_method="system_default",
            validation_result="uses Windows file association",
        )

    configured = configured_path.strip().strip('"')
    if configured:
        return _validate_slicer_executable(Path(configured).expanduser(), slicer, "configured")

    for candidate in common_paths if common_paths is not None else _common_slicer_paths(slicer, home=home):
        result = _validate_slicer_executable(candidate, slicer, "common_path")
        if result.found:
            return result

    lookup = path_lookup or shutil.which
    executable_name = SLICER_EXECUTABLE_NAMES[slicer]
    found_on_path = lookup(executable_name)
    if found_on_path:
        result = _validate_slicer_executable(Path(found_on_path), slicer, "path")
        if result.found:
            return result

    reader = registry_reader or _registry_app_paths
    for candidate in reader(slicer):
        result = _validate_slicer_executable(candidate, slicer, "registry")
        if result.found:
            return result

    return SlicerDiscoveryResult(
        slicer=slicer,
        found=False,
        executable_path=None,
        discovery_method="not_found",
        validation_result="missing",
        warning=f"{SLICER_LABELS[slicer]} could not be found.",
    )


def build_slicer_launch_plan(
    selection: SlicerInputSelection,
    *,
    preferred_slicer: str,
    orca_executable_path: str = "",
    bambu_executable_path: str = "",
    discovery_func: Callable[..., SlicerDiscoveryResult] = discover_slicer,
) -> SlicerLaunchPlan:
    if not selection.success or selection.path is None:
        return SlicerLaunchPlan(
            preferred_slicer=normalize_preferred_slicer(preferred_slicer),
            display_name="",
            selected_file=None,
            file_format=selection.file_format,
            executable_path=None,
            arguments=[],
            message="",
            error=selection.error or "No slicer input is available.",
        )

    preferred = normalize_preferred_slicer(preferred_slicer)
    display_name = SLICER_LABELS[preferred]
    format_label = "generic 3MF" if selection.file_format == "3mf" else "STL"
    fallback_prefix = "Generic 3MF unavailable; " if selection.fallback_used else ""

    if preferred == "system_default":
        return SlicerLaunchPlan(
            preferred_slicer=preferred,
            display_name=display_name,
            selected_file=selection.path,
            file_format=selection.file_format,
            executable_path=None,
            arguments=[str(selection.path)],
            message=f"{fallback_prefix}Opening {format_label} in the system default slicer.",
        )

    configured_path = orca_executable_path if preferred == "orca" else bambu_executable_path
    discovery = discovery_func(preferred, configured_path=configured_path)
    if not discovery.found or discovery.executable_path is None:
        return SlicerLaunchPlan(
            preferred_slicer=preferred,
            display_name=display_name,
            selected_file=selection.path,
            file_format=selection.file_format,
            executable_path=None,
            arguments=[],
            message="",
            error=discovery.warning or f"{display_name} could not be found.",
        )

    return SlicerLaunchPlan(
        preferred_slicer=preferred,
        display_name=display_name,
        selected_file=selection.path,
        file_format=selection.file_format,
        executable_path=discovery.executable_path,
        arguments=[str(selection.path)],
        message=f"{fallback_prefix}Opening {format_label} in {display_name}.",
    )


def launch_slicer_plan(
    plan: SlicerLaunchPlan,
    *,
    system_default_launcher: Callable[[Path], bool] | None = None,
    popen_factory: Callable[..., object] = subprocess.Popen,
) -> SlicerLaunchResult:
    if not plan.can_launch or plan.selected_file is None:
        return SlicerLaunchResult(False, "", plan.error or "No slicer launch plan is available.")

    try:
        if plan.preferred_slicer == "system_default":
            if system_default_launcher is not None:
                opened = bool(system_default_launcher(plan.selected_file))
            elif sys.platform.startswith("win"):
                os.startfile(str(plan.selected_file))  # type: ignore[attr-defined]
                opened = True
            else:
                opened = False
            if not opened:
                return SlicerLaunchResult(False, "", "Could not open the selected model with the system default app.")
            return SlicerLaunchResult(True, plan.message)

        if plan.executable_path is None:
            return SlicerLaunchResult(False, "", f"{plan.display_name} executable was not available.")
        popen_factory([str(plan.executable_path), *plan.arguments], shell=False)
        return SlicerLaunchResult(True, plan.message)
    except Exception as error:
        return SlicerLaunchResult(False, "", str(error))


def safe_slicer_info_diagnostic(
    slicer: str,
    executable_path: Path,
    model_path: Path,
    *,
    timeout_seconds: float = 10.0,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> SlicerInfoResult:
    slicer = normalize_preferred_slicer(slicer)
    if slicer != "bambu":
        return SlicerInfoResult(
            slicer=slicer,
            supported=False,
            command=[],
            exit_code=None,
            stdout="",
            stderr="",
            timed_out=False,
            success=False,
            message="Read-only CLI diagnostics are only enabled for locally verified Bambu Studio --info.",
        )
    command = [str(executable_path), "--info", str(model_path)]
    try:
        completed = runner(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        return SlicerInfoResult(
            slicer=slicer,
            supported=True,
            command=command,
            exit_code=None,
            stdout=error.stdout or "",
            stderr=error.stderr or "",
            timed_out=True,
            success=False,
            message="Slicer information command timed out.",
        )
    return SlicerInfoResult(
        slicer=slicer,
        supported=True,
        command=command,
        exit_code=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
        timed_out=False,
        success=completed.returncode == 0,
        message="Slicer information command completed." if completed.returncode == 0 else "Slicer information command failed.",
    )


def _validated_generic_3mf_available(paths: JobOutputPaths, job_status: dict | None) -> bool:
    if not paths.generic_3mf_path.exists():
        return False
    if not job_status:
        return False
    summary = job_status.get("generic_3mf_summary") or {}
    return bool(
        summary.get("generic_3mf_created")
        and summary.get("generic_3mf_validation_passed")
        and Path(str(summary.get("generic_3mf_path") or paths.generic_3mf_path)) == paths.generic_3mf_path
    )


def _successful_stl_available(paths: JobOutputPaths, job_status: dict | None) -> bool:
    if not paths.stl_path.exists():
        return False
    if not job_status:
        return True
    if job_status.get("failures"):
        return False
    mesh_summary = job_status.get("mesh_summary") or {}
    if mesh_summary:
        return bool(mesh_summary.get("exists", True) and not mesh_summary.get("failures"))
    return True


def _validate_slicer_executable(path: Path, slicer: str, method: str) -> SlicerDiscoveryResult:
    try:
        resolved = path.expanduser().resolve()
    except Exception:
        resolved = path
    label = SLICER_LABELS.get(slicer, slicer)
    if not resolved.exists():
        return SlicerDiscoveryResult(slicer, False, None, method, "missing", f"{label} executable does not exist: {resolved}")
    if not resolved.is_file():
        return SlicerDiscoveryResult(slicer, False, None, method, "not_file", f"{label} path is not a file: {resolved}")
    if resolved.suffix.lower() != ".exe":
        return SlicerDiscoveryResult(slicer, False, None, method, "not_exe", f"{label} path must be an .exe file: {resolved}")
    haystack = f"{resolved.name} {resolved.parent.name}".lower()
    tokens = {
        "orca": ("orca-slicer", "orcaslicer", "orca"),
        "bambu": ("bambu-studio", "bambustudio", "bambu studio", "bambu"),
    }[slicer]
    if not any(token in haystack for token in tokens):
        return SlicerDiscoveryResult(
            slicer,
            False,
            None,
            method,
            "wrong_app",
            f"{label} path does not look like {label}: {resolved}",
        )
    return SlicerDiscoveryResult(slicer, True, resolved, method, "valid")


def _common_slicer_paths(slicer: str, *, home: Path | None = None) -> list[Path]:
    home = home or Path.home()
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    local_programs = home / "AppData" / "Local" / "Programs"
    if slicer == "orca":
        return [
            program_files / "OrcaSlicer" / "orca-slicer.exe",
            local_programs / "OrcaSlicer" / "orca-slicer.exe",
        ]
    if slicer == "bambu":
        return [
            program_files / "Bambu Studio" / "bambu-studio.exe",
            local_programs / "Bambu Studio" / "bambu-studio.exe",
        ]
    return []


def _registry_app_paths(slicer: str) -> list[Path]:
    if not sys.platform.startswith("win"):
        return []
    try:
        import winreg
    except Exception:
        return []

    executable = SLICER_EXECUTABLE_NAMES.get(slicer)
    if not executable:
        return []
    candidates: list[Path] = []
    roots = (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE)
    subkey = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{executable}"
    for root in roots:
        try:
            with winreg.OpenKey(root, subkey) as key:
                value, _kind = winreg.QueryValueEx(key, "")
                if isinstance(value, str) and value.strip():
                    candidates.append(Path(value.strip('"')))
        except OSError:
            continue
    return candidates
