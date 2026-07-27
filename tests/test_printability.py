from __future__ import annotations

import json
import logging
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from spool_house_ai.config import apply_cleanup_preset, derive_printer_aware_printability_defaults, load_config
from spool_house_ai.output_paths import build_job_output_paths
from spool_house_ai.pipeline import ImagePipeline
from spool_house_ai.processing.printability import (
    enforce_printable_height_map,
    enforce_printable_mask,
    enforce_printable_polygons,
    save_mask_change_preview,
    validate_lithophane_printability,
)
from spool_house_ai.processing.stl import create_relief_stl, validate_stl_mesh


class PrintabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config(Path("config/config.yaml"))
        self.printability = self.config.printability

    def test_printability_defaults_load(self) -> None:
        self.assertEqual(self.config.printer_profile.profile_name, "Generic FDM 0.4 mm nozzle")
        self.assertAlmostEqual(self.config.printer_profile.nozzle_diameter_mm, 0.4)
        self.assertAlmostEqual(self.config.printer_profile.line_width_mm, 0.4)
        self.assertAlmostEqual(self.config.printer_profile.layer_height_mm, 0.2)
        self.assertAlmostEqual(self.config.printer_profile.first_layer_height_mm, 0.2)
        self.assertTrue(self.printability.use_printer_aware_defaults)
        self.assertTrue(self.printability.enforce_minimum_printable_geometry)
        self.assertAlmostEqual(self.printability.minimum_feature_width_mm, 0.8)
        self.assertAlmostEqual(self.printability.minimum_segment_length_mm, 1.5)
        self.assertAlmostEqual(self.printability.minimum_island_area_mm2, 2.0)
        self.assertAlmostEqual(self.printability.minimum_connection_width_mm, 0.8)
        self.assertAlmostEqual(self.printability.maximum_mergeable_gap_mm, 0.6)
        self.assertAlmostEqual(self.printability.minimum_hole_area_mm2, 1.0)
        self.assertAlmostEqual(self.printability.minimum_component_dimension_mm, 0.8)
        self.assertEqual(self.config.stl.printability, self.printability)
        self.assertEqual(self.config.filament_swap_relief.printability, self.printability)

    def test_printer_aware_defaults_scale_from_nozzle_and_line_width(self) -> None:
        profile = replace(self.config.printer_profile, nozzle_diameter_mm=0.6, line_width_mm=0.6)

        defaults = derive_printer_aware_printability_defaults(profile)

        self.assertAlmostEqual(defaults["minimum_feature_width_mm"], 1.2)
        self.assertAlmostEqual(defaults["minimum_segment_length_mm"], 2.25)
        self.assertAlmostEqual(defaults["minimum_island_area_mm2"], 4.5)
        self.assertAlmostEqual(defaults["minimum_connection_width_mm"], 1.2)
        self.assertAlmostEqual(defaults["maximum_mergeable_gap_mm"], 0.9)
        self.assertAlmostEqual(defaults["minimum_hole_area_mm2"], 2.25)
        self.assertAlmostEqual(defaults["minimum_component_dimension_mm"], 1.2)

    def test_missing_printability_thresholds_derive_from_printer_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir) / "config"
            config_dir.mkdir()
            config_path = config_dir / "config.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "printer:",
                        "  nozzle_diameter_mm: 0.6",
                        "  line_width_mm: 0.6",
                        "printability:",
                        "  use_printer_aware_defaults: true",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            config = load_config(config_path)

            self.assertTrue(config.printability.use_printer_aware_defaults)
            self.assertAlmostEqual(config.printability.minimum_feature_width_mm, 1.2)
            self.assertAlmostEqual(config.printability.minimum_segment_length_mm, 2.25)
            self.assertAlmostEqual(config.printability.maximum_mergeable_gap_mm, 0.9)

    def test_explicit_printability_thresholds_override_printer_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir) / "config"
            config_dir.mkdir()
            config_path = config_dir / "config.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "printer:",
                        "  nozzle_diameter_mm: 0.6",
                        "  line_width_mm: 0.6",
                        "printability:",
                        "  use_printer_aware_defaults: true",
                        "  minimum_feature_width_mm: 1.4",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            config = load_config(config_path)

            self.assertTrue(config.printability.use_printer_aware_defaults)
            self.assertAlmostEqual(config.printability.minimum_feature_width_mm, 1.4)
            self.assertAlmostEqual(config.printability.minimum_segment_length_mm, 2.25)

    def test_same_pixel_island_uses_final_physical_scale(self) -> None:
        mask = np.zeros((20, 20), dtype=bool)
        mask[8:10, 8:10] = True

        small_result, small_report = enforce_printable_mask(
            mask,
            scale_x_mm=0.2,
            scale_y_mm=0.2,
            config=self.printability,
            product_mode="wall_art",
            generation_path="test",
        )
        large_result, large_report = enforce_printable_mask(
            mask,
            scale_x_mm=1.0,
            scale_y_mm=1.0,
            config=self.printability,
            product_mode="wall_art",
            generation_path="test",
        )

        self.assertFalse(np.any(small_result))
        self.assertGreaterEqual(small_report["removed_islands"], 1)
        self.assertTrue(np.any(large_result))
        self.assertEqual(large_report["removed_islands"], 0)

    def test_long_narrow_line_is_reinforced_not_removed(self) -> None:
        mask = np.zeros((20, 80), dtype=bool)
        mask[9:11, 10:70] = True

        cleaned, report = enforce_printable_mask(
            mask,
            scale_x_mm=0.2,
            scale_y_mm=0.2,
            config=self.printability,
            product_mode="wall_art",
            generation_path="test",
        )

        self.assertTrue(np.any(cleaned))
        self.assertGreater(report["thickened_features"], 0)
        self.assertGreater(report["pixels_added"], 0)
        self.assertEqual(report["removed_short_segments"], 0)

    def test_mask_printability_writes_visual_warning_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            preview_path = temp_path / "print_safe_cleanup.png"
            mask = np.zeros((20, 80), dtype=bool)
            mask[9:11, 10:70] = True

            cleaned, report = enforce_printable_mask(
                mask,
                scale_x_mm=0.2,
                scale_y_mm=0.2,
                config=self.printability,
                product_mode="wall_art",
                generation_path="test",
                visual_warning_preview_path=preview_path,
            )

            self.assertTrue(np.any(cleaned))
            self.assertTrue(preview_path.exists())
            self.assertGreater(preview_path.stat().st_size, 0)
            self.assertTrue(report["visual_warning_preview_created"])
            self.assertEqual(report["visual_warning_preview_path"], str(preview_path))

    def test_unchanged_visual_warning_preview_is_not_written(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            preview_path = temp_path / "unchanged.png"
            mask = np.zeros((20, 20), dtype=bool)
            mask[5:15, 5:15] = True

            created = save_mask_change_preview(mask, mask.copy(), preview_path)

            self.assertFalse(created)
            self.assertFalse(preview_path.exists())

    def test_short_fragment_is_removed(self) -> None:
        mask = np.zeros((20, 20), dtype=bool)
        mask[8:11, 8:14] = True
        config = replace(self.printability, minimum_island_area_mm2=0.0)

        cleaned, report = enforce_printable_mask(
            mask,
            scale_x_mm=0.2,
            scale_y_mm=0.2,
            config=config,
            product_mode="wall_art",
            generation_path="test",
        )

        self.assertFalse(np.any(cleaned))
        self.assertGreaterEqual(report["removed_short_segments"], 1)

    def test_tiny_gap_is_closed_and_tiny_hole_is_filled(self) -> None:
        mask = np.zeros((30, 40), dtype=bool)
        mask[8:22, 5:18] = True
        mask[8:22, 20:34] = True
        mask[12:14, 10:12] = False

        cleaned, report = enforce_printable_mask(
            mask,
            scale_x_mm=0.2,
            scale_y_mm=0.2,
            config=self.printability,
            product_mode="wall_art",
            generation_path="test",
        )

        self.assertTrue(cleaned[10, 19])
        self.assertTrue(cleaned[12, 10])
        self.assertGreaterEqual(report["closed_gaps"], 1)
        self.assertGreaterEqual(report["removed_or_filled_tiny_holes"], 1)

    def test_nested_polygon_keeps_large_hole_and_removes_tiny_hole(self) -> None:
        from shapely.geometry import Polygon

        polygon = Polygon(
            [(0, 0), (10, 0), (10, 10), (0, 10)],
            [
                [(2, 2), (5, 2), (5, 5), (2, 5)],
                [(8, 8), (8.4, 8), (8.4, 8.4), (8, 8.4)],
            ],
        )

        cleaned, report = enforce_printable_polygons(
            [polygon],
            config=self.printability,
            product_mode="wall_art",
            generation_path="vector_extrusion",
        )

        self.assertEqual(len(cleaned), 1)
        self.assertEqual(len(cleaned[0].interiors), 1)
        self.assertEqual(report["removed_or_filled_tiny_holes"], 1)

    def test_filament_height_map_removes_tiny_partial_color_fragment(self) -> None:
        height_map = np.full((24, 24), 0.8, dtype=np.float32)
        height_map[10, 10] = 1.2

        cleaned, report = enforce_printable_height_map(
            height_map,
            width_mm=24.0,
            config=self.printability,
            product_mode="filament_swap_relief",
            generation_path="filament_swap_heightfield",
        )

        self.assertFalse(np.any(np.isclose(cleaned, 1.2)))
        self.assertTrue(np.all(cleaned > 0))
        self.assertGreaterEqual(report["removed_partial_color_fragments"], 1)

    def test_height_map_printability_writes_visual_warning_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            preview_path = temp_path / "height_print_safe_cleanup.png"
            height_map = np.full((24, 24), 0.8, dtype=np.float32)
            height_map[10, 10] = 1.2

            cleaned, report = enforce_printable_height_map(
                height_map,
                width_mm=24.0,
                config=self.printability,
                product_mode="filament_swap_relief",
                generation_path="filament_swap_heightfield",
                visual_warning_preview_path=preview_path,
            )

            self.assertFalse(np.any(np.isclose(cleaned, 1.2)))
            self.assertTrue(preview_path.exists())
            self.assertGreater(preview_path.stat().st_size, 0)
            self.assertTrue(report["visual_warning_preview_created"])
            self.assertEqual(report["visual_warning_preview_path"], str(preview_path))

    def test_lithophane_uses_framework_without_vector_fragment_cleanup(self) -> None:
        import trimesh

        mesh = trimesh.creation.box(extents=(40.0, 30.0, 2.0))
        mesh.apply_translation((20.0, 15.0, 1.0))
        report = validate_lithophane_printability(
            mesh,
            config=self.printability,
            product_mode="lithophane",
            generation_path="lithophane_heightfield",
        )

        self.assertTrue(report["validator_invoked"])
        self.assertFalse(report["vector_fragment_cleanup_applied"])
        self.assertFalse(report["heightfield_texture_cleanup_applied"])
        self.assertEqual(report["unresolved_printability_warnings"], [])

    def test_pipeline_status_contains_printability_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "printability_status.png"
            output_dir = temp_path / "output"
            log_dir = temp_path / "logs"
            output_dir.mkdir()
            log_dir.mkdir()

            image = Image.new("RGBA", (80, 60), (255, 255, 255, 255))
            draw = ImageDraw.Draw(image)
            draw.rectangle((12, 12, 68, 48), fill=(0, 0, 0, 255))
            draw.rectangle((3, 3, 4, 4), fill=(0, 0, 0, 255))
            image.save(input_path)

            config = replace(self.config, input_dir=temp_path, output_dir=output_dir, log_dir=log_dir)
            logger = logging.getLogger("spool_house_ai.tests.printability_status")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())

            self.assertTrue(ImagePipeline(config, logger).process(input_path))

            paths = build_job_output_paths(output_dir, input_path)
            status = json.loads(paths.job_status_path.read_text(encoding="utf-8"))
            self.assertIn("printability_summary", status)
            self.assertIn("printability_preview_path", status)
            self.assertTrue(status["printability_summary"]["validator_invoked"])
            self.assertTrue(status["printability_summary"]["enforcement_enabled"])
            summary = paths.job_summary_path.read_text(encoding="utf-8")
            self.assertIn("Print-Safe Cleanup", summary)
            self.assertIn("Minimum detail width mm", summary)

    def test_every_visible_cleanup_preset_invokes_printability_in_relief_pipeline(self) -> None:
        presets = [
            "default",
            "clean_logo",
            "detail_preserving",
            "drip_logo",
            "splatter_logo",
            "line_art",
            "preserve_floating_islands",
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            image_path = temp_path / "preset_source.png"
            image = Image.new("RGBA", (120, 80), (255, 255, 255, 255))
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle((12, 12, 108, 68), radius=12, fill=(0, 0, 0, 255))
            draw.ellipse((48, 24, 72, 48), fill=(255, 255, 255, 255))
            image.save(image_path)

            for preset in presets:
                with self.subTest(preset=preset):
                    analysis_path = temp_path / f"{preset}_mask.png"
                    silhouette = apply_cleanup_preset(replace(self.config.silhouette, cleanup_preset=preset))
                    from spool_house_ai.processing.analysis import analyze_image

                    analysis = analyze_image(image_path, analysis_path, silhouette)
                    stl_path = temp_path / f"{preset}.stl"
                    stl_config = replace(
                        self.config.stl,
                        stl_backend="auto_vector_first",
                        detail_mode=silhouette.detail_mode,
                        preserve_holes=silhouette.preserve_holes,
                    )
                    stl_result = create_relief_stl(analysis, stl_path, stl_config)
                    mesh_report = validate_stl_mesh(
                        stl_path,
                        stl_result.requested_backend,
                        stl_result.actual_backend,
                        stl_result.fallback_reason,
                    )
                    self.assertTrue(mesh_report.watertight)
                    self.assertTrue(stl_result.printability_report["validator_invoked"])
                    self.assertIn(stl_result.actual_backend, stl_result.printability_report["paths_invoked"][0])


if __name__ == "__main__":
    unittest.main()
