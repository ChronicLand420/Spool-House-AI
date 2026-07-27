from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np
import trimesh
from PIL import Image, ImageDraw


PRINTABILITY_REPORT_VERSION = "1.0"


def empty_printability_report(
    config: Any,
    *,
    product_mode: str,
    generation_path: str,
    preset: str = "",
) -> dict[str, Any]:
    thresholds = _thresholds(config)
    return {
        "schema_version": PRINTABILITY_REPORT_VERSION,
        "enforcement_enabled": bool(getattr(config, "enforce_minimum_printable_geometry", True)),
        "thresholds": thresholds,
        "product_mode": product_mode,
        "generation_path": generation_path,
        "preset": preset,
        "validator_invoked": True,
        "removed_short_segments": 0,
        "removed_islands": 0,
        "removed_slivers": 0,
        "removed_partial_color_fragments": 0,
        "thickened_features": 0,
        "closed_gaps": 0,
        "reinforced_connections": 0,
        "removed_or_filled_tiny_holes": 0,
        "pixels_removed": 0,
        "pixels_added": 0,
        "pixels_recolored": 0,
        "smallest_retained_feature_width_mm": None,
        "smallest_retained_segment_length_mm": None,
        "smallest_retained_component_area_mm2": None,
        "unresolved_printability_warnings": [],
        "paths_invoked": [generation_path],
        "component_records": [],
        "visual_warning_preview_created": False,
        "visual_warning_preview_path": "",
        "visual_warning_preview_paths": [],
        "visual_warning_preview_legend": {
            "gray": "retained printable geometry",
            "red": "removed fragments or holes",
            "cyan": "added, thickened, closed, or reinforced geometry",
            "yellow": "changed height or reassigned color region",
        },
    }


def combine_printability_reports(
    reports: Iterable[dict[str, Any] | None],
    *,
    product_mode: str = "",
    generation_path: str = "combined",
    config: Any | None = None,
) -> dict[str, Any]:
    reports = [report for report in reports if report]
    if not reports:
        return empty_printability_report(config or {}, product_mode=product_mode, generation_path=generation_path)

    combined = empty_printability_report(
        config or reports[0],
        product_mode=product_mode or reports[0].get("product_mode", ""),
        generation_path=generation_path,
        preset=reports[0].get("preset", ""),
    )
    combined["enforcement_enabled"] = any(bool(report.get("enforcement_enabled")) for report in reports)
    combined["thresholds"] = reports[0].get("thresholds", combined["thresholds"])
    combined["paths_invoked"] = sorted(
        {
            path
            for report in reports
            for path in (report.get("paths_invoked") or [report.get("generation_path", "")])
            if path
        }
    )
    min_fields = {
        "smallest_retained_feature_width_mm",
        "smallest_retained_segment_length_mm",
        "smallest_retained_component_area_mm2",
    }
    for report in reports:
        for key, value in report.items():
            if key in min_fields:
                if value is None:
                    continue
                if combined[key] is None or float(value) < float(combined[key]):
                    combined[key] = value
            elif key in {
                "removed_short_segments",
                "removed_islands",
                "removed_slivers",
                "removed_partial_color_fragments",
                "thickened_features",
                "closed_gaps",
                "reinforced_connections",
                "removed_or_filled_tiny_holes",
                "pixels_removed",
                "pixels_added",
                "pixels_recolored",
            }:
                combined[key] += int(value or 0)
        combined["unresolved_printability_warnings"].extend(report.get("unresolved_printability_warnings") or [])
        combined["component_records"].extend(report.get("component_records") or [])
        preview_paths = report.get("visual_warning_preview_paths") or []
        if report.get("visual_warning_preview_path"):
            preview_paths = [report["visual_warning_preview_path"], *preview_paths]
        for preview_path in preview_paths:
            if preview_path and preview_path not in combined["visual_warning_preview_paths"]:
                combined["visual_warning_preview_paths"].append(preview_path)
    combined["unresolved_printability_warnings"] = list(dict.fromkeys(combined["unresolved_printability_warnings"]))
    if combined["visual_warning_preview_paths"]:
        combined["visual_warning_preview_created"] = True
        combined["visual_warning_preview_path"] = combined["visual_warning_preview_paths"][0]
    return combined


