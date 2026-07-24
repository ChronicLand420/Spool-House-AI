from __future__ import annotations

import json
import logging
import shutil
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

from PIL import UnidentifiedImageError

from spool_house_ai.config import AppConfig
from spool_house_ai.output_paths import JobOutputPaths, build_job_output_paths
from spool_house_ai.processing.analysis import analyze_image, save_mask
from spool_house_ai.processing.background import background_removal_available, remove_background
from spool_house_ai.processing.preview import create_preview, save_stage_previews
from spool_house_ai.processing.filament_swap import FILAMENT_SWAP_BACKEND, create_filament_swap_relief_stl
from spool_house_ai.processing.stl import (
    MeshReport,
    StlCreationResult,
    create_lithophane_stl,
    create_relief_stl,
    validate_stl_mesh,
    write_mesh_report,
)
from spool_house_ai.processing.vectorize import create_svg


StageCallback = Callable[[str, str, str, Path | None], None]


class ImagePipeline:
    def __init__(self, config: AppConfig, logger: logging.Logger) -> None:
        self.config = config
        self.logger = logger

    def process(self, image_path: Path, stage_callback: StageCallback | None = None) -> bool:
        started_at = datetime.now(timezone.utc)
        started_timer = perf_counter()
        image_path = image_path.resolve()
        if not image_path.exists():
            self.logger.warning("Skipped missing file: %s", image_path)
            return False

        paths = build_job_output_paths(self.config.output_dir, image_path)
        paths.create_directories()

        cleaned_png_path = paths.cleaned_png_path
        silhouette_png_path = paths.silhouette_png_path
        svg_path = paths.svg_path
        review_svg_path = paths.review_svg_path
        stl_path = paths.stl_path
        mesh_report_path = paths.mesh_report_path
        job_status_path = paths.job_status_path
        job_summary_path = paths.job_summary_path
        preview_path = paths.preview_path
        body_mask_path = paths.body_mask_path
        detail_mask_path = paths.detail_mask_path
        contour_debug_path = paths.contour_debug_path
        settings_path = paths.settings_path
        warnings: list[str] = []
        failures: list[str] = []
        stl_result: StlCreationResult | None = None
        mesh_report: MeshReport | None = None
        analysis = None
        lithophane_metadata: dict[str, Any] | None = None
        filament_swap_metadata: dict[str, Any] | None = None

        def write_job_status() -> None:
            try:
                finished_at = datetime.now(timezone.utc)
                duration_seconds = perf_counter() - started_timer
                status_payload = _write_job_status(
                    job_status_path,
                    config=self.config,
                    image_path=image_path,
                    paths=paths,
                    stl_result=stl_result,
                    mesh_report=mesh_report,
                    warnings=warnings,
                    failures=failures,
                    artifact_summary=_artifact_summary(analysis, self.config),
                    lithophane_metadata=lithophane_metadata,
                    filament_swap_metadata=filament_swap_metadata,
                    started_at=started_at,
                    finished_at=finished_at,
                    duration_seconds=duration_seconds,
                )
                _write_job_summary(job_summary_path, status_payload)
                self.logger.info("Created job status: %s", job_status_path)
                self.logger.info("Created job summary: %s", job_summary_path)
            except Exception:
                self.logger.exception("Failed to write job status for image: %s", image_path)

        self.logger.info("Processing image: %s", image_path.name)
        _emit(stage_callback, "Intake Room", "active", "Image queued for processing", image_path)
        try:
            if image_path != paths.source_copy_path.resolve():
                shutil.copy2(image_path, paths.source_copy_path)
        except Exception as error:
            message = f"Could not copy source image into job package: {error}"
            warnings.append(message)
            self.logger.warning(message)
        _emit(stage_callback, "Intake Room", "done", "Image accepted", image_path)

        if self.config.stl.product_mode == "lithophane":
            _emit(stage_callback, "Cleanup Lab", "done", "Cleanup skipped for lithophane", image_path)
            _emit(stage_callback, "Detail Analyzer", "active", "Sampling grayscale brightness", image_path)
            stl_created = False
            processed_lithophane_path = paths.previews_dir / f"{image_path.stem}_lithophane_processed.png"
            try:
                _emit(stage_callback, "Mesh Forge", "active", "Generating lithophane heightfield", image_path)
                stl_result, lithophane_metadata = create_lithophane_stl(
                    image_path,
                    stl_path,
                    self.config.stl,
                    preview_path=preview_path,
                    cleaned_png_path=cleaned_png_path,
                    silhouette_png_path=silhouette_png_path,
                    processed_preview_path=processed_lithophane_path,
                    generic_3mf_path=paths.generic_3mf_path,
                )
                _append_generic_3mf_warnings(warnings, stl_result.generic_3mf_metadata)
                if lithophane_metadata.get("source_downscaled"):
                    message = (
                        "Lithophane image was downscaled to "
                        f"{lithophane_metadata.get('sampled_width_px')}x{lithophane_metadata.get('sampled_height_px')} "
                        "pixels for mesh safety."
                    )
                    warnings.append(message)
                    self.logger.warning(message)
                _emit(stage_callback, "Detail Analyzer", "done", "Brightness map prepared", cleaned_png_path)
                _emit(stage_callback, "Vector Workshop", "done", "SVG not applicable for lithophane", None)
                self.logger.info("Lithophane backend used: %s", stl_result.actual_backend)
                mesh_report = validate_stl_mesh(
                    stl_path,
                    requested_backend=stl_result.requested_backend,
                    actual_backend=stl_result.actual_backend,
                    fallback_reason=stl_result.fallback_reason,
                )
                write_mesh_report(mesh_report, mesh_report_path)
                if mesh_report.failures:
                    failures.extend(mesh_report.failures)
                    for failure in mesh_report.failures:
                        self.logger.error("Mesh validation failure: %s", failure)
                    _emit(stage_callback, "Mesh Forge", "failed", f"Mesh report: {mesh_report_path}", mesh_report_path)
                elif mesh_report.warnings:
                    warnings.extend(mesh_report.warnings)
                    for warning in mesh_report.warnings:
                        self.logger.warning("Mesh validation warning: %s", warning)
                    _emit(stage_callback, "Mesh Forge", "done", f"STL created with warnings: {mesh_report_path}", stl_path)
                else:
                    _emit(stage_callback, "Mesh Forge", "done", "Lithophane STL created", stl_path)
                stl_created = not mesh_report.failures
                _emit(stage_callback, "Render Bay", "done", "Thickness preview rendered", preview_path)
            except (UnidentifiedImageError, OSError) as error:
                failures.append(f"Skipped invalid lithophane image: {error}")
                self.logger.warning("Skipped invalid lithophane image %s: %s", image_path, error)
                _emit(stage_callback, "Mesh Forge", "failed", "Invalid lithophane image", None)
            except Exception as error:
                failures.append(f"Failed to generate lithophane STL: {error}")
                self.logger.exception("Failed to generate lithophane STL for image: %s", image_path)
                _emit(stage_callback, "Mesh Forge", "failed", "Lithophane STL generation failed", None)

            _write_job_settings(settings_path, self.config)
            write_job_status()
            _emit(stage_callback, "Output Vault", "done", "Files saved to job folder", preview_path)
            if stl_created:
                self.logger.info("Created lithophane STL: %s", stl_path)
                _log_generic_3mf(self.logger, stl_result, paths)
                self.logger.info("Created mesh report: %s", mesh_report_path)
            if preview_path.exists():
                self.logger.info("Created preview: %s", preview_path)
            return stl_created

        if self.config.stl.product_mode == "filament_swap_relief":
            _emit(stage_callback, "Cleanup Lab", "done", "Cleanup presets ignored for filament swaps", image_path)
            _emit(stage_callback, "Detail Analyzer", "active", "Detecting printable color groups", image_path)
            stl_created = False
            try:
                _emit(stage_callback, "Mesh Forge", "active", "Generating filament swap heightfield", image_path)
                stl_result, filament_swap_metadata = create_filament_swap_relief_stl(
                    image_path,
                    stl_path,
                    self.config.filament_swap_relief,
                    preview_path=preview_path,
                    cleaned_png_path=cleaned_png_path,
                    silhouette_png_path=silhouette_png_path,
                    color_preview_path=paths.previews_dir / f"{image_path.stem}_filament_colors.png",
                    height_preview_path=paths.previews_dir / f"{image_path.stem}_filament_height_map.png",
                    contact_sheet_path=paths.previews_dir / f"{image_path.stem}_filament_swap_preview.png",
                    island_detected_preview_path=paths.previews_dir / f"{image_path.stem}_filament_islands_detected.png",
                    island_actions_preview_path=paths.previews_dir / f"{image_path.stem}_filament_islands_actions.png",
                    island_preserved_preview_path=paths.previews_dir / f"{image_path.stem}_filament_islands_preserved.png",
                    island_removed_preview_path=paths.previews_dir / f"{image_path.stem}_filament_islands_removed.png",
                    island_merged_preview_path=paths.previews_dir / f"{image_path.stem}_filament_islands_merged.png",
                    island_connected_preview_path=paths.previews_dir / f"{image_path.stem}_filament_islands_connected.png",
                    report_path=paths.filament_swap_report_path,
                    color_plan_path=paths.color_plan_path,
                    filament_swap_plan_path=paths.filament_swap_plan_path,
                    generic_3mf_path=paths.generic_3mf_path,
                )
                for warning in filament_swap_metadata.get("warnings") or []:
                    if warning not in warnings:
                        warnings.append(warning)
                        self.logger.warning(warning)
                _append_generic_3mf_warnings(warnings, stl_result.generic_3mf_metadata)
                _emit(stage_callback, "Detail Analyzer", "done", "Color height plan prepared", cleaned_png_path)
                _emit(stage_callback, "Vector Workshop", "done", "SVG not applicable for filament swaps", None)
                self.logger.info("Filament swap backend used: %s", stl_result.actual_backend)
                mesh_report = validate_stl_mesh(
                    stl_path,
                    requested_backend=stl_result.requested_backend,
                    actual_backend=stl_result.actual_backend,
                    fallback_reason=stl_result.fallback_reason,
                )
                write_mesh_report(mesh_report, mesh_report_path)
                if mesh_report.failures:
                    failures.extend(mesh_report.failures)
                    for failure in mesh_report.failures:
                        self.logger.error("Mesh validation failure: %s", failure)
                    _emit(stage_callback, "Mesh Forge", "failed", f"Mesh report: {mesh_report_path}", mesh_report_path)
                elif mesh_report.warnings:
                    warnings.extend(mesh_report.warnings)
                    for warning in mesh_report.warnings:
                        self.logger.warning("Mesh validation warning: %s", warning)
                    _emit(stage_callback, "Mesh Forge", "done", f"STL created with warnings: {mesh_report_path}", stl_path)
                else:
                    _emit(stage_callback, "Mesh Forge", "done", "Filament swap STL created", stl_path)
                stl_created = not mesh_report.failures
                _emit(stage_callback, "Render Bay", "done", "Swap preview rendered", preview_path)
            except (UnidentifiedImageError, OSError) as error:
                failures.append(f"Skipped invalid filament swap image: {error}")
                self.logger.warning("Skipped invalid filament swap image %s: %s", image_path, error)
                _emit(stage_callback, "Mesh Forge", "failed", "Invalid filament swap image", None)
            except Exception as error:
                failures.append(f"Failed to generate filament swap STL: {error}")
                self.logger.exception("Failed to generate filament swap STL for image: %s", image_path)
                _emit(stage_callback, "Mesh Forge", "failed", "Filament swap STL generation failed", None)

            _write_job_settings(settings_path, self.config)
            write_job_status()
            _emit(stage_callback, "Output Vault", "done", "Files saved to job folder", preview_path)
            if stl_created:
                self.logger.info("Created filament swap STL: %s", stl_path)
                self.logger.info("Created mesh report: %s", mesh_report_path)
                self.logger.info("Created filament swap report: %s", paths.filament_swap_report_path)
                self.logger.info("Created color plan: %s", paths.color_plan_path)
                self.logger.info("Created filament swap plan: %s", paths.filament_swap_plan_path)
                if filament_swap_metadata.get("generic_3mf_created"):
                    self.logger.info("Created generic 3MF: %s", paths.generic_3mf_path)
            if preview_path.exists():
                self.logger.info("Created preview: %s", preview_path)
            return stl_created

        if not self.config.pipeline.background_removal_enabled:
            self.logger.info("Background removal is disabled; using the original image as the cleaned PNG")
        elif not background_removal_available():
            message = "rembg is not installed; using the original image as the cleaned PNG"
            warnings.append(message)
            self.logger.warning(message)

        try:
            remove_background(
                image_path,
                cleaned_png_path,
                enabled=self.config.pipeline.background_removal_enabled,
            )
            self.logger.info("Cleanup stage complete: %s", cleaned_png_path)
            _emit(stage_callback, "Cleanup Lab", "done", "Cleaned image saved", cleaned_png_path)
        except (UnidentifiedImageError, OSError) as error:
            failures.append(f"Skipped invalid image: {error}")
            self.logger.warning("Skipped invalid image %s: %s", image_path, error)
            write_job_status()
            return False
        except ImportError as error:
            failures.append(f"Missing dependency while cleaning image: {error}")
            self.logger.error("Missing dependency while cleaning image %s: %s", image_path, error)
            write_job_status()
            return False
        except Exception as error:
            failures.append(f"Failed to clean image: {error}")
            self.logger.exception("Failed to clean image: %s", image_path)
            write_job_status()
            return False

        try:
            _emit(stage_callback, "Detail Analyzer", "active", "Classifying body, holes, and detail strokes", cleaned_png_path)
            analysis = analyze_image(
                cleaned_png_path,
                silhouette_png_path,
                self.config.silhouette,
            )
            artifact_report = analysis.artifact_report
            if artifact_report.removed_island_count:
                self.logger.info("Removed isolated islands: %s", artifact_report.removed_island_count)
            if artifact_report.preserved_island_count:
                message = (
                    f"Small isolated islands detected: {artifact_report.isolated_island_count} "
                    f"(removed: {artifact_report.removed_island_count}, "
                    f"preserved: {artifact_report.preserved_island_count})"
                )
                warnings.append(message)
                self.logger.warning(message)
            save_mask(analysis.body_mask, body_mask_path)
            save_mask(analysis.detail_mask, detail_mask_path)
            save_mask(analysis.hole_mask, paths.hole_mask_path)
            self.logger.info(
                "Analysis stage complete: body/hole/detail masks saved; contour points %s -> %s",
                analysis.geometry_report.original_total_points,
                analysis.geometry_report.smoothed_total_points,
            )
            _emit(stage_callback, "Detail Analyzer", "done", "Analysis masks created", body_mask_path)
            _emit(stage_callback, "Vector Workshop", "active", "Writing editable SVG paths", silhouette_png_path)
            create_svg(
                analysis,
                svg_path,
                self.config.svg,
                metadata={
                    "app_version": _load_app_version(self.config.project_root),
                    "input_filename": image_path.name,
                    "product_mode": self.config.stl.product_mode,
                    "detail_mode": self.config.stl.detail_mode,
                },
                review_output_path=review_svg_path,
            )
            self.logger.info("Vector stage complete: %s", svg_path)
            self.logger.info("Review SVG created: %s", review_svg_path)
            save_stage_previews(
                original_path=image_path,
                cleaned_png_path=cleaned_png_path,
                analysis=analysis,
                output_dir=paths.previews_dir,
                stem=image_path.stem,
                config=self.config.preview,
                svg_path=svg_path,
                stl_path=stl_path,
            )
            preview_contours = paths.previews_dir / f"{image_path.stem}_preview_contours.png"
            if preview_contours.exists():
                shutil.copyfile(preview_contours, contour_debug_path)
            preview_geometry_report = paths.previews_dir / "geometry_report.txt"
            if preview_geometry_report.exists():
                shutil.copyfile(preview_geometry_report, paths.geometry_report_path)
                preview_geometry_report.unlink(missing_ok=True)
            _emit(stage_callback, "Vector Workshop", "done", "SVG created", svg_path)
        except ImportError as error:
            failures.append(f"Missing dependency while analyzing image: {error}")
            self.logger.error("Missing dependency while analyzing image %s: %s", image_path, error)
            write_job_status()
            return False
        except Exception as error:
            failures.append(f"Failed to create PNG/SVG outputs: {error}")
            self.logger.exception("Failed to create PNG/SVG outputs for image: %s", image_path)
            write_job_status()
            return False

        stl_created = False
        try:
            _emit(stage_callback, "Mesh Forge", "active", "Generating printable mesh", svg_path)
            stl_result = create_relief_stl(analysis, stl_path, self.config.stl, generic_3mf_path=paths.generic_3mf_path)
            _append_generic_3mf_warnings(warnings, stl_result.generic_3mf_metadata)
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
                failures.extend(mesh_report.failures)
                for failure in mesh_report.failures:
                    self.logger.error("Mesh validation failure: %s", failure)
                _emit(stage_callback, "Mesh Forge", "failed", f"Mesh report: {mesh_report_path}", mesh_report_path)
            elif mesh_report.warnings:
                warnings.extend(mesh_report.warnings)
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
            failures.append(f"Missing dependency while generating STL: {error}")
            self.logger.error("Missing dependency while generating STL %s: %s", image_path, error)
            _emit(stage_callback, "Mesh Forge", "failed", f"Missing dependency: {error}", None)
        except Exception as error:
            failures.append(f"Failed to generate STL; PNG/SVG outputs were kept: {error}")
            self.logger.exception("Failed to generate STL for %s; PNG/SVG outputs were kept", image_path)
            _emit(stage_callback, "Mesh Forge", "failed", "STL generation failed; PNG/SVG kept", None)

        try:
            _emit(stage_callback, "Render Bay", "active", "Rendering previews", silhouette_png_path)
            create_preview(silhouette_png_path, preview_path, self.config.preview)
            _emit(stage_callback, "Render Bay", "done", "Preview rendered", preview_path)
        except Exception as error:
            warnings.append(f"Failed to create final preview: {error}")
            self.logger.exception("Failed to create final preview for image: %s", image_path)

        _write_job_settings(settings_path, self.config)
        write_job_status()
        _emit(stage_callback, "Output Vault", "done", "Files saved to job folder", preview_path)

        self.logger.info("Created cleaned PNG: %s", cleaned_png_path)
        self.logger.info("Created SVG: %s", svg_path)
        self.logger.info("Created review SVG: %s", review_svg_path)
        if stl_created:
            self.logger.info("Created STL: %s", stl_path)
            _log_generic_3mf(self.logger, stl_result, paths)
            self.logger.info("Created mesh report: %s", mesh_report_path)
        self.logger.info("Created preview: %s", preview_path)
        return stl_created


