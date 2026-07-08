from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw

from spool_house_ai.config import apply_cleanup_preset, load_config
from spool_house_ai.processing.analysis import analyze_image
from spool_house_ai.processing.stl import create_relief_stl, validate_stl_mesh
from spool_house_ai.test_mode import create_real_world_geometry_test_image


class GeometryRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        config = load_config(Path("config/config.yaml"))
        self.silhouette_config = replace(
            config.silhouette,
            detail_mode="raised_details",
            preserve_holes=True,
            preserve_internal_details=True,
            min_contour_area=25,
            min_island_area_px=75,
        )
        self.stl_config = replace(
            config.stl,
            detail_mode="raised_details",
            max_mesh_pixels=30000,
        )

    def test_geometry_masks_and_stl_remain_sane(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            image_path = temp_path / "geometry_regression.png"
            output_mask = temp_path / "geometry_regression_silhouette.png"
            stl_path = temp_path / "geometry_regression.stl"

            self._create_regression_artwork(image_path)
            analysis = analyze_image(image_path, output_mask, self.silhouette_config)

            self.assertTrue(np.any(analysis.hole_mask), "Expected transparent interior hole to be preserved")
            self.assertTrue(np.any(analysis.detail_mask), "Expected internal dark detail lines to survive cleanup")
            self.assertTrue(np.any(analysis.removed_island_mask), "Expected tiny floating islands to be removed")
            self.assertGreaterEqual(analysis.artifact_report.isolated_island_count, 1)
            self.assertGreaterEqual(analysis.artifact_report.removed_island_count, 1)
            self.assertGreaterEqual(analysis.artifact_report.preserved_detail_count, 1)
            self.assertLessEqual(
                analysis.geometry_report.bbox_change_percent,
                self.silhouette_config.max_bbox_change_percent,
            )
            self.assertLessEqual(
                analysis.geometry_report.aspect_ratio_change_percent,
                self.silhouette_config.max_aspect_ratio_change_percent,
            )

            hole_y = 145 * self.silhouette_config.upscale_factor
            hole_x = 190 * self.silhouette_config.upscale_factor
            self.assertFalse(analysis.body_mask[hole_y, hole_x], "Hole center should not be filled in the body mask")

            stl_result = create_relief_stl(analysis, stl_path, self.stl_config)
            self.assertEqual(stl_result.actual_backend, "raster_heightfield")
            report = validate_stl_mesh(stl_path)
            self.assertTrue(report.exists)
            self.assertGreater(report.file_size_bytes, 0)
            self.assertGreater(report.vertex_count, 0)
            self.assertGreater(report.face_count, 0)
            self.assertEqual(len(report.bounding_box_mm), 3)
            self.assertFalse(report.empty_mesh)
            self.assertFalse(report.invalid_bounds)
            self.assertTrue(report.watertight)
            self.assertEqual(report.open_edge_count, 0)
            self.assertEqual(report.overused_edge_count, 0)
            self.assertEqual(report.non_manifold_edge_count, 0)
            self.assertEqual(report.failures, [])

    def test_raster_mesh_with_hole_is_watertight(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            stl_path = Path(temp_dir) / "raster_hole.stl"
            mask = np.zeros((64, 96), dtype=bool)
            mask[8:56, 10:86] = True
            mask[24:40, 38:58] = False

            create_relief_stl(mask, stl_path, self.stl_config)
            report = validate_stl_mesh(stl_path)

            self.assertTrue(report.exists)
            self.assertTrue(report.watertight)
            self.assertEqual(report.open_edge_count, 0)
            self.assertEqual(report.overused_edge_count, 0)
            self.assertEqual(report.non_manifold_edge_count, 0)
            self.assertEqual(report.warnings, [])
            self.assertEqual(report.failures, [])

    def test_raster_mesh_resolves_diagonal_contacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            stl_path = Path(temp_dir) / "raster_diagonal_contacts.stl"
            mask = np.zeros((18, 18), dtype=bool)
            for index in range(3, 15):
                mask[index, index] = True
                mask[index, index + 1] = True

            create_relief_stl(mask, stl_path, self.stl_config)
            report = validate_stl_mesh(stl_path)

            self.assertTrue(report.exists)
            self.assertTrue(report.watertight)
            self.assertEqual(report.open_edge_count, 0)
            self.assertEqual(report.overused_edge_count, 0)
            self.assertEqual(report.non_manifold_edge_count, 0)
            self.assertEqual(report.warnings, [])
            self.assertEqual(report.failures, [])

    def test_vector_backend_creates_or_falls_back_to_sane_stl(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            image_path = temp_path / "vector_backend_regression.png"
            output_mask = temp_path / "vector_backend_regression_silhouette.png"
            stl_path = temp_path / "vector_backend_regression.stl"

            self._create_regression_artwork(image_path)
            analysis = analyze_image(image_path, output_mask, self.silhouette_config)
            vector_config = replace(
                self.stl_config,
                stl_backend="vector_extrusion",
                detail_mode="preserve_holes",
            )

            stl_result = create_relief_stl(analysis, stl_path, vector_config)
            self.assertIn(stl_result.actual_backend, {"vector_extrusion", "raster_heightfield"})

            report = validate_stl_mesh(stl_path)
            self.assertTrue(report.exists)
            self.assertGreater(report.file_size_bytes, 0)
            self.assertGreater(report.vertex_count, 0)
            self.assertGreater(report.face_count, 0)
            self.assertEqual(report.open_edge_count, 0)
            self.assertEqual(report.non_manifold_edge_count, 0)
            self.assertEqual(report.failures, [])

    def test_auto_vector_first_reports_fallback_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            image_path = temp_path / "auto_backend_regression.png"
            output_mask = temp_path / "auto_backend_regression_silhouette.png"
            stl_path = temp_path / "auto_backend_regression.stl"

            self._create_regression_artwork(image_path)
            analysis = analyze_image(image_path, output_mask, self.silhouette_config)
            auto_config = replace(
                self.stl_config,
                stl_backend="auto_vector_first",
                detail_mode="raised_details",
            )

            stl_result = create_relief_stl(analysis, stl_path, auto_config)
            self.assertEqual(stl_result.requested_backend, "auto_vector_first")
            self.assertEqual(stl_result.actual_backend, "raster_heightfield")
            self.assertTrue(stl_result.fallback_used)
            self.assertIn("supports silhouette and hole-preserving modes", stl_result.fallback_reason)

            report = validate_stl_mesh(
                stl_path,
                requested_backend=stl_result.requested_backend,
                actual_backend=stl_result.actual_backend,
                fallback_reason=stl_result.fallback_reason,
            )
            self.assertTrue(report.fallback_used)
            self.assertEqual(report.actual_backend, "raster_heightfield")
            self.assertTrue(report.watertight)
            self.assertEqual(report.failures, [])

    def test_auto_vector_first_falls_back_on_vector_attribute_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            stl_path = Path(temp_dir) / "attribute_error_fallback.stl"
            mask = np.zeros((32, 48), dtype=bool)
            mask[6:26, 8:40] = True
            auto_config = replace(self.stl_config, stl_backend="auto_vector_first")

            with patch(
                "spool_house_ai.processing.stl._create_vector_extrusion_stl",
                side_effect=AttributeError("'MultiPolygon' object has no attribute 'exterior'"),
            ):
                stl_result = create_relief_stl(mask, stl_path, auto_config)

            self.assertEqual(stl_result.requested_backend, "auto_vector_first")
            self.assertEqual(stl_result.actual_backend, "raster_heightfield")
            self.assertTrue(stl_result.fallback_used)
            self.assertIn("MultiPolygon", stl_result.fallback_reason)

            report = validate_stl_mesh(
                stl_path,
                requested_backend=stl_result.requested_backend,
                actual_backend=stl_result.actual_backend,
                fallback_reason=stl_result.fallback_reason,
            )
            self.assertTrue(report.exists)
            self.assertTrue(report.watertight)
            self.assertEqual(report.failures, [])

    def test_vector_backend_handles_multipolygon_contours_without_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            config = load_config(Path("config/config.yaml"))
            sample_config = replace(config, input_dir=temp_path, output_dir=temp_path / "output")
            image_path = create_real_world_geometry_test_image(sample_config)
            output_mask = temp_path / "multipolygon_silhouette.png"
            stl_path = temp_path / "multipolygon_vector.stl"

            silhouette_config = apply_cleanup_preset(
                replace(
                    config.silhouette,
                    cleanup_preset="detail_preserving",
                    detail_mode="preserve_holes",
                )
            )
            analysis = analyze_image(image_path, output_mask, silhouette_config)
            stl_config = replace(
                config.stl,
                stl_backend="auto_vector_first",
                detail_mode="preserve_holes",
            )

            stl_result = create_relief_stl(analysis, stl_path, stl_config)
            self.assertEqual(stl_result.requested_backend, "auto_vector_first")
            self.assertEqual(stl_result.actual_backend, "vector_extrusion")
            self.assertFalse(stl_result.fallback_used)
            self.assertEqual(stl_result.fallback_reason, "")

            report = validate_stl_mesh(
                stl_path,
                requested_backend=stl_result.requested_backend,
                actual_backend=stl_result.actual_backend,
                fallback_reason=stl_result.fallback_reason,
            )
            self.assertTrue(report.exists)
            self.assertTrue(report.watertight)
            self.assertEqual(report.open_edge_count, 0)
            self.assertEqual(report.overused_edge_count, 0)
            self.assertEqual(report.non_manifold_edge_count, 0)
            self.assertEqual(report.failures, [])

    def test_colored_logo_foreground_survives_thresholding(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            image_path = temp_path / "colored_logo.png"
            output_mask = temp_path / "colored_logo_silhouette.png"

            image = Image.new("RGBA", (180, 90), (255, 255, 255, 255))
            draw = ImageDraw.Draw(image)
            draw.line((12, 70, 160, 18), fill=(255, 80, 20, 255), width=9)
            draw.line((45, 66, 86, 42), fill=(255, 110, 40, 255), width=4)
            image.save(image_path)

            analysis = analyze_image(
                image_path,
                output_mask,
                replace(self.silhouette_config, detail_mode="preserve_holes"),
            )

            self.assertGreater(int(analysis.final_mask.sum()), 500)
            self.assertGreaterEqual(len(analysis.vector_contours), 1)

    def test_logo_clean_preset_uses_stronger_island_cleanup(self) -> None:
        logo_config = apply_cleanup_preset(
            replace(
                self.silhouette_config,
                cleanup_preset="logo_clean",
                min_island_area_px=20,
                preserve_islands_near_body=True,
                island_near_body_distance_px=8,
            )
        )

        self.assertEqual(logo_config.cleanup_preset, "logo_clean")
        self.assertTrue(logo_config.remove_small_islands)
        self.assertGreaterEqual(logo_config.min_island_area_px, 150)
        self.assertFalse(logo_config.preserve_islands_near_body)
        self.assertEqual(logo_config.island_near_body_distance_px, 0)

    def test_phase_10_logo_presets_have_distinct_cleanup_profiles(self) -> None:
        clean_logo = apply_cleanup_preset(replace(self.silhouette_config, cleanup_preset="clean_logo"))
        drip_logo = apply_cleanup_preset(replace(self.silhouette_config, cleanup_preset="drip_logo"))
        splatter_logo = apply_cleanup_preset(replace(self.silhouette_config, cleanup_preset="splatter_logo"))

        self.assertEqual(clean_logo.cleanup_preset, "clean_logo")
        self.assertGreaterEqual(clean_logo.min_island_area_px, 150)
        self.assertFalse(clean_logo.preserve_islands_near_body)

        self.assertEqual(drip_logo.cleanup_preset, "drip_logo")
        self.assertTrue(drip_logo.preserve_islands_near_body)
        self.assertGreaterEqual(drip_logo.island_near_body_distance_px, 14)
        self.assertGreaterEqual(drip_logo.min_island_area_px, 110)

        self.assertEqual(splatter_logo.cleanup_preset, "splatter_logo")
        self.assertTrue(splatter_logo.preserve_islands_near_body)
        self.assertGreaterEqual(splatter_logo.island_near_body_distance_px, 18)
        self.assertLessEqual(splatter_logo.min_contour_area, 18)
        self.assertLessEqual(splatter_logo.simplify_tolerance, 0.65)

    @staticmethod
    def _create_regression_artwork(path: Path) -> None:
        image = Image.new("RGBA", (420, 280), (255, 255, 255, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((56, 58, 352, 222), radius=36, fill=(210, 40, 40, 255))
        draw.ellipse((152, 108, 228, 182), fill=(255, 255, 255, 0))
        draw.line((92, 206, 326, 78), fill=(20, 20, 20, 255), width=10)
        draw.arc((86, 82, 326, 230), 200, 335, fill=(20, 20, 20, 255), width=7)
        draw.polygon([(304, 96), (358, 115), (314, 150)], fill=(20, 20, 20, 255))
        for x, y in [(16, 16), (392, 18), (398, 252), (24, 258), (210, 18)]:
            draw.rectangle((x, y, x + 2, y + 2), fill=(20, 20, 20, 255))
        image.save(path)


if __name__ == "__main__":
    unittest.main()
