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
from spool_house_ai.processing.geometry import smooth_contour_points
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
    labels, centers, color_merge_metadata = _merge_similar_color_labels(labels, centers, valid_mask, config)
    warnings: list[str] = []
    if color_merge_metadata["merge_count"]:
        warnings.append(
            f"Merged {color_merge_metadata['merge_count']} similar color shade cluster(s) before height assignment."
        )
    background_label, background_confident, background_fraction = _detect_background_label(labels, valid_mask, config)
    printable_labels, ignored_label, selection_warnings = _select_printable_labels(
        labels,
        centers,
        valid_mask,
        config,
        background_label,
        background_confident,
        background_fraction,
    )
    warnings.extend(selection_warnings)
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
    height_map, topology_repair_pixels = _resolve_height_level_diagonal_contacts(height_map)
    if topology_repair_pixels:
        warnings.append(
            "Filament Swap Relief added local bridge pixels to remove non-manifold diagonal height contacts "
            f"({topology_repair_pixels} pixels)."
        )
    if color_plan.get("snapping_occurred"):
        warnings.append(
            "Filament swap heights were aligned to the configured first-layer/normal-layer schedule."
        )
    warnings.extend(color_plan.get("warnings") or [])
    printable_mask = height_map > 0
    if not np.any(printable_mask):
        raise ValueError("Filament Swap Relief did not find printable color regions.")

    mesh, bounds_metadata = _mesh_from_height_map(height_map, config.width_mm, config)
    if bounds_metadata.get("mesh_generation_warning"):
        warnings.append(str(bounds_metadata["mesh_generation_warning"]))
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
        "min_model_thickness_mm": round(float(config.min_model_thickness_mm), 4),
        "first_layer_height_mm": round(float(config.first_layer_height_mm), 4),
        "layer_height_mm": round(float(config.layer_height_mm), 4),
        "height_alignment_mode": config.height_alignment_mode,
        "height_alignment_tolerance_mm": round(float(config.height_alignment_tolerance_mm), 4),
        "color_count_requested": int(config.color_count),
        "color_count_kept": len(color_rows),
        "color_order": config.color_order,
        "relief_style": config.relief_style,
        "palette_color_space": config.palette_color_space,
        "palette_random_seed": int(config.palette_random_seed),
        "merge_similar_colors": bool(config.merge_similar_colors),
        "similar_color_hue_tolerance_degrees": round(float(config.similar_color_hue_tolerance_degrees), 4),
        "similar_color_max_area_ratio": round(float(config.similar_color_max_area_ratio), 4),
        "solid_base_enabled": bool(config.solid_base_enabled),
        "similar_color_merge_count": color_merge_metadata["merge_count"],
        "similar_color_merges": color_merge_metadata["merges"],
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
        "heightfield_topology_repair_pixels": int(topology_repair_pixels),
        "mesh_style": config.mesh_style,
        "mesh_generation_mode": bounds_metadata.get("mesh_generation_mode", "pixel_heightfield"),
        "mesh_generation_warning": bounds_metadata.get("mesh_generation_warning", ""),
        "vector_contour_count": bounds_metadata.get("vector_contour_count", 0),
        "vector_polygon_count": bounds_metadata.get("vector_polygon_count", 0),
        "contour_simplify_tolerance_px": round(float(config.contour_simplify_tolerance_px), 4),
        "contour_smoothing_enabled": bool(config.contour_smoothing_enabled),
        "contour_smoothing_strength": int(config.contour_smoothing_strength),
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