def enforce_printable_mask(
    mask: np.ndarray,
    *,
    scale_x_mm: float,
    scale_y_mm: float,
    config: Any,
    product_mode: str,
    generation_path: str,
    preset: str = "",
    color_label: str = "",
    visual_warning_preview_path: Path | None = None,
) -> tuple[np.ndarray, dict[str, Any]]:
    report = empty_printability_report(
        config,
        product_mode=product_mode,
        generation_path=generation_path,
        preset=preset,
    )
    if color_label:
        report["color_label"] = color_label
    working = np.asarray(mask, dtype=bool).copy()
    if not bool(getattr(config, "enforce_minimum_printable_geometry", True)):
        _record_mask_retained_metrics(working, report, scale_x_mm, scale_y_mm)
        _record_visual_warning_preview(
            report,
            visual_warning_preview_path,
            False,
        )
        return working, report

    before = working.copy()
    working, hole_report = _fill_tiny_holes(working, scale_x_mm, scale_y_mm, config)
    _merge_counts(report, hole_report)
    working, gap_report = _close_small_gaps(working, scale_x_mm, scale_y_mm, config)
    _merge_counts(report, gap_report)
    working, component_report = _clean_mask_components(working, scale_x_mm, scale_y_mm, config)
    _merge_counts(report, component_report)
    added = int(np.count_nonzero(working & ~before))
    removed = int(np.count_nonzero(before & ~working))
    report["pixels_added"] += added
    report["pixels_removed"] += removed
    _record_mask_retained_metrics(working, report, scale_x_mm, scale_y_mm)
    _add_mask_warnings(working, report, scale_x_mm, scale_y_mm, config)
    _record_visual_warning_preview(
        report,
        visual_warning_preview_path,
        save_mask_change_preview(before, working, visual_warning_preview_path),
    )
    return working, report


def enforce_printable_height_map(
    height_map: np.ndarray,
    *,
    width_mm: float,
    config: Any,
    product_mode: str,
    generation_path: str,
    preset: str = "",
    visual_warning_preview_path: Path | None = None,
) -> tuple[np.ndarray, dict[str, Any]]:
    working = np.asarray(height_map, dtype=np.float32).copy()
    if working.ndim != 2:
        raise ValueError("Printability height map cleanup expects a 2D height map.")
    scale = float(width_mm) / float(max(working.shape[1], 1))
    reports: list[dict[str, Any]] = []
    levels = [float(level) for level in np.unique(working) if float(level) > 0]
    levels.sort()
    original = working.copy()
    for index, level in enumerate(levels):
        exact_mask = np.isclose(working, level, atol=1e-5)
        cleaned, report = enforce_printable_mask(
            exact_mask,
            scale_x_mm=scale,
            scale_y_mm=scale,
            config=config,
            product_mode=product_mode,
            generation_path=f"{generation_path}:{level:.4f}mm",
            preset=preset,
            color_label=f"{level:.4f}mm",
        )
        removed = exact_mask & ~cleaned
        added = cleaned & ~exact_mask
        replacement = levels[index - 1] if index > 0 else 0.0
        working[removed] = replacement
        working[added & (working < level)] = level
        report["removed_partial_color_fragments"] += int(report.get("removed_islands", 0)) + int(
            report.get("removed_short_segments", 0)
        )
        reports.append(report)
    combined = combine_printability_reports(
        reports,
        product_mode=product_mode,
        generation_path=generation_path,
        config=config,
    )
    combined["height_levels_checked"] = len(levels)
    combined["pixels_recolored"] = int(np.count_nonzero(~np.isclose(original, working, atol=1e-5)))
    _record_visual_warning_preview(
        combined,
        visual_warning_preview_path,
        save_height_map_change_preview(original, working, visual_warning_preview_path),
    )
    return working, combined