def _emit(callback: StageCallback | None, room: str, state: str, message: str, thumbnail: Path | None) -> None:
    if callback is not None:
        callback(room, state, message, thumbnail)


def _append_generic_3mf_warnings(warnings: list[str], metadata: dict[str, Any] | None) -> None:
    if not metadata or not metadata.get("generic_3mf_enabled"):
        return
    if metadata.get("generic_3mf_created"):
        return
    validation_errors = metadata.get("generic_3mf_validation_errors") or []
    message = "Generic 3MF export failed"
    if validation_errors:
        message += ": " + "; ".join(str(error) for error in validation_errors)
    if message not in warnings:
        warnings.append(message)


def _log_generic_3mf(logger: logging.Logger, stl_result: StlCreationResult | None, paths: JobOutputPaths) -> None:
    metadata = (stl_result.generic_3mf_metadata if stl_result else {}) or {}
    if metadata.get("generic_3mf_created"):
        logger.info("Created generic 3MF: %s", paths.generic_3mf_path)
    elif metadata.get("generic_3mf_enabled"):
        logger.warning("Generic 3MF export failed: %s", "; ".join(metadata.get("generic_3mf_validation_errors") or []))


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
        f"  lithophane_width_mm: {config.stl.lithophane_width_mm}",
        f"  lithophane_min_thickness_mm: {config.stl.lithophane_min_thickness_mm}",
        f"  lithophane_max_thickness_mm: {config.stl.lithophane_max_thickness_mm}",
        f"  lithophane_invert: {str(config.stl.lithophane_invert).lower()}",
        f"  lithophane_max_pixels: {config.stl.lithophane_max_pixels}",
        f"  lithophane_autocontrast_enabled: {str(config.stl.lithophane_autocontrast_enabled).lower()}",
        f"  lithophane_autocontrast_cutoff_percent: {config.stl.lithophane_autocontrast_cutoff_percent}",
        f"  lithophane_contrast: {config.stl.lithophane_contrast}",
        f"  lithophane_gamma: {config.stl.lithophane_gamma}",
        f"  lithophane_sharpen_strength: {config.stl.lithophane_sharpen_strength}",
        f"  lithophane_denoise_radius_px: {config.stl.lithophane_denoise_radius_px}",
        f"  filament_swap_width_mm: {config.filament_swap_relief.width_mm}",
        f"  filament_swap_color_count: {config.filament_swap_relief.color_count}",
        f"  filament_swap_base_height_mm: {config.filament_swap_relief.base_height_mm}",
        f"  filament_swap_layer_step_mm: {config.filament_swap_relief.layer_step_mm}",
        f"  filament_swap_min_model_thickness_mm: {config.filament_swap_relief.min_model_thickness_mm}",
        f"  filament_swap_first_layer_height_mm: {config.filament_swap_relief.first_layer_height_mm}",
        f"  filament_swap_layer_height_mm: {config.filament_swap_relief.layer_height_mm}",
        f"  filament_swap_height_alignment_mode: {config.filament_swap_relief.height_alignment_mode}",
        f"  filament_swap_height_alignment_tolerance_mm: {config.filament_swap_relief.height_alignment_tolerance_mm}",
        f"  filament_swap_auto_background_ignore: {str(config.filament_swap_relief.auto_background_ignore).lower()}",
        f"  filament_swap_min_region_area_px: {config.filament_swap_relief.min_region_area_px}",
        f"  filament_swap_palette_color_space: {config.filament_swap_relief.palette_color_space}",
        f"  filament_swap_palette_random_seed: {config.filament_swap_relief.palette_random_seed}",
        f"  filament_swap_merge_similar_colors: {str(config.filament_swap_relief.merge_similar_colors).lower()}",
        f"  filament_swap_solid_base_enabled: {str(config.filament_swap_relief.solid_base_enabled).lower()}",
        f"  filament_swap_relief_style: {config.filament_swap_relief.relief_style}",
        f"  filament_swap_mesh_style: {config.filament_swap_relief.mesh_style}",
        f"  filament_swap_contour_simplify_tolerance_px: {config.filament_swap_relief.contour_simplify_tolerance_px}",
        f"  filament_swap_contour_smoothing_enabled: {str(config.filament_swap_relief.contour_smoothing_enabled).lower()}",
        f"  filament_swap_contour_smoothing_strength: {config.filament_swap_relief.contour_smoothing_strength}",
        f"  filament_swap_background_confidence_threshold: {config.filament_swap_relief.background_confidence_threshold}",
        f"  filament_swap_island_policy: {config.filament_swap_relief.island_policy}",
        f"  filament_swap_island_merge_max_distance_px: {config.filament_swap_relief.island_merge_max_distance_px}",
        f"  filament_swap_island_merge_fallback: {config.filament_swap_relief.island_merge_fallback}",
        f"  filament_swap_island_connect_max_gap_px: {config.filament_swap_relief.island_connect_max_gap_px}",
        f"  filament_swap_island_connection_width_px: {config.filament_swap_relief.island_connection_width_px}",
        f"  filament_swap_island_connect_fallback: {config.filament_swap_relief.island_connect_fallback}",
        "  generic_3mf_export: automatic",
        "analysis:",
        f"  threshold_value: {config.silhouette.threshold_value}",
        f"  smoothing_strength: {config.silhouette.smoothing_strength}",
        f"  min_contour_area: {config.silhouette.min_contour_area}",
        f"  simplify_tolerance: {config.silhouette.simplify_tolerance}",
        f"  preserve_holes: {str(config.silhouette.preserve_holes).lower()}",
        f"  preserve_internal_details: {str(config.silhouette.preserve_internal_details).lower()}",
        f"  default_detail_behavior: {config.silhouette.default_detail_behavior}",
        f"  cleanup_preset: {config.silhouette.cleanup_preset}",
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