def _merge_similar_color_labels(
    labels: np.ndarray,
    centers: np.ndarray,
    valid_mask: np.ndarray,
    config: FilamentSwapReliefConfig,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    metadata: dict[str, Any] = {"merge_count": 0, "merges": []}
    if not config.merge_similar_colors:
        return labels, centers, metadata

    counts = _label_counts(labels, valid_mask)
    active_labels = sorted(counts)
    if len(active_labels) <= 1:
        return labels, centers, metadata

    mutable_labels = labels.copy()
    mutable_centers = centers.astype(np.float32).copy()
    mutable_counts = dict(counts)
    for source_label in sorted(active_labels, key=lambda label: (mutable_counts.get(label, 0), label)):
        source_count = mutable_counts.get(source_label, 0)
        if source_count <= 0:
            continue
        source_hue, source_saturation = _hue_saturation(mutable_centers[source_label])
        if source_saturation < 0.20:
            continue
        candidates = []
        for destination_label in sorted(active_labels):
            if destination_label == source_label:
                continue
            destination_count = mutable_counts.get(destination_label, 0)
            if destination_count <= source_count:
                continue
            destination_hue, destination_saturation = _hue_saturation(mutable_centers[destination_label])
            if destination_saturation < 0.20:
                continue
            source_name = _suggest_color_name(_rgb_tuple(mutable_centers[source_label]))
            destination_name = _suggest_color_name(_rgb_tuple(mutable_centers[destination_label]))
            same_named_family = source_name == destination_name and source_name != "custom color"
            hue_distance = _hue_distance_degrees(source_hue, destination_hue)
            area_ratio = source_count / max(1, destination_count)
            if (
                (same_named_family or hue_distance <= float(config.similar_color_hue_tolerance_degrees))
                and area_ratio <= float(config.similar_color_max_area_ratio)
            ):
                rgb_distance = float(np.linalg.norm(mutable_centers[source_label] - mutable_centers[destination_label]))
                candidates.append((not same_named_family, rgb_distance, hue_distance, -destination_count, destination_label, area_ratio))
        if not candidates:
            continue
        _not_same_named_family, rgb_distance, hue_distance, _negative_count, destination_label, area_ratio = min(candidates)
        destination_count = mutable_counts[destination_label]
        merged_count = source_count + destination_count
        mutable_centers[destination_label] = (
            (mutable_centers[destination_label] * destination_count + mutable_centers[source_label] * source_count)
            / max(1, merged_count)
        )
        mutable_counts[destination_label] = merged_count
        mutable_counts[source_label] = 0
        mutable_labels[mutable_labels == source_label] = destination_label
        metadata["merges"].append(
            {
                "source_label": int(source_label),
                "destination_label": int(destination_label),
                "source_hex": _rgb_hex(_rgb_tuple(centers[source_label])),
                "destination_hex_before": _rgb_hex(_rgb_tuple(centers[destination_label])),
                "destination_hex_after": _rgb_hex(_rgb_tuple(mutable_centers[destination_label])),
                "source_pixel_count": int(source_count),
                "destination_pixel_count_before": int(destination_count),
                "hue_distance_degrees": round(float(hue_distance), 4),
                "rgb_distance": round(float(rgb_distance), 4),
                "reason": "same named color family" if not _not_same_named_family else "similar hue and small area",
                "area_ratio": round(float(area_ratio), 6),
            }
        )

    metadata["merge_count"] = len(metadata["merges"])
    if not metadata["merges"]:
        return labels, centers, metadata

    active_after = [label for label in sorted(active_labels) if mutable_counts.get(label, 0) > 0]
    relabel_map = {label: index for index, label in enumerate(active_after)}
    relabeled = np.full(labels.shape, -1, dtype=np.int32)
    for old_label, new_label in relabel_map.items():
        relabeled[mutable_labels == old_label] = new_label
    new_centers = np.asarray([mutable_centers[label] for label in active_after], dtype=np.float32)
    for record in metadata["merges"]:
        record["source_label_after_relabel"] = int(relabel_map.get(record["source_label"], -1))
        record["destination_label_after_relabel"] = int(relabel_map[record["destination_label"]])
    return relabeled, new_centers, metadata


def _hue_saturation(rgb: np.ndarray) -> tuple[float, float]:
    pixel = np.clip(np.round(rgb), 0, 255).astype(np.uint8).reshape(1, 1, 3)
    hsv = cv2.cvtColor(pixel, cv2.COLOR_RGB2HSV)[0, 0]
    return float(hsv[0]) * 2.0, float(hsv[1]) / 255.0


def _hue_distance_degrees(first: float, second: float) -> float:
    distance = abs(float(first) - float(second)) % 360.0
    return min(distance, 360.0 - distance)


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
    return _order_printable_labels(selected, centers, counts, config), ignored_label, warnings


def _order_printable_labels(
    selected: list[int],
    centers: np.ndarray,
    counts: dict[int, int],
    config: FilamentSwapReliefConfig,
) -> list[int]:
    if not selected:
        return []
    if config.relief_style == "stacked_blocks":
        base_label = max(selected, key=lambda label: (counts.get(label, 0), -label))
        detail_labels = [label for label in selected if label != base_label]
        detail_labels.sort(
            key=lambda label: (
                -counts.get(label, 0),
                -_luminance(centers[label]),
                label,
            )
        )
        return [base_label, *detail_labels]
    ordered = list(selected)
    ordered.sort(key=lambda label: _luminance(centers[label]), reverse=config.color_order == "light_to_dark")
    return ordered


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
        min_model_thickness_mm=config.min_model_thickness_mm,
        source=source_metadata,
        palette_order=config.color_order,
    )
    color_plan["relief_style"] = config.relief_style
    color_plan["mesh_style"] = config.mesh_style
    color_plan["merge_similar_colors"] = bool(config.merge_similar_colors)
    color_plan["solid_base_enabled"] = bool(config.solid_base_enabled)
    color_rows = color_plan["colors"]
    if config.solid_base_enabled and color_rows:
        height_map[:, :] = float(color_rows[0]["aligned_top_z_mm"])
    for row in color_rows:
        mask = labels == int(row["cluster_label"])
        height_map[mask] = float(row["aligned_top_z_mm"])
    return height_map, color_rows, color_plan


