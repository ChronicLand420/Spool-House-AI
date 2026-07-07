from __future__ import annotations

import json
import logging
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from PIL import Image, ImageDraw

from spool_house_ai.config import load_config
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
            image.save(input_path)

            config = load_config(Path("config/config.yaml"))
            config = replace(config, input_dir=temp_path, output_dir=output_dir, log_dir=log_dir)
            logger = logging.getLogger("spool_house_ai.tests.job_status")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())

            self.assertTrue(ImagePipeline(config, logger).process(input_path))

            job_dir = output_dir / input_path.stem
            status_path = job_dir / "job_status.json"
            self.assertTrue(status_path.exists())

            status = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(status["input_file_path"], str(input_path.resolve()))
            self.assertEqual(status["output_folder_path"], str(job_dir))
            self.assertEqual(status["svg_path"], str(job_dir / f"{input_path.stem}.svg"))
            self.assertEqual(status["stl_path"], str(job_dir / f"{input_path.stem}.stl"))
            self.assertEqual(status["mesh_report_path"], str(job_dir / "mesh_report.json"))
            self.assertEqual(status["job_status_path"], str(status_path))
            self.assertEqual(status["requested_backend"], config.stl.stl_backend)
            self.assertIn(status["actual_backend"], {"vector_extrusion", "raster_heightfield"})
            self.assertEqual(status["product_mode"], config.stl.product_mode)
            self.assertEqual(status["detail_mode"], config.stl.detail_mode)
            self.assertGreater(status["mesh_summary"]["face_count"], 0)
            self.assertFalse(status["failures"])


if __name__ == "__main__":
    unittest.main()
