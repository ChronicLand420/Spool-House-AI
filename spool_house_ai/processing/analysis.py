from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from spool_house_ai.config import SilhouetteConfig
from spool_house_ai.processing.geometry import GeometryReport, VectorContour, extract_vector_contours, vector_contours_to_mask


@dataclass(frozen=True)
class ContourFeature:
    contour: np.ndarray
    area: float
    is_hole: bool
    kept: bool


@dataclass(frozen=True)
class ArtifactReport:
    isolated_island_count: int
    removed_island_count: int
    preserved_island_count: int
    preserved_detail_count: int
    smallest_island_area_px: float
    largest_island_area_px: float
    island_cleanup_enabled: bool
    cleanup_preset: str
    min_island_area_px: float
    preserve_islands_near_body: bool
    island_near_body_distance_px: float


@dataclass(frozen=True)
class ImageAnalysis:
    raw_threshold_mask: np.ndarray
    removed_island_mask: np.ndarray
    threshold_mask: np.ndarray
    final_mask: np.ndarray
    body_mask: np.ndarray
    hole_mask: np.ndarray
    detail_mask: np.ndarray
    color_region_masks: list[np.ndarray]
    vector_contours: list[VectorContour]
    geometry_report: GeometryReport
    kept_features: list[ContourFeature]
    removed_features: list[ContourFeature]
    artifact_report: ArtifactReport


def analyze_image(cleaned_png_path: Path, output_path: Path, config: SilhouetteConfig) -> ImageAnalysis:
    """Analyze a cleaned image and create a detail-preserving binary mask."""
    with Image.open(cleaned_png_path) as image:
        rgba = np.array(image.convert("RGBA"))
    rgba = _upscale_rgba(rgba, config.upscale_factor)

    alpha = rgba[:, :, 3]
    visible_mask = alpha > config.threshold
    grayscale = cv2.cvtColor(rgba[:, :, :3], cv2.COLOR_RGB2GRAY)
    if config.pre_blur_radius > 0:
        grayscale = cv2.GaussianBlur(grayscale, _kernel(config.pre_blur_radius), 0)
    dark_detail_mask = (grayscale < config.threshold_value) & visible_mask
    if config.adaptive_threshold:
        adaptive = cv2.adaptiveThreshold(
            grayscale,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV,
            max(3, _odd_kernel_size(config.smoothing_strength * 2 + 1)),
            3,
        )
        dark_detail_mask = (adaptive > 0) & visible_mask
    color_foreground_mask = _saturated_foreground_mask(rgba) & visible_mask

    if np.any(rgba[:, :, 3] < 255):
        threshold_mask = visible_mask.copy()
        if config.preserve_internal_details:
            threshold_mask = threshold_mask | dark_detail_mask
    else:
        threshold_mask = dark_detail_mask | color_foreground_mask

    if config.invert:
        threshold_mask = np.logical_not(threshold_mask)

    raw_threshold_mask = threshold_mask.copy()
    threshold_mask, removed_island_mask, artifact_report = _remove_islands_if_enabled(threshold_mask, config)
    smoothed_mask = _smooth_mask(threshold_mask, config)
    silhouette_mask, kept_features, removed_features = _remove_small_features(smoothed_mask, config)
    has_transparency = bool(np.any(rgba[:, :, 3] < 255))
    body_mask, hole_mask, detail_mask = _classify_body_holes_and_details(
        visible_mask=visible_mask,
        dark_detail_mask=dark_detail_mask,
        silhouette_mask=silhouette_mask,
        has_transparency=has_transparency,
        config=config,
    )
    artifact_report = replace(artifact_report, preserved_detail_count=_component_count(detail_mask))
    color_region_masks = _major_color_regions(rgba, body_mask, config)
    final_mask = _final_mask_for_detail_mode(
        silhouette_mask=silhouette_mask,
        body_mask=body_mask,
        hole_mask=hole_mask,
        detail_mask=detail_mask,
        config=config,
    )
    vector_contours, geometry_report = extract_vector_contours(
        final_mask,
        min_area=config.min_contour_area,
        simplify_tolerance=config.simplify_tolerance,
        smoothing_enabled=config.contour_smoothing_enabled,
        smoothing_strength=config.contour_smoothing_strength,
        collinear_merge_tolerance=config.collinear_merge_tolerance,
        sharp_corner_angle_threshold=config.sharp_corner_angle_threshold,
        safe_smoothing_enabled=config.safe_smoothing_enabled,
        smoothing_profile=config.smoothing_profile,
        max_area_change_percent=config.max_area_change_percent,
        max_bbox_change_percent=config.max_bbox_change_percent,
        max_aspect_ratio_change_percent=config.max_aspect_ratio_change_percent,
        max_point_reduction_percent=config.max_point_reduction_percent,
        straight_line_cleanup_enabled=config.straight_line_cleanup_enabled,
        straight_line_tolerance=config.straight_line_tolerance,
        min_straight_segment_length_px=config.min_straight_segment_length_px,
        curve_fit_enabled=config.curve_fit_enabled,
        curve_fit_tolerance=config.curve_fit_tolerance,
        min_curve_segment_length_px=config.min_curve_segment_length_px,
        max_curve_error_percent=config.max_curve_error_percent,
    )
    vector_mask = vector_contours_to_mask(vector_contours, final_mask.shape)
    if np.any(vector_mask) and not geometry_report.fallback_used:
        final_mask = vector_mask

    output_image = np.where(final_mask, 0, 255).astype(np.uint8)
    Image.fromarray(output_image, mode="L").save(output_path)

    return ImageAnalysis(
        raw_threshold_mask=raw_threshold_mask,
        removed_island_mask=removed_island_mask,
        threshold_mask=threshold_mask,
        final_mask=final_mask,
        body_mask=body_mask,
        hole_mask=hole_mask,
        detail_mask=detail_mask,
        color_region_masks=color_region_masks,
        vector_contours=vector_contours,
        geometry_report=geometry_report,
        kept_features=kept_features,
        removed_features=removed_features,
        artifact_report=artifact_report,
    )