def _resolve_height_level_diagonal_contacts(height_map: np.ndarray) -> tuple[np.ndarray, int]:
    """Make cumulative height bands 4-connected so the STL has no point-contact edges."""
    if not np.any(height_map > 0):
        return height_map, 0

    repaired = height_map.copy()
    pixels_added = 0
    for level in sorted(float(value) for value in np.unique(height_map[height_map > 0])):
        before = repaired >= level
        after = _resolve_diagonal_contacts(before)
        added = after & ~before
        if np.any(added):
            repaired[added] = level
            pixels_added += int(np.count_nonzero(added))
    return repaired, pixels_added


def _resolve_diagonal_contacts(mask: np.ndarray) -> np.ndarray:
    fixed = mask.astype(bool).copy()
    height, width = fixed.shape
    max_passes = max(1, min(max(height, width), 64))

    for _ in range(max_passes):
        source = fixed.copy()
        changed = False
        for y in range(height - 1):
            for x in range(width - 1):
                top_left = source[y, x]
                top_right = source[y, x + 1]
                bottom_left = source[y + 1, x]
                bottom_right = source[y + 1, x + 1]

                if top_left and bottom_right and not top_right and not bottom_left:
                    changed = _fill_stronger_bridge_pixel(fixed, source, [(y, x + 1), (y + 1, x)]) or changed
                elif top_right and bottom_left and not top_left and not bottom_right:
                    changed = _fill_stronger_bridge_pixel(fixed, source, [(y, x), (y + 1, x + 1)]) or changed
        if not changed:
            break

    return fixed


def _fill_stronger_bridge_pixel(
    fixed: np.ndarray,
    source: np.ndarray,
    candidates: list[tuple[int, int]],
) -> bool:
    best_y, best_x = max(candidates, key=lambda point: (_neighbor_count(source, point[0], point[1]), -point[0], -point[1]))
    if fixed[best_y, best_x]:
        return False
    fixed[best_y, best_x] = True
    return True


def _neighbor_count(mask: np.ndarray, y: int, x: int) -> int:
    y0 = max(0, y - 1)
    y1 = min(mask.shape[0], y + 2)
    x0 = max(0, x - 1)
    x1 = min(mask.shape[1], x + 2)
    return int(np.count_nonzero(mask[y0:y1, x0:x1]))


