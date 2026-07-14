from __future__ import annotations

import math
import shutil
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass(frozen=True)
class VectorContour:
    points: np.ndarray
    is_hole: bool
    area: float
    original_points: np.ndarray
    fallback_used: bool


@dataclass(frozen=True)
class GeometryReport:
    original_contour_count: int
    smoothed_contour_count: int
    original_total_points: int
    smoothed_total_points: int
    area_change_percent: float
    bbox_change_percent: float
    aspect_ratio_change_percent: float
    point_reduction_percent: float
    fallback_used: bool
    smoothing_profile: str
    straightened_segments: int
    curve_fitted_segments: int
    rejected_cleanup_count: int


def external_vectorizer_available(backend: str) -> bool:
    if backend == "potrace":
        return shutil.which("potrace") is not None
    if backend == "inkscape":
        return shutil.which("inkscape") is not None
    return backend == "opencv"


def extract_vector_contours(
    mask: np.ndarray,
    min_area: float,
    simplify_tolerance: float,
    smoothing_enabled: bool,
    smoothing_strength: int,
    collinear_merge_tolerance: float,
    sharp_corner_angle_threshold: float,
    safe_smoothing_enabled: bool = True,
    smoothing_profile: str = "conservative",
    max_area_change_percent: float = 10.0,
    max_bbox_change_percent: float = 10.0,
    max_aspect_ratio_change_percent: float = 10.0,
    max_point_reduction_percent: float = 80.0,
    straight_line_cleanup_enabled: bool = True,
    straight_line_tolerance: float = 4.0,
    min_straight_segment_length_px: float = 24.0,
    curve_fit_enabled: bool = True,
    curve_fit_tolerance: float = 1.0,
    min_curve_segment_length_px: float = 12.0,
    max_curve_error_percent: float = 5.0,
) -> tuple[list[VectorContour], GeometryReport]:
    image = mask.astype(np.uint8) * 255
    raw_contours, hierarchy = cv2.findContours(image, cv2.RETR_TREE, cv2.CHAIN_APPROX_NONE)
    raw_point_count = sum(len(contour) for contour in raw_contours)
    vector_contours: list[VectorContour] = []
    valid_original_count = 0
    original_area = 0.0
    smoothed_area = 0.0
    fallback_used = False
    original_boxes: list[tuple[int, int, int, int]] = []
    smoothed_boxes: list[tuple[int, int, int, int]] = []
    straightened_segments = 0
    curve_fitted_segments = 0
    rejected_cleanup_count = 0

    profile = smoothing_profile.lower()
    if profile == "conservative":
        simplify_tolerance = min(simplify_tolerance, 0.8)
        smoothing_strength = min(smoothing_strength, 1)
        collinear_merge_tolerance = min(collinear_merge_tolerance, 2.0)
    elif profile == "balanced":
        smoothing_strength = min(smoothing_strength, 2)

    for index, contour in enumerate(raw_contours):
        area = abs(float(cv2.contourArea(contour)))
        if area < min_area:
            continue
        valid_original_count += 1
        original_area += area
        original_points = contour.reshape(-1, 2).astype(np.float32)
        original_boxes.append(cv2.boundingRect(contour))

        points = _safe_approx_points(original_points, simplify_tolerance)
        candidate = merge_collinear_points(points, collinear_merge_tolerance)
        before_cleanup = candidate.copy()
        if straight_line_cleanup_enabled:
            candidate, straight_count = straighten_long_runs(
                candidate,
                straight_line_tolerance,
                min_straight_segment_length_px,
                sharp_corner_angle_threshold,
            )
            straightened_segments += straight_count
        if curve_fit_enabled:
            candidate, curve_count = fit_curve_sections(
                candidate,
                curve_fit_tolerance,
                min_curve_segment_length_px,
                max_curve_error_percent,
                sharp_corner_angle_threshold,
            )
            curve_fitted_segments += curve_count
        if safe_smoothing_enabled and not _candidate_is_safe(
            original_points=original_points,
            candidate_points=candidate,
            max_area_change_percent=max_area_change_percent,
            max_bbox_change_percent=max_bbox_change_percent,
            max_aspect_ratio_change_percent=max_aspect_ratio_change_percent,
            max_point_reduction_percent=max_point_reduction_percent,
        ):
            candidate = before_cleanup
            rejected_cleanup_count += 1
        if smoothing_enabled:
            candidate = smooth_contour_points(candidate, smoothing_strength, sharp_corner_angle_threshold)
            candidate = merge_collinear_points(candidate, collinear_merge_tolerance)
        candidate_fallback = False
        if safe_smoothing_enabled and not _candidate_is_safe(
            original_points=original_points,
            candidate_points=candidate,
            max_area_change_percent=max_area_change_percent,
            max_bbox_change_percent=max_bbox_change_percent,
            max_aspect_ratio_change_percent=max_aspect_ratio_change_percent,
            max_point_reduction_percent=max_point_reduction_percent,
        ):
            candidate = _safe_approx_points(original_points, min(simplify_tolerance, 0.35))
            candidate_fallback = True
            fallback_used = True
            rejected_cleanup_count += 1
        if len(candidate) < 3:
            candidate = original_points
            candidate_fallback = True
            fallback_used = True
        if len(candidate) < 3:
            continue
        smoothed_area += abs(float(cv2.contourArea(candidate.astype(np.float32))))
        smoothed_boxes.append(cv2.boundingRect(np.round(candidate).astype(np.int32).reshape(-1, 1, 2)))
        vector_contours.append(
            VectorContour(
                points=candidate,
                is_hole=_contour_is_hole(index, hierarchy),
                area=area,
                original_points=original_points,
                fallback_used=candidate_fallback,
            )
        )

    smoothed_point_count = sum(len(contour.points) for contour in vector_contours)
    report = GeometryReport(
        original_contour_count=valid_original_count,
        smoothed_contour_count=len(vector_contours),
        original_total_points=raw_point_count,
        smoothed_total_points=smoothed_point_count,
        area_change_percent=_percent_change(original_area, smoothed_area),
        bbox_change_percent=_bbox_change_percent(_union_bbox(original_boxes), _union_bbox(smoothed_boxes)),
        aspect_ratio_change_percent=_aspect_ratio_change_percent(_union_bbox(original_boxes), _union_bbox(smoothed_boxes)),
        point_reduction_percent=_point_reduction_percent(raw_point_count, smoothed_point_count),
        fallback_used=fallback_used,
        smoothing_profile=profile,
        straightened_segments=straightened_segments,
        curve_fitted_segments=curve_fitted_segments,
        rejected_cleanup_count=rejected_cleanup_count,
    )
    return vector_contours, report


