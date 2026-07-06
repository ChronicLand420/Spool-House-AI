from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from spool_house_ai.config import load_config
from spool_house_ai.processing.analysis import analyze_image
from spool_house_ai.processing.stl import create_relief_stl, validate_stl_mesh


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

            create_relief_stl(analysis, stl_path, self.stl_config)
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

    def test_vector_backend_falls_back_to_sane_stl_when_optional_extrusion_is_unavailable(self) -> None:
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

            backend_used = create_relief_stl(analysis, stl_path, vector_config)
            self.assertIn(backend_used, {"vector_extrusion", "raster_heightfield"})

            report = validate_stl_mesh(stl_path)
            self.assertTrue(report.exists)
            self.assertGreater(report.file_size_bytes, 0)
            self.assertGreater(report.vertex_count, 0)
            self.assertGreater(report.face_count, 0)
            self.assertEqual(report.open_edge_count, 0)
            self.assertEqual(report.non_manifold_edge_count, 0)
            self.assertEqual(report.failures, [])

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
