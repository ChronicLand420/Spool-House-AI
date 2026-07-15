from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from spool_house_ai.config import normalize_cleanup_preset


RECOMMENDATION_ANALYSIS_VERSION = "artwork-recommendation-v1"
MIN_RECOMMENDED_THICKNESS_MM = 2.0
PRESET_SCORE_ORDER = (
    "line_art",
    "detail_preserving",
    "drip_logo",
    "splatter_logo",
    "preserve_floating_islands",
    "clean_logo",
    "default",
)
SUPPORTED_RECOMMENDATION_PRODUCTS = {"flat_relief", "keychain", "wall_art"}


@dataclass(frozen=True)
class ArtworkMetrics:
    source_width: int
    source_height: int
    analysis_width: int
    analysis_height: int
    foreground_percent: float
    edge_density: float
    component_count: int
    significant_component_count: int
    tiny_component_count: int
    small_component_count: int
    contour_count: int
    hole_count: int
    average_hole_area_percent: float
    narrow_feature_percent: float
    median_feature_width_px: float
    large_solid_region_percent: float
    boundary_roughness: float
    lower_profile_variation: float
    lower_slender_component_count: int
    contrast_quality: float


@dataclass(frozen=True)
class ArtworkRecommendation:
    recommended_preset: str
    recommended_thickness_mm: float
    confidence: str
    reasons: tuple[str, ...]
    metrics: ArtworkMetrics | None = None
    scores: dict[str, float] = field(default_factory=dict)
    unavailable_reason: str = ""

    @property
    def available(self) -> bool:
        return not self.unavailable_reason


class ArtworkRecommendationCache:
    def __init__(self) -> None:
        self._cache: dict[tuple[str, float, str, str], ArtworkRecommendation] = {}

    def get(
        self,
        image_path: Path,
        *,
        output_width_mm: float,
        product_mode: str,
        factory: Callable[[Path, float, str], ArtworkRecommendation] | None = None,
    ) -> ArtworkRecommendation:
        key = recommendation_cache_key(image_path, output_width_mm=output_width_mm, product_mode=product_mode)
        if key not in self._cache:
            builder = factory or (lambda path, width, product: safe_recommend_artwork_settings(
                path,
                output_width_mm=width,
                product_mode=product,
            ))
            self._cache[key] = builder(image_path, output_width_mm, product_mode)
        return self._cache[key]

    def clear(self) -> None:
        self._cache.clear()


def recommendation_cache_key(
    image_path: Path,
    *,
    output_width_mm: float,
    product_mode: str,
) -> tuple[str, float, str, str]:
    return (
        _sha256_file(image_path),
        round(float(output_width_mm), 3),
        str(product_mode or "flat_relief"),
        RECOMMENDATION_ANALYSIS_VERSION,
    )


def safe_recommend_artwork_settings(
    image_path: Path,
    *,
    output_width_mm: float = 120.0,
    product_mode: str = "flat_relief",
) -> ArtworkRecommendation:
    try:
        return recommend_artwork_settings(
            image_path,
            output_width_mm=output_width_mm,
            product_mode=product_mode,
        )
    except Exception as error:
        return ArtworkRecommendation(
            recommended_preset="default",
            recommended_thickness_mm=2.5,
            confidence="low",
            reasons=("Recommendation unavailable; current selections were kept.",),
            unavailable_reason=str(error),
        )


def recommend_artwork_settings(
    image_path: Path,
    *,
    output_width_mm: float = 120.0,
    product_mode: str = "flat_relief",
) -> ArtworkRecommendation:
    product_mode = str(product_mode or "flat_relief")
    if product_mode not in SUPPORTED_RECOMMENDATION_PRODUCTS:
        return ArtworkRecommendation(
            recommended_preset="default",
            recommended_thickness_mm=2.5,
            confidence="low",
            reasons=("Cleanup presets do not apply to this product mode.",),
            unavailable_reason="Cleanup presets are ignored for this product mode.",
        )

    metrics = analyze_artwork_metrics(image_path)
    scores = _score_presets(metrics)
    recommended_preset = _top_scoring_preset(scores)
    thickness = _recommend_thickness(metrics, recommended_preset, output_width_mm)
    confidence = _confidence_for_scores(scores)
    reasons = _reasons_for_recommendation(metrics, recommended_preset)
    return ArtworkRecommendation(
        recommended_preset=recommended_preset,
        recommended_thickness_mm=thickness,
        confidence=confidence,
        reasons=reasons,
        metrics=metrics,
        scores=scores,
    )


