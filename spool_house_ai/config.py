from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None


@dataclass(frozen=True)
class PipelineConfig:
    product_mode: str
    detail_mode: str
    background_removal_enabled: bool
    debug: bool


@dataclass(frozen=True)
class WatcherConfig:
    stable_check_seconds: float
    stable_check_attempts: int


@dataclass(frozen=True)
class SilhouetteConfig:
    upscale_factor: int
    pre_blur_radius: int
    adaptive_threshold: bool
    cleanup_preset: str
    threshold: int
    threshold_value: int
    blur_radius: int
    morphology_enabled: bool
    morph_kernel_size: int
    morph_iterations: int
    invert: bool
    smoothing_enabled: bool
    smoothing_strength: int
    min_contour_area: float
    simplify_tolerance: float
    preserve_holes: bool
    preserve_internal_details: bool
    default_detail_behavior: str
    detail_mode: str
    detail_height_mm: float
    engraving_depth_mm: float
    contour_smoothing_enabled: bool
    contour_smoothing_strength: int
    collinear_merge_tolerance: float
    sharp_corner_angle_threshold: float
    safe_smoothing_enabled: bool
    smoothing_profile: str
    max_area_change_percent: float
    max_bbox_change_percent: float
    max_aspect_ratio_change_percent: float
    max_point_reduction_percent: float
    straight_line_cleanup_enabled: bool
    straight_line_tolerance: float
    min_straight_segment_length_px: float
    curve_fit_enabled: bool
    curve_fit_tolerance: float
    min_curve_segment_length_px: float
    max_curve_error_percent: float
    remove_small_islands: bool
    min_island_area_px: float
    preserve_islands_near_body: bool
    island_near_body_distance_px: float


@dataclass(frozen=True)
class SvgConfig:
    vectorizer_backend: str
    min_contour_area: float
    simplify_epsilon: float
    simplify_tolerance: float
    smoothing_enabled: bool
    contour_smoothing_enabled: bool
    contour_smoothing_strength: int
    collinear_merge_tolerance: float
    sharp_corner_angle_threshold: float
    safe_smoothing_enabled: bool
    smoothing_profile: str
    max_area_change_percent: float
    max_bbox_change_percent: float
    max_aspect_ratio_change_percent: float
    max_point_reduction_percent: float
    straight_line_cleanup_enabled: bool
    straight_line_tolerance: float
    min_straight_segment_length_px: float
    curve_fit_enabled: bool
    curve_fit_tolerance: float
    min_curve_segment_length_px: float
    max_curve_error_percent: float


@dataclass(frozen=True)
class StlConfig:
    stl_backend: str
    product_mode: str
    width_mm: float
    output_scale_mm: float
    base_height_mm: float
    relief_height_mm: float
    extrusion_height_mm: float
    detail_height_mm: float
    engraving_depth_mm: float
    detail_mode: str
    max_mesh_pixels: int
    preserve_holes: bool
    add_keychain_hole: bool
    keychain_hole_diameter_mm: float
    keychain_loop_outer_diameter_mm: float
    bevel_enabled: bool
    bevel_pixels: int
    curve_sample_resolution: int
    lithophane_width_mm: float
    lithophane_min_thickness_mm: float
    lithophane_max_thickness_mm: float
    lithophane_invert: bool
    lithophane_max_pixels: int


@dataclass(frozen=True)
class PreviewConfig:
    image_size_px: int


@dataclass(frozen=True)
class AppConfig:
    project_root: Path
    input_dir: Path
    output_dir: Path
    log_dir: Path
    pipeline: PipelineConfig
    watcher: WatcherConfig
    silhouette: SilhouetteConfig
    svg: SvgConfig
    stl: StlConfig
    preview: PreviewConfig


def load_config(config_path: Path) -> AppConfig:
    config_path = config_path.resolve()
    project_root = config_path.parent.parent

    raw_config = _load_yaml_config(config_path)

    paths = raw_config.get("paths", {})
    input_dir = _resolve_project_path(project_root, paths.get("input_dir", "input"))
    output_dir = _resolve_project_path(project_root, paths.get("output_dir", "output"))
    log_dir = _resolve_project_path(project_root, paths.get("log_dir", "logs"))

    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    return AppConfig(
        project_root=project_root,
        input_dir=input_dir,
        output_dir=output_dir,
        log_dir=log_dir,
        pipeline=_pipeline_config(raw_config.get("pipeline", {})),
        watcher=_watcher_config(raw_config.get("watcher", {})),
        silhouette=_silhouette_config(raw_config.get("silhouette", {})),
        svg=_svg_config(raw_config.get("svg", {})),
        stl=_stl_config(raw_config.get("stl", {})),
        preview=_preview_config(raw_config.get("preview", {})),
    )


