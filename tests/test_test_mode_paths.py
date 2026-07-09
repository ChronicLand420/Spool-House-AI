from __future__ import annotations

import logging
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from spool_house_ai.config import load_config
from spool_house_ai.output_paths import build_job_output_paths
from spool_house_ai.pipeline import ImagePipeline
from spool_house_ai.test_mode import run_test_mode


class TestModePathTests(unittest.TestCase):
    def test_test_mode_verifies_organized_job_packages_without_flat_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_dir = temp_path / "input"
            output_dir = temp_path / "output"
            log_dir = temp_path / "logs"
            input_dir.mkdir()
            output_dir.mkdir()
            log_dir.mkdir()

            config = load_config(Path("config/config.yaml"))
            config = replace(config, input_dir=input_dir, output_dir=output_dir, log_dir=log_dir)
            logger = logging.getLogger("spool_house_ai.tests.test_mode_paths")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())

            self.assertTrue(run_test_mode(config, ImagePipeline(config, logger), logger))

            paths = build_job_output_paths(output_dir, input_dir / "v2_test_artwork.png")
            self.assertTrue(paths.svg_path.exists())
            self.assertTrue(paths.review_svg_path.exists())
            self.assertTrue(paths.stl_path.exists())
            self.assertTrue(paths.mesh_report_path.exists())
            self.assertTrue(paths.job_status_path.exists())
            self.assertFalse((paths.job_root / "v2_test_artwork.svg").exists())
            self.assertFalse((paths.job_root / "mesh_report.json").exists())


if __name__ == "__main__":
    unittest.main()
