from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from spool_house_ai.config import PreviewConfig
from spool_house_ai.processing.analysis import ImageAnalysis
from spool_house_ai.processing.geometry import draw_before_after_overlay, draw_contours_preview, write_geometry_report


def create_preview(
    silhouette_png_path: Path,
    output_path: Path,
    config: PreviewConfig,
) -> None:
    """Create a simple PNG preview of the raised silhouette."""
    with Image.open(silhouette_png_path) as image:
        silhouette = image.convert("L")

    silhouette.thumbnail((config.image_size_px, config.image_size_px), Image.Resampling.LANCZOS)
    preview = Image.new("RGB", silhouette.size, "white")
    shadow = Image.new("RGBA", silhouette.size, (0, 0, 0, 0))
    relief = Image.new("RGBA", silhouette.size, (0, 0, 0, 0))

    mask = silhouette.point(lambda value: 255 if value < 128 else 0)
    shadow_mask = mask.filter(ImageFilter.GaussianBlur(radius=6))

    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.bitmap((8, 8), shadow_mask, fill=(0, 0, 0, 60))
    relief_draw = ImageDraw.Draw(relief)
    relief_draw.bitmap((0, 0), mask, fill=(35, 35, 35, 255))

    preview = Image.alpha_composite(preview.convert("RGBA"), shadow)
    preview = Image.alpha_composite(preview, relief)
    preview.convert("RGB").save(output_path)


def save_stage_previews(
    original_path: Path,
    cleaned_png_path: Path,
    analysis: ImageAnalysis,
    output_dir: Path,
    stem: str,
    config: PreviewConfig,
    svg_path: Path | None = None,
    stl_path: Path | None = None,
) -> None:
    """Save PNG previews for cleaned, threshold, contour, SVG, and STL stages."""
    _save_cleaned_preview(original_path, output_dir / f"{stem}_preview_original.png", config)
    _save_cleaned_preview(cleaned_png_path, output_dir / f"{stem}_preview_cleaned.png", config)
    _save_mask_preview(analysis.threshold_mask, output_dir / f"{stem}_preview_threshold.png", config)
    _save_contour_debug_preview(analysis, output_dir / f"{stem}_preview_contours.png", config)
    _save_mask_preview(analysis.body_mask, output_dir / f"{stem}_preview_body_mask.png", config)
    _save_mask_preview(analysis.hole_mask, output_dir / f"{stem}_preview_hole_mask.png", config)
    _save_mask_preview(analysis.detail_mask, output_dir / f"{stem}_preview_detail_mask.png", config)
    _save_mask_preview(analysis.final_mask, output_dir / f"{stem}_preview_svg.png", config)
    _save_stl_style_preview(analysis.final_mask, output_dir / f"{stem}_preview_stl.png", config)
    _save_mask_preview(analysis.removed_island_mask, output_dir / "removed_islands_debug.png", config)
    _save_comparison(original_path, cleaned_png_path, output_dir / "original_vs_cleaned_compare.png", config)
    _save_comparison(original_path, output_dir / f"{stem}_preview_body_mask.png", output_dir / "original_vs_body_mask_compare.png", config)
    _save_comparison(original_path, output_dir / f"{stem}_preview_detail_mask.png", output_dir / "original_vs_detail_mask_compare.png", config)
    _save_comparison(original_path, output_dir / f"{stem}_preview_svg.png", output_dir / "original_vs_final_vector_compare.png", config)
    _save_comparison(original_path, output_dir / f"{stem}_preview_stl.png", output_dir / "original_vs_stl_preview_compare.png", config)
    save_geometry_debug_previews(analysis, output_dir, stem, config)


def save_geometry_debug_previews(
    analysis: ImageAnalysis,
    output_dir: Path,
    stem: str,
    config: PreviewConfig,
) -> None:
    raw_threshold_path = output_dir / "raw_threshold.png"
    raw_contours_path = output_dir / "raw_contours.png"
    smoothed_contours_path = output_dir / "smoothed_contours.png"
    final_vector_path = output_dir / "final_vector_preview.png"

    _save_mask_preview(analysis.raw_threshold_mask, raw_threshold_path, config)
    _save_raw_contour_preview(analysis.raw_threshold_mask, raw_contours_path, config)
    draw_contours_preview(analysis.final_mask.shape, analysis.vector_contours, smoothed_contours_path)
    _save_mask_preview(analysis.final_mask, final_vector_path, config)
    draw_before_after_overlay(
        analysis.final_mask.shape,
        analysis.vector_contours,
        output_dir / "geometry_before_after_overlay.png",
    )
    write_geometry_report(analysis.geometry_report, output_dir / "geometry_report.txt")