def _write_job_status(
    path: Path,
    *,
    config: AppConfig,
    image_path: Path,
    paths: JobOutputPaths,
    stl_result: StlCreationResult | None,
    mesh_report: MeshReport | None,
    warnings: list[str],
    failures: list[str],
    artifact_summary: dict[str, Any],
    lithophane_metadata: dict[str, Any] | None,
    filament_swap_metadata: dict[str, Any] | None,
    started_at: datetime,
    finished_at: datetime,
    duration_seconds: float,
) -> dict[str, Any]:
    svg_applicable = config.stl.product_mode not in {"lithophane", "filament_swap_relief"}
    generic_3mf_summary = _generic_3mf_status_summary(stl_result, lithophane_metadata, filament_swap_metadata)
    payload = {
        "app_version": _load_app_version(config.project_root),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_seconds": round(duration_seconds, 3),
        "input_file_path": str(image_path),
        "output_root_path": str(paths.output_root),
        "output_folder_path": str(paths.job_root),
        "job_root_path": str(paths.job_root),
        "source_folder_path": str(paths.source_dir),
        "svg_folder_path": str(paths.svg_dir),
        "stl_folder_path": str(paths.stl_dir),
        "three_mf_folder_path": str(paths.three_mf_dir),
        "previews_folder_path": str(paths.previews_dir),
        "reports_folder_path": str(paths.reports_dir),
        "source_copy_path": str(paths.source_copy_path),
        "cleaned_png_path": str(paths.cleaned_png_path),
        "silhouette_png_path": str(paths.silhouette_png_path),
        "body_mask_path": str(paths.body_mask_path),
        "hole_mask_path": str(paths.hole_mask_path),
        "detail_mask_path": str(paths.detail_mask_path),
        "contour_debug_path": str(paths.contour_debug_path),
        "preview_path": str(paths.preview_path),
        "svg_path": str(paths.svg_path) if svg_applicable else "",
        "review_svg_path": str(paths.review_svg_path) if svg_applicable else "",
        "stl_path": str(paths.stl_path),
        "generic_3mf_path": generic_3mf_summary.get("generic_3mf_path", "") if generic_3mf_summary.get("generic_3mf_created") else "",
        "mesh_report_path": str(paths.mesh_report_path),
        "job_status_path": str(paths.job_status_path),
        "job_summary_path": str(paths.job_summary_path),
        "filament_swap_report_path": str(paths.filament_swap_report_path) if filament_swap_metadata else "",
        "color_plan_path": str(paths.color_plan_path) if filament_swap_metadata else "",
        "filament_swap_plan_path": str(paths.filament_swap_plan_path) if filament_swap_metadata else "",
        "geometry_report_path": str(paths.geometry_report_path),
        "requested_backend": stl_result.requested_backend if stl_result else _requested_backend_for_status(config),
        "actual_backend": stl_result.actual_backend if stl_result else "",
        "fallback_used": stl_result.fallback_used if stl_result else False,
        "fallback_reason": stl_result.fallback_reason if stl_result else "",
        "product_mode": config.stl.product_mode,
        "detail_mode": config.stl.detail_mode,
        "dimensions": {
            "output_scale_mm": config.stl.output_scale_mm,
            "base_height_mm": config.stl.base_height_mm,
            "extrusion_height_mm": config.stl.extrusion_height_mm,
            "detail_height_mm": config.stl.detail_height_mm,
            "engraving_depth_mm": config.stl.engraving_depth_mm,
            "keychain_hole_diameter_mm": config.stl.keychain_hole_diameter_mm,
            "lithophane_width_mm": config.stl.lithophane_width_mm,
            "lithophane_min_thickness_mm": config.stl.lithophane_min_thickness_mm,
            "lithophane_max_thickness_mm": config.stl.lithophane_max_thickness_mm,
            "lithophane_invert": config.stl.lithophane_invert,
            "lithophane_max_pixels": config.stl.lithophane_max_pixels,
            "lithophane_autocontrast_enabled": config.stl.lithophane_autocontrast_enabled,
            "lithophane_autocontrast_cutoff_percent": config.stl.lithophane_autocontrast_cutoff_percent,
            "lithophane_contrast": config.stl.lithophane_contrast,
            "lithophane_gamma": config.stl.lithophane_gamma,
            "lithophane_sharpen_strength": config.stl.lithophane_sharpen_strength,
            "lithophane_denoise_radius_px": config.stl.lithophane_denoise_radius_px,
            "filament_swap_width_mm": config.filament_swap_relief.width_mm,
            "filament_swap_color_count": config.filament_swap_relief.color_count,
            "filament_swap_base_height_mm": config.filament_swap_relief.base_height_mm,
            "filament_swap_layer_step_mm": config.filament_swap_relief.layer_step_mm,
            "filament_swap_min_model_thickness_mm": config.filament_swap_relief.min_model_thickness_mm,
            "filament_swap_first_layer_height_mm": config.filament_swap_relief.first_layer_height_mm,
            "filament_swap_layer_height_mm": config.filament_swap_relief.layer_height_mm,
            "filament_swap_height_alignment_mode": config.filament_swap_relief.height_alignment_mode,
            "filament_swap_height_alignment_tolerance_mm": config.filament_swap_relief.height_alignment_tolerance_mm,
            "filament_swap_palette_color_space": config.filament_swap_relief.palette_color_space,
            "filament_swap_palette_random_seed": config.filament_swap_relief.palette_random_seed,
            "filament_swap_merge_similar_colors": config.filament_swap_relief.merge_similar_colors,
            "filament_swap_solid_base_enabled": config.filament_swap_relief.solid_base_enabled,
            "filament_swap_relief_style": config.filament_swap_relief.relief_style,
            "filament_swap_mesh_style": config.filament_swap_relief.mesh_style,
            "filament_swap_contour_simplify_tolerance_px": config.filament_swap_relief.contour_simplify_tolerance_px,
            "filament_swap_contour_smoothing_enabled": config.filament_swap_relief.contour_smoothing_enabled,
            "filament_swap_contour_smoothing_strength": config.filament_swap_relief.contour_smoothing_strength,
            "filament_swap_background_confidence_threshold": config.filament_swap_relief.background_confidence_threshold,
            "filament_swap_island_policy": config.filament_swap_relief.island_policy,
            "filament_swap_island_merge_max_distance_px": config.filament_swap_relief.island_merge_max_distance_px,
            "filament_swap_island_connect_max_gap_px": config.filament_swap_relief.island_connect_max_gap_px,
            "filament_swap_island_connection_width_px": config.filament_swap_relief.island_connection_width_px,
            "generic_3mf_export": "automatic",
        },
        "settings_used": {
            "pipeline": asdict(config.pipeline),
            "silhouette": asdict(config.silhouette),
            "svg": asdict(config.svg),
            "stl": asdict(config.stl),
            "filament_swap_relief": asdict(config.filament_swap_relief),
        },
        "warnings": warnings,
        "failures": failures,
        "artifact_summary": artifact_summary,
        "lithophane_summary": lithophane_metadata or {},
        "filament_swap_summary": _filament_swap_status_summary(filament_swap_metadata),
        "generic_3mf_summary": generic_3mf_summary,
        "mesh_summary": _mesh_summary(mesh_report),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def _write_job_summary(path: Path, status: dict[str, Any]) -> None:
    mesh = status.get("mesh_summary") or {}
    artifact = status.get("artifact_summary") or {}
    lithophane = status.get("lithophane_summary") or {}
    filament_swap = status.get("filament_swap_summary") or {}
    generic_3mf = status.get("generic_3mf_summary") or {}
    warnings = status.get("warnings") or []
    failures = status.get("failures") or []
    if failures:
        next_step = "Fix failures before slicer review."
    elif warnings:
        next_step = "Inspect the review SVG and mesh report before slicer review."
    elif artifact.get("preserved_island_count"):
        next_step = "Inspect preserved islands in the review SVG before export."
    else:
        next_step = "Ready for slicer review."

    lines = [
        f"# {Path(status.get('input_file_path', 'job')).name}",
        "",
        "## Job",
        f"- Input: `{status.get('input_file_path', '')}`",
        f"- Output root: `{status.get('output_root_path', '')}`",
        f"- Output folder: `{status.get('output_folder_path', '')}`",
        f"- Cleanup preset: `{artifact.get('cleanup_preset') or (status.get('settings_used') or {}).get('silhouette', {}).get('cleanup_preset', '')}`",
        f"- Product mode: `{status.get('product_mode', '')}`",
        f"- Detail mode: `{status.get('detail_mode', '')}`",
        f"- Duration: `{status.get('duration_seconds', 0)}` seconds",
        "",
        "## Folders",
        f"- Source: `{status.get('source_folder_path', '')}`",
        f"- SVG: `{status.get('svg_folder_path', '')}`",
        f"- STL: `{status.get('stl_folder_path', '')}`",
        f"- 3MF: `{status.get('three_mf_folder_path', '')}`",
        f"- Previews: `{status.get('previews_folder_path', '')}`",
        f"- Reports: `{status.get('reports_folder_path', '')}`",
        "",
        "## Files",
        f"- Source copy: `{status.get('source_copy_path', '')}`",
        f"- SVG: `{status.get('svg_path', '')}`",
        f"- Review SVG: `{status.get('review_svg_path', '')}`",
        f"- STL: `{status.get('stl_path', '')}`",
        f"- Generic 3MF: `{status.get('generic_3mf_path', '')}`",
        f"- Preview: `{status.get('preview_path', '')}`",
        f"- Mesh report: `{status.get('mesh_report_path', '')}`",
        f"- Job status: `{status.get('job_status_path', '')}`",
        f"- Job summary: `{status.get('job_summary_path', '')}`",
        f"- Filament swap report: `{status.get('filament_swap_report_path', '')}`",
        f"- Color plan: `{status.get('color_plan_path', '')}`",
        f"- Filament swap plan: `{status.get('filament_swap_plan_path', '')}`",
        "",
        "## Mesh",
        f"- Requested backend: `{status.get('requested_backend', '')}`",
        f"- Actual backend: `{status.get('actual_backend', '')}`",
        f"- Fallback used: `{status.get('fallback_used', False)}`",
        f"- Fallback reason: `{status.get('fallback_reason', '')}`",
        f"- Watertight: `{mesh.get('watertight', '')}`",
        f"- Face count: `{mesh.get('face_count', '')}`",
        f"- Bounds mm: `{mesh.get('bounding_box_mm', '')}`",
        "",
        "## Generic 3MF Export",
        f"- Export: `automatic`",
        f"- Created: `{generic_3mf.get('generic_3mf_created', False)}`",
        f"- Path: `{generic_3mf.get('generic_3mf_path', '')}`",
        f"- Validation passed: `{generic_3mf.get('generic_3mf_validation_passed', False)}`",
        f"- Units: `{generic_3mf.get('generic_3mf_units', '')}`",
        f"- Bounds mm: `{generic_3mf.get('generic_3mf_bounds', '')}`",
        f"- Bounds match STL mesh: `{generic_3mf.get('bounds_match', False)}`",
        f"- Notice: {generic_3mf.get('generic_export_notice', '')}",
        "",
        "## Artwork Cleanup",
        f"- Isolated islands: `{artifact.get('isolated_island_count', 0)}`",
        f"- Removed islands: `{artifact.get('removed_island_count', 0)}`",
        f"- Preserved islands: `{artifact.get('preserved_island_count', 0)}`",
        f"- Preserved details: `{artifact.get('preserved_detail_count', 0)}`",
        "",
    ]
    if lithophane:
        lines.extend(
            [
                "## Lithophane",
                f"- Mapping: `{lithophane.get('mapping', '')}`",
                f"- Invert: `{lithophane.get('invert', False)}`",
                f"- Width mm: `{lithophane.get('width_mm', '')}`",
                f"- Height mm: `{lithophane.get('height_mm', '')}`",
                f"- Min thickness mm: `{lithophane.get('min_thickness_mm', '')}`",
                f"- Max thickness mm: `{lithophane.get('max_thickness_mm', '')}`",
                f"- Actual min thickness mm: `{lithophane.get('actual_min_thickness_mm', '')}`",
                f"- Actual max thickness mm: `{lithophane.get('actual_max_thickness_mm', '')}`",
                f"- Sampled pixels: `{lithophane.get('sampled_width_px', '')}x{lithophane.get('sampled_height_px', '')}`",
                f"- Downscaled: `{lithophane.get('source_downscaled', False)}`",
                "",
            ]
        )
        preprocessing = lithophane.get("preprocessing") or {}
        if preprocessing:
            lines.extend(
                [
                    "## Lithophane Crispness",
                    f"- Autocontrast: `{preprocessing.get('autocontrast_enabled', False)}`",
                    f"- Autocontrast cutoff percent: `{preprocessing.get('autocontrast_cutoff_percent', '')}`",
                    f"- Contrast: `{preprocessing.get('contrast', '')}`",
                    f"- Gamma: `{preprocessing.get('gamma', '')}`",
                    f"- Sharpen strength: `{preprocessing.get('sharpen_strength', '')}`",
                    f"- Denoise radius px: `{preprocessing.get('denoise_radius_px', '')}`",
                    f"- Processed preview: `{preprocessing.get('processed_preview_path', '')}`",
                    "",
                ]
            )
    if filament_swap:
        lines.extend(
            [
                "## Filament Swap Relief",
                f"- Backend: `{filament_swap.get('backend', '')}`",
                f"- Requested colors: `{filament_swap.get('color_count_requested', '')}`",
                f"- Kept colors: `{filament_swap.get('color_count_kept', '')}`",
                f"- Ignored background: `{filament_swap.get('ignored_background_color_hex', '')}`",
                f"- Palette color space: `{filament_swap.get('palette_color_space', '')}`",
                f"- Color order: `{filament_swap.get('color_order', '')}`",
                f"- Relief style: `{filament_swap.get('relief_style', '')}`",
                f"- Solid base plate: `{filament_swap.get('solid_base_enabled', False)}`",
                f"- Similar shade merges: `{filament_swap.get('similar_color_merge_count', 0)}`",
                f"- Mesh generation mode: `{filament_swap.get('mesh_generation_mode', '')}`",
                f"- Sampled pixels: `{filament_swap.get('sampled_width_px', '')}x{filament_swap.get('sampled_height_px', '')}`",
                f"- Downscaled: `{filament_swap.get('source_downscaled', False)}`",
                f"- Final height mm: `{filament_swap.get('final_height_mm', '')}`",
                f"- Final top layer: `{filament_swap.get('final_top_layer', '')}`",
                f"- Total printed layers: `{filament_swap.get('total_printed_layers', '')}`",
                f"- Snapping occurred: `{filament_swap.get('snapping_occurred', False)}`",
                "",
                "## Filament Island Handling",
            ]
        )
        island_summary = filament_swap.get("island_summary") or {}
        lines.extend(
            [
                f"- Policy: `{island_summary.get('island_policy', '')}`",
                f"- Min region area px: `{island_summary.get('min_region_area_px', '')}`",
                f"- Detected components: `{island_summary.get('total_detected_components', '')}`",
                f"- Preserved components: `{island_summary.get('intentionally_preserved_components', '')}`",
                f"- Removed components: `{island_summary.get('removed_components', '')}`",
                f"- Merged components: `{island_summary.get('merged_components', '')}`",
                f"- Connected components: `{island_summary.get('connected_components', '')}`",
                f"- Pixels removed: `{island_summary.get('pixels_removed', '')}`",
                f"- Pixels recolored: `{island_summary.get('pixels_recolored', '')}`",
                f"- Connector pixels added: `{island_summary.get('connector_pixels_added', '')}`",
                "",
                "## Filament Swap Plan",
            ]
        )
        swap_summary = filament_swap.get("swap_plan_summary") or {}
        layer_settings = swap_summary.get("layer_settings") or {}
        lines.extend(
            [
                "Layer numbering: one-based.",
                "Swap convention: Change before layer N means finish layer N-1, pause, load the new filament, and print layer N.",
                f"- First layer height: `{layer_settings.get('first_layer_height_mm', '')} mm`",
                f"- Normal layer height: `{layer_settings.get('layer_height_mm', '')} mm`",
                f"- Alignment mode: `{layer_settings.get('height_alignment_mode', '')}`",
                f"- Color plan: `{status.get('color_plan_path', '')}`",
                f"- Text swap plan: `{status.get('filament_swap_plan_path', '')}`",
                "",
            ]
        )
        detected_colors = (swap_summary.get("colors") or filament_swap.get("detected_colors") or [])
        for color in detected_colors:
            if color.get("order", color.get("index")) == 1:
                lines.append(
                    f"- Start with `{color.get('hex', '')}` / {color.get('suggested_color_name', 'color')}, "
                    f"layers `{color.get('first_layer_using_color', '')}` through `{color.get('last_layer_using_color', '')}` "
                    f"to `{color.get('aligned_top_z_mm', color.get('assigned_height_mm', ''))} mm`"
                )
            else:
                lines.append(
                    f"- Change before layer `{color.get('change_before_layer', '')}` to `{color.get('hex', '')}` / "
                    f"{color.get('suggested_color_name', 'color')}; transition Z "
                    f"`{color.get('aligned_start_z_mm', color.get('filament_change_at_mm', ''))} mm`; "
                    f"previous filament last layer `{color.get('previous_filament_last_layer', '')}`"
                )
        lines.extend(
            [
                f"- Final height: `{filament_swap.get('final_height_mm', '')} mm`",
                "",
            ]
        )
    validation_errors = generic_3mf.get("generic_3mf_validation_errors") or []
    if validation_errors:
        lines.extend(["## Generic 3MF Validation Errors"])
        lines.extend(f"- {error}" for error in validation_errors)
        lines.append("")
    lines.extend(
        [
        "## Review",
        f"- Warnings: `{len(warnings)}`",
        f"- Failures: `{len(failures)}`",
        f"- Recommended next step: {next_step}",
        ]
    )
    if warnings:
        lines.extend(["", "### Warnings"])
        lines.extend(f"- {warning}" for warning in warnings)
    if failures:
        lines.extend(["", "### Failures"])
        lines.extend(f"- {failure}" for failure in failures)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _artifact_summary(analysis: Any | None, config: AppConfig) -> dict[str, Any]:
    if config.stl.product_mode in {"lithophane", "filament_swap_relief"}:
        return {
            "cleanup_preset": "not_applicable",
            "cleanup_presets_ignored": True,
            "isolated_island_count": 0,
            "removed_island_count": 0,
            "preserved_island_count": 0,
            "preserved_detail_count": 0,
        }
    if analysis is None:
        return {}
    return asdict(analysis.artifact_report)


def _requested_backend_for_status(config: AppConfig) -> str:
    if config.stl.product_mode == "lithophane":
        return "lithophane_heightfield"
    if config.stl.product_mode == "filament_swap_relief":
        return FILAMENT_SWAP_BACKEND
    return config.stl.stl_backend


def _mesh_summary(mesh_report: MeshReport | None) -> dict[str, Any]:
    if mesh_report is None:
        return {}
    return {
        "exists": mesh_report.exists,
        "file_size_bytes": mesh_report.file_size_bytes,
        "vertex_count": mesh_report.vertex_count,
        "face_count": mesh_report.face_count,
        "bounding_box_mm": mesh_report.bounding_box_mm,
        "empty_mesh": mesh_report.empty_mesh,
        "invalid_bounds": mesh_report.invalid_bounds,
        "watertight": mesh_report.watertight,
        "open_edge_count": mesh_report.open_edge_count,
        "overused_edge_count": mesh_report.overused_edge_count,
        "non_manifold_edge_count": mesh_report.non_manifold_edge_count,
        "warnings": mesh_report.warnings,
        "failures": mesh_report.failures,
    }


def _generic_3mf_status_summary(
    stl_result: StlCreationResult | None,
    lithophane_metadata: dict[str, Any] | None,
    filament_swap_metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    source = (stl_result.generic_3mf_metadata if stl_result else None) or {}
    if not source and filament_swap_metadata:
        source = filament_swap_metadata
    if not source and lithophane_metadata:
        source = lithophane_metadata
    if not source:
        return {}
    keys = {
        "generic_3mf_enabled",
        "generic_3mf_created",
        "generic_3mf_path",
        "generic_3mf_validation_passed",
        "generic_3mf_validation_errors",
        "generic_3mf_units",
        "generic_3mf_bounds",
        "source_mesh_bounds",
        "bounds_match",
        "archive_entries",
        "generic_3mf_exporter_version",
        "generic_export_notice",
    }
    return {key: source.get(key) for key in keys if key in source}


def _filament_swap_status_summary(metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not metadata:
        return {}
    omitted = {"component_actions", "color_plan"}
    return {key: value for key, value in metadata.items() if key not in omitted}


def _load_app_version(project_root: Path) -> str:
    version_path = project_root / "VERSION"
    if not version_path.exists():
        return ""
    return version_path.read_text(encoding="utf-8").strip()
