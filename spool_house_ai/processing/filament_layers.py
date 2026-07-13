from __future__ import annotations

import math
from copy import deepcopy
from typing import Any, Mapping, Sequence


ALIGNMENT_MODES = {"snap_up", "snap_nearest", "strict"}


def layer_top_z(layer_number: int, first_layer_height: float, layer_height: float) -> float:
    _validate_layer_number(layer_number)
    _validate_layer_heights(first_layer_height, layer_height)
    if layer_number == 1:
        return float(first_layer_height)
    return float(first_layer_height + ((layer_number - 1) * layer_height))


def layer_start_z(layer_number: int, first_layer_height: float, layer_height: float) -> float:
    _validate_layer_number(layer_number)
    _validate_layer_heights(first_layer_height, layer_height)
    if layer_number == 1:
        return 0.0
    return layer_top_z(layer_number - 1, first_layer_height, layer_height)


def first_layer_starting_at_or_above_z(
    z: float,
    first_layer_height: float,
    layer_height: float,
    tolerance: float = 0.001,
) -> int:
    _validate_layer_settings(first_layer_height, layer_height, tolerance)
    selected_z = float(z)
    if selected_z <= float(tolerance):
        return 1
    boundary_index = _boundary_index_at_or_above(selected_z, first_layer_height, layer_height, tolerance)
    return boundary_index + 1


def snap_z_to_layer_start(
    z: float,
    first_layer_height: float,
    layer_height: float,
    mode: str = "snap_up",
    tolerance: float = 0.001,
    *,
    minimum_boundary_index: int = 0,
) -> dict[str, Any]:
    _validate_layer_settings(first_layer_height, layer_height, tolerance)
    alignment_mode = _normalize_alignment_mode(mode)
    requested_z = float(z)
    if minimum_boundary_index < 0:
        raise ValueError("minimum_boundary_index must be nonnegative.")

    if alignment_mode == "snap_up":
        selected_index = max(
            minimum_boundary_index,
            _boundary_index_at_or_above(requested_z, first_layer_height, layer_height, tolerance),
        )
    elif alignment_mode == "snap_nearest":
        selected_index = _nearest_noncollapsing_boundary_index(
            requested_z,
            first_layer_height,
            layer_height,
            tolerance,
            minimum_boundary_index,
        )
    else:
        nearest_index = _nearest_boundary_index(requested_z, first_layer_height, layer_height, tolerance)
        nearest_z = _boundary_z(nearest_index, first_layer_height, layer_height)
        if abs(nearest_z - requested_z) > tolerance:
            lower_index, upper_index = _surrounding_boundary_indices(requested_z, first_layer_height, layer_height)
            lower_z = _boundary_z(lower_index, first_layer_height, layer_height)
            upper_z = _boundary_z(upper_index, first_layer_height, layer_height)
            raise ValueError(
                "Filament Swap Relief height is not aligned to the layer schedule: "
                f"requested Z {requested_z:.4f} mm, nearest lower {lower_z:.4f} mm, "
                f"nearest upper {upper_z:.4f} mm."
            )
        if nearest_index < minimum_boundary_index:
            minimum_z = _boundary_z(minimum_boundary_index, first_layer_height, layer_height)
            raise ValueError(
                "Filament Swap Relief layer alignment would collapse a color band: "
                f"requested Z {requested_z:.4f} mm, minimum non-collapsing boundary {minimum_z:.4f} mm."
            )
        selected_index = nearest_index

    aligned_z = _boundary_z(selected_index, first_layer_height, layer_height)
    snapped = abs(aligned_z - requested_z) > tolerance
    direction = "none"
    if aligned_z > requested_z + tolerance:
        direction = "up"
    elif aligned_z < requested_z - tolerance:
        direction = "down"
    return {
        "requested_z_mm": _round_mm(requested_z),
        "aligned_z_mm": _round_mm(aligned_z),
        "boundary_index": int(selected_index),
        "snapped": bool(snapped),
        "direction": direction,
    }


