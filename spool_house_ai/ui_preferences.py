from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from spool_house_ai.config import normalize_cleanup_preset


THEMES = {"dark", "light"}
ACCENT_COLORS = {"purple", "green", "orange", "blue", "red", "pink", "gray"}
UI_DENSITIES = {"comfortable", "compact"}
PREVIEW_SIZES = {"small", "medium", "large"}
LOG_BEHAVIORS = {"collapsed", "expanded"}
LAST_PRESET_VALUES = {"default", "clean_logo", "detail_preserving", "drip_logo", "splatter_logo", "line_art"}


@dataclass(frozen=True)
class UiPreferences:
    appearance_theme: str = "dark"
    accent_color: str = "purple"
    ui_density: str = "comfortable"
    preview_size: str = "medium"
    startup_log_behavior: str = "collapsed"
    open_output_folder_after_generation: bool = False
    show_job_summary_after_generation: bool = False
    use_last_selected_preset: bool = True
    last_cleanup_preset: str = ""
    output_folder: str = ""


def default_ui_preferences() -> UiPreferences:
    return UiPreferences()


def ui_preferences_path(project_root: Path) -> Path:
    return project_root / "config" / "ui_preferences.json"


def load_ui_preferences(path: Path) -> UiPreferences:
    if not path.exists():
        return default_ui_preferences()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_ui_preferences()
    if not isinstance(raw, dict):
        return default_ui_preferences()
    return ui_preferences_from_mapping(raw)


def save_ui_preferences(path: Path, preferences: UiPreferences) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(preferences), indent=2), encoding="utf-8")


def ui_preferences_from_mapping(raw: dict[str, Any]) -> UiPreferences:
    defaults = default_ui_preferences()
    return UiPreferences(
        appearance_theme=_choice(raw.get("appearance_theme"), THEMES, defaults.appearance_theme),
        accent_color=_choice(raw.get("accent_color"), ACCENT_COLORS, defaults.accent_color),
        ui_density=_choice(raw.get("ui_density"), UI_DENSITIES, defaults.ui_density),
        preview_size=_choice(raw.get("preview_size"), PREVIEW_SIZES, defaults.preview_size),
        startup_log_behavior=_choice(raw.get("startup_log_behavior"), LOG_BEHAVIORS, defaults.startup_log_behavior),
        open_output_folder_after_generation=_bool(
            raw.get("open_output_folder_after_generation"),
            defaults.open_output_folder_after_generation,
        ),
        show_job_summary_after_generation=_bool(
            raw.get("show_job_summary_after_generation"),
            defaults.show_job_summary_after_generation,
        ),
        use_last_selected_preset=_bool(raw.get("use_last_selected_preset"), defaults.use_last_selected_preset),
        last_cleanup_preset=_cleanup_preset(raw.get("last_cleanup_preset"), defaults.last_cleanup_preset),
        output_folder=_text(raw.get("output_folder"), defaults.output_folder),
    )


def _choice(value: Any, allowed: set[str], fallback: str) -> str:
    if isinstance(value, str) and value in allowed:
        return value
    return fallback


def _bool(value: Any, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    return fallback


def _text(value: Any, fallback: str) -> str:
    if isinstance(value, str):
        return value
    return fallback


def _cleanup_preset(value: Any, fallback: str) -> str:
    if not isinstance(value, str) or not value.strip():
        return fallback
    normalized = normalize_cleanup_preset(value)
    if normalized in LAST_PRESET_VALUES:
        return normalized
    return fallback