def save_mask(mask: np.ndarray, output_path: Path) -> None:
    Image.fromarray(np.where(mask, 0, 255).astype(np.uint8), mode="L").save(output_path)


def _classify_body_holes_and_details(
    visible_mask: np.ndarray,
    dark_detail_mask: np.ndarray,
    silhouette_mask: np.ndarray,
    has_transparency: bool,
    config: SilhouetteConfig,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if config.detail_mode == "silhouette_only":
        empty = np.zeros(silhouette_mask.shape, dtype=bool)
        return silhouette_mask, empty, empty

    body_seed = visible_mask if has_transparency else silhouette_mask
    body_source = _remove_small_components(body_seed | silhouette_mask, config.min_contour_area)
    body_filled = _fill_all_holes(body_source)
    hole_mask = body_filled & np.logical_not(body_source)
    hole_mask = _remove_small_components(hole_mask, config.min_contour_area)

    body_mask = body_filled
    if config.preserve_holes:
        body_mask = body_filled & np.logical_not(hole_mask)

    detail_source = dark_detail_mask & body_filled & np.logical_not(hole_mask)
    detail_source = detail_source & np.logical_not(_outer_edge_mask(body_filled))
    detail_mask = _remove_small_components(detail_source, max(2.0, config.min_contour_area / 4.0))

    if not config.preserve_internal_details or config.detail_mode == "silhouette_only":
        detail_mask = np.zeros(detail_mask.shape, dtype=bool)

    return body_mask, hole_mask, detail_mask


def _final_mask_for_detail_mode(
    silhouette_mask: np.ndarray,
    body_mask: np.ndarray,
    hole_mask: np.ndarray,
    detail_mask: np.ndarray,
    config: SilhouetteConfig,
) -> np.ndarray:
    if config.detail_mode == "silhouette_only":
        return silhouette_mask
    if config.default_detail_behavior == "cut":
        return (body_mask | detail_mask) if config.detail_mode == "raised_details" else body_mask & np.logical_not(detail_mask)
    if config.default_detail_behavior == "ignore":
        detail_mask = np.zeros(detail_mask.shape, dtype=bool)
    if config.detail_mode in {"raised_details", "layered_color_relief"}:
        return body_mask | detail_mask
    if config.detail_mode == "engraved_details":
        return body_mask
    return body_mask & np.logical_not(hole_mask)


def _outer_edge_mask(mask: np.ndarray) -> np.ndarray:
    kernel = np.ones((3, 3), np.uint8)
    eroded = cv2.erode(mask.astype(np.uint8), kernel, iterations=1) > 0
    return mask & np.logical_not(eroded)


def _remove_small_components(mask: np.ndarray, min_area: float) -> np.ndarray:
    cleaned = np.zeros(mask.shape, dtype=np.uint8)
    component_count, labels, stats, _ = cv2.connectedComponentsWithStats(
        mask.astype(np.uint8),
        connectivity=8,
    )
    for label in range(1, component_count):
        if float(stats[label, cv2.CC_STAT_AREA]) >= min_area:
            cleaned[labels == label] = 1
    return cleaned > 0


def _remove_islands_if_enabled(mask: np.ndarray, config: SilhouetteConfig) -> tuple[np.ndarray, np.ndarray, ArtifactReport]:
    component_count, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    small_areas = [
        float(stats[label, cv2.CC_STAT_AREA])
        for label in range(1, component_count)
        if float(stats[label, cv2.CC_STAT_AREA]) < config.min_island_area_px
    ]
    if not config.remove_small_islands:
        report = ArtifactReport(
            isolated_island_count=len(small_areas),
            removed_island_count=0,
            preserved_island_count=len(small_areas),
            preserved_detail_count=0,
            smallest_island_area_px=min(small_areas) if small_areas else 0.0,
            largest_island_area_px=max(small_areas) if small_areas else 0.0,
            island_cleanup_enabled=False,
            cleanup_preset=config.cleanup_preset,
            min_island_area_px=config.min_island_area_px,
            preserve_islands_near_body=config.preserve_islands_near_body,
            island_near_body_distance_px=config.island_near_body_distance_px,
        )
        return mask, np.zeros(mask.shape, dtype=bool), report

    kept = np.zeros(mask.shape, dtype=np.uint8)
    removed = np.zeros(mask.shape, dtype=np.uint8)
    large_body = np.zeros(mask.shape, dtype=np.uint8)
    removed_count = 0
    preserved_count = 0
    for label in range(1, component_count):
        area = float(stats[label, cv2.CC_STAT_AREA])
        if area >= config.min_island_area_px:
            large_body[labels == label] = 1

    distance = None
    if config.preserve_islands_near_body and np.any(large_body):
        distance = cv2.distanceTransform((1 - large_body).astype(np.uint8), cv2.DIST_L2, 3)

    for label in range(1, component_count):
        area = float(stats[label, cv2.CC_STAT_AREA])
        component = labels == label
        keep = area >= config.min_island_area_px
        if not keep and distance is not None:
            keep = bool(np.min(distance[component]) <= config.island_near_body_distance_px)
        if keep:
            kept[component] = 1
            if area < config.min_island_area_px:
                preserved_count += 1
        else:
            removed[component] = 1
            if area < config.min_island_area_px:
                removed_count += 1
    report = ArtifactReport(
        isolated_island_count=len(small_areas),
        removed_island_count=removed_count,
        preserved_island_count=preserved_count,
        preserved_detail_count=0,
        smallest_island_area_px=min(small_areas) if small_areas else 0.0,
        largest_island_area_px=max(small_areas) if small_areas else 0.0,
        island_cleanup_enabled=True,
        cleanup_preset=config.cleanup_preset,
        min_island_area_px=config.min_island_area_px,
        preserve_islands_near_body=config.preserve_islands_near_body,
        island_near_body_distance_px=config.island_near_body_distance_px,
    )
    return kept > 0, removed > 0, report


def _component_count(mask: np.ndarray) -> int:
    component_count, _, _, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    return max(0, component_count - 1)


def _major_color_regions(
    rgba: np.ndarray,
    body_mask: np.ndarray,
    config: SilhouetteConfig,
) -> list[np.ndarray]:
    pixels = rgba[:, :, :3][body_mask]
    if len(pixels) < config.min_contour_area * 3:
        return []

    quantized = (pixels // 64).astype(np.uint8)
    packed = quantized[:, 0] * 16 + quantized[:, 1] * 4 + quantized[:, 2]
    values, counts = np.unique(packed, return_counts=True)
    major_values = values[np.argsort(counts)[-3:]]

    masks: list[np.ndarray] = []
    quantized_image = (rgba[:, :, :3] // 64).astype(np.uint8)
    packed_image = quantized_image[:, :, 0] * 16 + quantized_image[:, :, 1] * 4 + quantized_image[:, :, 2]
    for value in major_values:
        region = (packed_image == value) & body_mask
        region = _remove_small_components(region, config.min_contour_area)
        if np.any(region):
            masks.append(region)
    return masks


def _saturated_foreground_mask(rgba: np.ndarray) -> np.ndarray:
    rgb = rgba[:, :, :3]
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]
    return (saturation > 35) & (value > 45)


def _smooth_mask(mask: np.ndarray, config: SilhouetteConfig) -> np.ndarray:
    working = mask.astype(np.uint8) * 255

    if config.smoothing_enabled and config.smoothing_strength > 0:
        kernel_size = _odd_kernel_size(config.smoothing_strength)
        working = cv2.medianBlur(working, kernel_size)

    if config.morphology_enabled and config.morph_kernel_size > 0 and config.morph_iterations > 0:
        kernel_size = _odd_kernel_size(config.morph_kernel_size)
        kernel = np.ones((kernel_size, kernel_size), np.uint8)
        working = cv2.morphologyEx(
            working,
            cv2.MORPH_CLOSE,
            kernel,
            iterations=config.morph_iterations,
        )
        working = cv2.morphologyEx(
            working,
            cv2.MORPH_OPEN,
            kernel,
            iterations=config.morph_iterations,
        )

    return working > 0


def _remove_small_features(
    mask: np.ndarray,
    config: SilhouetteConfig,
) -> tuple[np.ndarray, list[ContourFeature], list[ContourFeature]]:
    cleaned = np.zeros(mask.shape, dtype=np.uint8)
    source = mask.astype(np.uint8)
    kept_features: list[ContourFeature] = []
    removed_features: list[ContourFeature] = []

    component_count, labels, stats, _ = cv2.connectedComponentsWithStats(source, connectivity=8)
    for label in range(1, component_count):
        area = float(stats[label, cv2.CC_STAT_AREA])
        component_mask = labels == label
        contours, _ = cv2.findContours(
            component_mask.astype(np.uint8) * 255,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE,
        )
        feature = ContourFeature(contours[0], area, is_hole=False, kept=area >= config.min_contour_area)
        if area >= config.min_contour_area:
            cleaned[component_mask] = 1
            kept_features.append(feature)
        else:
            removed_features.append(feature)

    if config.preserve_holes:
        cleaned = _restore_meaningful_holes(mask, cleaned > 0, config)
    else:
        cleaned = _fill_all_holes(cleaned > 0).astype(np.uint8)

    hole_features = _find_hole_features(cleaned > 0, config)
    kept_features.extend([feature for feature in hole_features if feature.kept])
    removed_features.extend([feature for feature in hole_features if not feature.kept])

    return cleaned > 0, kept_features, removed_features


def _restore_meaningful_holes(
    original_mask: np.ndarray,
    cleaned_mask: np.ndarray,
    config: SilhouetteConfig,
) -> np.ndarray:
    filled = _fill_all_holes(cleaned_mask)
    holes = filled & np.logical_not(original_mask)
    component_count, labels, stats, _ = cv2.connectedComponentsWithStats(
        holes.astype(np.uint8),
        connectivity=8,
    )

    output = filled.copy()
    for label in range(1, component_count):
        area = float(stats[label, cv2.CC_STAT_AREA])
        if area >= config.min_contour_area:
            output[labels == label] = False
    return output.astype(np.uint8)


def _fill_all_holes(mask: np.ndarray) -> np.ndarray:
    flood = np.logical_not(mask).astype(np.uint8) * 255
    flood_padded = cv2.copyMakeBorder(flood, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
    cv2.floodFill(flood_padded, None, (0, 0), 128)
    outside = flood_padded[1:-1, 1:-1] == 128
    return mask | np.logical_not(outside)


def _find_hole_features(mask: np.ndarray, config: SilhouetteConfig) -> list[ContourFeature]:
    filled = _fill_all_holes(mask)
    holes = filled & np.logical_not(mask)
    contours, _ = cv2.findContours(
        holes.astype(np.uint8) * 255,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )
    features: list[ContourFeature] = []
    for contour in contours:
        area = float(cv2.contourArea(contour))
        features.append(
            ContourFeature(
                contour=contour,
                area=area,
                is_hole=True,
                kept=area >= config.min_contour_area,
            )
        )
    return features


def _odd_kernel_size(value: int) -> int:
    value = max(1, int(value))
    if value % 2 == 0:
        value += 1
    return value


def _kernel(value: int) -> tuple[int, int]:
    size = _odd_kernel_size(value)
    return size, size


def _upscale_rgba(rgba: np.ndarray, factor: int) -> np.ndarray:
    factor = max(1, int(factor))
    if factor == 1:
        return rgba
    height, width = rgba.shape[:2]
    return cv2.resize(rgba, (width * factor, height * factor), interpolation=cv2.INTER_CUBIC)
