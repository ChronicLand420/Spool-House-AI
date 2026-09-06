from __future__ import annotations

import json
import logging
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from PIL import Image, ImageDraw

from spool_house_ai.config import load_config
from spool_house_ai.output_paths import build_job_output_paths
from spool_house_ai.pipeline import ImagePipeline


class PipelineJobStatusTests(unittest.TestCase):
    def test_pipeline_writes_job_status_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "status_test.png"
            output_dir = temp_path / "output"
            log_dir = temp_path / "logs"
            output_dir.mkdir()
            log_dir.mkdir()

            image = Image.new("RGBA", (160, 120), (255, 255, 255, 255))
            draw = ImageDraw.Draw(image)
            draw.rectangle((28, 24, 132, 96), fill=(20, 20, 20, 255))
            draw.ellipse((62, 42, 98, 78), fill=(255, 255, 255, 255))
            draw.rectangle((6, 6, 8, 8), fill=(20, 20, 20, 255))
            image.save(input_path)

            config = load_config(Path("config/config.yaml"))
            config = replace(config, input_dir=temp_path, output_dir=output_dir, log_dir=log_dir)
            logger = logging.getLogger("spool_house_ai.tests.job_status")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())

            self.assertTrue(ImagePipeline(config, logger).process(input_path))

            paths = build_job_output_paths(output_dir, input_path)
            job_dir = paths.job_root
            status_path = paths.job_status_path
            summary_path = paths.job_summary_path
            self.assertTrue(paths.source_dir.exists())
            self.assertTrue(paths.svg_dir.exists())
            self.assertTrue(paths.stl_dir.exists())
            self.assertTrue(paths.previews_dir.exists())
            self.assertTrue(paths.reports_dir.exists())
            self.assertTrue(paths.source_copy_path.exists())
            self.assertTrue(paths.svg_path.exists())
            self.assertTrue(paths.review_svg_path.exists())
            self.assertTrue(paths.stl_path.exists())
            self.assertTrue(paths.generic_3mf_path.exists())
            self.assertTrue(paths.preview_path.exists())
            self.assertTrue(paths.mesh_report_path.exists())
            self.assertTrue(status_path.exists())
            self.assertTrue(summary_path.exists())

            status = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(status["input_file_path"], str(input_path.resolve()))
            self.assertEqual(status["output_root_path"], str(output_dir.resolve()))
            self.assertEqual(status["output_folder_path"], str(job_dir))
            self.assertEqual(status["job_root_path"], str(job_dir))
            self.assertEqual(status["source_folder_path"], str(paths.source_dir))
            self.assertEqual(status["svg_folder_path"], str(paths.svg_dir))
            self.assertEqual(status["stl_folder_path"], str(paths.stl_dir))
            self.assertEqual(status["three_mf_folder_path"], str(paths.three_mf_dir))
            self.assertEqual(status["previews_folder_path"], str(paths.previews_dir))
            self.assertEqual(status["reports_folder_path"], str(paths.reports_dir))
            self.assertEqual(status["source_copy_path"], str(paths.source_copy_path))
            self.assertEqual(status["svg_path"], str(paths.svg_path))
            self.assertEqual(status["review_svg_path"], str(paths.review_svg_path))
            self.assertEqual(status["stl_path"], str(paths.stl_path))
            self.assertEqual(status["generic_3mf_path"], str(paths.generic_3mf_path))
            self.assertEqual(status["preview_path"], str(paths.preview_path))
            self.assertEqual(status["mesh_report_path"], str(paths.mesh_report_path))
            self.assertEqual(status["job_status_path"], str(status_path))
            self.assertEqual(status["job_summary_path"], str(summary_path))
            self.assertIn("started_at", status)
            self.assertIn("finished_at", status)
            self.assertGreaterEqual(status["duration_seconds"], 0)
            self.assertEqual(status["requested_backend"], config.stl.stl_backend)
            self.assertIn(status["actual_backend"], {"vector_extrusion", "raster_heightfield"})
            self.assertEqual(status["product_mode"], config.stl.product_mode)
            self.assertEqual(status["detail_mode"], config.stl.detail_mode)
            self.assertGreater(status["mesh_summary"]["face_count"], 0)
            self.assertEqual(status["dimensions"]["generic_3mf_export"], "automatic")
            self.assertEqual(status["dimensions"]["printer_profile_name"], config.printer_profile.profile_name)
            self.assertEqual(status["dimensions"]["printer_nozzle_diameter_mm"], config.printer_profile.nozzle_diameter_mm)
            self.assertEqual(status["settings_used"]["printer_profile"]["profile_name"], config.printer_profile.profile_name)
            self.assertTrue(status["settings_used"]["printability"]["use_printer_aware_defaults"])
            self.assertTrue(status["generic_3mf_summary"]["generic_3mf_created"])
            self.assertTrue(status["generic_3mf_summary"]["generic_3mf_validation_passed"])
            self.assertEqual(status["generic_3mf_summary"]["generic_3mf_units"], "millimeter")
            self.assertTrue(status["generic_3mf_summary"]["bounds_match"])
            self.assertIn("artifact_summary", status)
            self.assertGreaterEqual(status["artifact_summary"]["isolated_island_count"], 1)
            self.assertGreaterEqual(status["artifact_summary"]["removed_island_count"], 1)
            self.assertEqual(status["artifact_summary"]["cleanup_preset"], config.silhouette.cleanup_preset)
            self.assertFalse(status["failures"])
            summary = summary_path.read_text(encoding="utf-8")
            self.assertIn("Ready for slicer review", summary)
            self.assertIn("## Folders", summary)
            self.assertIn(str(paths.reports_dir), summary)
            self.assertIn("Mesh", summary)
            self.assertIn("Generic 3MF Export", summary)
            self.assertIn("Manual filament-change instructions are stored separately", summary)
            self.assertIn("Artwork Cleanup", summary)

    def test_pipeline_saves_negative_input_without_modifying_source_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "negative_test.png"
            output_dir = temp_path / "output"
            log_dir = temp_path / "logs"
            output_dir.mkdir()
            log_dir.mkdir()

            image = Image.new("RGB", (120, 90), "black")
            draw = ImageDraw.Draw(image)
            draw.rectangle((30, 20, 90, 70), fill="white")
            image.save(input_path)

            config = load_config(Path("config/config.yaml"))
            config = replace(
                config,
                input_dir=temp_path,
                output_dir=output_dir,
                log_dir=log_dir,
                pipeline=replace(config.pipeline, invert_input_enabled=True),
            )
            logger = logging.getLogger("spool_house_ai.tests.negative_input")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())

            self.assertTrue(ImagePipeline(config, logger).process(input_path))

            paths = build_job_output_paths(output_dir, input_path)
            self.assertTrue(paths.source_copy_path.exists())
            self.assertTrue(paths.preprocessed_input_path.exists())
            with Image.open(paths.source_copy_path) as copied_source:
                source_rgb = copied_source.convert("RGB")
                self.assertEqual(source_rgb.getpixel((0, 0)), (0, 0, 0))
                self.assertEqual(source_rgb.getpixel((60, 45)), (255, 255, 255))
            with Image.open(paths.preprocessed_input_path) as negative_input:
                negative_rgb = negative_input.convert("RGB")
                self.assertEqual(negative_rgb.getpixel((0, 0)), (255, 255, 255))
                self.assertEqual(negative_rgb.getpixel((60, 45)), (0, 0, 0))

            status = json.loads(paths.job_status_path.read_text(encoding="utf-8"))
            self.assertEqual(status["preprocessed_input_path"], str(paths.preprocessed_input_path))
            self.assertTrue(status["input_preprocessing"]["invert_input_enabled"])
            self.assertEqual(status["input_preprocessing"]["processing_input_path"], str(paths.preprocessed_input_path))
            self.assertTrue(status["settings_used"]["pipeline"]["invert_input_enabled"])
            self.assertTrue(status["dimensions"]["invert_input_enabled"])
            settings_text = paths.settings_path.read_text(encoding="utf-8")
            self.assertIn("invert_input_enabled: true", settings_text)
            summary = paths.job_summary_path.read_text(encoding="utf-8")
            self.assertIn("Negative before processing: `True`", summary)
            self.assertIn(str(paths.preprocessed_input_path), summary)


if __name__ == "__main__":
    unittest.main()
