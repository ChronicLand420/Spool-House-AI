from __future__ import annotations

import json
import logging
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np
from PIL import Image

from spool_house_ai.config import load_config
from spool_house_ai.output_paths import build_job_output_paths
from spool_house_ai.pipeline import ImagePipeline
from spool_house_ai.processing.generic_3mf import validate_generic_3mf
from spool_house_ai.processing.stl import (
    create_lithophane_stl,
    _preprocess_lithophane_image,
    lithophane_thickness_from_brightness,
    validate_stl_mesh,
)


class LithophaneTests(unittest.TestCase):
    def test_bright_pixels_are_thinner_by_default(self) -> None:
        brightness = np.array([[0.0, 0.5, 1.0]], dtype=np.float32)

        thickness = lithophane_thickness_from_brightness(
            brightness,
            min_thickness_mm=0.8,
            max_thickness_mm=3.0,
        )

        self.assertAlmostEqual(float(thickness[0, 0]), 3.0)
        self.assertAlmostEqual(float(thickness[0, 2]), 0.8)
        self.assertGreater(float(thickness[0, 0]), float(thickness[0, 1]))
        self.assertGreater(float(thickness[0, 1]), float(thickness[0, 2]))

    def test_invert_reverses_lithophane_mapping(self) -> None:
        brightness = np.array([[0.0, 1.0]], dtype=np.float32)

        thickness = lithophane_thickness_from_brightness(
            brightness,
            min_thickness_mm=0.8,
            max_thickness_mm=3.0,
            invert=True,
        )

        self.assertAlmostEqual(float(thickness[0, 0]), 0.8)
        self.assertAlmostEqual(float(thickness[0, 1]), 3.0)

    def test_default_lithophane_preprocessing_is_no_op(self) -> None:
        image = Image.fromarray(np.array([[0, 64, 128, 255]], dtype=np.uint8), mode="L")
        config = load_config(Path("config/config.yaml")).stl

        processed = _preprocess_lithophane_image(image, config)

        np.testing.assert_array_equal(np.asarray(processed), np.asarray(image))

    def test_lithophane_gamma_changes_processed_midtones(self) -> None:
        image = Image.fromarray(np.array([[64, 128, 192]], dtype=np.uint8), mode="L")
        config = replace(load_config(Path("config/config.yaml")).stl, lithophane_gamma=2.0)

        processed = _preprocess_lithophane_image(image, config)

        self.assertLess(processed.getpixel((1, 0)), image.getpixel((1, 0)))
        self.assertEqual(processed.mode, "L")

    def test_lithophane_contrast_changes_processed_values(self) -> None:
        image = Image.fromarray(np.array([[96, 128, 160]], dtype=np.uint8), mode="L")
        config = replace(load_config(Path("config/config.yaml")).stl, lithophane_contrast=2.0)

        processed = _preprocess_lithophane_image(image, config)

        self.assertLess(processed.getpixel((0, 0)), image.getpixel((0, 0)))
        self.assertGreater(processed.getpixel((2, 0)), image.getpixel((2, 0)))

    def test_lithophane_stl_is_watertight_for_tiny_image(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            image_path = temp_path / "gradient.png"
            stl_path = temp_path / "gradient.stl"
            generic_3mf_path = temp_path / "gradient.3mf"
            preview_path = temp_path / "gradient_preview.png"
            processed_preview_path = temp_path / "gradient_processed.png"
            image = Image.fromarray(
                np.array(
                    [
                        [0, 64, 128, 255],
                        [255, 128, 64, 0],
                        [40, 120, 200, 240],
                    ],
                    dtype=np.uint8,
                ),
                mode="L",
            )
            image.save(image_path)

            config = replace(
                load_config(Path("config/config.yaml")).stl,
                lithophane_width_mm=40.0,
                lithophane_min_thickness_mm=0.8,
                lithophane_max_thickness_mm=3.0,
                lithophane_max_pixels=1000,
            )

            stl_result, metadata = create_lithophane_stl(
                image_path,
                stl_path,
                config,
                preview_path=preview_path,
                processed_preview_path=processed_preview_path,
                generic_3mf_path=generic_3mf_path,
            )
            report = validate_stl_mesh(
                stl_path,
                requested_backend=stl_result.requested_backend,
                actual_backend=stl_result.actual_backend,
            )

            self.assertEqual(stl_result.actual_backend, "lithophane_heightfield")
            self.assertFalse(stl_result.fallback_used)
            self.assertTrue(generic_3mf_path.exists())
            self.assertTrue(stl_result.generic_3mf_metadata["generic_3mf_created"])
            self.assertTrue(stl_result.generic_3mf_metadata["generic_3mf_validation_passed"])
            self.assertTrue(validate_generic_3mf(generic_3mf_path).passed)
            self.assertTrue(preview_path.exists())
            self.assertTrue(processed_preview_path.exists())
            self.assertTrue(report.watertight)
            self.assertEqual(report.open_edge_count, 0)
            self.assertEqual(report.overused_edge_count, 0)
            self.assertEqual(report.non_manifold_edge_count, 0)
            self.assertEqual(report.failures, [])
            self.assertEqual(metadata["sampled_width_px"], 4)
            self.assertEqual(metadata["sampled_height_px"], 3)
            self.assertAlmostEqual(metadata["width_mm"], 40.0)
            self.assertAlmostEqual(metadata["height_mm"], 30.0)
            self.assertEqual(metadata["preprocessing"]["contrast"], 1.0)
            self.assertEqual(metadata["preprocessing"]["gamma"], 1.0)
            self.assertEqual(metadata["preprocessing"]["sharpen_strength"], 0.0)
            self.assertEqual(metadata["preprocessing"]["denoise_radius_px"], 0)
            self.assertEqual(metadata["preprocessing"]["processed_preview_path"], str(processed_preview_path))

    def test_pipeline_writes_lithophane_job_package_without_svg_claims(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "lithophane_input.png"
            output_dir = temp_path / "output"
            log_dir = temp_path / "logs"
            output_dir.mkdir()
            log_dir.mkdir()
            Image.fromarray(np.tile(np.arange(16, dtype=np.uint8), (12, 1)) * 16, mode="L").save(input_path)

            config = load_config(Path("config/config.yaml"))
            config = replace(
                config,
                input_dir=temp_path,
                output_dir=output_dir,
                log_dir=log_dir,
                pipeline=replace(config.pipeline, product_mode="lithophane"),
                stl=replace(
                    config.stl,
                    product_mode="lithophane",
                    lithophane_width_mm=32.0,
                    lithophane_min_thickness_mm=0.8,
                    lithophane_max_thickness_mm=3.0,
                    lithophane_max_pixels=1000,
                ),
            )
            logger = logging.getLogger("spool_house_ai.tests.lithophane")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())

            self.assertTrue(ImagePipeline(config, logger).process(input_path))

            paths = build_job_output_paths(output_dir, input_path)
            self.assertTrue(paths.source_copy_path.exists())
            self.assertTrue(paths.stl_path.exists())
            self.assertTrue(paths.generic_3mf_path.exists())
            self.assertTrue(paths.preview_path.exists())
            self.assertTrue(paths.mesh_report_path.exists())
            self.assertTrue(paths.job_status_path.exists())
            self.assertTrue(paths.job_summary_path.exists())
            self.assertFalse(paths.svg_path.exists())
            self.assertFalse(paths.review_svg_path.exists())

            status = json.loads(paths.job_status_path.read_text(encoding="utf-8"))
            self.assertEqual(status["product_mode"], "lithophane")
            self.assertEqual(status["requested_backend"], "lithophane_heightfield")
            self.assertEqual(status["actual_backend"], "lithophane_heightfield")
            self.assertEqual(status["svg_path"], "")
            self.assertEqual(status["review_svg_path"], "")
            self.assertEqual(status["generic_3mf_path"], str(paths.generic_3mf_path))
            self.assertTrue(status["generic_3mf_summary"]["generic_3mf_created"])
            self.assertTrue(status["generic_3mf_summary"]["generic_3mf_validation_passed"])
            self.assertEqual(status["artifact_summary"]["cleanup_preset"], "not_applicable")
            self.assertTrue(status["artifact_summary"]["cleanup_presets_ignored"])
            self.assertEqual(status["lithophane_summary"]["mapping"], "bright_thin_dark_thick")
            self.assertEqual(status["lithophane_summary"]["sampled_width_px"], 16)
            self.assertEqual(status["lithophane_summary"]["sampled_height_px"], 12)
            preprocessing = status["lithophane_summary"]["preprocessing"]
            self.assertFalse(preprocessing["autocontrast_enabled"])
            self.assertEqual(preprocessing["contrast"], 1.0)
            self.assertEqual(preprocessing["gamma"], 1.0)
            self.assertEqual(preprocessing["sharpen_strength"], 0.0)
            self.assertEqual(preprocessing["denoise_radius_px"], 0)
            self.assertTrue(Path(preprocessing["processed_preview_path"]).exists())
            self.assertTrue(status["mesh_summary"]["watertight"])

            summary = paths.job_summary_path.read_text(encoding="utf-8")
            self.assertIn("## Lithophane", summary)
            self.assertIn("## Lithophane Crispness", summary)
            self.assertIn("Processed preview", summary)
            self.assertIn("not_applicable", summary)


if __name__ == "__main__":
    unittest.main()