def enforce_printable_polygons(
    polygons: list[Any],
    *,
    config: Any,
    product_mode: str,
    generation_path: str,
    preset: str = "",
    visual_warning_preview_path: Path | None = None,
) -> tuple[list[Any], dict[str, Any]]:
    report = empty_printability_report(
        config,
        product_mode=product_mode,
        generation_path=generation_path,
        preset=preset,
    )
    if not bool(getattr(config, "enforce_minimum_printable_geometry", True)):
        _record_polygon_metrics(polygons, report)
        _record_visual_warning_preview(report, visual_warning_preview_path, False)
        return polygons, report

    try:
        from shapely.geometry import Polygon
        from shapely.ops import unary_union
    except ImportError as error:
        report["unresolved_printability_warnings"].append(
            f"Vector printability cleanup skipped because Shapely is unavailable: {error}"
        )
        return polygons, report

    valid = [polygon for polygon in polygons if not polygon.is_empty and polygon.area > 0]
    if not valid:
        _record_visual_warning_preview(report, visual_warning_preview_path, False)
        return [], report
    before_polygons = list(valid)

    valid = [_drop_tiny_holes_from_polygon(polygon, config, report) for polygon in valid]
    valid = [polygon for polygon in valid if not polygon.is_empty and polygon.area > 0]

    gap = float(getattr(config, "maximum_mergeable_gap_mm", 0.6))
    if gap > 0:
        before_area = sum(float(polygon.area) for polygon in valid)
        closed = unary_union(valid).buffer(gap / 2.0, join_style=1).buffer(-gap / 2.0, join_style=1)
        valid = _polygon_parts(closed)
        after_area = sum(float(polygon.area) for polygon in valid)
        if abs(after_area - before_area) > 1e-6:
            report["closed_gaps"] += 1
            report["reinforced_connections"] += 1

    cleaned: list[Any] = []
    min_area = float(getattr(config, "minimum_island_area_mm2", 2.0))
    min_segment = float(getattr(config, "minimum_segment_length_mm", 1.5))
    min_dim = float(getattr(config, "minimum_component_dimension_mm", 0.8))
    min_feature = max(
        float(getattr(config, "minimum_feature_width_mm", 0.8)),
        float(getattr(config, "minimum_connection_width_mm", 0.8)),
    )
    for component_index, polygon in enumerate(valid):
        if polygon.is_empty or polygon.area <= 0:
            continue
        bounds = polygon.bounds
        width = max(0.0, float(bounds[2] - bounds[0]))
        height = max(0.0, float(bounds[3] - bounds[1]))
        smallest_dim = min(width, height)
        longest_dim = max(width, height)
        remove_reason = ""
        if 0 < smallest_dim < min_feature and longest_dim >= min_segment and polygon.area >= min_area:
            growth = min((min_feature - smallest_dim) / 2.0, 0.25)
            if growth > 0:
                thickened = polygon.buffer(growth, join_style=1)
                parts = _polygon_parts(thickened)
                cleaned.extend(parts)
                report["thickened_features"] += 1
                report["reinforced_connections"] += 1
                report["component_records"].append(
                    {
                        "component": component_index,
                        "action": "thickened",
                        "growth_mm": round(growth, 4),
                        "reason": "long_feature_below_minimum_width",
                    }
                )
                continue
        if polygon.area < min_area:
            remove_reason = "area_below_minimum"
            report["removed_islands"] += 1
        elif min_dim > 0 and smallest_dim < min_dim and longest_dim < min_segment:
            remove_reason = "component_dimension_below_minimum"
            report["removed_short_segments"] += 1
        elif min_dim > 0 and smallest_dim < min_dim:
            remove_reason = "sliver_dimension_below_minimum"
            report["removed_slivers"] += 1
        if remove_reason:
            report["component_records"].append(
                {
                    "component": component_index,
                    "action": "removed",
                    "reason": remove_reason,
                    "area_mm2": round(float(polygon.area), 4),
                    "width_mm": round(width, 4),
                    "height_mm": round(height, 4),
                }
            )
            continue
        cleaned.append(polygon)

    if cleaned:
        merged = unary_union(cleaned)
        cleaned = _polygon_parts(merged)
    _record_polygon_metrics(cleaned, report)
    _add_polygon_warnings(cleaned, report, config)
    _record_visual_warning_preview(
        report,
        visual_warning_preview_path,
        save_polygon_change_preview(before_polygons, cleaned, visual_warning_preview_path),
    )
    return cleaned, report


def save_mask_change_preview(
    before_mask: np.ndarray,
    after_mask: np.ndarray,
    output_path: Path | None,
) -> bool:
    if output_path is None:
        return False
    before = np.asarray(before_mask, dtype=bool)
    after = np.asarray(after_mask, dtype=bool)
    if before.shape != after.shape:
        raise ValueError("Printability preview masks must have matching shapes.")
    if np.array_equal(before, after):
        output_path.unlink(missing_ok=True)
        return False
    image = _change_preview_image(before, after)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)
    return True


