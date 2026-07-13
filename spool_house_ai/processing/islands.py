from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np


SUPPORTED_ISLAND_POLICIES = {
    "preserve_all",
    "remove_below_threshold",
    "merge_with_nearest_region",
    "connect_within_maximum_gap",
}
SUPPORTED_ISLAND_FALLBACKS = {"remove", "preserve"}


@dataclass(frozen=True)
class IslandPolicyResult:
    labels: np.ndarray
    summary: dict[str, Any]
    component_actions: list[dict[str, Any]]
    per_color_component_counts: list[dict[str, Any]]
    action_masks: dict[str, np.ndarray]
    warnings: list[str]


@dataclass(frozen=True)
class _Component:
    component_id: int
    label: int
    area: int
    mask: np.ndarray
    bbox: tuple[int, int, int, int]
    centroid: tuple[float, float]


def apply_island_policy(
    labels: np.ndarray,
    printable_labels: list[int],
    *,
    min_region_area_px: int,
    island_policy: str,
    merge_max_distance_px: int,
    merge_fallback: str,
    connect_max_gap_px: int,
    connection_width_px: int,
    connect_fallback: str,
) -> IslandPolicyResult:
    if island_policy not in SUPPORTED_ISLAND_POLICIES:
        raise ValueError(f"Unsupported island policy: {island_policy}")
    if merge_fallback not in SUPPORTED_ISLAND_FALLBACKS:
        raise ValueError(f"Unsupported island merge fallback: {merge_fallback}")
    if connect_fallback not in SUPPORTED_ISLAND_FALLBACKS:
        raise ValueError(f"Unsupported island connect fallback: {connect_fallback}")

    printable_set = set(printable_labels)
    palette_rank = {label: index for index, label in enumerate(printable_labels)}
    working = np.where(np.isin(labels, printable_labels), labels, -1).astype(np.int32)
    original = working.copy()
    components = _components_for_labels(working, printable_labels)
    kept_masks = _kept_masks_by_label(components, printable_labels, min_region_area_px)

    action_masks = {
        "detected": working >= 0,
        "preserved": np.zeros(working.shape, dtype=bool),
        "removed": np.zeros(working.shape, dtype=bool),
        "merged": np.zeros(working.shape, dtype=bool),
        "connected": np.zeros(working.shape, dtype=bool),
        "connectors": np.zeros(working.shape, dtype=bool),
    }
    records: list[dict[str, Any]] = []
    counts = {
        "total_detected_components": len(components),
        "normal_kept_components": 0,
        "intentionally_preserved_components": 0,
        "removed_components": 0,
        "merged_components": 0,
        "connected_components": 0,
        "pixels_removed": 0,
        "pixels_recolored": 0,
        "connector_pixels_added": 0,
    }
    warnings: list[str] = []

    if island_policy == "preserve_all":
        for component in components:
            action_masks["preserved"] |= component.mask
            counts["intentionally_preserved_components"] += 1
            records.append(_component_record(component, "preserved", "preserve_all policy"))
        summary = _summary(
            island_policy,
            min_region_area_px,
            merge_max_distance_px,
            merge_fallback,
            connect_max_gap_px,
            connection_width_px,
            connect_fallback,
            counts,
        )
        summary["hole_filling_occurred"] = False
        summary["pre_island_processing_losses"] = {
            "non_printable_pixel_count": int(np.count_nonzero((labels >= 0) & ~np.isin(labels, printable_labels))),
            "input_components_seen_at_island_stage": len(components),
        }
        if any(component.area < min_region_area_px for component in components):
            warnings.append("Preserved components smaller than the printable feature recommendation.")
        return IslandPolicyResult(
            labels=working,
            summary=summary,
            component_actions=records,
            per_color_component_counts=_per_color_counts(components, printable_labels, min_region_area_px),
            action_masks=action_masks,
            warnings=warnings,
        )

    for component in components:
        if component.area >= min_region_area_px:
            counts["normal_kept_components"] += 1
            records.append(_component_record(component, "kept", "area at or above threshold"))
            continue

        if island_policy == "remove_below_threshold":
            _remove_component(working, component, action_masks, counts)
            records.append(_component_record(component, "removed", "area below threshold"))
            continue

        if island_policy == "merge_with_nearest_region":
            destination = _merge_destination(
                component,
                kept_masks,
                palette_rank,
                merge_max_distance_px,
            )
            if destination is None:
                _apply_fallback(working, component, action_masks, counts, records, merge_fallback, "no merge target within distance")
                continue
            destination_label, distance, boundary_contact = destination
            before = working[component.mask].copy()
            working[component.mask] = destination_label
            recolored = int(np.count_nonzero(before != destination_label))
            action_masks["merged"] |= component.mask
            counts["merged_components"] += 1
            counts["pixels_recolored"] += recolored
            records.append(
                _component_record(
                    component,
                    "merged",
                    "nearest printable region selected",
                    destination_label=destination_label,
                    distance_px=distance,
                    boundary_contact_px=boundary_contact,
                    pixels_recolored=recolored,
                )
            )
            continue

        if island_policy == "connect_within_maximum_gap":
            connection = _connection_for_component(component, kept_masks.get(component.label), connect_max_gap_px)
            if connection is None:
                _apply_fallback(working, component, action_masks, counts, records, connect_fallback, "no same-color kept component within gap")
                continue
            source_point, destination_point, distance = connection
            line_mask = np.zeros(working.shape, dtype=np.uint8)
            cv2.line(
                line_mask,
                (int(source_point[1]), int(source_point[0])),
                (int(destination_point[1]), int(destination_point[0])),
                1,
                thickness=max(1, int(connection_width_px)),
            )
            connector_mask = line_mask > 0
            blocked = connector_mask & (working >= 0) & (working != component.label)
            if np.any(blocked):
                _apply_fallback(working, component, action_masks, counts, records, connect_fallback, "connector would cross another color")
                continue
            new_connector_pixels = connector_mask & (working < 0)
            working[connector_mask] = component.label
            working[component.mask] = component.label
            action_masks["connected"] |= component.mask | connector_mask
            action_masks["connectors"] |= new_connector_pixels
            counts["connected_components"] += 1
            counts["connector_pixels_added"] += int(np.count_nonzero(new_connector_pixels))
            records.append(
                _component_record(
                    component,
                    "connected",
                    "same-color kept component within gap",
                    destination_label=component.label,
                    distance_px=distance,
                    connector_length_px=round(float(distance), 4),
                    connector_width_px=int(connection_width_px),
                    connector_pixels_added=int(np.count_nonzero(new_connector_pixels)),
                )
            )

    working[~np.isin(working, list(printable_set))] = -1
    summary = _summary(
        island_policy,
        min_region_area_px,
        merge_max_distance_px,
        merge_fallback,
        connect_max_gap_px,
        connection_width_px,
        connect_fallback,
        counts,
    )
    summary["hole_filling_occurred"] = False
    summary["pre_island_processing_losses"] = {
        "non_printable_pixel_count": int(np.count_nonzero((labels >= 0) & ~np.isin(labels, printable_labels))),
        "input_components_seen_at_island_stage": len(components),
    }
    if island_policy == "preserve_all" and any(component.area < min_region_area_px for component in components):
        warnings.append("Preserved components smaller than the printable feature recommendation.")
    return IslandPolicyResult(
        labels=working,
        summary=summary,
        component_actions=records,
        per_color_component_counts=_per_color_counts(components, printable_labels, min_region_area_px),
        action_masks=action_masks,
        warnings=warnings,
    )


