from __future__ import annotations

import unittest

from spool_house_ai.processing.filament_layers import (
    calculate_filament_swap_plan,
    first_layer_starting_at_or_above_z,
    layer_start_z,
    layer_top_z,
    snap_z_to_layer_start,
)


class FilamentSwapLayerMathTests(unittest.TestCase):
    def test_layer_definitions_are_one_based(self) -> None:
        self.assertAlmostEqual(layer_start_z(1, 0.2, 0.2), 0.0)
        self.assertAlmostEqual(layer_top_z(1, 0.2, 0.2), 0.2)
        self.assertAlmostEqual(layer_start_z(2, 0.2, 0.2), 0.2)
        self.assertAlmostEqual(layer_top_z(3, 0.2, 0.2), 0.6)
        self.assertEqual(first_layer_starting_at_or_above_z(0.2, 0.2, 0.2), 2)
        self.assertEqual(first_layer_starting_at_or_above_z(0.4, 0.2, 0.2), 3)

    def test_equal_layer_heights_keep_existing_default_boundaries(self) -> None:
        plan = _plan(base=0.8, step=0.4, first=0.2, normal=0.2, mode="snap_up")

        self.assertFalse(plan["snapping_occurred"])
        self.assertEqual(plan["height_settings"]["aligned_cumulative_boundaries_mm"], [0.0, 0.8, 1.2, 1.6])
        self.assertEqual(plan["total_printed_layers"], 8)
        self.assertEqual([color["layer_count"] for color in plan["colors"]], [4, 2, 2])
        self.assertEqual(plan["colors"][0]["first_layer_using_color"], 1)
        self.assertEqual(plan["colors"][1]["change_before_layer"], 5)
        self.assertEqual(plan["colors"][1]["previous_filament_last_layer"], 4)
        self.assertEqual(plan["colors"][2]["change_before_layer"], 7)

    def test_different_first_layer_height_does_not_use_simple_division(self) -> None:
        plan = _plan(base=0.8, step=0.4, first=0.28, normal=0.16, mode="snap_up")

        self.assertTrue(plan["snapping_occurred"])
        self.assertEqual(plan["height_settings"]["aligned_cumulative_boundaries_mm"], [0.0, 0.92, 1.24, 1.72])
        self.assertEqual(plan["colors"][1]["change_before_layer"], 6)
        self.assertEqual(plan["colors"][1]["previous_filament_last_layer"], 5)
        self.assertEqual(plan["total_printed_layers"], 10)

    def test_snap_modes_and_strict_validation(self) -> None:
        self.assertEqual(snap_z_to_layer_start(0.29, 0.2, 0.2, "snap_up")["aligned_z_mm"], 0.4)
        nearest_down = snap_z_to_layer_start(0.29, 0.2, 0.2, "snap_nearest")
        self.assertEqual(nearest_down["aligned_z_mm"], 0.2)
        self.assertEqual(nearest_down["direction"], "down")
        nearest_up = snap_z_to_layer_start(0.31, 0.2, 0.2, "snap_nearest")
        self.assertEqual(nearest_up["aligned_z_mm"], 0.4)
        self.assertEqual(nearest_up["direction"], "up")
        upward_tie = snap_z_to_layer_start(0.3, 0.2, 0.2, "snap_nearest")
        self.assertEqual(upward_tie["aligned_z_mm"], 0.4)
        self.assertEqual(snap_z_to_layer_start(0.4, 0.2, 0.2, "strict")["aligned_z_mm"], 0.4)
        with self.assertRaises(ValueError):
            snap_z_to_layer_start(0.41, 0.2, 0.2, "strict")

    def test_band_collapse_is_prevented(self) -> None:
        plan = _plan(base=0.21, step=0.01, first=0.2, normal=0.2, mode="snap_nearest")

        boundaries = plan["height_settings"]["aligned_cumulative_boundaries_mm"]
        self.assertEqual(boundaries, [0.0, 0.2, 0.4, 0.6])
        self.assertEqual([color["layer_count"] for color in plan["colors"]], [1, 1, 1])
        self.assertTrue(plan["warnings"])

    def test_strict_rejects_collapsed_band(self) -> None:
        with self.assertRaises(ValueError):
            _plan(base=0.2, step=0.01, first=0.2, normal=0.2, mode="strict")


def _colors() -> list[dict[str, object]]:
    return [
        {"index": 1, "cluster_label": 0, "hex": "#FFFFFF", "suggested_color_name": "white"},
        {"index": 2, "cluster_label": 1, "hex": "#FF0000", "suggested_color_name": "red"},
        {"index": 3, "cluster_label": 2, "hex": "#000000", "suggested_color_name": "black"},
    ]


def _plan(*, base: float, step: float, first: float, normal: float, mode: str) -> dict:
    return calculate_filament_swap_plan(
        _colors(),
        base_height_mm=base,
        layer_step_mm=step,
        first_layer_height_mm=first,
        layer_height_mm=normal,
        height_alignment_mode=mode,
        height_alignment_tolerance_mm=0.001,
        palette_order="light_to_dark",
    )


if __name__ == "__main__":
    unittest.main()
