from __future__ import annotations

import json
import logging
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
import trimesh

from spool_house_ai.config import load_config
from spool_house_ai.output_paths import build_job_output_paths
from spool_house_ai.pipeline import ImagePipeline
from spool_house_ai.processing.filament_swap import FILAMENT_SWAP_BACKEND, create_filament_swap_relief_stl
from spool_house_ai.processing.stl import validate_stl_mesh


class FilamentSwapReliefTests(unittest.TestCase):
    def test_config_defaults_load(self) -> None:
        config = load_config(Path("config/config.yaml"))

        self.assertEqual(config.filament_swap_relief.color_count, 3)
        self.assertAlmostEqual(config.filament_swap_relief.width_mm, 120.0)
        self.assertAlmostEqual(config.filament_swap_relief.base_height_mm, 0.8)
        self.assertAlmostEqual(config.filament_swap_relief.layer_step_mm, 0.4)
        self.assertAlmostEqual(config.filament_swap_relief.first_layer_height_mm, 0.2)
        self.assertAlmostEqual(config.filament_swap_relief.layer_height_mm, 0.2)
        self.assertEqual(config.filament_swap_relief.height_alignment_mode, "snap_up")
        self.assertAlmostEqual(config.filament_swap_relief.height_alignment_tolerance_mm, 0.001)
        self.assertTrue(config.filament_swap_relief.auto_background_ignore)
        self.assertEqual(config.filament_swap_relief.color_order, "light_to_dark")
        self.assertEqual(config.filament_swap_relief.palette_color_space, "rgb")
        self.assertEqual(config.filament_swap_relief.palette_random_seed, 17)
        self.assertTrue(config.filament_swap_relief.merge_similar_colors)
        self.assertAlmostEqual(config.filament_swap_relief.similar_color_hue_tolerance_degrees, 18.0)
        self.assertAlmostEqual(config.filament_swap_relief.similar_color_max_area_ratio, 0.12)
        self.assertFalse(config.filament_swap_relief.solid_base_enabled)
        self.assertEqual(config.filament_swap_relief.relief_style, "stacked_blocks")
        self.assertEqual(config.filament_swap_relief.mesh_style, "vector_contours")
        self.assertAlmostEqual(config.filament_swap_relief.contour_simplify_tolerance_px, 0.45)
        self.assertTrue(config.filament_swap_relief.contour_smoothing_enabled)
        self.assertEqual(config.filament_swap_relief.contour_smoothing_strength, 2)
        self.assertAlmostEqual(config.filament_swap_relief.background_confidence_threshold, 0.45)
        self.assertEqual(config.filament_swap_relief.max_sampled_pixels, 700000)
        self.assertAlmostEqual(config.filament_swap_relief.min_model_thickness_mm, 2.0)
        self.assertEqual(config.filament_swap_relief.island_policy, "remove_below_threshold")
        self.assertEqual(config.filament_swap_relief.island_merge_fallback, "remove")
        self.assertEqual(config.filament_swap_relief.island_connect_fallback, "remove")

    def test_invalid_filament_island_policy_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            load_config_dict = __import__("spool_house_ai.config", fromlist=["_filament_swap_relief_config"])
            load_config_dict._filament_swap_relief_config({"island_policy": "teleport"})

    def test_old_filament_config_without_layer_fields_loads(self) -> None:
        load_config_dict = __import__("spool_house_ai.config", fromlist=["_filament_swap_relief_config"])
        config = load_config_dict._filament_swap_relief_config({"base_height_mm": 0.8, "layer_step_mm": 0.4})

        self.assertAlmostEqual(config.first_layer_height_mm, 0.2)
        self.assertAlmostEqual(config.layer_height_mm, 0.2)
        self.assertEqual(config.height_alignment_mode, "snap_up")
        self.assertAlmostEqual(config.height_alignment_tolerance_mm, 0.001)
        self.assertAlmostEqual(config.min_model_thickness_mm, 2.0)
        self.assertEqual(config.relief_style, "stacked_blocks")
        self.assertEqual(config.mesh_style, "vector_contours")
        self.assertTrue(config.merge_similar_colors)
        self.assertFalse(config.solid_base_enabled)
        self.assertEqual(config.max_sampled_pixels, 700000)
        self.assertFalse(hasattr(config, "export_generic_3mf"))

    def test_invalid_filament_relief_style_and_mesh_style_are_rejected(self) -> None:
        load_config_dict = __import__("spool_house_ai.config", fromlist=["_filament_swap_relief_config"])
        with self.assertRaises(ValueError):
            load_config_dict._filament_swap_relief_config({"relief_style": "sunken_magic"})
        with self.assertRaises(ValueError):
            load_config_dict._filament_swap_relief_config({"mesh_style": "triangle_soup"})

    def test_legacy_generic_3mf_export_field_is_ignored(self) -> None:
        load_config_dict = __import__("spool_house_ai.config", fromlist=["_filament_swap_relief_config"])
        config = load_config_dict._filament_swap_relief_config({"export_generic_3mf": False})

        self.assertFalse(hasattr(config, "export_generic_3mf"))

    def test_three_color_relief_is_watertight_and_uses_expected_stack_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            image_path = temp_path / "three_color.png"
            stl_path = temp_path / "three_color.stl"
            self._save_three_color_test_image(image_path)

            config = replace(
                load_config(Path("config/config.yaml")).filament_swap_relief,
                width_mm=48.0,
                max_sampled_pixels=5000,
                min_region_area_px=3,
            )

            stl_result, metadata = create_filament_swap_relief_stl(
                image_path,
                stl_path,
                config,
                preview_path=temp_path / "preview.png",
                report_path=temp_path / "filament_swap_report.json",
            )
            report = validate_stl_mesh(
                stl_path,
                requested_backend=stl_result.requested_backend,
                actual_backend=stl_result.actual_backend,
            )

            self.assertEqual(stl_result.requested_backend, FILAMENT_SWAP_BACKEND)
            self.assertEqual(stl_result.actual_backend, FILAMENT_SWAP_BACKEND)
            self.assertTrue(report.watertight)
            self.assertEqual(report.open_edge_count, 0)
            self.assertEqual(report.overused_edge_count, 0)
            self.assertEqual(report.non_manifold_edge_count, 0)
            self.assertLess(report.bounding_box_mm[0], config.width_mm)
            mesh = trimesh.load_mesh(stl_path, force="mesh")
            self.assertTrue(mesh.is_winding_consistent)
            self.assertEqual(metadata["ignored_background_color_hex"], "#505A69")
            self.assertEqual(metadata["color_count_kept"], 3)
            heights = [color["assigned_height_mm"] for color in metadata["detected_colors"]]
            self.assertEqual(heights, [0.8, 1.2, 2.0])
            self.assertEqual(metadata["detected_colors"][0]["suggested_color_name"], "white")
            self.assertEqual(metadata["detected_colors"][1]["suggested_color_name"], "red")
            self.assertEqual(metadata["detected_colors"][2]["suggested_color_name"], "black")
            self.assertEqual(metadata["detected_colors"][1]["filament_change_at_mm"], 0.8)
            self.assertEqual(metadata["detected_colors"][2]["filament_change_at_mm"], 1.2)
            self.assertEqual(metadata["detected_colors"][1]["change_before_layer"], 5)
            self.assertEqual(metadata["detected_colors"][2]["change_before_layer"], 7)
            self.assertEqual(metadata["total_printed_layers"], 10)
            self.assertEqual(metadata["color_plan"]["height_settings"]["aligned_cumulative_boundaries_mm"], [0.0, 0.8, 1.2, 2.0])
            self.assertEqual(metadata["final_height_mm"], 2.0)
            self.assertGreaterEqual(metadata["final_height_mm"], metadata["min_model_thickness_mm"])
            self.assertEqual(metadata["palette_color_space"], "rgb")
            self.assertEqual(metadata["background_confidence_threshold"], 0.45)
            self.assertTrue(metadata["background_ignored"])
            self.assertIn("island_summary", metadata)

    def test_stacked_blocks_use_largest_color_as_base_and_raise_details(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            image_path = temp_path / "green_yellow_sign.png"
            stl_path = temp_path / "green_yellow_sign.stl"
            image = Image.new("RGB", (100, 60), (255, 255, 255))
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle((6, 12, 94, 48), radius=7, fill=(8, 104, 27))
            draw.rectangle((18, 27, 82, 34), fill=(250, 224, 14))
            draw.rectangle((88, 13, 91, 18), fill=(103, 153, 35))
            image.save(image_path)

            config = replace(
                load_config(Path("config/config.yaml")).filament_swap_relief,
                width_mm=100.0,
                color_count=3,
                max_sampled_pixels=10000,
                min_region_area_px=1,
                smooth_edges=False,
                contour_smoothing_enabled=False,
                contour_simplify_tolerance_px=0.0,
            )

            stl_result, metadata = create_filament_swap_relief_stl(image_path, stl_path, config)
            report = validate_stl_mesh(stl_path, stl_result.requested_backend, stl_result.actual_backend)

            self.assertEqual(metadata["relief_style"], "stacked_blocks")
            self.assertEqual(metadata["similar_color_merge_count"], 1)
            self.assertEqual(metadata["color_count_kept"], 2)
            self.assertEqual(metadata["mesh_generation_mode"], "vector_contours")
            self.assertEqual(metadata["mesh_generation_warning"], "")
            self.assertTrue(report.watertight)
            self.assertEqual(report.open_edge_count, 0)
            self.assertEqual(report.overused_edge_count, 0)
            self.assertEqual(report.non_manifold_edge_count, 0)
            self.assertEqual(metadata["detected_colors"][0]["suggested_color_name"], "green")
            self.assertEqual(metadata["detected_colors"][0]["assigned_height_mm"], 0.8)
            self.assertEqual(metadata["detected_colors"][1]["hex"], "#FAE00E")
            self.assertEqual(metadata["detected_colors"][1]["assigned_height_mm"], 2.0)
            self.assertLess(report.face_count, 1000)

    def test_stacked_blocks_do_not_fill_raised_border_interior(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            image_path = temp_path / "street_sign.png"
            stl_path = temp_path / "street_sign.stl"
            image = Image.new("RGB", (140, 80), (255, 255, 255))
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle((8, 12, 132, 68), radius=6, fill=(8, 104, 27))
            draw.rectangle((18, 24, 122, 56), fill=(250, 224, 14))
            draw.rectangle((23, 29, 117, 51), fill=(8, 104, 27))
            draw.rectangle((38, 37, 102, 44), fill=(250, 224, 14))
            image.save(image_path)

            config = replace(
                load_config(Path("config/config.yaml")).filament_swap_relief,
                width_mm=112.0,
                color_count=2,
                max_sampled_pixels=20000,
                min_region_area_px=1,
                smooth_edges=False,
                contour_smoothing_enabled=False,
                contour_simplify_tolerance_px=0.45,
            )

            stl_result, metadata = create_filament_swap_relief_stl(image_path, stl_path, config)
            report = validate_stl_mesh(stl_path, stl_result.requested_backend, stl_result.actual_backend)
            mesh = trimesh.load_mesh(stl_path, process=True)
            lower_area = self._horizontal_face_area(mesh, 0.8)
            upper_area = self._horizontal_face_area(mesh, 2.0)

            self.assertEqual(metadata["mesh_generation_mode"], "vector_contours")
            self.assertEqual(metadata["detected_colors"][0]["suggested_color_name"], "green")
            self.assertEqual(metadata["detected_colors"][1]["hex"], "#FAE00E")
            self.assertTrue(report.watertight)
            self.assertEqual(report.open_edge_count, 0)
            self.assertEqual(report.overused_edge_count, 0)
            self.assertEqual(report.non_manifold_edge_count, 0)
            self.assertGreater(lower_area, 0.0)
            self.assertGreater(upper_area, 0.0)
            self.assertLess(upper_area, lower_area * 0.75)

    def test_solid_base_plate_fills_background_without_flattening_raised_details(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            image_path = temp_path / "solid_base_sign.png"
            stl_path = temp_path / "solid_base_sign.stl"
            image = Image.new("RGB", (140, 80), (255, 255, 255))
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle((28, 22, 112, 58), radius=5, fill=(8, 104, 27))
            draw.rectangle((48, 36, 92, 43), fill=(250, 224, 14))
            image.save(image_path)

            config = replace(
                load_config(Path("config/config.yaml")).filament_swap_relief,
                width_mm=112.0,
                color_count=2,
                max_sampled_pixels=20000,
                min_region_area_px=1,
                smooth_edges=False,
                contour_smoothing_enabled=False,
                contour_simplify_tolerance_px=0.45,
                solid_base_enabled=True,
            )

            stl_result, metadata = create_filament_swap_relief_stl(image_path, stl_path, config)
            report = validate_stl_mesh(stl_path, stl_result.requested_backend, stl_result.actual_backend)
            mesh = trimesh.load_mesh(stl_path, process=True)
            lower_area = self._horizontal_face_area(mesh, 0.8)
            upper_area = self._horizontal_face_area(mesh, 2.0)

            self.assertTrue(metadata["solid_base_enabled"])
            self.assertTrue(metadata["color_plan"]["solid_base_enabled"])
            self.assertEqual(metadata["mesh_generation_mode"], "vector_contours")
            self.assertTrue(report.watertight)
            self.assertEqual(report.open_edge_count, 0)
            self.assertEqual(report.overused_edge_count, 0)
            self.assertEqual(report.non_manifold_edge_count, 0)
            self.assertAlmostEqual(report.bounding_box_mm[0], 112.0, delta=0.2)
            self.assertAlmostEqual(report.bounding_box_mm[1], 64.0, delta=0.2)
            self.assertGreater(lower_area, upper_area)
            self.assertGreater(upper_area, 0.0)

    def test_engraved_details_style_keeps_legacy_luminance_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            image_path = temp_path / "legacy_order_sign.png"
            stl_path = temp_path / "legacy_order_sign.stl"
            image = Image.new("RGB", (80, 40), (255, 255, 255))
            draw = ImageDraw.Draw(image)
            draw.rectangle((8, 8, 72, 32), fill=(8, 104, 27))
            draw.rectangle((22, 18, 58, 24), fill=(250, 224, 14))
            image.save(image_path)

            config = replace(
                load_config(Path("config/config.yaml")).filament_swap_relief,
                width_mm=80.0,
                color_count=2,
                relief_style="engraved_details",
                max_sampled_pixels=10000,
                min_region_area_px=1,
                smooth_edges=False,
                contour_smoothing_enabled=False,
                contour_simplify_tolerance_px=0.0,
            )

            _stl_result, metadata = create_filament_swap_relief_stl(image_path, stl_path, config)

            self.assertEqual(metadata["relief_style"], "engraved_details")
            self.assertEqual(metadata["detected_colors"][0]["hex"], "#FAE00E")
            self.assertEqual(metadata["detected_colors"][0]["assigned_height_mm"], 0.8)
            self.assertEqual(metadata["detected_colors"][1]["suggested_color_name"], "green")
            self.assertEqual(metadata["detected_colors"][1]["assigned_height_mm"], 2.0)

    def test_repeated_rgb_and_lab_clustering_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            image_path = temp_path / "many_colors.png"
            self._save_many_color_test_image(image_path)
            base = replace(
                load_config(Path("config/config.yaml")).filament_swap_relief,
                color_count=3,
                auto_background_ignore=False,
                max_sampled_pixels=2000,
                min_region_area_px=1,
                smooth_edges=False,
            )

            for color_space in ("rgb", "lab"):
                config = replace(base, palette_color_space=color_space, palette_random_seed=17)
                first = create_filament_swap_relief_stl(image_path, temp_path / f"{color_space}_first.stl", config)[1]
                second = create_filament_swap_relief_stl(image_path, temp_path / f"{color_space}_second.stl", config)[1]

                self.assertEqual(
                    [color["hex"] for color in first["detected_colors"]],
                    [color["hex"] for color in second["detected_colors"]],
                )

    @staticmethod
    def _horizontal_face_area(mesh: trimesh.Trimesh, z: float) -> float:
        vertices = np.asarray(mesh.vertices)
        area = 0.0
        for face in np.asarray(mesh.faces):
            points = vertices[face]
            if np.allclose(points[:, 2], z, atol=1e-6):
                area += float(np.linalg.norm(np.cross(points[1] - points[0], points[2] - points[0])) / 2.0)
        return area

    def test_background_confidence_threshold_controls_ignore_decision(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            image_path = temp_path / "background.png"
            stl_path = temp_path / "background.stl"
            self._save_three_color_test_image(image_path)

            config = replace(
                load_config(Path("config/config.yaml")).filament_swap_relief,
                background_confidence_threshold=0.95,
                min_region_area_px=1,
            )

            _stl_result, metadata = create_filament_swap_relief_stl(image_path, stl_path, config)

            self.assertFalse(metadata["background_ignored"])
            self.assertLess(metadata["background_border_fraction"], metadata["background_confidence_threshold"])
            self.assertTrue(any("Background detection was uncertain" in warning for warning in metadata["warnings"]))

    def test_two_color_relief_maps_dark_color_highest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            image_path = temp_path / "two_color.png"
            stl_path = temp_path / "two_color.stl"
            image = Image.new("RGB", (24, 18), (80, 90, 105))
            draw = ImageDraw.Draw(image)
            draw.rectangle((4, 4, 20, 14), fill=(250, 250, 250))
            draw.rectangle((9, 7, 15, 11), fill=(5, 5, 5))
            image.save(image_path)

            config = replace(
                load_config(Path("config/config.yaml")).filament_swap_relief,
                width_mm=24.0,
                color_count=2,
                max_sampled_pixels=1000,
                min_region_area_px=1,
            )

            stl_result, metadata = create_filament_swap_relief_stl(image_path, stl_path, config)
            report = validate_stl_mesh(stl_path, stl_result.requested_backend, stl_result.actual_backend)

            self.assertTrue(report.watertight)
            self.assertEqual([color["suggested_color_name"] for color in metadata["detected_colors"]], ["white", "black"])
            self.assertEqual([color["assigned_height_mm"] for color in metadata["detected_colors"]], [0.8, 2.0])

    def test_stl_geometry_uses_aligned_heights(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            image_path = temp_path / "snap_up.png"
            stl_path = temp_path / "snap_up.stl"
            self._save_three_color_test_image(image_path)
            config = replace(
                load_config(Path("config/config.yaml")).filament_swap_relief,
                width_mm=24.0,
                color_count=2,
                base_height_mm=0.85,
                layer_step_mm=0.35,
                first_layer_height_mm=0.2,
                layer_height_mm=0.2,
                height_alignment_mode="snap_up",
                min_model_thickness_mm=0.0,
                max_sampled_pixels=1000,
                min_region_area_px=1,
            )

            stl_result, metadata = create_filament_swap_relief_stl(image_path, stl_path, config)
            report = validate_stl_mesh(stl_path, stl_result.requested_backend, stl_result.actual_backend)

            self.assertTrue(report.watertight)
            self.assertEqual(metadata["color_plan"]["height_settings"]["aligned_cumulative_boundaries_mm"], [0.0, 1.0, 1.2])
            self.assertAlmostEqual(report.bounding_box_mm[2], 1.2, places=3)
            self.assertEqual([color["assigned_height_mm"] for color in metadata["detected_colors"]], [1.0, 1.2])

    def test_tiny_isolated_color_regions_are_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            image_path = temp_path / "specks.png"
            stl_path = temp_path / "specks.stl"
            image = Image.new("RGB", (36, 24), (80, 90, 105))
            draw = ImageDraw.Draw(image)
            draw.rectangle((5, 5, 30, 18), fill=(250, 250, 245))
            draw.rectangle((12, 9, 22, 14), fill=(8, 8, 10))
            for x, y in [(2, 2), (33, 2), (2, 21), (33, 21)]:
                draw.rectangle((x, y, x + 1, y + 1), fill=(8, 8, 10))
            image.save(image_path)

            config = replace(
                load_config(Path("config/config.yaml")).filament_swap_relief,
                width_mm=36.0,
                color_count=2,
                max_sampled_pixels=1000,
                min_region_area_px=8,
                smooth_edges=False,
            )

            _stl_result, metadata = create_filament_swap_relief_stl(image_path, stl_path, config)

            self.assertGreater(metadata["removed_region_count"], 0)
            self.assertGreater(metadata["removed_pixel_count"], 0)
            self.assertGreater(metadata["island_summary"]["removed_components"], 0)

    def test_preserve_all_keeps_tiny_islands_in_height_map_and_stl(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            image_path = temp_path / "specks_preserve.png"
            removed_stl = temp_path / "removed.stl"
            preserved_stl = temp_path / "preserved.stl"
            self._save_speck_test_image(image_path)
            base = replace(
                load_config(Path("config/config.yaml")).filament_swap_relief,
                width_mm=36.0,
                color_count=2,
                max_sampled_pixels=1000,
                min_region_area_px=8,
                smooth_edges=False,
            )
            _removed_result, removed = create_filament_swap_relief_stl(image_path, removed_stl, base)
            preserved_result, preserved = create_filament_swap_relief_stl(
                image_path,
                preserved_stl,
                replace(base, island_policy="preserve_all"),
            )
            report = validate_stl_mesh(preserved_stl, preserved_result.requested_backend, preserved_result.actual_backend)

            self.assertTrue(report.watertight)
            self.assertEqual(preserved["island_summary"]["removed_components"], 0)
            self.assertEqual(preserved["island_summary"]["merged_components"], 0)
            self.assertEqual(preserved["island_summary"]["connected_components"], 0)
            self.assertGreater(preserved["island_summary"]["intentionally_preserved_components"], 0)
            self.assertGreater(
                sum(color["pixel_count"] for color in preserved["detected_colors"]),
                sum(color["pixel_count"] for color in removed["detected_colors"]),
            )

    def test_merge_policy_records_deterministic_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            image_path = temp_path / "merge.png"
            stl_path = temp_path / "merge.stl"
            image = Image.new("RGB", (24, 18), (80, 90, 105))
            draw = ImageDraw.Draw(image)
            draw.rectangle((4, 4, 18, 14), fill=(250, 250, 250))
            draw.point((19, 9), fill=(230, 20, 20))
            image.save(image_path)
            config = replace(
                load_config(Path("config/config.yaml")).filament_swap_relief,
                color_count=2,
                min_region_area_px=2,
                island_policy="merge_with_nearest_region",
                island_merge_max_distance_px=4,
                smooth_edges=False,
            )

            _stl_result, metadata = create_filament_swap_relief_stl(image_path, stl_path, config)

            merged = [record for record in metadata["component_actions"] if record["action"] == "merged"]
            self.assertEqual(metadata["island_summary"]["merged_components"], 1)
            self.assertEqual(len(merged), 1)
            self.assertIn("destination_label", merged[0])
            self.assertGreaterEqual(merged[0]["boundary_contact_px"], 1)

    def test_connect_policy_connects_only_same_color_components(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            image_path = temp_path / "connect.png"
            stl_path = temp_path / "connect.stl"
            image = Image.new("RGB", (30, 20), (80, 90, 105))
            draw = ImageDraw.Draw(image)
            draw.rectangle((4, 5, 14, 14), fill=(250, 250, 250))
            draw.point((17, 10), fill=(250, 250, 250))
            image.save(image_path)
            config = replace(
                load_config(Path("config/config.yaml")).filament_swap_relief,
                color_count=1,
                min_region_area_px=2,
                island_policy="connect_within_maximum_gap",
                island_connect_max_gap_px=4,
                island_connection_width_px=1,
                smooth_edges=False,
            )

            stl_result, metadata = create_filament_swap_relief_stl(image_path, stl_path, config)
            report = validate_stl_mesh(stl_path, stl_result.requested_backend, stl_result.actual_backend)

            self.assertTrue(report.watertight)
            self.assertEqual(metadata["island_summary"]["connected_components"], 1)
            self.assertGreater(metadata["island_summary"]["connector_pixels_added"], 0)
            connected = [record for record in metadata["component_actions"] if record["action"] == "connected"]
            self.assertEqual(len(connected), 1)
            self.assertEqual(connected[0]["source_palette_index"], connected[0]["destination_label"])

    def test_pipeline_writes_filament_swap_package_without_svg_claims(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "filament_swap_input.png"
            output_dir = temp_path / "output"
            log_dir = temp_path / "logs"
            output_dir.mkdir()
            log_dir.mkdir()
            self._save_three_color_test_image(input_path)

            config = load_config(Path("config/config.yaml"))
            config = replace(
                config,
                input_dir=temp_path,
                output_dir=output_dir,
                log_dir=log_dir,
                pipeline=replace(config.pipeline, product_mode="filament_swap_relief"),
                stl=replace(config.stl, product_mode="filament_swap_relief"),
                filament_swap_relief=replace(
                    config.filament_swap_relief,
                    width_mm=48.0,
                    max_sampled_pixels=5000,
                    min_region_area_px=3,
                ),
            )
            logger = logging.getLogger("spool_house_ai.tests.filament_swap")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())

            self.assertTrue(ImagePipeline(config, logger).process(input_path))

            paths = build_job_output_paths(output_dir, input_path)
            self.assertTrue(paths.source_copy_path.exists())
            self.assertTrue(paths.stl_path.exists())
            self.assertTrue(paths.generic_3mf_path.exists())
            self.assertTrue(paths.preview_path.exists())
            self.assertTrue(paths.cleaned_png_path.exists())
            self.assertTrue(paths.silhouette_png_path.exists())
            self.assertTrue(paths.mesh_report_path.exists())
            self.assertTrue(paths.job_status_path.exists())
            self.assertTrue(paths.job_summary_path.exists())
            self.assertTrue(paths.filament_swap_report_path.exists())
            self.assertTrue(paths.color_plan_path.exists())
            self.assertTrue(paths.filament_swap_plan_path.exists())
            self.assertTrue((paths.previews_dir / f"{input_path.stem}_filament_islands_detected.png").exists())
            self.assertFalse(paths.svg_path.exists())
            self.assertFalse(paths.review_svg_path.exists())
            self.assertFalse((paths.reports_dir / "filament_swap.gcode").exists())

            status = json.loads(paths.job_status_path.read_text(encoding="utf-8"))
            self.assertEqual(status["product_mode"], "filament_swap_relief")
            self.assertEqual(status["requested_backend"], FILAMENT_SWAP_BACKEND)
            self.assertEqual(status["actual_backend"], FILAMENT_SWAP_BACKEND)
            self.assertEqual(status["svg_path"], "")
            self.assertEqual(status["review_svg_path"], "")
            self.assertEqual(status["artifact_summary"]["cleanup_preset"], "not_applicable")
            self.assertTrue(status["artifact_summary"]["cleanup_presets_ignored"])
            self.assertEqual(status["filament_swap_report_path"], str(paths.filament_swap_report_path))
            self.assertEqual(status["color_plan_path"], str(paths.color_plan_path))
            self.assertEqual(status["filament_swap_plan_path"], str(paths.filament_swap_plan_path))
            self.assertEqual(status["generic_3mf_path"], str(paths.generic_3mf_path))
            self.assertEqual(status["three_mf_folder_path"], str(paths.three_mf_dir))
            self.assertEqual(status["dimensions"]["generic_3mf_export"], "automatic")
            self.assertTrue(status["generic_3mf_summary"]["generic_3mf_created"])
            self.assertTrue(status["generic_3mf_summary"]["generic_3mf_validation_passed"])
            self.assertEqual(status["generic_3mf_summary"]["generic_3mf_path"], str(paths.generic_3mf_path))
            self.assertEqual(status["filament_swap_summary"]["color_count_kept"], 3)
            self.assertEqual(status["filament_swap_summary"]["swap_plan_summary"]["total_printed_layers"], 10)
            self.assertTrue(status["filament_swap_summary"]["generic_3mf_enabled"])
            self.assertTrue(status["filament_swap_summary"]["generic_3mf_created"])
            self.assertTrue(status["filament_swap_summary"]["generic_3mf_validation_passed"])
            self.assertEqual(status["filament_swap_summary"]["generic_3mf_path"], str(paths.generic_3mf_path))
            self.assertEqual(status["filament_swap_summary"]["generic_3mf_units"], "millimeter")
            self.assertTrue(status["filament_swap_summary"]["bounds_match"])
            self.assertIn("generic_export_notice", status["filament_swap_summary"])
            self.assertIn("island_summary", status["filament_swap_summary"])
            self.assertNotIn("component_actions", status["filament_swap_summary"])
            self.assertNotIn("color_plan", status["filament_swap_summary"])
            self.assertTrue(status["mesh_summary"]["watertight"])

            summary = paths.job_summary_path.read_text(encoding="utf-8")
            self.assertIn("## Filament Swap Relief", summary)
            self.assertIn("## Filament Island Handling", summary)
            self.assertIn("## Filament Swap Plan", summary)
            self.assertIn("## Generic 3MF Export", summary)
            self.assertIn("Manual filament-change instructions are stored separately", summary)
            self.assertIn("Change before layer", summary)
            self.assertIn("not_applicable", summary)
            text_plan = paths.filament_swap_plan_path.read_text(encoding="utf-8")
            self.assertIn("FILAMENT RELIEF MANUAL SWAP PLAN", text_plan)
            self.assertIn("Layers are one-based.", text_plan)
            self.assertIn("CHANGE BEFORE LAYER 5", text_plan)

    def test_diagonal_height_contacts_are_repaired_before_stl_export(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            image_path = temp_path / "diagonal_contacts.png"
            stl_path = temp_path / "diagonal_contacts.stl"
            image = Image.new("RGB", (4, 4), (80, 90, 105))
            draw = ImageDraw.Draw(image)
            draw.point((1, 1), fill=(250, 250, 245))
            draw.point((2, 2), fill=(250, 250, 245))
            draw.point((2, 1), fill=(8, 8, 10))
            draw.point((1, 2), fill=(8, 8, 10))
            image.save(image_path)
            config = replace(
                load_config(Path("config/config.yaml")).filament_swap_relief,
                width_mm=4.0,
                color_count=2,
                auto_background_ignore=True,
                max_sampled_pixels=100,
                min_region_area_px=1,
                smooth_edges=False,
                min_model_thickness_mm=0.0,
            )

            stl_result, metadata = create_filament_swap_relief_stl(image_path, stl_path, config)
            report = validate_stl_mesh(stl_path, stl_result.requested_backend, stl_result.actual_backend)

            self.assertTrue(report.watertight)
            self.assertEqual(report.open_edge_count, 0)
            self.assertEqual(report.overused_edge_count, 0)
            self.assertEqual(report.non_manifold_edge_count, 0)
            self.assertGreater(metadata["heightfield_topology_repair_pixels"], 0)

    def test_pipeline_always_exports_generic_3mf_for_filament_relief(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "filament_swap_disabled.png"
            output_dir = temp_path / "output"
            log_dir = temp_path / "logs"
            output_dir.mkdir()
            log_dir.mkdir()
            self._save_three_color_test_image(input_path)

            config = load_config(Path("config/config.yaml"))
            config = replace(
                config,
                input_dir=temp_path,
                output_dir=output_dir,
                log_dir=log_dir,
                pipeline=replace(config.pipeline, product_mode="filament_swap_relief"),
                stl=replace(config.stl, product_mode="filament_swap_relief"),
                filament_swap_relief=replace(
                    config.filament_swap_relief,
                    width_mm=48.0,
                    max_sampled_pixels=5000,
                    min_region_area_px=3,
                ),
            )
            logger = logging.getLogger("spool_house_ai.tests.filament_swap.automatic_3mf")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())

            self.assertTrue(ImagePipeline(config, logger).process(input_path))

            paths = build_job_output_paths(output_dir, input_path)
            self.assertTrue(paths.stl_path.exists())
            self.assertTrue(paths.generic_3mf_path.exists())
            status = json.loads(paths.job_status_path.read_text(encoding="utf-8"))
            self.assertEqual(status["generic_3mf_path"], str(paths.generic_3mf_path))
            self.assertEqual(status["dimensions"]["generic_3mf_export"], "automatic")
            self.assertTrue(status["generic_3mf_summary"]["generic_3mf_enabled"])
            self.assertTrue(status["generic_3mf_summary"]["generic_3mf_created"])
            self.assertTrue(status["generic_3mf_summary"]["generic_3mf_validation_passed"])

    @staticmethod
    def _save_three_color_test_image(path: Path) -> None:
        image = Image.new("RGB", (48, 32), (80, 90, 105))
        draw = ImageDraw.Draw(image)
        draw.rectangle((6, 6, 42, 26), fill=(250, 250, 245))
        draw.ellipse((13, 8, 35, 24), outline=(230, 20, 20), width=5)
        draw.rectangle((20, 12, 28, 20), fill=(10, 10, 12))
        image.save(path)

    @staticmethod
    def _save_speck_test_image(path: Path) -> None:
        image = Image.new("RGB", (36, 24), (80, 90, 105))
        draw = ImageDraw.Draw(image)
        draw.rectangle((5, 5, 30, 18), fill=(250, 250, 245))
        draw.rectangle((12, 9, 22, 14), fill=(8, 8, 10))
        for x, y in [(2, 2), (33, 2), (2, 21), (33, 21)]:
            draw.rectangle((x, y, x + 1, y + 1), fill=(8, 8, 10))
        image.save(path)

    @staticmethod
    def _save_many_color_test_image(path: Path) -> None:
        image = Image.new("RGB", (40, 30), (45, 52, 65))
        draw = ImageDraw.Draw(image)
        for y in range(30):
            for x in range(40):
                if 4 <= x <= 34 and 4 <= y <= 24:
                    draw.point((x, y), fill=(180 + (x % 30), 40 + (y % 80), 40 + ((x + y) % 60)))
                if 12 <= x <= 28 and 10 <= y <= 20:
                    draw.point((x, y), fill=(235 - (x % 25), 232 - (y % 25), 220))
                if 18 <= x <= 23 and 13 <= y <= 17:
                    draw.point((x, y), fill=(8 + x % 10, 8 + y % 10, 10))
        image.save(path)


if __name__ == "__main__":
    unittest.main()