def _components_for_labels(labels: np.ndarray, printable_labels: list[int]) -> list[_Component]:
    components: list[_Component] = []
    component_id = 0
    for label in printable_labels:
        count, component_labels, stats, centroids = cv2.connectedComponentsWithStats((labels == label).astype(np.uint8), 8)
        for local_id in range(1, count):
            component_id += 1
            x = int(stats[local_id, cv2.CC_STAT_LEFT])
            y = int(stats[local_id, cv2.CC_STAT_TOP])
            width = int(stats[local_id, cv2.CC_STAT_WIDTH])
            height = int(stats[local_id, cv2.CC_STAT_HEIGHT])
            components.append(
                _Component(
                    component_id=component_id,
                    label=int(label),
                    area=int(stats[local_id, cv2.CC_STAT_AREA]),
                    mask=component_labels == local_id,
                    bbox=(x, y, width, height),
                    centroid=(float(centroids[local_id][0]), float(centroids[local_id][1])),
                )
            )
    return components


def _kept_masks_by_label(components: list[_Component], printable_labels: list[int], min_area: int) -> dict[int, np.ndarray]:
    if not components:
        return {}
    shape = components[0].mask.shape
    masks = {label: np.zeros(shape, dtype=bool) for label in printable_labels}
    for component in components:
        if component.area >= min_area:
            masks[component.label] |= component.mask
    return masks


def _merge_destination(
    component: _Component,
    kept_masks: dict[int, np.ndarray],
    palette_rank: dict[int, int],
    max_distance: int,
) -> tuple[int, float, int] | None:
    kernel = np.ones((3, 3), np.uint8)
    dilated = cv2.dilate(component.mask.astype(np.uint8), kernel, iterations=1) > 0
    contact_candidates: list[tuple[int, int]] = []
    for label, mask in kept_masks.items():
        contact = int(np.count_nonzero(dilated & mask))
        if contact:
            contact_candidates.append((label, contact))
    if contact_candidates:
        label, contact = sorted(contact_candidates, key=lambda item: (-item[1], palette_rank.get(item[0], 9999), item[0]))[0]
        return label, 1.0, contact

    if max_distance <= 0:
        return None
    distance_candidates: list[tuple[float, int, int]] = []
    for label, mask in kept_masks.items():
        if not np.any(mask):
            continue
        distance = cv2.distanceTransform((~mask).astype(np.uint8), cv2.DIST_L2, 3)
        min_distance = float(np.min(distance[component.mask]))
        if min_distance <= max_distance:
            distance_candidates.append((min_distance, palette_rank.get(label, 9999), label))
    if not distance_candidates:
        return None
    distance, _rank, label = sorted(distance_candidates, key=lambda item: (item[0], item[1], item[2]))[0]
    return label, distance, 0


