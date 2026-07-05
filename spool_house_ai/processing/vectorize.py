from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from spool_house_ai.config import SvgConfig
from spool_house_ai.processing.analysis import ImageAnalysis
from spool_house_ai.processing.geometry import (
    external_vectorizer_available,
    extract_vector_contours,
    vector_contours_to_svg_paths,
)


def create_svg(analysis: ImageAnalysis | np.ndarray, output_path: Path, config: SvgConfig) -> None:
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

    svg_parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        "<title>Spool House AI V4 vectorized artwork</title>",
        f'<desc>Vectorizer backend requested: {config.vectorizer_backend}; used: {backend}</desc>',
        '<rect width="100%" height="100%" fill="white"/>',
        '<g id="artwork" fill="black" stroke="none" fill-rule="evenodd">',
    ]
    if foreground_paths or hole_paths:
        svg_parts.append(f'  <path id="kept-contours" d="{" ".join(foreground_paths + hole_paths)}"/>')
    svg_parts.append("</g>")
    svg_parts.append('<g id="edit-guides" fill="none" stroke="#00a3ff" stroke-width="1" opacity="0.45">')
    for index, path in enumerate(foreground_paths, start=1):
        svg_parts.append(f'  <path id="contour-{index}" d="{path}"/>')
    svg_parts.append("</g>")
    svg_parts.append("</svg>")

    output_path.write_text("\n".join(svg_parts), encoding="utf-8")
