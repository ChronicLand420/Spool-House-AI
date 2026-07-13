from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import trimesh
from PIL import Image, ImageDraw

from spool_house_ai.config import FilamentSwapReliefConfig
from spool_house_ai.processing.filament_layers import calculate_filament_swap_plan
from spool_house_ai.processing.generic_3mf import GENERIC_3MF_NOTICE
from spool_house_ai.processing.islands import apply_island_policy
from spool_house_ai.processing.stl import StlCreationResult, export_generic_3mf_for_stl_mesh


FILAMENT_SWAP_BACKEND = "filament_swap_heightfield"


def create_filament_swap_relief_stl(
    image_path: Path,
    output_path: Path,
    config: FilamentSwapReliefConfig,
    *,
    preview_path: Path | None = None,
    cleaned_png_path: Path | None = None,
    silhouette_png_path: Path | None = None,
    color_preview_path: Path | None = None,
    height_preview_path: Path | None = None,
    contact_sheet_path: Path | None = None,
    island_detected_preview_path: Path | None = None,
    island_actions_preview_path: Path | None = None,
    island_preserved_preview_path: Path | None = None,
    island_removed_preview_path: Path | None = None,
    island_merged_preview_path: Path | None = None,
    island_connected_preview_path: Path | None = None,
    report_path: Path | None = None,
    color_plan_path: Path | None = None,
    filament_swap_plan_path: Path | None = None,
    generic_3mf_path: Path | None = None,
) -> tuple[StlCreationResult, dict[str, Any]]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rgb, alpha, load_metadata = _load_sampled_rgba(image_path, config.max_sampled_pixels)
    if config.smooth_edges and config.edge_smoothing_px > 0:
        kernel = max(3, int(config.edge_smoothing_px) * 2 + 1)
        rgb = cv2.medianBlur(rgb, kernel)

    valid_mask = alpha > 15
    labels, centers = _cluster_color_labels(rgb, valid_mask, config)
    background_label, background_confident, background_fraction = _detect_background_label(labels, valid_mask, config)
    printable_labels, ignored_label, warnings = _select_printable_labels(
        labels,
        centers,
        valid_mask,
        config,
        background_label,
        background_confident,
        background_fraction,
    )
    island_result = apply_island_policy(
        labels,
        printable_labels,
        min_region_area_px=int(config.min_region_area_px),
        island_policy=config.island_policy,
        merge_max_distance_px=int(config.island_merge_max_distance_px),
        merge_fallback=config.island_merge_fallback,
        connect_max_gap_px=int(config.island_connect_max_gap_px),
        connection_width_px=int(config.island_connection_width_px),
        connect_fallback=config.island_connect_fallback,
    )
    warnings.extend(island_result.warnings)
    source_metadata = {
        "filename": image_path.name,
        "path": str(image_path),
        "format": image_path.suffix.lower().lstrip("."),
        "original_width_px": int(load_metadata["original_width_px"]),
        "original_height_px": int(load_metadata["original_height_px"]),
        "sampled_width_px": int(labels.shape[1]),
        "sampled_height_px": int(labels.shape[0]),
    }
    height_map, color_rows, color_plan = _height_map_for_labels(
        island_result.labels,
        centers,
        printable_labels,
        config,
        source_metadata=source_metadata,
    )
    if color_plan.get("snapping_occurred"):
        warnings.append(
            "Filament swap heights were aligned to the configured first-layer/normal-layer schedule."
        )
    warnings.extend(color_plan.get("warnings") or [])
    printable_mask = height_map > 0
    if not np.any(printable_mask):
        raise ValueError("Filament Swap Relief did not find printable color regions.")

    mesh, bounds_metadata = _mesh_from_height_map(height_map, config.width_mm)
    mesh.export(output_path)
    generic_3mf_metadata = _export_generic_3mf_metadata(
        mesh,
        generic_3mf_path,
        title=image_path.stem,
    )
    if generic_3mf_metadata["generic_3mf_enabled"] and not generic_3mf_metadata["generic_3mf_created"]:
        warning = "Generic 3MF export failed: " + "; ".join(generic_3mf_metadata["generic_3mf_validation_errors"])
        warnings.append(warning)

    ignored_color = _rgb_tuple(centers[ignored_label]) if ignored_label is not None else None
    metadata: dict[str, Any] = {
        "product": "Filament Swap Relief",
        "product_mode": "filament_swap_relief",
        "backend": FILAMENT_SWAP_BACKEND,
        "input_image_path": str(image_path),
        "stl_path": str(output_path),
        "width_mm": round(float(config.width_mm), 4),
        "height_mm": bounds_metadata["height_mm"],
        "base_height_mm": round(float(config.base_height_mm), 4),
        "layer_step_mm": round(float(config.layer_step_mm), 4),
        "first_layer_height_mm": round(float(config.first_layer_height_mm), 4),
        "layer_height_mm": round(float(config.layer_height_mm), 4),
        "height_alignment_mode": config.height_alignment_mode,
        "height_alignment_tolerance_mm": round(float(config.height_alignment_tolerance_mm), 4),
        "color_count_requested": int(config.color_count),
        "color_count_kept": len(color_rows),
        "color_order": config.color_order,
        "palette_color_space": config.palette_color_space,
        "palette_random_seed": int(config.palette_random_seed),
        "clustering_seed": int(config.palette_random_seed),
        "determinism_method": (
            "Exact unique RGB colors when possible; otherwise OpenCV k-means with cv2.setRNGSeed, "
            "fixed criteria, and fixed attempt count."
        ),
        "auto_background_ignore": bool(config.auto_background_ignore),
        "ignored_background_color_rgb": list(ignored_color) if ignored_color else None,
        "ignored_background_color_hex": _rgb_hex(ignored_color) if ignored_color else "",
        "detected_background_label": int(background_label) if background_label is not None else None,
        "background_confidence_threshold": round(float(config.background_confidence_threshold), 4),
        "background_detection_confident": bool(background_confident),
        "background_border_fraction": round(float(background_fraction), 4),
        "background_ignored": ignored_label is not None,
        "background_decision_reason": _background_decision_reason(config, background_label, background_confident, background_fraction, ignored_label),
        "detected_colors": color_rows,
        "color_plan": color_plan,
        "swap_plan_summary": _swap_plan_summary(color_plan),
        "final_height_mm": round(float(np.max(height_map)), 4),
        "final_top_layer": color_plan["final_top_layer"],
        "total_printed_layers": color_plan["total_printed_layers"],
        "snapping_occurred": color_plan["snapping_occurred"],
        "sampled_width_px": int(height_map.shape[1]),
        "sampled_height_px": int(height_map.shape[0]),
        "downscaled": bool(load_metadata["source_downscaled"]),
        "source_downscaled": bool(load_metadata["source_downscaled"]),
        "max_sampled_pixels": int(config.max_sampled_pixels),
        "min_region_area_px": int(config.min_region_area_px),
        "smooth_edges": bool(config.smooth_edges),
        "edge_smoothing_px": int(config.edge_smoothing_px),
        "island_summary": island_result.summary,
        "island_component_counts_by_color": island_result.per_color_component_counts,
        "component_actions": island_result.component_actions if config.island_report_components else [],
        "component_reporting_enabled": bool(config.island_report_components),
        "removed_region_count": island_result.summary["removed_components"],
        "removed_pixel_count": island_result.summary["pixels_removed"],
        "preserved_region_count": island_result.summary["intentionally_preserved_components"],
        "warnings": warnings,
    }
    metadata.update(load_metadata)
    metadata.update(bounds_metadata)
    metadata.update(generic_3mf_metadata)

    if load_metadata["source_downscaled"]:
        warnings.append(
            "Filament Swap Relief image was downscaled to "
            f"{height_map.shape[1]}x{height_map.shape[0]} pixels for mesh safety."
        )
    if len(color_rows) < int(config.color_count):
        warnings.append(
            f"Only {len(color_rows)} printable color groups were detected from requested {int(config.color_count)}."
        )

    cluster_image = _cluster_preview(labels, centers, printable_labels, ignored_label, valid_mask)
    height_image = _height_preview(height_map)
    if cleaned_png_path is not None:
        _save_image(cluster_image, cleaned_png_path)
    if silhouette_png_path is not None:
        _save_image(height_image, silhouette_png_path)
    if color_preview_path is not None:
        _save_image(cluster_image, color_preview_path)
        metadata["color_preview_path"] = str(color_preview_path)
    if height_preview_path is not None:
        _save_image(height_image, height_preview_path)
        metadata["height_preview_path"] = str(height_preview_path)
    if contact_sheet_path is not None:
        _save_contact_sheet(image_path, cluster_image, height_image, color_rows, contact_sheet_path)
        metadata["contact_sheet_path"] = str(contact_sheet_path)
    if preview_path is not None:
        _save_contact_sheet(image_path, cluster_image, height_image, color_rows, preview_path)
        metadata["preview_path"] = str(preview_path)
    _save_island_previews(
        island_result.action_masks,
        metadata,
        detected_path=island_detected_preview_path,
        actions_path=island_actions_preview_path,
        preserved_path=island_preserved_preview_path,
        removed_path=island_removed_preview_path,
        merged_path=island_merged_preview_path,
        connected_path=island_connected_preview_path,
    )
    if color_plan_path is not None:
        color_plan_path.parent.mkdir(parents=True, exist_ok=True)
        metadata["color_plan_path"] = str(color_plan_path)
        color_plan["color_plan_path"] = str(color_plan_path)
        color_plan_path.write_text(json.dumps(color_plan, indent=2) + "\n", encoding="utf-8")
    if filament_swap_plan_path is not None:
        filament_swap_plan_path.parent.mkdir(parents=True, exist_ok=True)
        metadata["filament_swap_plan_path"] = str(filament_swap_plan_path)
        filament_swap_plan_path.write_text(_format_filament_swap_plan(color_plan), encoding="utf-8")
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        metadata["filament_swap_report_path"] = str(report_path)
        report_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")

    return (
        StlCreationResult(
            requested_backend=FILAMENT_SWAP_BACKEND,
            actual_backend=FILAMENT_SWAP_BACKEND,
            fallback_used=False,
            fallback_reason="",
            generic_3mf_metadata=generic_3mf_metadata,
        ),
        metadata,
    )


