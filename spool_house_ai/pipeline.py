from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Callable

from PIL import UnidentifiedImageError

from spool_house_ai.config import AppConfig
from spool_house_ai.processing.analysis import analyze_image, save_mask
from spool_house_ai.processing.background import background_removal_available, remove_background
from spool_house_ai.processing.preview import create_preview, save_stage_previews
from spool_house_ai.processing.stl import create_relief_stl, validate_stl_mesh, write_mesh_report
from spool_house_ai.processing.vectorize import create_svg


StageCallback = Callable[[str, str, str, Path | None], None]


class ImagePipeline:
    def __init__(self, config: AppConfig, logger: logging.Logger) -> None:
        self.config = config
        self.logger = logger

    def process(self, image_path: Path, stage_callback: StageCallback | None = None) -> bool:
        image_path = image_path.resolve()
        if not image_path.exists():
            self.logger.warning("Skipped missing file: %s", image_path)
            return False

        output_dir = self.config.output_dir / image_path.stem
        output_dir.mkdir(parents=True, exist_ok=True)

        cleaned_png_path = output_dir / f"{image_path.stem}_cleaned.png"
        silhouette_png_path = output_dir / f"{image_path.stem}_silhouette.png"
        svg_path = output_dir / f"{image_path.stem}.svg"
        stl_path = output_dir / f"{image_path.stem}.stl"
        mesh_report_path = output_dir / "mesh_report.json"
        preview_path = output_dir / f"{image_path.stem}_preview.png"
        body_mask_path = output_dir / f"{image_path.stem}_body_mask.png"
        detail_mask_path = output_dir / f"{image_path.stem}_detail_mask.png"
        contour_debug_path = output_dir / f"{image_path.stem}_contour_debug.png"
        settings_path = output_dir / "job_settings.yaml"

        self.logger.info("Processing image: %s", image_path.name)
        _emit(stage_callback, "Intake Room", "active", "Image queued for processing", image_path)
        if not self.config.pipeline.background_removal_enabled:
            self.logger.info("Background removal is disabled; using the original image as the cleaned PNG")
        elif not background_removal_available():
            self.logger.warning("rembg is not installed; using the original image as the cleaned PNG")

        try:
            remove_background(
                image_path,
                cleaned_png_path,
                enabled=self.config.pipeline.background_removal_enabled,
            )
            self.logger.info("Cleanup stage complete: %s", cleaned_png_path)
            _emit(stage_callback, "Cleanup Lab", "done", "Cleaned image saved", cleaned_png_path)
        except (UnidentifiedImageError, OSError) as error:
            self.logger.warning("Skipped invalid image %s: %s", image_path, error)
            return False
        except ImportError as error:
            self.logger.error("Missing dependency while cleaning image %s: %s", image_path, error)
            return False
        except Exception:
            self.logger.exception("Failed to clean image: %s", image_path)
            return False

        try:
            _emit(stage_callback, "Detail Analyzer", "active", "Classifying body, holes, and detail strokes", cleaned_png_path)
            analysis = analyze_image(
                cleaned_png_path,
                silhouette_png_path,
                self.config.silhouette,
            )
            save_mask(analysis.body_mask, body_mask_path)
            save_mask(analysis.detail_mask, detail_mask_path)
            save_mask(analysis.hole_mask, output_dir / f"{image_path.stem}_hole_mask.png")
            self.logger.info(
                "Analysis stage complete: body/hole/detail masks saved; contour points %s -> %s",
                analysis.geometry_report.original_total_points,
                analysis.geometry_report.smoothed_total_points,
            )
            _emit(stage_callback, "Detail Analyzer", "done", "Analysis masks created", body_mask_path)
            _emit(stage_callback, "Vector Workshop", "active", "Writing editable SVG paths", silhouette_png_path)
            create_svg(analysis, svg_path, self.config.svg)
            self.logger.info("Vector stage complete: %s", svg_path)
            save_stage_previews(
                original_path=image_path,
                cleaned_png_path=cleaned_png_path,
                analysis=analysis,
                output_dir=output_dir,
                stem=image_path.stem,
                config=self.config.preview,
                svg_path=svg_path,
                stl_path=stl_path,
            )
            preview_contours = output_dir / f"{image_path.stem}_preview_contours.png"
            if preview_contours.exists():
                shutil.copyfile(preview_contours, contour_debug_path)
            _emit(stage_callback, "Vector Workshop", "done", "SVG created", svg_path)
        except ImportError as error:
            self.logger.error("Missing dependency while analyzing image %s: %s", image_path, error)
            return False
        except Exception:
            self.logger.exception("Failed to create PNG/SVG outputs for image: %s", image_path)
            return False

        stl_created = False
        try:
            _emit(stage_callback, "Mesh Forge", "active", "Generating printable mesh", svg_path)
            stl_result = create_relief_stl(analysis, stl_path, self.config.stl)
            self.logger.info("STL backend requested: %s", stl_result.requested_backend)
            self.logger.info("STL backend used: %s", stl_result.actual_backend)
            if stl_result.fallback_used:
                self.logger.warning(
                    "Requested STL backend %s fell back to %s: %s",
                    stl_result.requested_backend,
                    stl_result.actual_backend,
                    stl_result.fallback_reason,
                )
            mesh_report = validate_stl_mesh(
                stl_path,
                requested_backend=stl_result.requested_backend,
                actual_backend=stl_result.actual_backend,
                fallback_reason=stl_result.fallback_reason,
            )
            write_mesh_report(mesh_report, mesh_report_path)
            if mesh_report.failures:
                for failure in mesh_report.failures:
                    self.logger.error("Mesh validation failure: %s", failure)
                _emit(stage_callback, "Mesh Forge", "failed", f"Mesh report: {mesh_report_path}", mesh_report_path)
            elif mesh_report.warnings:
                for warning in mesh_report.warnings:
                    self.logger.warning("Mesh validation warning: %s", warning)
                self.logger.warning("Mesh report saved: %s", mesh_report_path)
                _emit(stage_callback, "Mesh Forge", "done", f"STL created with warnings: {mesh_report_path}", stl_path)
            else:
                self.logger.info("Mesh validation passed: %s", mesh_report_path)
                _emit(stage_callback, "Mesh Forge", "done", "STL mesh created", stl_path)
            stl_created = not mesh_report.failures
            self.logger.info("Mesh stage complete: %s", stl_path)
        except ImportError as error:
            self.logger.error("Missing dependency while generating STL %s: %s", image_path, error)
            _emit(stage_callback, "Mesh Forge", "failed", f"Missing dependency: {error}", None)
        except Exception:
            self.logger.exception("Failed to generate STL for %s; PNG/SVG outputs were kept", image_path)
            _emit(stage_callback, "Mesh Forge", "failed", "STL generation failed; PNG/SVG kept", None)

        try:
            _emit(stage_callback, "Render Bay", "active", "Rendering previews", silhouette_png_path)
            create_preview(silhouette_png_path, preview_path, self.config.preview)
            _emit(stage_callback, "Render Bay", "done", "Preview rendered", preview_path)
        except Exception:
            self.logger.exception("Failed to create final preview for image: %s", image_path)

        _write_job_settings(settings_path, self.config)
        _emit(stage_callback, "Output Vault", "done", "Output folder is ready", output_dir)

        self.logger.info("Created cleaned PNG: %s", cleaned_png_path)
        self.logger.info("Created SVG: %s", svg_path)
        if stl_created:
            self.logger.info("Created STL: %s", stl_path)
            self.logger.info("Created mesh report: %s", mesh_report_path)
        self.logger.info("Created preview: %s", preview_path)
        return stl_created