def calculate_filament_swap_plan(
    colors: Sequence[Mapping[str, Any]],
    *,
    base_height_mm: float,
    layer_step_mm: float,
    first_layer_height_mm: float,
    layer_height_mm: float,
    height_alignment_mode: str = "snap_up",
    height_alignment_tolerance_mm: float = 0.001,
    source: Mapping[str, Any] | None = None,
    palette_order: str = "light_to_dark",
) -> dict[str, Any]:
    if not colors:
        raise ValueError("Filament Swap Relief requires at least one printable color.")
    _validate_height_settings(
        base_height_mm,
        layer_step_mm,
        first_layer_height_mm,
        layer_height_mm,
        height_alignment_tolerance_mm,
    )
    alignment_mode = _normalize_alignment_mode(height_alignment_mode)
    color_count = len(colors)
    requested_boundaries = _requested_cumulative_boundaries(color_count, base_height_mm, layer_step_mm)
    aligned_indices = [0]
    aligned_boundaries = [0.0]
    boundary_snap_records: list[dict[str, Any]] = [
        {
            "requested_z_mm": 0.0,
            "aligned_z_mm": 0.0,
            "boundary_index": 0,
            "snapped": False,
            "direction": "none",
            "warnings": [],
        }
    ]
    warnings: list[str] = []

    for boundary_number, requested_z in enumerate(requested_boundaries[1:], start=1):
        minimum_index = aligned_indices[-1] + 1
        snap = snap_z_to_layer_start(
            requested_z,
            first_layer_height_mm,
            layer_height_mm,
            alignment_mode,
            height_alignment_tolerance_mm,
            minimum_boundary_index=minimum_index,
        )
        snap_warnings: list[str] = []
        natural = snap_z_to_layer_start(
            requested_z,
            first_layer_height_mm,
            layer_height_mm,
            alignment_mode if alignment_mode != "strict" else "snap_nearest",
            height_alignment_tolerance_mm,
            minimum_boundary_index=0,
        )
        if int(natural["boundary_index"]) < minimum_index:
            minimum_z = _boundary_z(minimum_index, first_layer_height_mm, layer_height_mm)
            warning = (
                f"Boundary {boundary_number} was moved to {minimum_z:.4f} mm to preserve at least "
                "one printed layer for the color band."
            )
            snap_warnings.append(warning)
            warnings.append(warning)
        aligned_indices.append(int(snap["boundary_index"]))
        aligned_boundaries.append(float(snap["aligned_z_mm"]))
        snap["warnings"] = snap_warnings
        boundary_snap_records.append(snap)

    plan_colors: list[dict[str, Any]] = []
    for zero_index, color in enumerate(colors):
        order = zero_index + 1
        start_boundary_index = aligned_indices[zero_index]
        top_boundary_index = aligned_indices[zero_index + 1]
        requested_start = requested_boundaries[zero_index]
        requested_top = requested_boundaries[zero_index + 1]
        aligned_start = aligned_boundaries[zero_index]
        aligned_top = aligned_boundaries[zero_index + 1]
        layer_count = top_boundary_index - start_boundary_index
        if layer_count <= 0:
            raise ValueError(f"Filament Swap Relief color band {order} collapsed to zero layers.")
        first_layer = start_boundary_index + 1
        last_layer = top_boundary_index
        row_warnings: list[str] = []
        if layer_count == 1:
            row_warnings.append("Color band is one printed layer thick.")
        row_warnings.extend(boundary_snap_records[zero_index + 1].get("warnings") or [])
        requested_start_snapped = abs(aligned_start - requested_start) > height_alignment_tolerance_mm
        requested_top_snapped = abs(aligned_top - requested_top) > height_alignment_tolerance_mm
        source_color = deepcopy(dict(color))
        plan_colors.append(
            {
                **source_color,
                "palette_index": int(source_color.get("cluster_label", source_color.get("palette_index", zero_index))),
                "order": order,
                "index": order,
                "requested_start_z_mm": _round_mm(requested_start),
                "aligned_start_z_mm": _round_mm(aligned_start),
                "requested_top_z_mm": _round_mm(requested_top),
                "aligned_top_z_mm": _round_mm(aligned_top),
                "assigned_height_mm": _round_mm(aligned_top),
                "filament_change_at_mm": 0.0 if order == 1 else _round_mm(aligned_start),
                "first_layer_using_color": int(first_layer),
                "last_layer_using_color": int(last_layer),
                "layer_count": int(layer_count),
                "change_before_layer": None if order == 1 else int(first_layer),
                "previous_filament_last_layer": None if order == 1 else int(first_layer - 1),
                "snapped_start": bool(requested_start_snapped),
                "snapped_top": bool(requested_top_snapped),
                "warnings": row_warnings,
            }
        )

    snapping_occurred = any(record["snapped"] for record in boundary_snap_records)
    final_top_layer = aligned_indices[-1]
    return {
        "schema_version": 1,
        "layer_numbering": "one_based",
        "swap_convention": (
            "Change before layer N means finish layer N-1, pause the printer, load the new filament, "
            "and begin layer N with the new filament."
        ),
        "source": dict(source or {}),
        "layer_settings": {
            "first_layer_height_mm": _round_mm(first_layer_height_mm),
            "layer_height_mm": _round_mm(layer_height_mm),
            "height_alignment_mode": alignment_mode,
            "height_alignment_tolerance_mm": _round_mm(height_alignment_tolerance_mm),
        },
        "height_settings": {
            "requested_base_height_mm": _round_mm(base_height_mm),
            "requested_step_height_mm": _round_mm(layer_step_mm),
            "aligned_first_transition_mm": _round_mm(aligned_boundaries[1]) if len(aligned_boundaries) > 1 else 0.0,
            "requested_cumulative_boundaries_mm": [_round_mm(value) for value in requested_boundaries],
            "aligned_cumulative_boundaries_mm": [_round_mm(value) for value in aligned_boundaries],
        },
        "palette_order": palette_order,
        "colors": plan_colors,
        "total_requested_thickness_mm": _round_mm(requested_boundaries[-1]),
        "total_aligned_thickness_mm": _round_mm(aligned_boundaries[-1]),
        "total_printed_layers": int(final_top_layer),
        "final_top_layer": int(final_top_layer),
        "snapping_occurred": bool(snapping_occurred),
        "warnings": warnings,
    }