def _load_sampled_rgba(image_path: Path, max_pixels: int) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    image = Image.open(image_path).convert("RGBA")
    original_width, original_height = image.size
    sampled_width, sampled_height = original_width, original_height
    source_downscaled = False
    current_pixels = original_width * original_height
    if max_pixels > 0 and current_pixels > max_pixels:
        scale = (max_pixels / current_pixels) ** 0.5
        sampled_width = max(2, int(original_width * scale))
        sampled_height = max(2, int(original_height * scale))
        image = image.resize((sampled_width, sampled_height), Image.Resampling.LANCZOS)
        source_downscaled = True
    rgba = np.asarray(image, dtype=np.uint8)
    return rgba[:, :, :3], rgba[:, :, 3], {
        "original_width_px": original_width,
        "original_height_px": original_height,
        "sampled_pixel_count": sampled_width * sampled_height,
        "source_downscaled": source_downscaled,
    }


def _export_generic_3mf_metadata(
    mesh: trimesh.Trimesh,
    output_path: Path | None,
    *,
    title: str,
) -> dict[str, Any]:
    return export_generic_3mf_for_stl_mesh(
        mesh,
        output_path,
        title=title,
        description=f"Filament Swap Relief generic 3MF export. {GENERIC_3MF_NOTICE}",
    )