def _emit(callback: StageCallback | None, room: str, state: str, message: str, thumbnail: Path | None) -> None:
    if callback is not None:
        callback(room, state, message, thumbnail)


def _write_job_settings(path: Path, config: AppConfig) -> None:
    lines = [
        "product:",
        f"  stl_backend: {config.stl.stl_backend}",
        f"  product_mode: {config.stl.product_mode}",
        f"  detail_mode: {config.stl.detail_mode}",
        f"  extrusion_height_mm: {config.stl.extrusion_height_mm}",
        f"  base_height_mm: {config.stl.base_height_mm}",
        f"  detail_height_mm: {config.stl.detail_height_mm}",
        f"  engraving_depth_mm: {config.stl.engraving_depth_mm}",
        f"  output_scale_mm: {config.stl.output_scale_mm}",
        f"  add_keychain_hole: {str(config.stl.add_keychain_hole).lower()}",
        f"  keychain_hole_diameter_mm: {config.stl.keychain_hole_diameter_mm}",
        "analysis:",
        f"  threshold_value: {config.silhouette.threshold_value}",
        f"  smoothing_strength: {config.silhouette.smoothing_strength}",
        f"  min_contour_area: {config.silhouette.min_contour_area}",
        f"  simplify_tolerance: {config.silhouette.simplify_tolerance}",
        f"  preserve_holes: {str(config.silhouette.preserve_holes).lower()}",
        f"  preserve_internal_details: {str(config.silhouette.preserve_internal_details).lower()}",
        f"  default_detail_behavior: {config.silhouette.default_detail_behavior}",
        "geometry:",
        f"  upscale_factor: {config.silhouette.upscale_factor}",
        f"  pre_blur_radius: {config.silhouette.pre_blur_radius}",
        f"  adaptive_threshold: {str(config.silhouette.adaptive_threshold).lower()}",
        f"  morphology_enabled: {str(config.silhouette.morphology_enabled).lower()}",
        f"  morphology_kernel_size: {config.silhouette.morph_kernel_size}",
        f"  vectorizer_backend: {config.svg.vectorizer_backend}",
        f"  contour_smoothing_enabled: {str(config.silhouette.contour_smoothing_enabled).lower()}",
        f"  contour_smoothing_strength: {config.silhouette.contour_smoothing_strength}",
        f"  collinear_merge_tolerance: {config.silhouette.collinear_merge_tolerance}",
        f"  curve_sample_resolution: {config.stl.curve_sample_resolution}",
        f"  sharp_corner_angle_threshold: {config.silhouette.sharp_corner_angle_threshold}",
        f"  safe_smoothing_enabled: {str(config.silhouette.safe_smoothing_enabled).lower()}",
        f"  smoothing_profile: {config.silhouette.smoothing_profile}",
        f"  max_area_change_percent: {config.silhouette.max_area_change_percent}",
        f"  max_bbox_change_percent: {config.silhouette.max_bbox_change_percent}",
        f"  max_aspect_ratio_change_percent: {config.silhouette.max_aspect_ratio_change_percent}",
        f"  max_point_reduction_percent: {config.silhouette.max_point_reduction_percent}",
        f"  straight_line_cleanup_enabled: {str(config.silhouette.straight_line_cleanup_enabled).lower()}",
        f"  straight_line_tolerance: {config.silhouette.straight_line_tolerance}",
        f"  min_straight_segment_length_px: {config.silhouette.min_straight_segment_length_px}",
        f"  curve_fit_enabled: {str(config.silhouette.curve_fit_enabled).lower()}",
        f"  curve_fit_tolerance: {config.silhouette.curve_fit_tolerance}",
        f"  min_curve_segment_length_px: {config.silhouette.min_curve_segment_length_px}",
        f"  max_curve_error_percent: {config.silhouette.max_curve_error_percent}",
        f"  remove_small_islands: {str(config.silhouette.remove_small_islands).lower()}",
        f"  min_island_area_px: {config.silhouette.min_island_area_px}",
        f"  preserve_islands_near_body: {str(config.silhouette.preserve_islands_near_body).lower()}",
        f"  island_near_body_distance_px: {config.silhouette.island_near_body_distance_px}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