def _mesh_from_height_map(
    height_map: np.ndarray,
    width_mm: float,
    config: FilamentSwapReliefConfig,
) -> tuple[trimesh.Trimesh, dict[str, Any]]:
    if config.mesh_style == "vector_contours":
        try:
            mesh, metadata = _vector_mesh_from_height_map(height_map, width_mm, config)
            if _mesh_is_safe(mesh):
                return mesh, metadata
            open_edges, overused_edges, non_manifold_edges = _mesh_edge_counts(mesh)
            fallback_reason = (
                "Vector-contour filament mesh did not validate "
                f"(watertight: {mesh.is_watertight}, open edges: {open_edges}, "
                f"overused edges: {overused_edges}, non-manifold edges: {non_manifold_edges})."
            )
        except Exception as error:
            fallback_reason = f"Vector-contour filament mesh failed: {error}"
        pixel_mesh, pixel_metadata = _pixel_mesh_from_height_map(height_map, width_mm)
        pixel_metadata["mesh_generation_warning"] = f"{fallback_reason} Used pixel heightfield fallback."
        pixel_metadata["mesh_generation_mode"] = "pixel_heightfield"
        return pixel_mesh, pixel_metadata
    return _pixel_mesh_from_height_map(height_map, width_mm)


def _pixel_mesh_from_height_map(height_map: np.ndarray, width_mm: float) -> tuple[trimesh.Trimesh, dict[str, Any]]:
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
        "mesh_generation_mode": "pixel_heightfield",
        "mesh_generation_warning": "",
        "vector_contour_count": 0,
        "vector_polygon_count": 0,
    }


def _vector_mesh_from_height_map(
    height_map: np.ndarray,
    width_mm: float,
    config: FilamentSwapReliefConfig,
) -> tuple[trimesh.Trimesh, dict[str, Any]]:
    height_px, width_px = height_map.shape
    if height_px < 1 or width_px < 1:
        raise ValueError("Filament Swap Relief height map did not contain pixels.")
    if width_mm <= 0:
        raise ValueError("Filament Swap Relief width must be greater than zero.")
    if not np.any(height_map > 0):
        raise ValueError("Filament Swap Relief height map has no printable pixels.")

    try:
        import shapely  # noqa: F401
    except ImportError as error:
        raise ImportError("Vector-contour filament mesh requires optional polygon dependencies.") from error

    height_mm = width_mm * (height_px / width_px)
    scale_x = width_mm / width_px
    scale_y = height_mm / height_px
    levels = sorted(float(value) for value in np.unique(height_map[height_map > 0]))
    cumulative_geometries = []
    contour_count = 0
    polygon_count = 0

    for index, level in enumerate(levels):
        mask = height_map >= (level - 1e-6)
        geometry, stats = _mask_to_geometry(mask, scale_x, scale_y, height_mm, config)
        if index > 0:
            geometry = _clean_geometry(geometry.intersection(cumulative_geometries[index - 1]))
        if geometry.is_empty:
            raise ValueError(f"Vector-contour filament mesh had no geometry at height {level:.4f} mm.")
        cumulative_geometries.append(geometry)
        contour_count += stats["contours"]
        polygon_count += len(_geometry_polygons(geometry))

    vertices: list[tuple[float, float, float]] = []
    vertex_lookup: dict[tuple[float, float, float], int] = {}
    faces: list[tuple[int, int, int]] = []

    def vertex(x: float, y: float, z: float) -> int:
        key = (round(float(x), 6), round(float(y), 6), round(float(z), 6))
        existing = vertex_lookup.get(key)
        if existing is not None:
            return existing
        vertex_lookup[key] = len(vertices)
        vertices.append(key)
        return len(vertices) - 1

    def add_cap(geometry, z: float, *, top: bool) -> None:
        for polygon in _geometry_polygons(geometry):
            for triangle in _triangulate_cap_polygon(polygon):
                coords = list(triangle.exterior.coords)[:3]
                if len(coords) < 3:
                    continue
                a, b, c = [vertex(point[0], point[1], z) for point in coords]
                faces.append((a, b, c) if top else (c, b, a))

    def add_walls(geometry, low_z: float, high_z: float) -> None:
        if high_z <= low_z:
            return
        for polygon in _geometry_polygons(geometry):
            _add_ring_walls(polygon.exterior.coords, low_z, high_z, vertex, faces)
            for interior in polygon.interiors:
                _add_ring_walls(interior.coords, low_z, high_z, vertex, faces)

    add_cap(cumulative_geometries[0], 0.0, top=False)
    for index, level in enumerate(levels):
        lower_level = 0.0 if index == 0 else levels[index - 1]
        add_walls(cumulative_geometries[index], lower_level, level)
        if index + 1 < len(cumulative_geometries):
            exposed_top = _clean_geometry(cumulative_geometries[index].difference(cumulative_geometries[index + 1]))
        else:
            exposed_top = cumulative_geometries[index]
        if not exposed_top.is_empty:
            add_cap(exposed_top, level, top=True)

    if not faces:
        raise ValueError("Vector-contour filament mesh did not create any faces.")
    mesh = trimesh.Trimesh(vertices=np.array(vertices), faces=np.array(faces), process=True)
    mesh.fix_normals()
    return mesh, {
        "height_mm": round(float(height_mm), 4),
        "scale_x_mm_per_pixel": round(float(scale_x), 6),
        "scale_y_mm_per_pixel": round(float(scale_y), 6),
        "mesh_generation_mode": "vector_contours",
        "mesh_generation_warning": "",
        "vector_contour_count": contour_count,
        "vector_polygon_count": polygon_count,
    }