def _connection_for_component(
    component: _Component,
    kept_mask: np.ndarray | None,
    max_gap: int,
) -> tuple[np.ndarray, np.ndarray, float] | None:
    if kept_mask is None or not np.any(kept_mask) or max_gap <= 0:
        return None
    distance = cv2.distanceTransform((~kept_mask).astype(np.uint8), cv2.DIST_L2, 3)
    min_distance = float(np.min(distance[component.mask]))
    if min_distance > max_gap:
        return None
    source_points = np.argwhere(component.mask & np.isclose(distance, min_distance))
    if source_points.size == 0:
        source_points = np.argwhere(component.mask)
    source_point = source_points[np.lexsort((source_points[:, 1], source_points[:, 0]))][0]
    kept_points = np.argwhere(kept_mask)
    deltas = kept_points - source_point
    squared = np.sum(deltas * deltas, axis=1)
    min_squared = int(np.min(squared))
    nearest = kept_points[squared == min_squared]
    destination_point = nearest[np.lexsort((nearest[:, 1], nearest[:, 0]))][0]
    return source_point, destination_point, float(min_squared**0.5)


def _remove_component(
    labels: np.ndarray,
    component: _Component,
    action_masks: dict[str, np.ndarray],
    counts: dict[str, int],
) -> None:
    labels[component.mask] = -1
    action_masks["removed"] |= component.mask
    counts["removed_components"] += 1
    counts["pixels_removed"] += component.area


def _apply_fallback(
    labels: np.ndarray,
    component: _Component,
    action_masks: dict[str, np.ndarray],
    counts: dict[str, int],
    records: list[dict[str, Any]],
    fallback: str,
    reason: str,
) -> None:
    if fallback == "preserve":
        action_masks["preserved"] |= component.mask
        counts["intentionally_preserved_components"] += 1
        records.append(_component_record(component, "preserved", f"fallback preserve: {reason}"))
        return
    _remove_component(labels, component, action_masks, counts)
    records.append(_component_record(component, "removed", f"fallback remove: {reason}"))


def _component_record(
    component: _Component,
    action: str,
    reason: str,
    **extra: Any,
) -> dict[str, Any]:
    record = {
        "component_id": component.component_id,
        "source_palette_index": component.label,
        "action": action,
        "component_area_px": component.area,
        "bbox": list(component.bbox),
        "centroid": [round(component.centroid[0], 3), round(component.centroid[1], 3)],
        "reason": reason,
    }
    record.update(extra)
    return record


def _summary(
    island_policy: str,
    min_region_area_px: int,
    merge_max_distance_px: int,
    merge_fallback: str,
    connect_max_gap_px: int,
    connection_width_px: int,
    connect_fallback: str,
    counts: dict[str, int],
) -> dict[str, Any]:
    return {
        "island_policy": island_policy,
        "min_region_area_px": int(min_region_area_px),
        "island_merge_max_distance_px": int(merge_max_distance_px),
        "island_merge_fallback": merge_fallback,
        "island_connect_max_gap_px": int(connect_max_gap_px),
        "island_connection_width_px": int(connection_width_px),
        "island_connect_fallback": connect_fallback,
        "total_detected_components": int(counts["total_detected_components"]),
        "normal_kept_components": int(counts["normal_kept_components"]),
        "intentionally_preserved_components": int(counts["intentionally_preserved_components"]),
        "removed_components": int(counts["removed_components"]),
        "merged_components": int(counts["merged_components"]),
        "connected_components": int(counts["connected_components"]),
        "pixels_removed": int(counts["pixels_removed"]),
        "pixels_recolored": int(counts["pixels_recolored"]),
        "connector_pixels_added": int(counts["connector_pixels_added"]),
        "hole_filling_occurred": False,
    }


def _per_color_counts(components: list[_Component], printable_labels: list[int], min_area: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for label in printable_labels:
        label_components = [component for component in components if component.label == label]
        areas = [component.area for component in label_components]
        rows.append(
            {
                "palette_index": int(label),
                "component_count": len(label_components),
                "small_component_count": sum(1 for area in areas if area < min_area),
                "smallest_component_area_px": min(areas) if areas else 0,
                "largest_component_area_px": max(areas) if areas else 0,
            }
        )
    return rows