def analyze_artwork_metrics(image_path: Path, *, analysis_max_dimension: int = 512) -> ArtworkMetrics:
    rgb = _load_rgb(image_path)
    source_height, source_width = rgb.shape[:2]
    scale = min(1.0, float(analysis_max_dimension) / max(source_width, source_height))
    analysis_width = max(1, int(round(source_width * scale)))
    analysis_height = max(1, int(round(source_height * scale)))
    if scale < 1.0:
        resized = cv2.resize(rgb, (analysis_width, analysis_height), interpolation=cv2.INTER_AREA)
    else:
        resized = cv2.resize(rgb, (analysis_width, analysis_height), interpolation=cv2.INTER_LINEAR)

    gray = cv2.cvtColor(resized, cv2.COLOR_RGB2GRAY)
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    threshold, _ = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask = _choose_foreground_mask(blurred, threshold)
    total_pixels = int(mask.size)
    foreground_pixels = int(mask.sum())
    foreground_percent = foreground_pixels / max(1, total_pixels)

    components, significant_components, tiny_components, small_components = _component_counts(mask)
    contours, hierarchy = cv2.findContours(mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_NONE)
    hole_count, average_hole_area_percent = _hole_metrics(contours, hierarchy, total_pixels)
    edge_density = float(np.mean(cv2.Canny(gray, 80, 160) > 0))
    boundary_roughness = _boundary_roughness(contours, foreground_pixels)
    narrow_feature_percent, median_feature_width_px, large_solid_region_percent = _feature_width_metrics(mask)
    lower_profile_variation, lower_slender_component_count = _lower_boundary_metrics(mask)
    contrast_quality = float((np.percentile(gray, 95) - np.percentile(gray, 5)) / 255.0)

    return ArtworkMetrics(
        source_width=source_width,
        source_height=source_height,
        analysis_width=analysis_width,
        analysis_height=analysis_height,
        foreground_percent=round(foreground_percent, 5),
        edge_density=round(edge_density, 5),
        component_count=components,
        significant_component_count=significant_components,
        tiny_component_count=tiny_components,
        small_component_count=small_components,
        contour_count=len(contours),
        hole_count=hole_count,
        average_hole_area_percent=round(average_hole_area_percent, 6),
        narrow_feature_percent=round(narrow_feature_percent, 5),
        median_feature_width_px=round(median_feature_width_px, 4),
        large_solid_region_percent=round(large_solid_region_percent, 5),
        boundary_roughness=round(boundary_roughness, 4),
        lower_profile_variation=round(lower_profile_variation, 5),
        lower_slender_component_count=lower_slender_component_count,
        contrast_quality=round(contrast_quality, 5),
    )


