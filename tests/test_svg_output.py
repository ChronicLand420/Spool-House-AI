from __future__ import annotations

import tempfile
import unittest
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path

from PIL import Image, ImageDraw

from spool_house_ai.config import load_config
from spool_house_ai.processing.analysis import analyze_image
from spool_house_ai.processing.vectorize import create_svg


class SvgOutputTests(unittest.TestCase):
    def test_svg_has_inspection_layers_and_review_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            image_path = temp_path / "layered_logo.png"
            silhouette_path = temp_path / "layered_logo_silhouette.png"
            svg_path = temp_path / "layered_logo.svg"
            review_svg_path = temp_path / "layered_logo_review.svg"

            image = Image.new("RGBA", (180, 130), (255, 255, 255, 0))
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle((24, 20, 156, 108), radius=18, fill=(210, 55, 40, 255))
            draw.ellipse((74, 48, 106, 80), fill=(255, 255, 255, 0))
            draw.line((42, 94, 138, 36), fill=(12, 12, 12, 255), width=7)
            draw.point((6, 6), fill=(12, 12, 12, 255))
            image.save(image_path)

            config = load_config(Path("config/config.yaml"))
            silhouette_config = replace(
                config.silhouette,
                detail_mode="raised_details",
                preserve_holes=True,
                preserve_internal_details=True,
                min_contour_area=10,
                min_island_area_px=20,
            )
            analysis = analyze_image(image_path, silhouette_path, silhouette_config)

            create_svg(
                analysis,
                svg_path,
                config.svg,
                metadata={
                    "app_version": "test",
                    "input_filename": image_path.name,
                    "product_mode": "flat_relief",
                    "detail_mode": "raised_details",
                },
                review_output_path=review_svg_path,
            )

            self.assertTrue(svg_path.exists())
            self.assertTrue(review_svg_path.exists())

            root = ET.fromstring(svg_path.read_text(encoding="utf-8"))
            review_root = ET.fromstring(review_svg_path.read_text(encoding="utf-8"))
            ids = {element.attrib.get("id") for element in root.iter()}
            review_ids = {element.attrib.get("id") for element in review_root.iter()}

            for expected_id in {
                "foreground_mask",
                "main_body",
                "holes",
                "preserved_details",
                "ignored_islands",
                "job_metadata",
            }:
                self.assertIn(expected_id, ids)
                self.assertIn(expected_id, review_ids)

            self.assertGreater(_path_count_with_prefix(root, "hole_contour_"), 0)
            self.assertGreater(_path_count_with_prefix(root, "detail_contour_"), 0)
            self.assertIn("Built by ChronicLand420", svg_path.read_text(encoding="utf-8"))
            self.assertIn("Spool House Studio", svg_path.read_text(encoding="utf-8"))


def _path_count_with_prefix(root: ET.Element, prefix: str) -> int:
    return sum(1 for element in root.iter() if str(element.attrib.get("id", "")).startswith(prefix))


if __name__ == "__main__":
    unittest.main()
