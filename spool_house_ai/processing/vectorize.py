from __future__ import annotations

import json
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from spool_house_ai.config import SvgConfig
from spool_house_ai.processing.analysis import ImageAnalysis
from spool_house_ai.processing.geometry import (
    contour_to_smooth_path,
    external_vectorizer_available,
    extract_vector_contours,
    vector_contours_to_svg_paths,
)


def create_svg(
    analysis: ImageAnalysis | np.ndarray,
    output_path: Path,
    config: SvgConfig,
    metadata: dict[str, Any] | None = None,
    review_output_path: Path | None = None,
) -> None:
    """Vectorize a binary mask into an SVG using contour paths."""
    mask = analysis.final_mask if isinstance(analysis, ImageAnalysis) else analysis
    height, width = mask.shape
    backend = config.vectorizer_backend
    if backend in {"potrace", "inkscape"} and not external_vectorizer_available(backend):
        backend = "opencv"

    if isinstance(analysis, ImageAnalysis) and analysis.vector_contours:
        vector_contours = analysis.vector_contours
    else:
        vector_contours, _ = extract_vector_contours(
            mask,
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
    foreground_paths, hole_paths = vector_contours_to_svg_paths(vector_contours)
    detail_paths: list[str] = []
    removed_paths: list[str] = []
    if isinstance(analysis, ImageAnalysis):
        detail_paths = _mask_to_svg_paths(analysis.detail_mask, min_area=max(1.0, config.min_contour_area / 4.0))
        removed_paths = _mask_to_svg_paths(analysis.removed_island_mask, min_area=1.0)

    svg_parts = _build_svg_document(
        width=width,
        height=height,
        foreground_paths=foreground_paths,
        hole_paths=hole_paths,
        detail_paths=detail_paths,
        removed_paths=removed_paths,
        requested_backend=config.vectorizer_backend,
        used_backend=backend,
        metadata=metadata,
        review=False,
    )

    output_path.write_text("\n".join(svg_parts), encoding="utf-8")

    if review_output_path is not None:
        review_parts = _build_svg_document(
            width=width,
            height=height,
            foreground_paths=foreground_paths,
            hole_paths=hole_paths,
            detail_paths=detail_paths,
            removed_paths=removed_paths,
            requested_backend=config.vectorizer_backend,
            used_backend=backend,
            metadata=metadata,
            review=True,
        )
        review_output_path.write_text("\n".join(review_parts), encoding="utf-8")


def _build_svg_document(
    *,
    width: int,
    height: int,
    foreground_paths: list[str],
    hole_paths: list[str],
    detail_paths: list[str],
    removed_paths: list[str],
    requested_backend: str,
    used_backend: str,
    metadata: dict[str, Any] | None,
    review: bool,
) -> list[str]:
    payload = {
        "app_name": "Spool House Studio",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "creator": "Built by ChronicLand420",
        **(metadata or {}),
    }
    visibility = "" if review else ' display="none"'
    foreground_opacity = "0.30" if review else "1"
    combined_paths = " ".join(foreground_paths + hole_paths)
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
            f'width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
        ),
        "<!-- Built by ChronicLand420 -->",
        "<title>Spool House Studio vectorized artwork</title>",
        f"<desc>Vectorizer backend requested: {escape(requested_backend)}; used: {escape(used_backend)}</desc>",
        f'<metadata id="job_metadata">{escape(json.dumps(payload, sort_keys=True))}</metadata>',
        '<rect id="canvas_background" width="100%" height="100%" fill="white"/>',
        '<g id="foreground_mask" inkscape:groupmode="layer" inkscape:label="foreground_mask" fill="black" stroke="none" fill-rule="evenodd">',
    ]
    if combined_paths:
        parts.append(f'  <path id="printable_mask" opacity="{foreground_opacity}" d="{combined_paths}"/>')
    parts.append("</g>")
    parts.append(f'<g id="main_body" inkscape:groupmode="layer" inkscape:label="main_body"{visibility} fill="none" stroke="#16a34a" stroke-width="1.25">')
    parts.extend(_path_elements(foreground_paths, "main_body_contour"))
    parts.append("</g>")
    parts.append(f'<g id="holes" inkscape:groupmode="layer" inkscape:label="holes"{visibility} fill="none" stroke="#f59e0b" stroke-width="1.25">')
    parts.extend(_path_elements(hole_paths, "hole_contour"))
    parts.append("</g>")
    parts.append(
        f'<g id="preserved_details" inkscape:groupmode="layer" inkscape:label="preserved_details"{visibility} '
        'fill="none" stroke="#a855f7" stroke-width="1.1">'
    )
    parts.extend(_path_elements(detail_paths, "detail_contour"))
    parts.append("</g>")
    parts.append(
        f'<g id="ignored_islands" inkscape:groupmode="layer" inkscape:label="ignored_islands"{visibility} '
        'fill="none" stroke="#ef4444" stroke-width="1.1">'
    )
    parts.extend(_path_elements(removed_paths, "removed_island"))
    parts.append("</g>")
    parts.append("</svg>")
    return parts


def _path_elements(paths: list[str], prefix: str) -> list[str]:
    return [f'  <path id="{prefix}_{index}" d="{path}"/>' for index, path in enumerate(paths, start=1) if path]


def _mask_to_svg_paths(mask: np.ndarray, min_area: float) -> list[str]:
    if not np.any(mask):
        return []
    contours, _ = cv2.findContours(mask.astype(np.uint8) * 255, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    paths: list[str] = []
    for contour in contours:
        area = abs(float(cv2.contourArea(contour)))
        if area < min_area:
            continue
        simplified = cv2.approxPolyDP(contour.astype(np.float32), 0.75, closed=True)
        if len(simplified) < 3:
            continue
        paths.append(contour_to_smooth_path(simplified.reshape(-1, 2).astype(np.float32)))
    return paths
