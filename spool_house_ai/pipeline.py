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
from spool_house_ai.processing.analysis import analyze_image, save_mask
from spool_house_ai.processing.background import background_removal_available, remove_background
from spool_house_ai.processing.preview import create_preview, save_stage_previews
from spool_house_ai.processing.stl import MeshReport, StlCreationResult, create_relief_stl, validate_stl_mesh, write_mesh_report
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

        output_dir = self.config.output_dir / image_path.stem
        output_dir.mkdir(parents=True, exist_ok=True)

        cleaned_png_path = output_dir / f"{image_path.stem}_cleaned.png"
        silhouette_png_path = output_dir / f"{image_path.stem}_silhouette.png"
        svg_path = output_dir / f"{image_path.stem}.svg"
        review_svg_path = output_dir / f"{image_path.stem}_review.svg"
        stl_path = output_dir / f"{image_path.stem}.stl"
        mesh_report_path = output_dir / "mesh_report.json"
        job_status_path = output_dir / "job_status.json"
        job_summary_path = output_dir / "job_summary.md"
        preview_path = output_dir / f"{image_path.stem}_preview.png"
        body_mask_path = output_dir / f"{image_path.stem}_body_mask.png"
        detail_mask_path = output_dir / f"{image_path.stem}_detail_mask.png"
        contour_debug_path = output_dir / f"{image_path.stem}_contour_debug.png"
        settings_path = output_dir / "job_settings.yaml"
        warnings: list[str] = []
        failures: list[str] = []
        stl_result: StlCreationResult | None = None
        mesh_report: MeshReport | None = None
        analysis = None

        def write_job_status() -> None:
            try:
                finished_at = datetime.now(timezone.utc)
                duration_seconds = perf_counter() - started_timer
                status_payload = _write_job_status(
                    job_status_path,
                    config=self.config,
                    image_path=image_path,
                    output_dir=output_dir,
                    svg_path=svg_path,
                    review_svg_path=review_svg_path,
                    stl_path=stl_path,
                    mesh_report_path=mesh_report_path,
                    job_status_path=job_status_path,
                    job_summary_path=job_summary_path,
                    stl_result=stl_result,
                    mesh_report=mesh_report,
                    warnings=warnings,
                    failures=failures,
                    artifact_summary=_artifact_summary(analysis),
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
        _emit(stage_callback, "Intake Room", "done", "Image accepted", image_path)
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
            save_mask(analysis.hole_mask, output_dir / f"{image_path.stem}_hole_mask.png")
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
        _emit(stage_callback, "Output Vault", "done", "Output folder is ready", preview_path)

        self.logger.info("Created cleaned PNG: %s", cleaned_png_path)
        self.logger.info("Created SVG: %s", svg_path)
        self.logger.info("Created review SVG: %s", review_svg_path)
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
    output_dir: Path,
    svg_path: Path,
    review_svg_path: Path,
    stl_path: Path,
    mesh_report_path: Path,
    job_status_path: Path,
    job_summary_path: Path,
    stl_result: StlCreationResult | None,
    mesh_report: MeshReport | None,
    warnings: list[str],
    failures: list[str],
    artifact_summary: dict[str, Any],
    started_at: datetime,
    finished_at: datetime,
    duration_seconds: float,
) -> dict[str, Any]:
    payload = {
        "app_version": _load_app_version(config.project_root),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_seconds": round(duration_seconds, 3),
        "input_file_path": str(image_path),
        "output_folder_path": str(output_dir),
        "svg_path": str(svg_path),
        "review_svg_path": str(review_svg_path),
        "stl_path": str(stl_path),
        "mesh_report_path": str(mesh_report_path),
        "job_status_path": str(job_status_path),
        "job_summary_path": str(job_summary_path),
        "requested_backend": stl_result.requested_backend if stl_result else config.stl.stl_backend,
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
        },
        "settings_used": {
            "pipeline": asdict(config.pipeline),
            "silhouette": asdict(config.silhouette),
            "svg": asdict(config.svg),
            "stl": asdict(config.stl),
        },
        "warnings": warnings,
        "failures": failures,
        "artifact_summary": artifact_summary,
        "mesh_summary": _mesh_summary(mesh_report),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def _write_job_summary(path: Path, status: dict[str, Any]) -> None:
    mesh = status.get("mesh_summary") or {}
    artifact = status.get("artifact_summary") or {}
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
        f"- Output folder: `{status.get('output_folder_path', '')}`",
        f"- Cleanup preset: `{(status.get('settings_used') or {}).get('silhouette', {}).get('cleanup_preset', '')}`",
        f"- Product mode: `{status.get('product_mode', '')}`",
        f"- Detail mode: `{status.get('detail_mode', '')}`",
        f"- Duration: `{status.get('duration_seconds', 0)}` seconds",
        "",
        "## Files",
        f"- SVG: `{status.get('svg_path', '')}`",
        f"- Review SVG: `{status.get('review_svg_path', '')}`",
        f"- STL: `{status.get('stl_path', '')}`",
        f"- Mesh report: `{status.get('mesh_report_path', '')}`",
        f"- Job status: `{status.get('job_status_path', '')}`",
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
        "## Artwork Cleanup",
        f"- Isolated islands: `{artifact.get('isolated_island_count', 0)}`",
        f"- Removed islands: `{artifact.get('removed_island_count', 0)}`",
        f"- Preserved islands: `{artifact.get('preserved_island_count', 0)}`",
        f"- Preserved details: `{artifact.get('preserved_detail_count', 0)}`",
        "",
        "## Review",
        f"- Warnings: `{len(warnings)}`",
        f"- Failures: `{len(failures)}`",
        f"- Recommended next step: {next_step}",
    ]
    if warnings:
        lines.extend(["", "### Warnings"])
        lines.extend(f"- {warning}" for warning in warnings)
    if failures:
        lines.extend(["", "### Failures"])
        lines.extend(f"- {failure}" for failure in failures)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _artifact_summary(analysis: Any | None) -> dict[str, Any]:
    if analysis is None:
        return {}
    return asdict(analysis.artifact_report)


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


def _load_app_version(project_root: Path) -> str:
    version_path = project_root / "VERSION"
    if not version_path.exists():
        return ""
    return version_path.read_text(encoding="utf-8").strip()