def _save_cleaned_preview(source_path: Path, output_path: Path, config: PreviewConfig) -> None:
    with Image.open(source_path) as image:
        image = image.convert("RGBA")
    canvas = Image.new("RGBA", image.size, (255, 255, 255, 255))
    checker = _checkerboard(image.size)
    canvas = Image.alpha_composite(canvas, checker)
    canvas = Image.alpha_composite(canvas, image)
    canvas.thumbnail((config.image_size_px, config.image_size_px), Image.Resampling.LANCZOS)
    canvas.convert("RGB").save(output_path)


def _save_mask_preview(mask: np.ndarray, output_path: Path, config: PreviewConfig) -> None:
    image = Image.fromarray(np.where(mask, 0, 255).astype(np.uint8), mode="L")
    image.thumbnail((config.image_size_px, config.image_size_px), Image.Resampling.NEAREST)
    image.save(output_path)


def _save_contour_debug_preview(
    analysis: ImageAnalysis,
    output_path: Path,
    config: PreviewConfig,
) -> None:
    height, width = analysis.final_mask.shape
    debug = np.full((height, width, 3), 255, dtype=np.uint8)
    debug[analysis.body_mask] = (54, 54, 54)
    debug[analysis.hole_mask] = (255, 190, 70)
    debug[analysis.detail_mask] = (80, 120, 255)

    for feature in analysis.kept_features:
        color = (0, 170, 80) if not feature.is_hole else (0, 120, 255)
        cv2.drawContours(debug, [feature.contour], -1, color, 2)

    for feature in analysis.removed_features:
        cv2.drawContours(debug, [feature.contour], -1, (220, 50, 50), 2)

    image = Image.fromarray(debug, mode="RGB")
    image.thumbnail((config.image_size_px, config.image_size_px), Image.Resampling.LANCZOS)
    image.save(output_path)


def _save_raw_contour_preview(mask: np.ndarray, output_path: Path, config: PreviewConfig) -> None:
    image = mask.astype(np.uint8) * 255
    contours, _ = cv2.findContours(image, cv2.RETR_TREE, cv2.CHAIN_APPROX_NONE)
    canvas = np.full((*mask.shape, 3), 255, dtype=np.uint8)
    cv2.drawContours(canvas, contours, -1, (220, 50, 50), 1)
    preview = Image.fromarray(canvas, mode="RGB")
    preview.thumbnail((config.image_size_px, config.image_size_px), Image.Resampling.LANCZOS)
    preview.save(output_path)


def _save_stl_style_preview(mask: np.ndarray, output_path: Path, config: PreviewConfig) -> None:
    foreground = mask.astype(np.uint8) * 255
    distance = cv2.distanceTransform(foreground, cv2.DIST_L2, 3)
    if distance.max() > 0:
        distance = distance / distance.max()
    shaded = np.full((*mask.shape, 3), 245, dtype=np.uint8)
    shaded[mask] = np.stack(
        [
            35 + (distance[mask] * 70),
            35 + (distance[mask] * 70),
            35 + (distance[mask] * 70),
        ],
        axis=1,
    ).astype(np.uint8)
    image = Image.fromarray(shaded, mode="RGB")
    image.thumbnail((config.image_size_px, config.image_size_px), Image.Resampling.LANCZOS)
    image.save(output_path)


def _save_comparison(
    original_path: Path,
    processed_path: Path,
    output_path: Path,
    config: PreviewConfig,
) -> None:
    if not processed_path.exists():
        return
    with Image.open(original_path) as original_image:
        original = original_image.convert("RGB")
    with Image.open(processed_path) as processed_image:
        processed = processed_image.convert("RGB")
    original.thumbnail((config.image_size_px // 2, config.image_size_px // 2), Image.Resampling.LANCZOS)
    processed.thumbnail((config.image_size_px // 2, config.image_size_px // 2), Image.Resampling.LANCZOS)
    height = max(original.height, processed.height)
    width = original.width + processed.width
    canvas = Image.new("RGB", (width, height), "white")
    canvas.paste(original, (0, 0))
    canvas.paste(processed, (original.width, 0))
    canvas.save(output_path)


def _checkerboard(size: tuple[int, int]) -> Image.Image:
    width, height = size
    block = 16
    image = Image.new("RGBA", size, (240, 240, 240, 255))
    draw = ImageDraw.Draw(image)
    for y in range(0, height, block):
        for x in range(0, width, block):
            if (x // block + y // block) % 2 == 0:
                draw.rectangle((x, y, x + block - 1, y + block - 1), fill=(220, 220, 220, 255))
    return image