def _mask_to_geometry(
    mask: np.ndarray,
    scale_x: float,
    scale_y: float,
    depth_mm: float,
    config: FilamentSwapReliefConfig,
):
    from shapely.geometry import box
    from shapely.ops import unary_union

    contours, _hierarchy = cv2.findContours(mask.astype(np.uint8) * 255, cv2.RETR_TREE, cv2.CHAIN_APPROX_NONE)
    rectangles = []
    for x_start, x_end, y_start, y_end in _mask_runs_to_rectangles(mask):
        rectangles.append(
            box(
                x_start * scale_x,
                depth_mm - (y_end * scale_y),
                x_end * scale_x,
                depth_mm - (y_start * scale_y),
            )
        )
    if not rectangles:
        raise ValueError("Vector-contour filament mesh did not find valid polygons.")
    geometry = _clean_geometry(unary_union(rectangles))
    geometry = _smooth_filament_geometry(geometry, scale_x, scale_y, config)
    tolerance_px = max(0.0, float(config.contour_simplify_tolerance_px))
    if tolerance_px > 0:
        tolerance_mm = tolerance_px * min(float(scale_x), float(scale_y))
        geometry = _clean_geometry(geometry.simplify(tolerance_mm, preserve_topology=True))
    return geometry, {"contours": len(contours)}


def _smooth_filament_geometry(geometry, scale_x: float, scale_y: float, config: FilamentSwapReliefConfig):
    if geometry.is_empty or not config.contour_smoothing_enabled:
        return geometry
    strength = max(0, int(config.contour_smoothing_strength))
    if strength <= 0:
        return geometry
    # Smooth about half a sampled pixel per strength step. This reduces stair-stepped
    # mask edges without acting like a global XY offset or filling intentional openings.
    radius_mm = min(float(scale_x), float(scale_y)) * min(strength, 3) * 0.5
    if radius_mm <= 0:
        return geometry
    smoothed = _clean_geometry(geometry.buffer(radius_mm, join_style=1).buffer(-radius_mm, join_style=1))
    smoothed = _clean_geometry(smoothed.buffer(-radius_mm, join_style=1).buffer(radius_mm, join_style=1))
    return smoothed if not smoothed.is_empty else geometry