def _score_presets(metrics: ArtworkMetrics) -> dict[str, float]:
    fine_detail_signal = (
        min(metrics.narrow_feature_percent / 0.35, 2.0)
        + min(metrics.edge_density / 0.08, 1.8)
        + min(metrics.hole_count / 60.0, 2.2)
        + min(metrics.contour_count / 90.0, 1.6)
    )
    splatter_signal = (
        min(metrics.small_component_count / 5.0, 1.8)
        + min(metrics.component_count / 18.0, 1.0)
        + min(metrics.boundary_roughness / 28.0, 1.8)
        + (0.8 if metrics.foreground_percent < 0.18 and metrics.component_count >= 8 else 0.0)
        + (0.6 if 12 <= metrics.hole_count <= 60 else 0.0)
    )
    drip_signal = (
        metrics.lower_slender_component_count * 2.8
        + min(metrics.lower_profile_variation / 0.18, 1.6)
        + (1.0 if metrics.foreground_percent < 0.14 else 0.0)
        + (0.7 if metrics.contour_count <= 30 else 0.0)
    )
    disconnected_signal = (
        min(metrics.significant_component_count / 4.0, 2.0)
        + (0.8 if metrics.component_count >= 8 and metrics.tiny_component_count < metrics.component_count * 0.5 else 0.0)
    )
    clean_signal = (
        (1.4 if metrics.significant_component_count <= 2 else 0.0)
        + min(metrics.large_solid_region_percent / 0.32, 1.4)
        + (0.8 if metrics.edge_density < 0.035 else 0.0)
        + (0.6 if metrics.boundary_roughness < 28 else 0.0)
    )

    scores = {
        "default": 2.2,
        "clean_logo": 1.0 + clean_signal,
        "detail_preserving": 1.2 + fine_detail_signal + min(metrics.boundary_roughness / 45.0, 1.0),
        "drip_logo": 0.8 + drip_signal,
        "splatter_logo": 0.7 + splatter_signal,
        "line_art": 1.0 + fine_detail_signal + (1.0 if metrics.narrow_feature_percent > 0.35 else 0.0),
        "preserve_floating_islands": 0.7 + disconnected_signal,
    }

    if metrics.narrow_feature_percent > 0.45 and metrics.median_feature_width_px < 4.0:
        scores["line_art"] += 1.5
    if metrics.foreground_percent > 0.22 and metrics.boundary_roughness > 38:
        scores["detail_preserving"] += 1.2
        scores["line_art"] -= 0.8
    if metrics.small_component_count >= 6 and metrics.foreground_percent < 0.2:
        scores["splatter_logo"] += 1.3
        scores["line_art"] -= 0.8
    if metrics.hole_count >= 30 and metrics.small_component_count < 4:
        scores["detail_preserving"] += 1.5
        scores["line_art"] += 0.8
        scores["splatter_logo"] -= 1.4
        scores["clean_logo"] -= 0.8
    if metrics.large_solid_region_percent > 0.5 and metrics.component_count > 8:
        scores["clean_logo"] -= 1.0
    if metrics.lower_slender_component_count > 0 and metrics.foreground_percent < 0.14:
        scores["drip_logo"] += 1.5
        scores["splatter_logo"] -= 0.5
    if metrics.component_count <= 3 and metrics.large_solid_region_percent > 0.28 and metrics.edge_density < 0.04:
        scores["clean_logo"] += 1.4
    if max(scores.values()) < 4.0:
        scores["default"] += 0.8

    return {preset: round(score, 4) for preset, score in scores.items()}


def _top_scoring_preset(scores: dict[str, float]) -> str:
    return max(PRESET_SCORE_ORDER, key=lambda preset: (scores.get(preset, 0.0), -PRESET_SCORE_ORDER.index(preset)))


def _confidence_for_scores(scores: dict[str, float]) -> str:
    ordered = sorted(scores.values(), reverse=True)
    top = ordered[0] if ordered else 0.0
    margin = top - (ordered[1] if len(ordered) > 1 else 0.0)
    if top >= 6.0 and margin >= 1.2:
        return "high"
    if top >= 4.0 and margin >= 0.55:
        return "medium"
    return "low"


def _recommend_thickness(metrics: ArtworkMetrics, preset: str, output_width_mm: float) -> float:
    if preset in {"line_art", "detail_preserving"} and (
        metrics.narrow_feature_percent > 0.32 or metrics.median_feature_width_px < 4.0
    ):
        return 2.0
    if preset in {"line_art", "detail_preserving"}:
        return 2.5
    if preset in {"drip_logo", "splatter_logo"}:
        return 3.0 if metrics.large_solid_region_percent > 0.28 and output_width_mm >= 140 else 2.5
    if preset == "clean_logo" and metrics.large_solid_region_percent > 0.36:
        return 3.0
    if preset == "clean_logo" and output_width_mm >= 220 and metrics.edge_density < 0.025:
        return 5.0
    return 2.5


def _reasons_for_recommendation(metrics: ArtworkMetrics, preset: str) -> tuple[str, ...]:
    reasons: list[str] = []
    if preset in {"line_art", "detail_preserving"}:
        if metrics.narrow_feature_percent > 0.25:
            reasons.append("many narrow interior features")
        if metrics.hole_count >= 20:
            reasons.append("many holes and negative spaces")
        if metrics.edge_density >= 0.05 or metrics.contour_count >= 60:
            reasons.append("high contour and edge density")
    if preset == "drip_logo":
        if metrics.lower_slender_component_count > 0:
            reasons.append("narrow downward drip-like regions")
        if metrics.lower_profile_variation >= 0.16:
            reasons.append("uneven lower boundary suggests dangling details")
    if preset == "splatter_logo":
        if metrics.small_component_count >= 4:
            reasons.append("many small rough components")
        if metrics.boundary_roughness >= 24:
            reasons.append("rough boundary texture")
    if preset == "preserve_floating_islands":
        if metrics.significant_component_count >= 3:
            reasons.append("multiple meaningful disconnected components")
    if preset == "clean_logo":
        if metrics.large_solid_region_percent >= 0.25:
            reasons.append("large solid printable regions")
        if metrics.edge_density < 0.04:
            reasons.append("low edge density")
    if preset == "default":
        reasons.append("mixed artwork without a strong specialized signal")

    if metrics.contrast_quality >= 0.45:
        reasons.append("good source contrast")
    else:
        reasons.append("lower contrast source")

    deduped: list[str] = []
    for reason in reasons:
        if reason not in deduped:
            deduped.append(reason)
    return tuple(deduped[:4] or ["balanced image structure"])