def save_height_map_change_preview(
    before_height_map: np.ndarray,
    after_height_map: np.ndarray,
    output_path: Path | None,
) -> bool:
    if output_path is None:
        return False
    before = np.asarray(before_height_map, dtype=np.float32)
    after = np.asarray(after_height_map, dtype=np.float32)
    if before.shape != after.shape:
        raise ValueError("Printability preview height maps must have matching shapes.")
    if np.allclose(before, after, atol=1e-5):
        output_path.unlink(missing_ok=True)
        return False
    before_active = before > 0
    after_active = after > 0
    image = _change_preview_image(
        before_active,
        after_active,
        changed_mask=before_active & after_active & ~np.isclose(before, after, atol=1e-5),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)
    return True


def save_polygon_change_preview(
    before_polygons: list[Any],
    after_polygons: list[Any],
    output_path: Path | None,
) -> bool:
    if output_path is None:
        return False
    try:
        from shapely.ops import unary_union
    except ImportError:
        return False
    before_valid = [polygon for polygon in before_polygons if not polygon.is_empty and polygon.area > 0]
    after_valid = [polygon for polygon in after_polygons if not polygon.is_empty and polygon.area > 0]
    if not before_valid and not after_valid:
        output_path.unlink(missing_ok=True)
        return False
    before_geometry = unary_union(before_valid) if before_valid else None
    after_geometry = unary_union(after_valid) if after_valid else None
    if before_geometry is not None and after_geometry is not None:
        if before_geometry.symmetric_difference(after_geometry).area <= 1e-6:
            output_path.unlink(missing_ok=True)
            return False
    before_mask, after_mask = _polygon_change_masks(before_valid, after_valid)
    image = _change_preview_image(before_mask, after_mask)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)
    return True


def validate_lithophane_printability(
    mesh: trimesh.Trimesh,
    *,
    config: Any,
    product_mode: str,
    generation_path: str,
) -> dict[str, Any]:
    report = empty_printability_report(
        config,
        product_mode=product_mode,
        generation_path=generation_path,
    )
    report["vector_fragment_cleanup_applied"] = False
    report["heightfield_texture_cleanup_applied"] = False
    if mesh.is_empty or mesh.vertices.size == 0:
        report["unresolved_printability_warnings"].append("Lithophane mesh is empty.")
        return report
    bounds = np.asarray(mesh.bounds, dtype=float)
    dims = bounds[1] - bounds[0]
    report["smallest_retained_component_area_mm2"] = round(float(dims[0] * dims[1]), 4)
    report["smallest_retained_segment_length_mm"] = round(float(np.min(dims[:2])), 4)
    report["smallest_retained_feature_width_mm"] = round(float(np.min(dims[:2])), 4)
    if float(bounds[0][2]) < -1e-5:
        report["unresolved_printability_warnings"].append("Lithophane bottom is below Z=0.")
    if float(dims[2]) <= 0:
        report["unresolved_printability_warnings"].append("Lithophane thickness is zero or negative.")
    min_feature = float(getattr(config, "minimum_component_dimension_mm", 0.8))
    if min_feature > 0 and (dims[0] < min_feature or dims[1] < min_feature):
        report["unresolved_printability_warnings"].append(
            "Lithophane physical dimensions are below the configured minimum component dimension."
        )
    return report


def _thresholds(config: Any) -> dict[str, Any]:
    try:
        return asdict(config)
    except TypeError:
        return {
            "use_printer_aware_defaults": bool(getattr(config, "use_printer_aware_defaults", True)),
            "enforce_minimum_printable_geometry": bool(
                getattr(config, "enforce_minimum_printable_geometry", True)
            ),
            "minimum_feature_width_mm": float(getattr(config, "minimum_feature_width_mm", 0.8)),
            "minimum_segment_length_mm": float(getattr(config, "minimum_segment_length_mm", 1.5)),
            "minimum_island_area_mm2": float(getattr(config, "minimum_island_area_mm2", 2.0)),
            "minimum_connection_width_mm": float(getattr(config, "minimum_connection_width_mm", 0.8)),
            "maximum_mergeable_gap_mm": float(getattr(config, "maximum_mergeable_gap_mm", 0.6)),
            "minimum_hole_area_mm2": float(getattr(config, "minimum_hole_area_mm2", 1.0)),
            "minimum_component_dimension_mm": float(getattr(config, "minimum_component_dimension_mm", 0.8)),
        }