def _contour_is_hole(index: int, hierarchy: np.ndarray | None) -> bool:
    if hierarchy is None:
        return False
    depth = 0
    parent_index = int(hierarchy[0][index][3])
    while parent_index >= 0:
        depth += 1
        parent_index = int(hierarchy[0][parent_index][3])
    return depth % 2 == 1


def straighten_long_runs(
    points: np.ndarray,
    tolerance_px: float,
    min_length_px: float,
    sharp_corner_angle_threshold: float,
) -> tuple[np.ndarray, int]:
    if len(points) < 5:
        return points, 0
    output: list[np.ndarray] = []
    count = 0
    index = 0
    n = len(points)
    while index < n:
        start = points[index]
        best_end = index
        for end in range(index + 2, min(index + 18, n)):
            segment = points[index : end + 1]
            chord = segment[-1] - start
            length = float(np.linalg.norm(chord))
            if length < min_length_px:
                continue
            if _max_distance_to_line(segment, start, segment[-1]) <= tolerance_px:
                best_end = end
        if best_end > index:
            if not output:
                output.append(start)
            output.append(points[best_end])
            count += 1
            index = best_end + 1
        else:
            output.append(points[index])
            index += 1
    if len(output) < 3:
        return points, 0
    cleaned = np.array(output, dtype=np.float32)
    cleaned = _restore_sharp_corner_samples(points, cleaned, sharp_corner_angle_threshold)
    return cleaned, count


def fit_curve_sections(
    points: np.ndarray,
    tolerance: float,
    min_length_px: float,
    max_error_percent: float,
    sharp_corner_angle_threshold: float,
) -> tuple[np.ndarray, int]:
    if len(points) < 6:
        return points, 0
    smoothed = points.copy()
    count = 0
    for index in range(len(points)):
        previous_point = points[index - 1]
        current_point = points[index]
        next_point = points[(index + 1) % len(points)]
        if _turn_angle(previous_point, current_point, next_point) < sharp_corner_angle_threshold:
            continue
        if np.linalg.norm(next_point - previous_point) < min_length_px:
            continue
        candidate = (previous_point + current_point * 2.0 + next_point) / 4.0
        error = float(np.linalg.norm(candidate - current_point))
        chord = max(1.0, float(np.linalg.norm(next_point - previous_point)))
        if error <= tolerance or (error / chord * 100.0) <= max_error_percent:
            smoothed[index] = candidate
            count += 1
    return smoothed, count


def merge_collinear_points(points: np.ndarray, tolerance_degrees: float) -> np.ndarray:
    if len(points) <= 3:
        return points
    kept: list[np.ndarray] = []
    tolerance = float(tolerance_degrees)
    for index in range(len(points)):
        previous_point = points[index - 1]
        current_point = points[index]
        next_point = points[(index + 1) % len(points)]
        angle = _turn_angle(previous_point, current_point, next_point)
        if abs(180.0 - angle) > tolerance:
            kept.append(current_point)
    if len(kept) < 3:
        return points
    return np.array(kept, dtype=np.float32)


