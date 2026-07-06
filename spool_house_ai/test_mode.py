from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from spool_house_ai.config import AppConfig
from spool_house_ai.pipeline import ImagePipeline
from spool_house_ai.processing.analysis import analyze_image


def create_test_image(config: AppConfig) -> Path:
    test_path = config.input_dir / "v2_test_artwork.png"
    image = Image.new("RGBA", (420, 320), (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle((60, 50, 360, 260), radius=52, fill=(20, 20, 20, 255))
    draw.ellipse((165, 105, 255, 195), fill=(255, 255, 255, 0))
    draw.line((115, 220, 305, 90), fill=(20, 20, 20, 255), width=12)
    draw.arc((98, 82, 322, 238), 205, 330, fill=(255, 255, 255, 0), width=18)

    for x, y in [(35, 30), (390, 80), (45, 285), (385, 275), (205, 35)]:
        draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill=(20, 20, 20, 255))

    image.save(test_path)
    return test_path


def create_geometry_test_image(config: AppConfig) -> Path:
    test_path = config.input_dir / "v4_geometry_test.png"
    image = Image.new("RGBA", (420, 320), (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((52, 52, 178, 178), fill=(20, 20, 20, 255))
    draw.line((230, 245, 365, 70), fill=(20, 20, 20, 255), width=18)
    draw.arc((65, 105, 355, 290), 190, 340, fill=(20, 20, 20, 255), width=24)
    draw.polygon([(260, 188), (286, 174), (273, 203)], fill=(20, 20, 20, 255))
    for x, y in [(30, 30), (34, 33), (392, 42), (388, 285), (205, 270), (207, 274)]:
        draw.rectangle((x, y, x + 2, y + 2), fill=(20, 20, 20, 255))
    image.save(test_path)
    return test_path


def create_real_world_geometry_test_image(config: AppConfig) -> Path:
    test_path = config.input_dir / "v4_real_world_shape_test.png"
    image = Image.new("RGBA", (560, 320), (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)
    draw.polygon(
        [
            (40, 165),
            (120, 120),
            (230, 105),
            (385, 70),
            (500, 35),
            (425, 100),
            (282, 158),
            (135, 215),
            (58, 225),
        ],
        fill=(20, 20, 20, 255),
    )
    for x, y, h in [(150, 212, 52), (205, 184, 82), (260, 160, 60), (318, 137, 96)]:
        draw.rounded_rectangle((x, y, x + 24, y + h), radius=12, fill=(20, 20, 20, 255))
    draw.ellipse((210, 133, 252, 169), fill=(255, 255, 255, 0))
    draw.line((112, 174, 410, 86), fill=(255, 255, 255, 0), width=9)
    draw.arc((75, 84, 445, 238), 198, 332, fill=(255, 255, 255, 0), width=7)
    draw.line((170, 190, 310, 145), fill=(20, 20, 20, 255), width=7)
    for x, y in [(30, 28), (34, 31), (520, 280), (516, 276), (450, 250), (454, 254)]:
        draw.rectangle((x, y, x + 2, y + 2), fill=(20, 20, 20, 255))
    image.save(test_path)
    return test_path


def create_v5_cleanup_test_image(config: AppConfig) -> Path:
    test_path = config.input_dir / "v5_smart_cleanup_test.png"
    image = Image.new("RGBA", (560, 360), (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)
    draw.line((55, 285, 455, 80), fill=(20, 20, 20, 255), width=26)
    draw.arc((92, 70, 455, 310), 188, 342, fill=(20, 20, 20, 255), width=30)
    draw.polygon([(405, 102), (505, 126), (426, 178)], fill=(20, 20, 20, 255))
    draw.line((180, 222, 318, 152), fill=(255, 255, 255, 0), width=8)
    draw.line((212, 244, 360, 170), fill=(255, 255, 255, 0), width=5)
    for x, y in [(18, 18), (532, 24), (536, 330), (24, 336), (280, 18)]:
        draw.point((x, y), fill=(20, 20, 20, 255))
    image.save(test_path)
    return test_path


def run_test_mode(config: AppConfig, pipeline: ImagePipeline, logger: logging.Logger) -> bool:
    test_image = create_test_image(config)
    logger.info("Created V2 test image: %s", test_image)
    stl_created = pipeline.process(test_image)

    output_dir = config.output_dir / test_image.stem
    expected_outputs = [
        output_dir / f"{test_image.stem}_cleaned.png",
        output_dir / f"{test_image.stem}_silhouette.png",
        output_dir / f"{test_image.stem}.svg",
        output_dir / f"{test_image.stem}.stl",
        output_dir / f"{test_image.stem}_preview.png",
        output_dir / f"{test_image.stem}_preview_cleaned.png",
        output_dir / f"{test_image.stem}_preview_threshold.png",
        output_dir / f"{test_image.stem}_preview_contours.png",
        output_dir / f"{test_image.stem}_preview_svg.png",
        output_dir / f"{test_image.stem}_preview_stl.png",
        output_dir / "mesh_report.json",
    ]
    missing = [path for path in expected_outputs if not path.exists()]
    if missing:
        for path in missing:
            logger.error("Test mode missing expected output: %s", path)
        return False
    if not _mesh_report_is_sane(output_dir / "mesh_report.json", logger):
        return False

    geometry_image = create_geometry_test_image(config)
    logger.info("Created V4 geometry test image: %s", geometry_image)
    geometry_stl_created = pipeline.process(geometry_image)
    geometry_output_dir = config.output_dir / geometry_image.stem
    geometry_expected = [
        geometry_output_dir / "raw_threshold.png",
        geometry_output_dir / "raw_contours.png",
        geometry_output_dir / "smoothed_contours.png",
        geometry_output_dir / "final_vector_preview.png",
        geometry_output_dir / f"{geometry_image.stem}.svg",
        geometry_output_dir / f"{geometry_image.stem}.stl",
        geometry_output_dir / "mesh_report.json",
    ]
    missing_geometry = [path for path in geometry_expected if not path.exists()]
    if missing_geometry:
        for path in missing_geometry:
            logger.error("Geometry test missing expected output: %s", path)
        return False
    if not _mesh_report_is_sane(geometry_output_dir / "mesh_report.json", logger):
        return False

    analysis = analyze_image(
        geometry_output_dir / f"{geometry_image.stem}_cleaned.png",
        geometry_output_dir / f"{geometry_image.stem}_test_reanalysis.png",
        config.silhouette,
    )
    if analysis.geometry_report.smoothed_total_points >= analysis.geometry_report.original_total_points:
        logger.error(
            "Geometry smoothing did not reduce contour points: %s -> %s",
            analysis.geometry_report.original_total_points,
            analysis.geometry_report.smoothed_total_points,
        )
        return False
    if analysis.geometry_report.bbox_change_percent > config.silhouette.max_bbox_change_percent:
        logger.error("Geometry bbox changed too much: %.2f", analysis.geometry_report.bbox_change_percent)
        return False
    if analysis.geometry_report.aspect_ratio_change_percent > config.silhouette.max_aspect_ratio_change_percent:
        logger.error("Geometry aspect ratio changed too much: %.2f", analysis.geometry_report.aspect_ratio_change_percent)
        return False
    logger.info(
        "Geometry test contour points reduced: %s -> %s",
        analysis.geometry_report.original_total_points,
        analysis.geometry_report.smoothed_total_points,
    )

    real_world_image = create_real_world_geometry_test_image(config)
    logger.info("Created V4 real-world geometry test image: %s", real_world_image)
    real_world_created = pipeline.process(real_world_image)
    real_world_output_dir = config.output_dir / real_world_image.stem
    real_world_analysis = analyze_image(
        real_world_output_dir / f"{real_world_image.stem}_cleaned.png",
        real_world_output_dir / f"{real_world_image.stem}_test_reanalysis.png",
        config.silhouette,
    )
    if real_world_analysis.geometry_report.bbox_change_percent > config.silhouette.max_bbox_change_percent:
        logger.error("Real-world geometry bbox changed too much: %.2f", real_world_analysis.geometry_report.bbox_change_percent)
        return False
    if real_world_analysis.geometry_report.aspect_ratio_change_percent > config.silhouette.max_aspect_ratio_change_percent:
        logger.error(
            "Real-world geometry aspect ratio changed too much: %.2f",
            real_world_analysis.geometry_report.aspect_ratio_change_percent,
        )
        return False

    fallback_config = replace_for_fallback_test(config)
    fallback_analysis = analyze_image(
        real_world_output_dir / f"{real_world_image.stem}_cleaned.png",
        real_world_output_dir / f"{real_world_image.stem}_fallback_reanalysis.png",
        fallback_config.silhouette,
    )
    if not fallback_analysis.geometry_report.fallback_used:
        logger.error("Expected safe smoothing fallback did not activate")
        return False
    logger.info("Safe smoothing fallback activated as expected")

    v5_image = create_v5_cleanup_test_image(config)
    logger.info("Created V5 smart cleanup test image: %s", v5_image)
    v5_created = pipeline.process(v5_image)
    v5_output_dir = config.output_dir / v5_image.stem
    v5_expected = [
        v5_output_dir / "removed_islands_debug.png",
        v5_output_dir / "original_vs_cleaned_compare.png",
        v5_output_dir / "original_vs_body_mask_compare.png",
        v5_output_dir / "original_vs_detail_mask_compare.png",
        v5_output_dir / "original_vs_final_vector_compare.png",
        v5_output_dir / "original_vs_stl_preview_compare.png",
        v5_output_dir / f"{v5_image.stem}_body_mask.png",
        v5_output_dir / f"{v5_image.stem}_detail_mask.png",
        v5_output_dir / f"{v5_image.stem}.stl",
        v5_output_dir / "mesh_report.json",
    ]
    missing_v5 = [path for path in v5_expected if not path.exists()]
    if missing_v5:
        for path in missing_v5:
            logger.error("V5 test missing expected output: %s", path)
        return False
    if not _mesh_report_is_sane(v5_output_dir / "mesh_report.json", logger):
        return False
    v5_analysis = analyze_image(
        v5_output_dir / f"{v5_image.stem}_cleaned.png",
        v5_output_dir / f"{v5_image.stem}_test_reanalysis.png",
        config.silhouette,
    )
    if not np.any(v5_analysis.removed_island_mask):
        logger.error("V5 test did not remove any small islands")
        return False
    if not np.any(v5_analysis.detail_mask):
        logger.error("V5 test did not preserve internal detail mask")
        return False
    if v5_analysis.geometry_report.smoothed_total_points >= v5_analysis.geometry_report.original_total_points:
        logger.error("V5 cleanup did not reduce contour points")
        return False

    logger.info("Test mode verified V2, V4, and V5 outputs")
    return stl_created and geometry_stl_created and real_world_created and v5_created


def _mesh_report_is_sane(path: Path, logger: logging.Logger) -> bool:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        logger.error("Could not read mesh report %s: %s", path, error)
        return False

    checks = {
        "exists": bool(report.get("exists")),
        "file_size_bytes": int(report.get("file_size_bytes", 0)) > 0,
        "vertex_count": int(report.get("vertex_count", 0)) > 0,
        "face_count": int(report.get("face_count", 0)) > 0,
        "bounding_box_mm": len(report.get("bounding_box_mm", [])) == 3,
        "empty_mesh": not bool(report.get("empty_mesh")),
        "invalid_bounds": not bool(report.get("invalid_bounds")),
        "failures": not report.get("failures"),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        logger.error("Mesh report failed sanity checks %s: %s", path, ", ".join(failed))
        return False
    return True


def replace_for_fallback_test(config: AppConfig) -> AppConfig:
    from dataclasses import replace

    aggressive = replace(
        config.silhouette,
        contour_smoothing_enabled=True,
        contour_smoothing_strength=4,
        simplify_tolerance=8.0,
        collinear_merge_tolerance=20.0,
        smoothing_profile="aggressive",
        max_point_reduction_percent=25.0,
    )
    return replace(config, silhouette=aggressive)