def _mm_to_pixels(value_mm: float, scale_mm: float) -> int:
    if value_mm <= 0 or scale_mm <= 0:
        return 0
    return max(1, int(np.ceil(value_mm / scale_mm)))


def _close_small_gaps(mask: np.ndarray, scale_x_mm: float, scale_y_mm: float, config: Any) -> tuple[np.ndarray, dict[str, Any]]:
    report = {}
    gap_mm = float(getattr(config, "maximum_mergeable_gap_mm", 0.6))
    gap_px = _mm_to_pixels(gap_mm, min(scale_x_mm, scale_y_mm))
    if gap_px <= 0:
        return mask, report
    radius = min(gap_px, 3)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1))
    closed = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_CLOSE, kernel).astype(bool)
    changed = int(np.count_nonzero(closed & ~mask))
    if changed:
        report["closed_gaps"] = 1
        report["reinforced_connections"] = 1
        report["pixels_added"] = changed
    return closed, report


def _fill_tiny_holes(mask: np.ndarray, scale_x_mm: float, scale_y_mm: float, config: Any) -> tuple[np.ndarray, dict[str, Any]]:
    report = {}
    min_hole_area = float(getattr(config, "minimum_hole_area_mm2", 1.0))
    if min_hole_area <= 0:
        return mask, report
    background = (~mask).astype(np.uint8)
    num_labels, labels, stats, _centroids = cv2.connectedComponentsWithStats(background, 8)
    filled = mask.copy()
    pixel_area = scale_x_mm * scale_y_mm
    filled_count = 0
    changed_pixels = 0
    height, width = mask.shape
    for label in range(1, num_labels):
        left, top, comp_width, comp_height, area = stats[label]
        touches_border = left == 0 or top == 0 or left + comp_width >= width or top + comp_height >= height
        area_mm2 = float(area) * pixel_area
        if not touches_border and area_mm2 < min_hole_area:
            hole_pixels = labels == label
            filled[hole_pixels] = True
            filled_count += 1
            changed_pixels += int(area)
    if filled_count:
        report["removed_or_filled_tiny_holes"] = filled_count
        report["pixels_added"] = changed_pixels
    return filled, report


def _clean_mask_components(mask: np.ndarray, scale_x_mm: float, scale_y_mm: float, config: Any) -> tuple[np.ndarray, dict[str, Any]]:
    report: dict[str, Any] = {}
    num_labels, labels, stats, _centroids = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    cleaned = np.zeros_like(mask, dtype=bool)
    min_area = float(getattr(config, "minimum_island_area_mm2", 2.0))
    min_segment = float(getattr(config, "minimum_segment_length_mm", 1.5))
    min_dim = float(getattr(config, "minimum_component_dimension_mm", 0.8))
    min_feature = max(
        float(getattr(config, "minimum_feature_width_mm", 0.8)),
        float(getattr(config, "minimum_connection_width_mm", 0.8)),
    )
    for label in range(1, num_labels):
        left, top, width_px, height_px, area_px = stats[label]
        area_mm2 = float(area_px) * scale_x_mm * scale_y_mm
        width_mm = float(width_px) * scale_x_mm
        height_mm = float(height_px) * scale_y_mm
        smallest_dim = min(width_mm, height_mm)
        longest_dim = max(width_mm, height_mm)
        component = labels == label
        remove_reason = ""
        if 0 < smallest_dim < min_feature and longest_dim >= min_segment and area_mm2 >= min_area:
            radius_px = min(_mm_to_pixels((min_feature - smallest_dim) / 2.0, min(scale_x_mm, scale_y_mm)), 1)
            if radius_px > 0:
                kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius_px * 2 + 1, radius_px * 2 + 1))
                component = cv2.dilate(component.astype(np.uint8), kernel).astype(bool)
                report["thickened_features"] = int(report.get("thickened_features", 0)) + 1
                report["reinforced_connections"] = int(report.get("reinforced_connections", 0)) + 1
            cleaned |= component
            continue
        if area_mm2 < min_area:
            remove_reason = "area_below_minimum"
            report["removed_islands"] = int(report.get("removed_islands", 0)) + 1
        elif min_dim > 0 and smallest_dim < min_dim and longest_dim < min_segment:
            remove_reason = "segment_below_minimum"
            report["removed_short_segments"] = int(report.get("removed_short_segments", 0)) + 1
        elif min_dim > 0 and smallest_dim < min_dim:
            remove_reason = "sliver_below_minimum_dimension"
            report["removed_slivers"] = int(report.get("removed_slivers", 0)) + 1
        if remove_reason:
            report.setdefault("component_records", []).append(
                {
                    "label": int(label),
                    "action": "removed",
                    "reason": remove_reason,
                    "area_mm2": round(area_mm2, 4),
                    "width_mm": round(width_mm, 4),
                    "height_mm": round(height_mm, 4),
                }
            )
            continue
        cleaned |= component
    return cleaned, report