def _swap_plan_summary(color_plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "layer_numbering": color_plan.get("layer_numbering", "one_based"),
        "swap_convention": color_plan.get("swap_convention", ""),
        "layer_settings": color_plan.get("layer_settings", {}),
        "height_settings": color_plan.get("height_settings", {}),
        "total_requested_thickness_mm": color_plan.get("total_requested_thickness_mm"),
        "total_aligned_thickness_mm": color_plan.get("total_aligned_thickness_mm"),
        "total_printed_layers": color_plan.get("total_printed_layers"),
        "final_top_layer": color_plan.get("final_top_layer"),
        "snapping_occurred": color_plan.get("snapping_occurred", False),
        "warnings": color_plan.get("warnings", []),
        "colors": [
            {
                "order": color.get("order"),
                "hex": color.get("hex"),
                "suggested_color_name": color.get("suggested_color_name", ""),
                "requested_start_z_mm": color.get("requested_start_z_mm"),
                "aligned_start_z_mm": color.get("aligned_start_z_mm"),
                "requested_top_z_mm": color.get("requested_top_z_mm"),
                "aligned_top_z_mm": color.get("aligned_top_z_mm"),
                "first_layer_using_color": color.get("first_layer_using_color"),
                "last_layer_using_color": color.get("last_layer_using_color"),
                "layer_count": color.get("layer_count"),
                "change_before_layer": color.get("change_before_layer"),
                "previous_filament_last_layer": color.get("previous_filament_last_layer"),
                "warnings": color.get("warnings", []),
            }
            for color in color_plan.get("colors", [])
        ],
    }