def _mask_runs_to_rectangles(mask: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Collapse a binary mask into deterministic rectangles without filling enclosed stroke holes."""
    rectangles: list[tuple[int, int, int, int]] = []
    active: dict[tuple[int, int], int] = {}
    height, width = mask.shape
    for y in range(height):
        row = mask[y]
        runs: list[tuple[int, int]] = []
        x = 0
        while x < width:
            if not row[x]:
                x += 1
                continue
            start = x
            while x < width and row[x]:
                x += 1
            runs.append((start, x))

        current = set(runs)
        for run, y_start in list(active.items()):
            if run not in current:
                rectangles.append((run[0], run[1], y_start, y))
                del active[run]
        for run in runs:
            active.setdefault(run, y)

    for run, y_start in sorted(active.items(), key=lambda item: (item[1], item[0][0], item[0][1])):
        rectangles.append((run[0], run[1], y_start, height))
    return rectangles


def _prepare_filament_contour_points(points: np.ndarray, config: FilamentSwapReliefConfig) -> np.ndarray:
    if float(config.contour_simplify_tolerance_px) > 0:
        simplified = cv2.approxPolyDP(
            points.reshape(-1, 1, 2).astype(np.float32),
            float(config.contour_simplify_tolerance_px),
            closed=True,
        )
        if len(simplified) >= 3:
            points = simplified.reshape(-1, 2).astype(np.float32)
    if config.contour_smoothing_enabled and int(config.contour_smoothing_strength) > 0:
        points = smooth_contour_points(points, int(config.contour_smoothing_strength), 35.0)
    return _dedupe_ring_points(points)


def _dedupe_ring_points(points: np.ndarray) -> np.ndarray:
    if len(points) <= 1:
        return points
    kept: list[np.ndarray] = []
    previous: tuple[float, float] | None = None
    for point in points:
        current = (round(float(point[0]), 6), round(float(point[1]), 6))
        if current != previous:
            kept.append(point)
            previous = current
    if len(kept) > 1:
        first = (round(float(kept[0][0]), 6), round(float(kept[0][1]), 6))
        last = (round(float(kept[-1][0]), 6), round(float(kept[-1][1]), 6))
        if first == last:
            kept.pop()
    return _remove_collinear_point_array(np.array(kept, dtype=np.float32))


def _remove_collinear_point_array(points: np.ndarray) -> np.ndarray:
    if len(points) <= 3:
        return points
    kept = []
    count = len(points)
    for index, point in enumerate(points):
        previous = points[index - 1]
        next_point = points[(index + 1) % count]
        if not _points_are_collinear(previous, point, next_point):
            kept.append(point)
    if len(kept) < 3:
        return points
    return np.array(kept, dtype=np.float32)


def _contour_points_to_mm_ring(
    points: np.ndarray,
    scale_x: float,
    scale_y: float,
    depth_mm: float,
) -> list[tuple[float, float]]:
    ring: list[tuple[float, float]] = []
    previous: tuple[float, float] | None = None
    for point in points:
        current = (float(point[0]) * scale_x, depth_mm - (float(point[1]) * scale_y))
        if current != previous:
            ring.append(current)
            previous = current
    if len(ring) > 1 and ring[0] == ring[-1]:
        ring.pop()
    return ring


def _contour_is_hole(index: int, hierarchy: np.ndarray | None) -> bool:
    if hierarchy is None:
        return False
    depth = 0
    parent_index = int(hierarchy[0][index][3])
    while parent_index >= 0:
        depth += 1
        parent_index = int(hierarchy[0][parent_index][3])
    return depth % 2 == 1


def _assign_holes_to_exteriors(exterior_polygons: list, hole_polygons: list) -> list[list]:
    assignments: list[list] = [[] for _ in exterior_polygons]
    for hole in hole_polygons:
        representative_point = hole.representative_point()
        containing_exteriors = [
            (index, exterior.area)
            for index, exterior in enumerate(exterior_polygons)
            if exterior.contains(representative_point)
        ]
        if not containing_exteriors:
            continue
        exterior_index, _ = min(containing_exteriors, key=lambda item: (item[1], item[0]))
        assignments[exterior_index].append(hole)
    return assignments


def _clean_geometry(geometry):
    if geometry.is_empty:
        return geometry
    if not getattr(geometry, "is_valid", True):
        geometry = geometry.buffer(0)
    return geometry


def _geometry_polygons(geometry) -> list:
    if geometry.is_empty:
        return []
    if geometry.geom_type == "Polygon":
        return [geometry] if geometry.area > 0 else []
    if geometry.geom_type in {"MultiPolygon", "GeometryCollection"}:
        polygons = []
        for part in geometry.geoms:
            polygons.extend(_geometry_polygons(part))
        return polygons
    return []


def _triangulate_cap_polygon(polygon) -> list:
    from shapely.ops import triangulate

    triangles = []
    for triangle in triangulate(polygon):
        if triangle.is_empty or triangle.area <= 1e-9:
            continue
        if not polygon.covers(triangle.representative_point()):
            continue
        coords = list(triangle.exterior.coords)[:3]
        if len(coords) < 3:
            continue
        if _ring_signed_area(coords) < 0:
            coords = [coords[0], coords[2], coords[1]]
            from shapely.geometry import Polygon

            triangle = Polygon(coords)
        triangles.append(triangle)
    return triangles


def _add_ring_walls(
    coords,
    low_z: float,
    high_z: float,
    vertex,
    faces: list[tuple[int, int, int]],
) -> None:
    points = _remove_collinear_wall_points(list(coords))
    if len(points) < 2:
        return
    for first, second in zip(points, points[1:]):
        if first == second:
            continue
        a_low = vertex(first[0], first[1], low_z)
        b_low = vertex(second[0], second[1], low_z)
        a_high = vertex(first[0], first[1], high_z)
        b_high = vertex(second[0], second[1], high_z)
        faces.append((a_high, b_high, b_low))
        faces.append((a_high, b_low, a_low))


def _remove_collinear_wall_points(points: list) -> list:
    if len(points) <= 3:
        return points
    closed = points[0] == points[-1]
    working = points[:-1] if closed else points[:]
    if len(working) <= 3:
        return points
    kept = []
    count = len(working)
    for index, point in enumerate(working):
        previous = working[index - 1]
        next_point = working[(index + 1) % count]
        if not _points_are_collinear(previous, point, next_point):
            kept.append(point)
    if len(kept) < 3:
        kept = working
    if closed:
        kept.append(kept[0])
    return kept


def _points_are_collinear(a, b, c, *, tolerance: float = 1e-9) -> bool:
    ab_x = float(b[0]) - float(a[0])
    ab_y = float(b[1]) - float(a[1])
    bc_x = float(c[0]) - float(b[0])
    bc_y = float(c[1]) - float(b[1])
    return abs(ab_x * bc_y - ab_y * bc_x) <= tolerance


def _ring_signed_area(points: list) -> float:
    if len(points) < 3:
        return 0.0
    area = 0.0
    for first, second in zip(points, points[1:] + points[:1]):
        area += (float(first[0]) * float(second[1])) - (float(second[0]) * float(first[1]))
    return area * 0.5


def _mesh_is_safe(mesh: trimesh.Trimesh) -> bool:
    open_edges, overused_edges, non_manifold_edges = _mesh_edge_counts(mesh)
    return bool(mesh.is_watertight and open_edges == 0 and overused_edges == 0 and non_manifold_edges == 0)


def _mesh_edge_counts(mesh: trimesh.Trimesh) -> tuple[int, int, int]:
    if len(mesh.faces) == 0 or len(mesh.edges_unique) == 0:
        return 0, 0, 0
    edge_use_counts = np.bincount(mesh.edges_unique_inverse, minlength=len(mesh.edges_unique))
    open_edge_count = int(np.count_nonzero(edge_use_counts == 1))
    overused_edge_count = int(np.count_nonzero(edge_use_counts > 2))
    non_manifold_edge_count = int(np.count_nonzero(edge_use_counts != 2))
    return open_edge_count, overused_edge_count, non_manifold_edge_count


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