def _record_mask_retained_metrics(mask: np.ndarray, report: dict[str, Any], scale_x_mm: float, scale_y_mm: float) -> None:
    num_labels, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    areas = []
    dims = []
    for label in range(1, num_labels):
        _left, _top, width_px, height_px, area_px = stats[label]
        areas.append(float(area_px) * scale_x_mm * scale_y_mm)
        dims.append(min(float(width_px) * scale_x_mm, float(height_px) * scale_y_mm))
    if areas:
        report["smallest_retained_component_area_mm2"] = round(min(areas), 4)
    if dims:
        report["smallest_retained_feature_width_mm"] = round(min(dims), 4)
        report["smallest_retained_segment_length_mm"] = round(min(dims), 4)


def _add_mask_warnings(mask: np.ndarray, report: dict[str, Any], scale_x_mm: float, scale_y_mm: float, config: Any) -> None:
    if not np.any(mask):
        report["unresolved_printability_warnings"].append("No printable pixels remain after printability cleanup.")
        return
    distance = cv2.distanceTransform(mask.astype(np.uint8), cv2.DIST_L2, 3).astype(np.float64)
    feature_widths = distance[mask] * 2.0 * min(scale_x_mm, scale_y_mm)
    if feature_widths.size and float(np.min(feature_widths)) < float(getattr(config, "minimum_feature_width_mm", 0.8)):
        report["unresolved_printability_warnings"].append(
            "Some retained pixels are still narrower than the configured minimum feature width."
        )


def _drop_tiny_holes_from_polygon(polygon: Any, config: Any, report: dict[str, Any]) -> Any:
    try:
        from shapely.geometry import Polygon
    except ImportError:
        return polygon
    min_hole_area = float(getattr(config, "minimum_hole_area_mm2", 1.0))
    if min_hole_area <= 0 or not getattr(polygon, "interiors", None):
        return polygon
    retained_holes = []
    removed = 0
    for ring in polygon.interiors:
        hole_polygon = Polygon(ring)
        if hole_polygon.area < min_hole_area:
            removed += 1
        else:
            retained_holes.append(list(ring.coords))
    if removed:
        report["removed_or_filled_tiny_holes"] += removed
    return Polygon(list(polygon.exterior.coords), retained_holes)


def _record_polygon_metrics(polygons: list[Any], report: dict[str, Any]) -> None:
    areas = []
    dims = []
    for polygon in polygons:
        if polygon.is_empty or polygon.area <= 0:
            continue
        bounds = polygon.bounds
        width = max(0.0, float(bounds[2] - bounds[0]))
        height = max(0.0, float(bounds[3] - bounds[1]))
        areas.append(float(polygon.area))
        dims.append(min(width, height))
    if areas:
        report["smallest_retained_component_area_mm2"] = round(min(areas), 4)
    if dims:
        report["smallest_retained_feature_width_mm"] = round(min(dims), 4)
        report["smallest_retained_segment_length_mm"] = round(min(dims), 4)


def _add_polygon_warnings(polygons: list[Any], report: dict[str, Any], config: Any) -> None:
    min_feature = max(
        float(getattr(config, "minimum_feature_width_mm", 0.8)),
        float(getattr(config, "minimum_connection_width_mm", 0.8)),
    )
    for polygon in polygons:
        if polygon.is_empty:
            continue
        bounds = polygon.bounds
        if min(float(bounds[2] - bounds[0]), float(bounds[3] - bounds[1])) < min_feature:
            report["unresolved_printability_warnings"].append(
                "Some retained vector components are still below the configured minimum feature width."
            )
            return


