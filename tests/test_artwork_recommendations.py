from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

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


class ArtworkRecommendationTests(unittest.TestCase):
    def test_reference_images_land_in_expected_preset_families(self) -> None:
        expectations = {
            "Tanjiro.jpg": {"detail_preserving", "line_art"},
            "Deer Scene.png": {"default", "detail_preserving", "preserve_floating_islands"},
            "mopar v3.png": {"splatter_logo"},
            "Nike Drip.jpg": {"drip_logo"},
            "Butterfly Flower.png": {"line_art", "detail_preserving"},
        }
        for name, allowed in expectations.items():
            with self.subTest(name=name):
                recommendation = recommend_artwork_settings(QA_IMAGES[name], product_mode="wall_art")
                self.assertIn(recommendation.recommended_preset, allowed)
                self.assertGreaterEqual(recommendation.recommended_thickness_mm, MIN_RECOMMENDED_THICKNESS_MM)
                self.assertIn(recommendation.confidence, {"low", "medium", "high"})
                self.assertGreaterEqual(len(recommendation.reasons), 2)

    def test_reference_thicknesses_are_practical_wall_art_values(self) -> None:
        allowed = {
            "Tanjiro.jpg": {2.0, 2.5},
            "Deer Scene.png": {2.5},
            "mopar v3.png": {2.5, 3.0},
            "Nike Drip.jpg": {2.5, 3.0},
            "Butterfly Flower.png": {2.0, 2.5},
        }
        for name, values in allowed.items():
            with self.subTest(name=name):
                recommendation = recommend_artwork_settings(QA_IMAGES[name], product_mode="wall_art")
                self.assertIn(recommendation.recommended_thickness_mm, values)

    def test_score_ordering_uses_visible_structure_not_filename(self) -> None:
        mopar = recommend_artwork_settings(QA_IMAGES["mopar v3.png"], product_mode="wall_art")
        nike = recommend_artwork_settings(QA_IMAGES["Nike Drip.jpg"], product_mode="wall_art")
        butterfly = recommend_artwork_settings(QA_IMAGES["Butterfly Flower.png"], product_mode="wall_art")

        self.assertGreater(mopar.scores["splatter_logo"], mopar.scores["clean_logo"])
        self.assertGreater(nike.scores["drip_logo"], nike.scores["clean_logo"])
        self.assertGreater(butterfly.scores["line_art"], butterfly.scores["splatter_logo"])

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

        first = cache.get(QA_IMAGES["Tanjiro.jpg"], output_width_mm=120.0, product_mode="wall_art", factory=factory)
        second = cache.get(QA_IMAGES["Tanjiro.jpg"], output_width_mm=120.0, product_mode="wall_art", factory=factory)
        third = cache.get(QA_IMAGES["Tanjiro.jpg"], output_width_mm=121.0, product_mode="wall_art", factory=factory)

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

    def test_cleanup_ignored_products_return_unavailable(self) -> None:
        recommendation = safe_recommend_artwork_settings(QA_IMAGES["Tanjiro.jpg"], product_mode="filament_swap_relief")
        self.assertFalse(recommendation.available)
        self.assertIn("ignored", recommendation.unavailable_reason)


if __name__ == "__main__":
    unittest.main()