def _choose_foreground_mask(gray: np.ndarray, threshold: float) -> np.ndarray:
    candidates = []
    for mask in (gray < threshold, gray > threshold):
        candidate = mask.astype(np.uint8)
        border = np.concatenate([candidate[0, :], candidate[-1, :], candidate[:, 0], candidate[:, -1]])
        area = float(candidate.mean())
        candidates.append((float(border.mean()), abs(area - 0.30), candidate))
    return min(candidates, key=lambda item: (item[0], item[1]))[2]


def _component_counts(mask: np.ndarray) -> tuple[int, int, int, int]:
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    if count <= 1:
        return 0, 0, 0, 0
    areas = stats[1:, cv2.CC_STAT_AREA]
    total = max(1, int(mask.size))
    tiny_threshold = max(6, total * 0.00025)
    significant_threshold = max(24, total * 0.002)
    tiny = int(np.sum(areas < tiny_threshold))
    small = int(np.sum((areas >= tiny_threshold) & (areas < significant_threshold)))
    significant = int(np.sum(areas >= significant_threshold))
    return int(len(areas)), significant, tiny, small


def _hole_metrics(contours: tuple[np.ndarray, ...], hierarchy: np.ndarray | None, total_pixels: int) -> tuple[int, float]:
    if hierarchy is None or len(contours) == 0:
        return 0, 0.0
    hole_areas = [
        cv2.contourArea(contours[index])
        for index, values in enumerate(hierarchy[0])
        if int(values[3]) >= 0
    ]
    if not hole_areas:
        return 0, 0.0
    return len(hole_areas), float(np.mean(hole_areas) / max(1, total_pixels))


def _boundary_roughness(contours: tuple[np.ndarray, ...], foreground_pixels: int) -> float:
    perimeter = sum(cv2.arcLength(contour, True) for contour in contours if len(contour) >= 3)
    return float(perimeter / max(1.0, foreground_pixels ** 0.5))


def _feature_width_metrics(mask: np.ndarray) -> tuple[float, float, float]:
    if not np.any(mask):
        return 0.0, 0.0, 0.0
    distance = cv2.distanceTransform(mask.astype(np.uint8), cv2.DIST_L2, 3)
    values = distance[mask > 0]
    if values.size == 0:
        return 0.0, 0.0, 0.0
    narrow = float(np.mean(values <= 1.5))
    median_width = float(np.median(values) * 2.0)
    solid = float(np.mean(values >= 6.0))
    return narrow, median_width, solid


def _lower_boundary_metrics(mask: np.ndarray) -> tuple[float, int]:
    if not np.any(mask):
        return 0.0, 0
    ys, xs = np.where(mask > 0)
    y0, y1 = int(ys.min()), int(ys.max())
    x0, x1 = int(xs.min()), int(xs.max())
    roi = mask[y0 : y1 + 1, x0 : x1 + 1]
    depths: list[float] = []
    for x in range(roi.shape[1]):
        column_y = np.where(roi[:, x] > 0)[0]
        if column_y.size:
            depths.append(float(column_y.max() / max(1, roi.shape[0])))
    variation = float(np.std(depths)) if depths else 0.0

    lower_mask = np.zeros_like(roi)
    lower_start = int(roi.shape[0] * 0.55)
    lower_mask[lower_start:, :] = roi[lower_start:, :]
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(lower_mask.astype(np.uint8), 8)
    slender = 0
    for stat in stats[1:count]:
        width = int(stat[cv2.CC_STAT_WIDTH])
        height = int(stat[cv2.CC_STAT_HEIGHT])
        area = int(stat[cv2.CC_STAT_AREA])
        if area <= 8:
            continue
        fill = area / max(1, width * height)
        if height / max(1, width) > 1.5 and fill < 0.65:
            slender += 1
    return variation, slender


def _load_rgb(path: Path) -> np.ndarray:
    data = np.fromfile(path, dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Could not read image: {path}")
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