def _format_filament_swap_plan(color_plan: dict[str, Any]) -> str:
    layer_settings = color_plan.get("layer_settings", {})
    height_settings = color_plan.get("height_settings", {})
    lines = [
        "FILAMENT RELIEF MANUAL SWAP PLAN",
        "",
        "Layer numbering:",
        "Layers are one-based.",
        "",
        "Swap convention:",
        "\"Change before layer N\" means finish layer N-1, pause the printer, load the next filament, and begin layer N with the new filament.",
        "",
        "Layer settings:",
        f"- First layer height: {layer_settings.get('first_layer_height_mm', '')} mm",
        f"- Normal layer height: {layer_settings.get('layer_height_mm', '')} mm",
        f"- Alignment mode: {layer_settings.get('height_alignment_mode', '')}",
        "",
        "Height settings:",
        f"- Requested base height: {height_settings.get('requested_base_height_mm', '')} mm",
        f"- Requested step height: {height_settings.get('requested_step_height_mm', '')} mm",
        f"- Aligned total thickness: {color_plan.get('total_aligned_thickness_mm', '')} mm",
        "",
    ]
    colors = color_plan.get("colors", [])
    if colors:
        first = colors[0]
        lines.extend(
            [
                "START WITH",
                f"Color: {first.get('hex', '') or first.get('suggested_color_name', 'color')}",
                f"Layers: {first.get('first_layer_using_color', '')} through {first.get('last_layer_using_color', '')}",
                f"Top Z: {first.get('aligned_top_z_mm', '')} mm",
                "",
            ]
        )
        for color in colors[1:]:
            change_layer = color.get("change_before_layer")
            previous_layer = color.get("previous_filament_last_layer")
            lines.extend(
                [
                    f"CHANGE BEFORE LAYER {change_layer}",
                    f"Transition Z: {color.get('aligned_start_z_mm', '')} mm",
                    f"Load: {color.get('hex', '') or color.get('suggested_color_name', 'color')}",
                    f"Previous filament last used on layer {previous_layer}",
                    f"New filament layers: {color.get('first_layer_using_color', '')} through {color.get('last_layer_using_color', '')}",
                    "",
                ]
            )
    lines.extend(
        [
            f"Total layer count: {color_plan.get('total_printed_layers', '')}",
            f"Final model thickness: {color_plan.get('total_aligned_thickness_mm', '')} mm",
            f"Snapping occurred: {color_plan.get('snapping_occurred', False)}",
            "",
            "Warnings:",
        ]
    )
    warnings = list(color_plan.get("warnings") or [])
    for color in color_plan.get("colors", []):
        for warning in color.get("warnings") or []:
            warnings.append(f"{color.get('hex', 'color')}: {warning}")
    if warnings:
        lines.extend(f"- {warning}" for warning in warnings)
    else:
        lines.append("- None")
    return "\n".join(lines) + "\n"


def _cluster_color_labels(
    rgb: np.ndarray,
    valid_mask: np.ndarray,
    config: FilamentSwapReliefConfig,
) -> tuple[np.ndarray, np.ndarray]:
    pixels = rgb[valid_mask]
    if pixels.size == 0:
        raise ValueError("Filament Swap Relief input has no visible pixels.")
    requested_clusters = max(1, int(config.color_count) + (1 if config.auto_background_ignore else 0))
    unique_colors, inverse = np.unique(pixels.reshape(-1, 3), axis=0, return_inverse=True)
    if len(unique_colors) <= requested_clusters:
        labels_flat = inverse.astype(np.int32)
        centers = unique_colors.astype(np.float32)
    else:
        k = min(requested_clusters, len(pixels))
        cluster_pixels = _palette_pixels(pixels, config.palette_color_space)
        criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 35, 1.0)
        cv2.setRNGSeed(int(config.palette_random_seed))
        _compactness, labels_cv, centers = cv2.kmeans(
            cluster_pixels,
            k,
            None,
            criteria,
            4,
            cv2.KMEANS_PP_CENTERS,
        )
        labels_flat = labels_cv.reshape(-1).astype(np.int32)
        centers = _rgb_centers_for_labels(pixels, labels_flat, k)
    labels = np.full(valid_mask.shape, -1, dtype=np.int32)
    labels[valid_mask] = labels_flat
    return labels, centers.astype(np.float32)