def _polygon_parts(geometry: Any) -> list[Any]:
    if geometry.is_empty:
        return []
    if geometry.geom_type == "Polygon":
        return [geometry] if geometry.area > 0 else []
    if geometry.geom_type in {"MultiPolygon", "GeometryCollection"}:
        parts = []
        for part in geometry.geoms:
            parts.extend(_polygon_parts(part))
        return parts
    return []


def _merge_counts(report: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if key == "component_records":
            report.setdefault("component_records", []).extend(value)
        elif isinstance(value, int):
            report[key] = int(report.get(key, 0)) + value


def _record_visual_warning_preview(
    report: dict[str, Any],
    output_path: Path | None,
    created: bool,
) -> None:
    if created and output_path is not None:
        report["visual_warning_preview_created"] = True
        report["visual_warning_preview_path"] = str(output_path)
        report["visual_warning_preview_paths"] = [str(output_path)]
    else:
        report["visual_warning_preview_created"] = False
        report["visual_warning_preview_path"] = ""
        report["visual_warning_preview_paths"] = []


def _change_preview_image(
    before_mask: np.ndarray,
    after_mask: np.ndarray,
    *,
    changed_mask: np.ndarray | None = None,
) -> Image.Image:
    before = np.asarray(before_mask, dtype=bool)
    after = np.asarray(after_mask, dtype=bool)
    rgb = np.zeros((*before.shape, 3), dtype=np.uint8)
    rgb[:, :] = (34, 36, 40)
    kept = before & after
    removed = before & ~after
    added = ~before & after
    rgb[kept] = (180, 184, 190)
    rgb[removed] = (232, 80, 72)
    rgb[added] = (70, 190, 230)
    if changed_mask is not None:
        rgb[np.asarray(changed_mask, dtype=bool)] = (245, 196, 74)
    image = Image.fromarray(rgb, mode="RGB")
    return _scale_preview_image(image)


def _scale_preview_image(image: Image.Image) -> Image.Image:
    width, height = image.size
    longest = max(width, height)
    if longest <= 0:
        return image
    if longest < 640:
        factor = int(np.ceil(640 / longest))
        return image.resize((width * factor, height * factor), Image.Resampling.NEAREST)
    if longest > 1400:
        scale = 1400 / float(longest)
        return image.resize((max(1, int(width * scale)), max(1, int(height * scale))), Image.Resampling.NEAREST)
    return image


def _polygon_change_masks(before_polygons: list[Any], after_polygons: list[Any]) -> tuple[np.ndarray, np.ndarray]:
    all_polygons = [*before_polygons, *after_polygons]
    min_x = min(float(polygon.bounds[0]) for polygon in all_polygons)
    min_y = min(float(polygon.bounds[1]) for polygon in all_polygons)
    max_x = max(float(polygon.bounds[2]) for polygon in all_polygons)
    max_y = max(float(polygon.bounds[3]) for polygon in all_polygons)
    width_mm = max(max_x - min_x, 1e-6)
    height_mm = max(max_y - min_y, 1e-6)
    longest = max(width_mm, height_mm)
    scale = min(12.0, 1200.0 / longest)
    padding = 8
    image_width = max(32, int(np.ceil(width_mm * scale)) + padding * 2)
    image_height = max(32, int(np.ceil(height_mm * scale)) + padding * 2)
    before_mask = _render_polygon_mask(before_polygons, min_x, min_y, max_y, scale, image_width, image_height, padding)
    after_mask = _render_polygon_mask(after_polygons, min_x, min_y, max_y, scale, image_width, image_height, padding)
    return before_mask, after_mask


def _render_polygon_mask(
    polygons: list[Any],
    min_x: float,
    min_y: float,
    max_y: float,
    scale: float,
    image_width: int,
    image_height: int,
    padding: int,
) -> np.ndarray:
    image = Image.new("L", (image_width, image_height), 0)
    draw = ImageDraw.Draw(image)

    def map_point(point: tuple[float, float]) -> tuple[int, int]:
        x = padding + int(round((float(point[0]) - min_x) * scale))
        y = padding + int(round((max_y - float(point[1])) * scale))
        return x, y

    for polygon in polygons:
        exterior = [map_point(point) for point in polygon.exterior.coords]
        if len(exterior) >= 3:
            draw.polygon(exterior, fill=255)
        for interior in polygon.interiors:
            hole = [map_point(point) for point in interior.coords]
            if len(hole) >= 3:
                draw.polygon(hole, fill=0)
    return np.asarray(image, dtype=np.uint8) > 127