def _requested_cumulative_boundaries(color_count: int, base_height_mm: float, layer_step_mm: float) -> list[float]:
    return [0.0] + [float(base_height_mm + ((index - 1) * layer_step_mm)) for index in range(1, color_count + 1)]


def _boundary_z(boundary_index: int, first_layer_height: float, layer_height: float) -> float:
    if boundary_index < 0:
        raise ValueError("boundary_index must be nonnegative.")
    if boundary_index == 0:
        return 0.0
    return layer_top_z(boundary_index, first_layer_height, layer_height)


def _boundary_index_at_or_above(z: float, first_layer_height: float, layer_height: float, tolerance: float) -> int:
    selected_z = float(z)
    if selected_z <= tolerance:
        return 0
    if selected_z <= first_layer_height + tolerance:
        return 1
    return 1 + max(0, math.ceil((selected_z - first_layer_height - tolerance) / layer_height))


def _surrounding_boundary_indices(z: float, first_layer_height: float, layer_height: float) -> tuple[int, int]:
    selected_z = float(z)
    if selected_z <= 0:
        return 0, 0
    upper = _boundary_index_at_or_above(selected_z, first_layer_height, layer_height, 0.0)
    lower = max(0, upper - 1)
    while _boundary_z(lower, first_layer_height, layer_height) > selected_z and lower > 0:
        upper = lower
        lower -= 1
    return lower, upper


def _nearest_boundary_index(z: float, first_layer_height: float, layer_height: float, tolerance: float) -> int:
    lower, upper = _surrounding_boundary_indices(z, first_layer_height, layer_height)
    lower_z = _boundary_z(lower, first_layer_height, layer_height)
    upper_z = _boundary_z(upper, first_layer_height, layer_height)
    if abs(lower_z - z) <= tolerance:
        return lower
    if abs(upper_z - z) <= tolerance:
        return upper
    lower_distance = abs(z - lower_z)
    upper_distance = abs(upper_z - z)
    if upper_distance <= lower_distance or abs(upper_distance - lower_distance) <= tolerance:
        return upper
    return lower


def _nearest_noncollapsing_boundary_index(
    z: float,
    first_layer_height: float,
    layer_height: float,
    tolerance: float,
    minimum_boundary_index: int,
) -> int:
    lower, upper = _surrounding_boundary_indices(z, first_layer_height, layer_height)
    candidates = {max(lower, minimum_boundary_index), max(upper, minimum_boundary_index), minimum_boundary_index}
    if lower >= minimum_boundary_index:
        candidates.add(lower)
    if upper >= minimum_boundary_index:
        candidates.add(upper)
    best_index = max(candidates)
    best_distance = abs(_boundary_z(best_index, first_layer_height, layer_height) - z)
    for index in sorted(candidates):
        distance = abs(_boundary_z(index, first_layer_height, layer_height) - z)
        if distance + tolerance < best_distance:
            best_index = index
            best_distance = distance
        elif abs(distance - best_distance) <= tolerance and _boundary_z(index, first_layer_height, layer_height) > _boundary_z(best_index, first_layer_height, layer_height):
            best_index = index
            best_distance = distance
    if abs(_boundary_z(best_index, first_layer_height, layer_height) - z) <= tolerance:
        return best_index
    return best_index


def _normalize_alignment_mode(value: str) -> str:
    selected = str(value).strip().lower().replace(" ", "_").replace("-", "_")
    if selected not in ALIGNMENT_MODES:
        raise ValueError(f"height_alignment_mode must be one of {sorted(ALIGNMENT_MODES)}; got {value!r}.")
    return selected


def _validate_layer_number(layer_number: int) -> None:
    if int(layer_number) < 1:
        raise ValueError(f"layer_number must be one-based and greater than zero; got {layer_number!r}.")


def _validate_layer_heights(first_layer_height: float, layer_height: float) -> None:
    if float(first_layer_height) <= 0:
        raise ValueError("first_layer_height_mm must be greater than zero.")
    if float(layer_height) <= 0:
        raise ValueError("layer_height_mm must be greater than zero.")


def _validate_layer_settings(first_layer_height: float, layer_height: float, tolerance: float) -> None:
    _validate_layer_heights(first_layer_height, layer_height)
    if float(tolerance) < 0:
        raise ValueError("height_alignment_tolerance_mm must be nonnegative.")
    if float(tolerance) >= min(float(first_layer_height), float(layer_height)) / 2.0:
        raise ValueError("height_alignment_tolerance_mm must be smaller than half of the layer heights.")


def _validate_height_settings(
    base_height_mm: float,
    layer_step_mm: float,
    first_layer_height_mm: float,
    layer_height_mm: float,
    tolerance: float,
) -> None:
    if float(base_height_mm) <= 0:
        raise ValueError("base_height_mm must be greater than zero.")
    if float(layer_step_mm) <= 0:
        raise ValueError("layer_step_mm must be greater than zero.")
    _validate_layer_settings(first_layer_height_mm, layer_height_mm, tolerance)


def _round_mm(value: float) -> float:
    return round(float(value), 4)
