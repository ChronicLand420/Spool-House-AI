from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from spool_house_ai.artwork_recommendations import (
    ArtworkRecommendation,
    ArtworkRecommendationCache,
    MIN_RECOMMENDED_THICKNESS_MM,
    recommend_artwork_settings,
    safe_recommend_artwork_settings,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
QA_IMAGES = {
    "Tanjiro.jpg": REPO_ROOT / "input" / "Tanjiro.jpg",
    "Deer Scene.png": REPO_ROOT / "input" / "Deer Scene.png",
    "mopar v3.png": REPO_ROOT / "input" / "mopar v3.png",
    "Nike Drip.jpg": REPO_ROOT / "input" / "Nike Drip.jpg",
    "Butterfly Flower.png": REPO_ROOT / "input" / "Butterfly Flower.png",
}
OPTIONAL_QA_ENV_VARS = {
    "Tanjiro.jpg": "SHS_QA_IMAGE_TANJIRO",
    "Deer Scene.png": "SHS_QA_IMAGE_DEER_SCENE",
    "mopar v3.png": "SHS_QA_IMAGE_MOPAR_V3",
    "Nike Drip.jpg": "SHS_QA_IMAGE_NIKE_DRIP",
    "Butterfly Flower.png": "SHS_QA_IMAGE_BUTTERFLY_FLOWER",
}
VALID_CONFIDENCE = {"low", "medium", "high"}


def _save_line_art(path: Path) -> None:
    image = Image.new("RGB", (220, 180), "white")
    draw = ImageDraw.Draw(image)
    for x in range(20, 200, 14):
        draw.line((x, 20, x, 160), fill="black", width=1)
    for y in range(25, 160, 14):
        draw.line((20, y, 200, y), fill="black", width=1)
    draw.ellipse((74, 48, 146, 120), outline="black", width=2)
    image.save(path)


def _save_clean_logo(path: Path) -> None:
    image = Image.new("RGB", (220, 180), "white")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((45, 35, 175, 145), radius=20, fill="black")
    draw.ellipse((90, 70, 130, 110), fill="white")
    image.save(path)


def _save_splatter(path: Path) -> None:
    image = Image.new("RGB", (220, 180), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse((76, 55, 142, 120), fill="black")
    for x, y, radius in [
        (32, 29, 4),
        (48, 130, 7),
        (172, 42, 6),
        (188, 135, 5),
        (150, 150, 4),
        (64, 80, 3),
        (120, 28, 4),
        (202, 92, 3),
        (28, 98, 5),
        (178, 76, 4),
        (96, 150, 3),
        (140, 34, 3),
    ]:
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill="black")
    for x, y in [(70, 30), (75, 33), (80, 37), (155, 115), (160, 119), (165, 122), (110, 145), (113, 149)]:
        draw.rectangle((x, y, x + 2, y + 2), fill="black")
    image.save(path)


def _save_drip(path: Path) -> None:
    image = Image.new("RGB", (240, 220), "white")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((70, 35, 170, 58), radius=8, fill="black")
    for x, lower_y in [(82, 145), (118, 190), (155, 165)]:
        draw.line((x, 55, x + 4, lower_y), fill="black", width=5)
        draw.ellipse((x - 4, lower_y - 4, x + 8, lower_y + 8), fill="black")
    image.save(path)


def _save_disconnected_components(path: Path) -> None:
    image = Image.new("RGB", (260, 220), "white")
    draw = ImageDraw.Draw(image)
    for index in range(8):
        x = 25 + (index % 4) * 58
        y = 30 + (index // 4) * 70
        draw.rounded_rectangle((x, y, x + 24, y + 24), radius=5, fill="black")
    image.save(path)


def _write_synthetic_fixture(temp_dir: Path, name: str) -> Path:
    path = temp_dir / f"{name}.png"
    makers = {
        "line_art": _save_line_art,
        "clean_logo": _save_clean_logo,
        "splatter": _save_splatter,
        "drip": _save_drip,
        "disconnected_components": _save_disconnected_components,
    }
    makers[name](path)
    return path


def _assert_recommendation_shape(testcase: unittest.TestCase, recommendation: ArtworkRecommendation) -> None:
    testcase.assertTrue(recommendation.available)
    testcase.assertGreaterEqual(recommendation.recommended_thickness_mm, MIN_RECOMMENDED_THICKNESS_MM)
    testcase.assertIn(recommendation.confidence, VALID_CONFIDENCE)
    testcase.assertGreaterEqual(len(recommendation.reasons), 1)
    for reason in recommendation.reasons:
        testcase.assertIsInstance(reason, str)
        testcase.assertGreaterEqual(len(reason.strip()), 6)
    testcase.assertTrue(recommendation.scores)


def _optional_qa_image_path(name: str) -> tuple[Path, str]:
    env_value = os.environ.get(OPTIONAL_QA_ENV_VARS[name])
    if env_value:
        path = Path(env_value)
        return path, str(path)
    return QA_IMAGES[name], f"input/{name}"


class ArtworkRecommendationSyntheticTests(unittest.TestCase):
    def test_synthetic_fixture_recommendations_are_deterministic_and_structural(self) -> None:
        cases = {
            "line_art": {
                "allowed": {"line_art", "detail_preserving"},
                "score_above": ("line_art", "clean_logo"),
            },
            "clean_logo": {
                "allowed": {"clean_logo"},
                "score_above": ("clean_logo", "line_art"),
            },
            "splatter": {
                "allowed": {"splatter_logo"},
                "score_above": ("splatter_logo", "clean_logo"),
            },
            "drip": {
                "allowed": {"drip_logo"},
                "score_above": ("drip_logo", "splatter_logo"),
            },
            "disconnected_components": {
                "allowed": {"preserve_floating_islands"},
                "score_above": ("preserve_floating_islands", "line_art"),
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for fixture_name, expectation in cases.items():
                with self.subTest(fixture_name=fixture_name):
                    image_path = _write_synthetic_fixture(root, fixture_name)
                    recommendation = recommend_artwork_settings(image_path, product_mode="wall_art")
                    _assert_recommendation_shape(self, recommendation)
                    self.assertIn(recommendation.recommended_preset, expectation["allowed"])
                    stronger, unrelated = expectation["score_above"]
                    self.assertGreater(recommendation.scores[stronger], recommendation.scores[unrelated])

    def test_minimum_thickness_never_drops_below_two_mm(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for fixture_name in ["line_art", "clean_logo", "splatter", "drip", "disconnected_components"]:
                with self.subTest(fixture_name=fixture_name):
                    image_path = _write_synthetic_fixture(root, fixture_name)
                    recommendation = recommend_artwork_settings(image_path, product_mode="wall_art")
                    self.assertGreaterEqual(recommendation.recommended_thickness_mm, 2.0)

    def test_cache_key_uses_content_and_relevant_settings(self) -> None:
        cache = ArtworkRecommendationCache()
        calls = 0

        def factory(path: Path, width: float, product: str) -> ArtworkRecommendation:
            nonlocal calls
            calls += 1
            return ArtworkRecommendation(
                recommended_preset="default",
                recommended_thickness_mm=2.5,
                confidence="low",
                reasons=(f"{path.name}:{width}:{product}", "cached"),
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = _write_synthetic_fixture(Path(temp_dir), "line_art")
            first = cache.get(image_path, output_width_mm=120.0, product_mode="wall_art", factory=factory)
            second = cache.get(image_path, output_width_mm=120.0, product_mode="wall_art", factory=factory)
            third = cache.get(image_path, output_width_mm=121.0, product_mode="wall_art", factory=factory)

        self.assertIs(first, second)
        self.assertIsNot(first, third)
        self.assertEqual(calls, 2)

    def test_failure_fallback_is_quiet_and_generation_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "missing.png"
            recommendation = safe_recommend_artwork_settings(missing, product_mode="wall_art")
            self.assertFalse(recommendation.available)
            self.assertEqual(recommendation.recommended_preset, "default")
            self.assertGreaterEqual(recommendation.recommended_thickness_mm, MIN_RECOMMENDED_THICKNESS_MM)

    def test_cleanup_ignored_products_return_unavailable_without_external_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = _write_synthetic_fixture(Path(temp_dir), "clean_logo")
            recommendation = safe_recommend_artwork_settings(image_path, product_mode="filament_swap_relief")
        self.assertFalse(recommendation.available)
        self.assertIn("ignored", recommendation.unavailable_reason)


class ArtworkRecommendationOptionalImageTests(unittest.TestCase):
    def _assert_optional_reference(
        self,
        name: str,
        allowed_presets: set[str],
        allowed_thicknesses: set[float],
        score_above: tuple[str, str] | None = None,
    ) -> None:
        image_path, display_path = _optional_qa_image_path(name)
        if not image_path.exists():
            self.skipTest(f"Optional local QA image not available: {display_path}")
        recommendation = recommend_artwork_settings(image_path, product_mode="wall_art")
        _assert_recommendation_shape(self, recommendation)
        self.assertIn(recommendation.recommended_preset, allowed_presets)
        self.assertIn(recommendation.recommended_thickness_mm, allowed_thicknesses)
        if score_above is not None:
            stronger, unrelated = score_above
            self.assertGreater(recommendation.scores[stronger], recommendation.scores[unrelated])

    def test_optional_tanjiro_reference_image(self) -> None:
        self._assert_optional_reference("Tanjiro.jpg", {"detail_preserving", "line_art"}, {2.0, 2.5})

    def test_optional_deer_scene_reference_image(self) -> None:
        self._assert_optional_reference("Deer Scene.png", {"default", "detail_preserving", "preserve_floating_islands"}, {2.5})

    def test_optional_mopar_v3_reference_image(self) -> None:
        self._assert_optional_reference("mopar v3.png", {"splatter_logo"}, {2.5, 3.0}, ("splatter_logo", "clean_logo"))

    def test_optional_nike_drip_reference_image(self) -> None:
        self._assert_optional_reference("Nike Drip.jpg", {"drip_logo"}, {2.5, 3.0}, ("drip_logo", "clean_logo"))

    def test_optional_butterfly_flower_reference_image(self) -> None:
        self._assert_optional_reference("Butterfly Flower.png", {"line_art", "detail_preserving"}, {2.0, 2.5}, ("line_art", "splatter_logo"))


if __name__ == "__main__":
    unittest.main()