def _palette_pixels(pixels: np.ndarray, color_space: str) -> np.ndarray:
    if color_space == "lab":
        lab = cv2.cvtColor(pixels.reshape(1, -1, 3).astype(np.uint8), cv2.COLOR_RGB2LAB)
        return lab.reshape(-1, 3).astype(np.float32)
    return pixels.astype(np.float32)


def _rgb_centers_for_labels(pixels: np.ndarray, labels_flat: np.ndarray, count: int) -> np.ndarray:
    centers = np.zeros((count, 3), dtype=np.float32)
    for label in range(count):
        selected = pixels[labels_flat == label]
        if selected.size:
            centers[label] = np.mean(selected.astype(np.float32), axis=0)
    return centers


def _detect_background_label(
    labels: np.ndarray,
    valid_mask: np.ndarray,
    config: FilamentSwapReliefConfig,
) -> tuple[int | None, bool, float]:
    if not config.auto_background_ignore:
        return None, False, 0.0
    height, width = labels.shape
    border = max(1, min(int(config.background_border_sample_px), max(1, height // 2), max(1, width // 2)))
    border_mask = np.zeros(labels.shape, dtype=bool)
    border_mask[:border, :] = True
    border_mask[-border:, :] = True
    border_mask[:, :border] = True
    border_mask[:, -border:] = True
    sampled = labels[border_mask & valid_mask]
    sampled = sampled[sampled >= 0]
    if sampled.size == 0:
        return None, False, 0.0
    counts = np.bincount(sampled)
    label = int(np.argmax(counts))
    fraction = float(counts[label] / sampled.size)
    return label, fraction >= float(config.background_confidence_threshold), fraction


def _select_printable_labels(
    labels: np.ndarray,
    centers: np.ndarray,
    valid_mask: np.ndarray,
    config: FilamentSwapReliefConfig,
    background_label: int | None,
    background_confident: bool,
    background_fraction: float,
) -> tuple[list[int], int | None, list[str]]:
    warnings: list[str] = []
    counts = _label_counts(labels, valid_mask)
    ignored_label: int | None = None
    candidates = list(counts)
    if config.auto_background_ignore and background_label is not None and background_confident and len(candidates) > 1:
        ignored_label = background_label
        candidates = [label for label in candidates if label != ignored_label]
    elif config.auto_background_ignore and background_label is not None:
        warnings.append(
            "Background detection was uncertain; no color was ignored "
            f"(border fraction {background_fraction:.2f})."
        )
    candidates.sort(key=lambda label: counts[label], reverse=True)
    selected = candidates[: max(1, int(config.color_count))]
    selected.sort(key=lambda label: _luminance(centers[label]), reverse=config.color_order == "light_to_dark")
    return selected, ignored_label, warnings


def _height_map_for_labels(
    labels: np.ndarray,
    centers: np.ndarray,
    printable_labels: list[int],
    config: FilamentSwapReliefConfig,
    *,
    source_metadata: dict[str, Any],
) -> tuple[np.ndarray, list[dict[str, Any]], dict[str, Any]]:
    height_map = np.zeros(labels.shape, dtype=np.float32)
    printable_pixels_total = int(sum(np.count_nonzero(labels == label) for label in printable_labels))
    raw_color_rows: list[dict[str, Any]] = []
    active_index = 0

    for label in printable_labels:
        mask = labels == label
        pixel_count = int(np.count_nonzero(mask))
        if pixel_count == 0:
            continue
        active_index += 1
        rgb = _rgb_tuple(centers[label])
        luminance = _luminance(centers[label])
        raw_color_rows.append(
            {
                "index": active_index,
                "cluster_label": int(label),
                "rgb": list(rgb),
                "hex": _rgb_hex(rgb),
                "suggested_color_name": _suggest_color_name(rgb),
                "luminance": round(float(luminance), 4),
                "pixel_count": pixel_count,
                "area_percent": round((pixel_count / max(1, printable_pixels_total)) * 100.0, 4),
            }
        )
    color_plan = calculate_filament_swap_plan(
        raw_color_rows,
        base_height_mm=config.base_height_mm,
        layer_step_mm=config.layer_step_mm,
        first_layer_height_mm=config.first_layer_height_mm,
        layer_height_mm=config.layer_height_mm,
        height_alignment_mode=config.height_alignment_mode,
        height_alignment_tolerance_mm=config.height_alignment_tolerance_mm,
        source=source_metadata,
        palette_order=config.color_order,
    )
    color_rows = color_plan["colors"]
    for row in color_rows:
        mask = labels == int(row["cluster_label"])
        height_map[mask] = float(row["aligned_top_z_mm"])
    return height_map, color_rows, color_plan


def _mesh_from_height_map(height_map: np.ndarray, width_mm: float) -> tuple[trimesh.Trimesh, dict[str, Any]]:
    height_px, width_px = height_map.shape
    if height_px < 1 or width_px < 1:
        raise ValueError("Filament Swap Relief height map did not contain pixels.")
    if width_mm <= 0:
        raise ValueError("Filament Swap Relief width must be greater than zero.")
    if not np.any(height_map > 0):
        raise ValueError("Filament Swap Relief height map has no printable pixels.")

    height_mm = width_mm * (height_px / width_px)
    scale_x = width_mm / width_px
    scale_y = height_mm / height_px
    vertices: list[tuple[float, float, float]] = []
    vertex_lookup: dict[tuple[float, float, float], int] = {}
    faces: list[tuple[int, int, int]] = []
    height_levels = [0.0] + sorted(float(value) for value in np.unique(height_map[height_map > 0]))

    def vertex(x: float, y: float, z: float) -> int:
        key = (round(float(x), 6), round(float(y), 6), round(float(z), 6))
        existing = vertex_lookup.get(key)
        if existing is not None:
            return existing
        vertex_lookup[key] = len(vertices)
        vertices.append(key)
        return len(vertices) - 1

    def add_quad(a, b, c, d) -> None:
        faces.append((vertex(*a), vertex(*d), vertex(*c)))
        faces.append((vertex(*a), vertex(*c), vertex(*b)))

    def add_wall(x0: float, y0: float, x1: float, y1: float, low: float, high: float) -> None:
        if high <= low:
            return
        split_levels = [level for level in height_levels if low <= level <= high]
        if split_levels[0] > low:
            split_levels.insert(0, low)
        if split_levels[-1] < high:
            split_levels.append(high)
        for lower, upper in zip(split_levels, split_levels[1:]):
            if upper > lower:
                add_quad((x0, y0, upper), (x1, y1, upper), (x1, y1, lower), (x0, y0, lower))

    for y in range(height_px):
        for x in range(width_px):
            top_z = float(height_map[y, x])
            if top_z <= 0:
                continue
            x0 = x * scale_x
            x1 = (x + 1) * scale_x
            y0 = height_mm - (y * scale_y)
            y1 = height_mm - ((y + 1) * scale_y)
            add_quad((x0, y0, top_z), (x1, y0, top_z), (x1, y1, top_z), (x0, y1, top_z))
            add_quad((x0, y0, 0.0), (x0, y1, 0.0), (x1, y1, 0.0), (x1, y0, 0.0))

            north = float(height_map[y - 1, x]) if y > 0 else 0.0
            south = float(height_map[y + 1, x]) if y < height_px - 1 else 0.0
            west = float(height_map[y, x - 1]) if x > 0 else 0.0
            east = float(height_map[y, x + 1]) if x < width_px - 1 else 0.0
            if north < top_z:
                add_wall(x0, y0, x1, y0, north, top_z)
            if south < top_z:
                add_wall(x1, y1, x0, y1, south, top_z)
            if west < top_z:
                add_wall(x0, y1, x0, y0, west, top_z)
            if east < top_z:
                add_wall(x1, y0, x1, y1, east, top_z)

    mesh = trimesh.Trimesh(vertices=np.array(vertices), faces=np.array(faces), process=True)
    mesh.fix_normals()
    return mesh, {
        "height_mm": round(float(height_mm), 4),
        "scale_x_mm_per_pixel": round(float(scale_x), 6),
        "scale_y_mm_per_pixel": round(float(scale_y), 6),
    }


def _cluster_preview(
    labels: np.ndarray,
    centers: np.ndarray,
    printable_labels: list[int],
    ignored_label: int | None,
    valid_mask: np.ndarray,
) -> Image.Image:
    image = np.zeros((*labels.shape, 3), dtype=np.uint8)
    image[:] = (26, 28, 34)
    for label in printable_labels:
        image[labels == label] = np.asarray(_rgb_tuple(centers[label]), dtype=np.uint8)
    if ignored_label is not None:
        image[labels == ignored_label] = (72, 76, 86)
    image[~valid_mask] = (20, 20, 24)
    return Image.fromarray(image, mode="RGB")


def _height_preview(height_map: np.ndarray) -> Image.Image:
    if not np.any(height_map > 0):
        preview = np.zeros(height_map.shape, dtype=np.uint8)
    else:
        nonzero = height_map[height_map > 0]
        min_height = float(np.min(nonzero))
        max_height = float(np.max(nonzero))
        if max_height <= min_height:
            normalized = np.where(height_map > 0, 180, 0).astype(np.uint8)
        else:
            normalized = np.where(
                height_map > 0,
                80 + ((height_map - min_height) / (max_height - min_height) * 175.0),
                0,
            ).astype(np.uint8)
        preview = normalized
    return Image.fromarray(preview, mode="L").convert("RGB")


def _background_decision_reason(
    config: FilamentSwapReliefConfig,
    background_label: int | None,
    background_confident: bool,
    background_fraction: float,
    ignored_label: int | None,
) -> str:
    if not config.auto_background_ignore:
        return "auto background ignore disabled"
    if background_label is None:
        return "no visible border pixels were available for background detection"
    if ignored_label is not None:
        return (
            f"border label {background_label} confidence {background_fraction:.2f} met "
            f"threshold {float(config.background_confidence_threshold):.2f}"
        )
    if not background_confident:
        return (
            f"border label {background_label} confidence {background_fraction:.2f} was below "
            f"threshold {float(config.background_confidence_threshold):.2f}"
        )
    return "background label was not ignored because no alternate printable colors were available"


def _save_island_previews(
    action_masks: dict[str, np.ndarray],
    metadata: dict[str, Any],
    *,
    detected_path: Path | None,
    actions_path: Path | None,
    preserved_path: Path | None,
    removed_path: Path | None,
    merged_path: Path | None,
    connected_path: Path | None,
) -> None:
    legend = {
        "normal_kept": "#7A7F8C",
        "preserved": "#22C55E",
        "removed": "#EF4444",
        "merged": "#3B82F6",
        "connected": "#F59E0B",
        "connector": "#06B6D4",
    }
    metadata["island_preview_legend"] = legend
    preview_paths: dict[str, str] = {}

    if detected_path is not None and np.any(action_masks["detected"]):
        _save_mask_preview(action_masks["detected"], detected_path, (168, 176, 190))
        preview_paths["detected"] = str(detected_path)
    if actions_path is not None and _has_any_action(action_masks):
        _save_image(_combined_island_action_preview(action_masks), actions_path)
        preview_paths["actions"] = str(actions_path)
    if preserved_path is not None and np.any(action_masks["preserved"]):
        _save_mask_preview(action_masks["preserved"], preserved_path, (34, 197, 94))
        preview_paths["preserved"] = str(preserved_path)
    if removed_path is not None and np.any(action_masks["removed"]):
        _save_mask_preview(action_masks["removed"], removed_path, (239, 68, 68))
        preview_paths["removed"] = str(removed_path)
    if merged_path is not None and np.any(action_masks["merged"]):
        _save_mask_preview(action_masks["merged"], merged_path, (59, 130, 246))
        preview_paths["merged"] = str(merged_path)
    if connected_path is not None and np.any(action_masks["connected"]):
        image = _mask_image(action_masks["connected"], (245, 158, 11))
        image_np = np.asarray(image).copy()
        image_np[action_masks["connectors"]] = np.asarray((6, 182, 212), dtype=np.uint8)
        _save_image(Image.fromarray(image_np, mode="RGB"), connected_path)
        preview_paths["connected"] = str(connected_path)

    metadata["island_preview_paths"] = preview_paths


def _has_any_action(action_masks: dict[str, np.ndarray]) -> bool:
    for key in ("preserved", "removed", "merged", "connected", "connectors"):
        if np.any(action_masks[key]):
            return True
    return False


def _combined_island_action_preview(action_masks: dict[str, np.ndarray]) -> Image.Image:
    image = np.zeros((*action_masks["detected"].shape, 3), dtype=np.uint8)
    image[:] = (18, 20, 26)
    image[action_masks["detected"]] = (122, 127, 140)
    image[action_masks["preserved"]] = (34, 197, 94)
    image[action_masks["removed"]] = (239, 68, 68)
    image[action_masks["merged"]] = (59, 130, 246)
    image[action_masks["connected"]] = (245, 158, 11)
    image[action_masks["connectors"]] = (6, 182, 212)
    return Image.fromarray(image, mode="RGB")


def _save_mask_preview(mask: np.ndarray, output_path: Path, color: tuple[int, int, int]) -> None:
    _save_image(_mask_image(mask, color), output_path)


def _mask_image(mask: np.ndarray, color: tuple[int, int, int]) -> Image.Image:
    image = np.zeros((*mask.shape, 3), dtype=np.uint8)
    image[:] = (18, 20, 26)
    image[mask] = np.asarray(color, dtype=np.uint8)
    return Image.fromarray(image, mode="RGB")


def _save_contact_sheet(
    image_path: Path,
    cluster_image: Image.Image,
    height_image: Image.Image,
    color_rows: list[dict[str, Any]],
    output_path: Path,
) -> None:
    source = Image.open(image_path).convert("RGB")
    tile_size = (260, 190)
    panels = [
        ("Source", source),
        ("Color groups", cluster_image),
        ("Height map", height_image),
    ]
    width = tile_size[0] * 3
    height = tile_size[1] + 150
    sheet = Image.new("RGB", (width, height), (15, 17, 22))
    draw = ImageDraw.Draw(sheet)
    for index, (title, image) in enumerate(panels):
        thumb = image.copy()
        thumb.thumbnail((tile_size[0] - 20, tile_size[1] - 38), Image.Resampling.LANCZOS)
        x = index * tile_size[0] + (tile_size[0] - thumb.width) // 2
        y = 32 + (tile_size[1] - 38 - thumb.height) // 2
        draw.text((index * tile_size[0] + 12, 10), title, fill=(232, 236, 244))
        sheet.paste(thumb, (x, y))
    y = tile_size[1] + 12
    draw.text((12, y), "Filament Swap Plan", fill=(232, 236, 244))
    y += 24
    for row in color_rows[:5]:
        if row["index"] == 1:
            text = (
                f"Start: {row['hex']} / {row['suggested_color_name']} "
                f"layers {row['first_layer_using_color']}-{row['last_layer_using_color']}"
            )
        else:
            text = (
                f"Change before layer {row['change_before_layer']}: {row['hex']} "
                f"at {row['aligned_start_z_mm']:.2f} mm"
            )
        draw.text((12, y), text, fill=(196, 203, 214))
        y += 22
    _save_image(sheet, output_path)


def _save_image(image: Image.Image, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)


def _label_counts(labels: np.ndarray, valid_mask: np.ndarray) -> dict[int, int]:
    sampled = labels[valid_mask]
    sampled = sampled[sampled >= 0]
    counts = np.bincount(sampled) if sampled.size else np.array([], dtype=np.int64)
    return {int(index): int(count) for index, count in enumerate(counts) if count > 0}


def _rgb_tuple(color: np.ndarray | tuple[int, int, int]) -> tuple[int, int, int]:
    return tuple(int(np.clip(round(float(value)), 0, 255)) for value in color[:3])  # type: ignore[index]


def _rgb_hex(color: tuple[int, int, int] | None) -> str:
    if color is None:
        return ""
    return "#{:02X}{:02X}{:02X}".format(*color)


def _luminance(color: np.ndarray) -> float:
    rgb = np.asarray(color[:3], dtype=np.float32)
    return float((0.2126 * rgb[0]) + (0.7152 * rgb[1]) + (0.0722 * rgb[2]))


def _suggest_color_name(rgb: tuple[int, int, int]) -> str:
    red, green, blue = rgb
    maximum = max(rgb)
    minimum = min(rgb)
    luminance = _luminance(np.asarray(rgb, dtype=np.float32))
    if maximum - minimum < 24:
        if luminance > 225:
            return "white"
        if luminance < 45:
            return "black"
        return "gray"
    if red >= maximum and red - max(green, blue) > 45:
        return "red"
    if green >= maximum and green - max(red, blue) > 45:
        return "green"
    if blue >= maximum and blue - max(red, green) > 45:
        return "blue"
    return "custom color"