def _restore_sharp_corner_samples(
    original: np.ndarray,
    cleaned: np.ndarray,
    sharp_corner_angle_threshold: float,
) -> np.ndarray:
    restored = [point for point in cleaned]
    for index in range(len(original)):
        angle = _turn_angle(original[index - 1], original[index], original[(index + 1) % len(original)])
        if angle < sharp_corner_angle_threshold:
            restored.append(original[index])
    return np.array(restored, dtype=np.float32)


def smooth_contour_points(
    points: np.ndarray,
    strength: int,
    sharp_corner_angle_threshold: float,
) -> np.ndarray:
    if len(points) < 4 or strength <= 0:
        return points
    smoothed = points.copy()
    for _ in range(strength):
        next_points: list[np.ndarray] = []
        for index in range(len(smoothed)):
            previous_point = smoothed[index - 1]
            current_point = smoothed[index]
            next_point = smoothed[(index + 1) % len(smoothed)]
            angle = _turn_angle(previous_point, current_point, next_point)
            if angle < sharp_corner_angle_threshold:
                next_points.append(current_point)
                continue
            q = current_point * 0.75 + next_point * 0.25
            r = current_point * 0.25 + next_point * 0.75
            next_points.extend([q, r])
        smoothed = np.array(next_points, dtype=np.float32)
    return smoothed


def _safe_approx_points(points: np.ndarray, simplify_tolerance: float) -> np.ndarray:
    if simplify_tolerance <= 0:
        return points
    contour = points.reshape(-1, 1, 2).astype(np.float32)
    simplified = cv2.approxPolyDP(contour, simplify_tolerance, closed=True)
    if len(simplified) < 3:
        return points
    return simplified.reshape(-1, 2).astype(np.float32)


def _candidate_is_safe(
    original_points: np.ndarray,
    candidate_points: np.ndarray,
    max_area_change_percent: float,
    max_bbox_change_percent: float,
    max_aspect_ratio_change_percent: float,
    max_point_reduction_percent: float,
) -> bool:
    if len(candidate_points) < 3:
        return False
    original_area = abs(float(cv2.contourArea(original_points.astype(np.float32))))
    candidate_area = abs(float(cv2.contourArea(candidate_points.astype(np.float32))))
    if _percent_change(original_area, candidate_area) > max_area_change_percent:
        return False

    original_bbox = cv2.boundingRect(np.round(original_points).astype(np.int32).reshape(-1, 1, 2))
    candidate_bbox = cv2.boundingRect(np.round(candidate_points).astype(np.int32).reshape(-1, 1, 2))
    if _bbox_change_percent(original_bbox, candidate_bbox) > max_bbox_change_percent:
        return False
    if _aspect_ratio_change_percent(original_bbox, candidate_bbox) > max_aspect_ratio_change_percent:
        return False
    if _point_reduction_percent(len(original_points), len(candidate_points)) > max_point_reduction_percent:
        return False
    return True


def vector_contours_to_mask(
    contours: list[VectorContour],
    shape: tuple[int, int],
    sample_resolution: int = 1,
) -> np.ndarray:
    scale = max(1, int(sample_resolution))
    height, width = shape
    canvas = np.zeros((height * scale, width * scale), dtype=np.uint8)
    for contour in contours:
        points = np.round(contour.points * scale).astype(np.int32).reshape(-1, 1, 2)
        color = 0 if contour.is_hole else 255
        cv2.drawContours(canvas, [points], -1, color, thickness=-1)
    if scale > 1:
        canvas = cv2.resize(canvas, (width, height), interpolation=cv2.INTER_AREA)
    return canvas > 127


def vector_contours_to_svg_paths(contours: list[VectorContour]) -> tuple[list[str], list[str]]:
    foreground: list[str] = []
    holes: list[str] = []
    for contour in contours:
        path = contour_to_smooth_path(contour.points)
        if contour.is_hole:
            holes.append(path)
        else:
            foreground.append(path)
    return foreground, holes


def contour_to_smooth_path(points: np.ndarray) -> str:
    if len(points) < 3:
        return ""
    commands = [f"M {_fmt(points[0][0])} {_fmt(points[0][1])}"]
    for index in range(1, len(points)):
        previous_point = points[index - 1]
        current_point = points[index]
        midpoint = (previous_point + current_point) / 2.0
        commands.append(
            f"Q {_fmt(previous_point[0])} {_fmt(previous_point[1])} {_fmt(midpoint[0])} {_fmt(midpoint[1])}"
        )
    last_point = points[-1]
    first_point = points[0]
    midpoint = (last_point + first_point) / 2.0
    commands.append(f"Q {_fmt(last_point[0])} {_fmt(last_point[1])} {_fmt(midpoint[0])} {_fmt(midpoint[1])}")
    commands.append("Z")
    return " ".join(commands)