def _resolve_project_path(project_root: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return project_root / path


def _load_yaml_config(config_path: Path) -> dict[str, Any]:
    with config_path.open("r", encoding="utf-8") as config_file:
        if yaml is not None:
            return yaml.safe_load(config_file) or {}
        return _parse_simple_yaml(config_file.read())


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    current_section: dict[str, Any] | None = None

    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not line.startswith(" ") and line.endswith(":"):
            section_name = line[:-1].strip()
            current_section = {}
            parsed[section_name] = current_section
            continue
        if current_section is None or ":" not in line:
            continue

        key, raw_value = line.strip().split(":", 1)
        current_section[key.strip()] = _parse_scalar(raw_value.strip())

    return parsed


def _parse_scalar(value: str) -> Any:
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


def _watcher_config(value: dict[str, Any]) -> WatcherConfig:
    return WatcherConfig(
        stable_check_seconds=float(value.get("stable_check_seconds", 1.0)),
        stable_check_attempts=int(value.get("stable_check_attempts", 5)),
    )


def _pipeline_config(value: dict[str, Any]) -> PipelineConfig:
    return PipelineConfig(
        product_mode=str(value.get("product_mode", "flat_relief")),
        detail_mode=str(value.get("detail_mode", "preserve_holes")),
        background_removal_enabled=bool(value.get("background_removal_enabled", False)),
        debug=bool(value.get("debug", False)),
    )


def _silhouette_config(value: dict[str, Any]) -> SilhouetteConfig:
    return apply_cleanup_preset(
        SilhouetteConfig(
            upscale_factor=int(value.get("upscale_factor", 2)),
            pre_blur_radius=int(value.get("pre_blur_radius", 1)),
            adaptive_threshold=bool(value.get("adaptive_threshold", False)),
            cleanup_preset=str(value.get("cleanup_preset", "default")),
            threshold=int(value.get("threshold", 20)),
            threshold_value=int(value.get("threshold_value", value.get("threshold", 128))),
            blur_radius=int(value.get("blur_radius", 3)),
            morphology_enabled=bool(value.get("morphology_enabled", True)),
            morph_kernel_size=int(value.get("morph_kernel_size", 5)),
            morph_iterations=int(value.get("morph_iterations", 1)),
            invert=bool(value.get("invert", False)),
            smoothing_enabled=bool(value.get("smoothing_enabled", True)),
            smoothing_strength=int(value.get("smoothing_strength", value.get("blur_radius", 3))),
            min_contour_area=float(value.get("min_contour_area", 25)),
            simplify_tolerance=float(value.get("simplify_tolerance", 0.8)),
            preserve_holes=bool(value.get("preserve_holes", True)),
            preserve_internal_details=bool(value.get("preserve_internal_details", True)),
            default_detail_behavior=str(value.get("default_detail_behavior", "raised")),
            detail_mode=str(value.get("detail_mode", "preserve_holes")),
            detail_height_mm=float(value.get("detail_height_mm", 0.8)),
            engraving_depth_mm=float(value.get("engraving_depth_mm", 0.6)),
            contour_smoothing_enabled=bool(value.get("contour_smoothing_enabled", True)),
            contour_smoothing_strength=int(value.get("contour_smoothing_strength", 1)),
            collinear_merge_tolerance=float(value.get("collinear_merge_tolerance", 2.0)),
            sharp_corner_angle_threshold=float(value.get("sharp_corner_angle_threshold", 35.0)),
            safe_smoothing_enabled=bool(value.get("safe_smoothing_enabled", True)),
            smoothing_profile=str(value.get("smoothing_profile", "conservative")),
            max_area_change_percent=float(value.get("max_area_change_percent", 10)),
            max_bbox_change_percent=float(value.get("max_bbox_change_percent", 10)),
            max_aspect_ratio_change_percent=float(value.get("max_aspect_ratio_change_percent", 10)),
            max_point_reduction_percent=float(value.get("max_point_reduction_percent", 80)),
            straight_line_cleanup_enabled=bool(value.get("straight_line_cleanup_enabled", True)),
            straight_line_tolerance=float(value.get("straight_line_tolerance", 4.0)),
            min_straight_segment_length_px=float(value.get("min_straight_segment_length_px", 24)),
            curve_fit_enabled=bool(value.get("curve_fit_enabled", True)),
            curve_fit_tolerance=float(value.get("curve_fit_tolerance", 1.0)),
            min_curve_segment_length_px=float(value.get("min_curve_segment_length_px", 12)),
            max_curve_error_percent=float(value.get("max_curve_error_percent", 5)),
            remove_small_islands=bool(value.get("remove_small_islands", True)),
            min_island_area_px=float(value.get("min_island_area_px", 75)),
            preserve_islands_near_body=bool(value.get("preserve_islands_near_body", True)),
            island_near_body_distance_px=float(value.get("island_near_body_distance_px", 8)),
        )
    )


def apply_cleanup_preset(config: SilhouetteConfig, preset: str | None = None) -> SilhouetteConfig:
    cleanup_preset = normalize_cleanup_preset(preset or config.cleanup_preset)
    config = replace(config, cleanup_preset=cleanup_preset)
    if cleanup_preset == "default":
        return config
    if cleanup_preset == "clean_logo":
        return replace(
            config,
            remove_small_islands=True,
            min_island_area_px=max(config.min_island_area_px, 150.0),
            preserve_islands_near_body=False,
            island_near_body_distance_px=0.0,
            preserve_holes=True,
            preserve_internal_details=True,
            morphology_enabled=True,
            morph_kernel_size=max(config.morph_kernel_size, 3),
            morph_iterations=max(config.morph_iterations, 1),
            contour_smoothing_enabled=True,
            straight_line_cleanup_enabled=True,
            curve_fit_enabled=True,
        )
    if cleanup_preset == "line_art":
        return replace(
            config,
            remove_small_islands=True,
            min_island_area_px=max(config.min_island_area_px, 85.0),
            preserve_islands_near_body=True,
            island_near_body_distance_px=min(max(config.island_near_body_distance_px, 4.0), 6.0),
            preserve_holes=True,
            preserve_internal_details=True,
            min_contour_area=min(config.min_contour_area, 18.0),
            simplify_tolerance=min(config.simplify_tolerance, 0.7),
            morphology_enabled=True,
            morph_kernel_size=max(config.morph_kernel_size, 3),
            morph_iterations=max(config.morph_iterations, 1),
            contour_smoothing_enabled=True,
            straight_line_cleanup_enabled=True,
            curve_fit_enabled=True,
        )
    if cleanup_preset == "drip_logo":
        return replace(
            config,
            remove_small_islands=True,
            min_island_area_px=max(config.min_island_area_px, 110.0),
            preserve_islands_near_body=True,
            island_near_body_distance_px=max(config.island_near_body_distance_px, 14.0),
            preserve_holes=True,
            preserve_internal_details=True,
            morphology_enabled=True,
            morph_kernel_size=max(config.morph_kernel_size, 3),
            morph_iterations=max(config.morph_iterations, 1),
            contour_smoothing_enabled=True,
            straight_line_cleanup_enabled=True,
            curve_fit_enabled=True,
        )
    if cleanup_preset == "splatter_logo":
        return replace(
            config,
            remove_small_islands=True,
            min_island_area_px=min(config.min_island_area_px, 55.0),
            preserve_islands_near_body=True,
            island_near_body_distance_px=max(config.island_near_body_distance_px, 18.0),
            preserve_holes=True,
            preserve_internal_details=True,
            min_contour_area=min(config.min_contour_area, 18.0),
            simplify_tolerance=min(config.simplify_tolerance, 0.65),
            contour_smoothing_enabled=True,
            straight_line_cleanup_enabled=True,
            curve_fit_enabled=True,
        )
    if cleanup_preset == "detail_preserving":
        return replace(
            config,
            remove_small_islands=True,
            min_island_area_px=min(config.min_island_area_px, 35.0),
            preserve_islands_near_body=True,
            island_near_body_distance_px=max(config.island_near_body_distance_px, 10.0),
            preserve_internal_details=True,
        )
    return config


def normalize_cleanup_preset(value: str | None) -> str:
    normalized = str(value or "default").strip().lower().replace(" ", "_").replace("-", "_")
    if normalized in {"logo", "logo_clean", "logo_cleaning"}:
        return "clean_logo"
    if normalized in {"clean_logo", "clean"}:
        return "clean_logo"
    if normalized in {"line", "line_art", "lineart", "outline", "outline_art", "coloring_page", "sneaker"}:
        return "line_art"
    if normalized in {"drip", "drip_logo", "graffiti", "graffiti_logo"}:
        return "drip_logo"
    if normalized in {"splatter", "splatter_logo", "rough", "rough_logo", "distressed"}:
        return "splatter_logo"
    if normalized in {"detail", "detail_preserve", "detail_preserving"}:
        return "detail_preserving"
    return "default"


def _svg_config(value: dict[str, Any]) -> SvgConfig:
    return SvgConfig(
        vectorizer_backend=str(value.get("vectorizer_backend", "opencv")),
        min_contour_area=float(value.get("min_contour_area", 25)),
        simplify_epsilon=float(value.get("simplify_epsilon", 1.5)),
        simplify_tolerance=float(value.get("simplify_tolerance", value.get("simplify_epsilon", 0.8))),
        smoothing_enabled=bool(value.get("smoothing_enabled", True)),
        contour_smoothing_enabled=bool(value.get("contour_smoothing_enabled", True)),
        contour_smoothing_strength=int(value.get("contour_smoothing_strength", 1)),
        collinear_merge_tolerance=float(value.get("collinear_merge_tolerance", 2.0)),
        sharp_corner_angle_threshold=float(value.get("sharp_corner_angle_threshold", 35.0)),
        safe_smoothing_enabled=bool(value.get("safe_smoothing_enabled", True)),
        smoothing_profile=str(value.get("smoothing_profile", "conservative")),
        max_area_change_percent=float(value.get("max_area_change_percent", 10)),
        max_bbox_change_percent=float(value.get("max_bbox_change_percent", 10)),
        max_aspect_ratio_change_percent=float(value.get("max_aspect_ratio_change_percent", 10)),
        max_point_reduction_percent=float(value.get("max_point_reduction_percent", 80)),
        straight_line_cleanup_enabled=bool(value.get("straight_line_cleanup_enabled", True)),
        straight_line_tolerance=float(value.get("straight_line_tolerance", 4.0)),
        min_straight_segment_length_px=float(value.get("min_straight_segment_length_px", 24)),
        curve_fit_enabled=bool(value.get("curve_fit_enabled", True)),
        curve_fit_tolerance=float(value.get("curve_fit_tolerance", 1.0)),
        min_curve_segment_length_px=float(value.get("min_curve_segment_length_px", 12)),
        max_curve_error_percent=float(value.get("max_curve_error_percent", 5)),
    )


def _stl_config(value: dict[str, Any]) -> StlConfig:
    return StlConfig(
        stl_backend=str(value.get("stl_backend", "auto_vector_first")),
        product_mode=str(value.get("product_mode", "flat_relief")),
        width_mm=float(value.get("width_mm", 100.0)),
        output_scale_mm=float(value.get("output_scale_mm", value.get("width_mm", 100.0))),
        base_height_mm=float(value.get("base_height_mm", 1.6)),
        relief_height_mm=float(value.get("relief_height_mm", 3.0)),
        extrusion_height_mm=float(value.get("extrusion_height_mm", value.get("relief_height_mm", 3.0))),
        detail_height_mm=float(value.get("detail_height_mm", 0.8)),
        engraving_depth_mm=float(value.get("engraving_depth_mm", 0.6)),
        detail_mode=str(value.get("detail_mode", "preserve_holes")),
        max_mesh_pixels=int(value.get("max_mesh_pixels", 160000)),
        preserve_holes=bool(value.get("preserve_holes", True)),
        add_keychain_hole=bool(value.get("add_keychain_hole", False)),
        keychain_hole_diameter_mm=float(value.get("keychain_hole_diameter_mm", 5.0)),
        keychain_loop_outer_diameter_mm=float(value.get("keychain_loop_outer_diameter_mm", 10.0)),
        bevel_enabled=bool(value.get("bevel_enabled", False)),
        bevel_pixels=int(value.get("bevel_pixels", 1)),
        curve_sample_resolution=int(value.get("curve_sample_resolution", 2)),
        lithophane_width_mm=float(value.get("lithophane_width_mm", 100.0)),
        lithophane_min_thickness_mm=float(value.get("lithophane_min_thickness_mm", 0.8)),
        lithophane_max_thickness_mm=float(value.get("lithophane_max_thickness_mm", 3.0)),
        lithophane_invert=bool(value.get("lithophane_invert", False)),
        lithophane_max_pixels=int(value.get("lithophane_max_pixels", 60000)),
    )


def _preview_config(value: dict[str, Any]) -> PreviewConfig:
    return PreviewConfig(image_size_px=int(value.get("image_size_px", 1200)))