def draw_contours_preview(
    shape: tuple[int, int],
    contours: list[VectorContour],
    output_path: Path,
) -> None:
    canvas = np.full((shape[0], shape[1], 3), 255, dtype=np.uint8)
    for contour in contours:
        points = np.round(contour.points).astype(np.int32).reshape(-1, 1, 2)
        color = (0, 120, 255) if contour.is_hole else (0, 170, 80)
        cv2.drawContours(canvas, [points], -1, color, 2)
    cv2.imwrite(str(output_path), cv2.cvtColor(canvas, cv2.COLOR_RGB2BGR))


def draw_before_after_overlay(
    shape: tuple[int, int],
    contours: list[VectorContour],
    output_path: Path,
) -> None:
    canvas = np.full((shape[0], shape[1], 3), 255, dtype=np.uint8)
    for contour in contours:
        original = np.round(contour.original_points).astype(np.int32).reshape(-1, 1, 2)
        smoothed = np.round(contour.points).astype(np.int32).reshape(-1, 1, 2)
        cv2.drawContours(canvas, [original], -1, (220, 60, 60), 1)
        cv2.drawContours(canvas, [smoothed], -1, (40, 150, 80), 1)
    cv2.imwrite(str(output_path), cv2.cvtColor(canvas, cv2.COLOR_RGB2BGR))


def write_geometry_report(report: GeometryReport, output_path: Path) -> None:
    lines = [
        f"original contour count: {report.original_contour_count}",
        f"smoothed contour count: {report.smoothed_contour_count}",
        f"original total points: {report.original_total_points}",
        f"smoothed total points: {report.smoothed_total_points}",
        f"area change percent: {report.area_change_percent:.2f}",
        f"bbox change percent: {report.bbox_change_percent:.2f}",
        f"aspect ratio change percent: {report.aspect_ratio_change_percent:.2f}",
        f"point reduction percent: {report.point_reduction_percent:.2f}",
        f"fallback used: {str(report.fallback_used).lower()}",
        f"smoothing profile used: {report.smoothing_profile}",
        f"straightened segments: {report.straightened_segments}",
        f"curve fitted segments: {report.curve_fitted_segments}",
        f"rejected cleanup count: {report.rejected_cleanup_count}",
    ]
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _turn_angle(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
    ba = a - b
    bc = c - b
    norm = float(np.linalg.norm(ba) * np.linalg.norm(bc))
    if norm == 0.0:
        return 180.0
    cosine = float(np.clip(np.dot(ba, bc) / norm, -1.0, 1.0))
    return math.degrees(math.acos(cosine))


def _fmt(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _max_distance_to_line(points: np.ndarray, start: np.ndarray, end: np.ndarray) -> float:
    line = end - start
    norm = float(np.linalg.norm(line))
    if norm == 0.0:
        return 0.0
    offsets = points - start
    distances = np.abs(line[0] * offsets[:, 1] - line[1] * offsets[:, 0]) / norm
    return float(np.max(distances))


def _percent_change(original: float, candidate: float) -> float:
    if original <= 0:
        return 0.0 if candidate <= 0 else 100.0
    return abs(candidate - original) / original * 100.0


def _point_reduction_percent(original: int, candidate: int) -> float:
    if original <= 0:
        return 0.0
    return max(0.0, (original - candidate) / original * 100.0)


def _bbox_change_percent(
    original: tuple[int, int, int, int] | None,
    candidate: tuple[int, int, int, int] | None,
) -> float:
    if original is None or candidate is None:
        return 0.0
    original_width = max(1, original[2])
    original_height = max(1, original[3])
    width_change = abs(candidate[2] - original_width) / original_width * 100.0
    height_change = abs(candidate[3] - original_height) / original_height * 100.0
    return max(width_change, height_change)


def _aspect_ratio_change_percent(
    original: tuple[int, int, int, int] | None,
    candidate: tuple[int, int, int, int] | None,
) -> float:
    if original is None or candidate is None:
        return 0.0
    original_ratio = max(1, original[2]) / max(1, original[3])
    candidate_ratio = max(1, candidate[2]) / max(1, candidate[3])
    return _percent_change(original_ratio, candidate_ratio)


def _union_bbox(boxes: list[tuple[int, int, int, int]]) -> tuple[int, int, int, int] | None:
    if not boxes:
        return None
    min_x = min(box[0] for box in boxes)
    min_y = min(box[1] for box in boxes)
    max_x = max(box[0] + box[2] for box in boxes)
    max_y = max(box[1] + box[3] for box in boxes)
    return min_x, min_y, max_x - min_x, max_y - min_y
