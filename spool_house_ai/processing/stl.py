from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np
import trimesh

from spool_house_ai.config import StlConfig
from spool_house_ai.processing.analysis import ImageAnalysis
from spool_house_ai.processing.geometry import VectorContour


@dataclass(frozen=True)
class MeshReport:
    stl_path: str
    exists: bool
    file_size_bytes: int
    vertex_count: int
    face_count: int
    bounding_box_mm: list[float]
    empty_mesh: bool
    invalid_bounds: bool
    warnings: list[str]
    failures: list[str]


def create_relief_stl(analysis: ImageAnalysis | np.ndarray, output_path: Path, config: StlConfig) -> str:
    """Create a simple raised relief STL from a binary silhouette mask."""
    if config.stl_backend == "vector_extrusion":
        try:
            _create_vector_extrusion_stl(analysis, output_path, config)
            return "vector_extrusion"
        except (ImportError, RuntimeError, ValueError):
            _create_raster_heightfield_stl(analysis, output_path, config)
            return "raster_heightfield"

    if config.stl_backend != "raster_heightfield":
        raise ValueError(f"Unsupported stl_backend: {config.stl_backend}")

    _create_raster_heightfield_stl(analysis, output_path, config)
    return "raster_heightfield"


def _create_raster_heightfield_stl(analysis: ImageAnalysis | np.ndarray, output_path: Path, config: StlConfig) -> None:
    mask = _mask_for_stl(analysis, config)
    mask = _prepare_product_mask(mask, config)
    resized_mask, detail_mask, color_masks = _resize_analysis_masks(analysis, mask, config)
    height, width = resized_mask.shape

    scale = config.output_scale_mm / width
    depth_mm = height * scale
    top_heights = _top_heights(resized_mask, config, detail_mask, color_masks)
    bottom_z = 0.0

    vertices: list[tuple[float, float, float]] = []
    top_indices = np.zeros((height + 1, width + 1), dtype=np.int64)
    bottom_indices = np.zeros((height + 1, width + 1), dtype=np.int64)

    for y in range(height + 1):
        for x in range(width + 1):
            px = x * scale
            py = depth_mm - (y * scale)
            top_z = _vertex_height(top_heights, y, x, config.base_height_mm)
            top_indices[y, x] = len(vertices)
            vertices.append((px, py, top_z))
            bottom_indices[y, x] = len(vertices)
            vertices.append((px, py, bottom_z))

    faces: list[tuple[int, int, int]] = []
    for y in range(height):
        for x in range(width):
            if not resized_mask[y, x]:
                continue

            top_a = top_indices[y, x]
            top_b = top_indices[y, x + 1]
            top_c = top_indices[y + 1, x + 1]
            top_d = top_indices[y + 1, x]
            bottom_a = bottom_indices[y, x]
            bottom_b = bottom_indices[y, x + 1]
            bottom_c = bottom_indices[y + 1, x + 1]
            bottom_d = bottom_indices[y + 1, x]

            faces.extend([(top_a, top_d, top_c), (top_a, top_c, top_b)])
            faces.extend([(bottom_a, bottom_b, bottom_c), (bottom_a, bottom_c, bottom_d)])

            if y == 0 or not resized_mask[y - 1, x]:
                faces.extend([(top_a, top_b, bottom_b), (top_a, bottom_b, bottom_a)])
            if y == height - 1 or not resized_mask[y + 1, x]:
                faces.extend([(top_d, bottom_d, bottom_c), (top_d, bottom_c, top_c)])
            if x == 0 or not resized_mask[y, x - 1]:
                faces.extend([(top_a, bottom_a, bottom_d), (top_a, bottom_d, top_d)])
            if x == width - 1 or not resized_mask[y, x + 1]:
                faces.extend([(top_b, top_c, bottom_c), (top_b, bottom_c, bottom_b)])

    if not faces:
        raise ValueError("Silhouette did not contain enough foreground pixels to create an STL.")

    mesh = trimesh.Trimesh(vertices=np.array(vertices), faces=np.array(faces), process=True)
    mesh.export(output_path)


def _create_vector_extrusion_stl(analysis: ImageAnalysis | np.ndarray, output_path: Path, config: StlConfig) -> None:
    if not isinstance(analysis, ImageAnalysis) or not analysis.vector_contours:
        raise ValueError("Vector extrusion requires analyzed vector contours.")
    if config.product_mode == "keychain" and config.add_keychain_hole:
        raise ValueError("Vector extrusion does not currently create keychain loops.")
    if config.detail_mode not in {"silhouette_only", "preserve_holes"}:
        raise ValueError("Vector extrusion currently supports silhouette and hole-preserving modes only.")

    try:
        from shapely.geometry import Polygon
        from shapely.ops import unary_union
    except ImportError as error:
        raise ImportError("Vector extrusion requires optional polygon dependencies.") from error

    height, width = analysis.final_mask.shape
    scale = config.output_scale_mm / width
    depth_mm = height * scale
    extrusion_height = config.base_height_mm + (
        config.extrusion_height_mm
        * {
            "flat_relief": 1.0,
            "keychain": 1.15,
            "wall_art": 1.6,
        }.get(config.product_mode, 1.0)
    )

    exterior_polygons: list[Polygon] = []
    hole_polygons: list[Polygon] = []
    for contour in analysis.vector_contours:
        ring = _contour_to_mm_ring(contour.points, scale, depth_mm)
        if len(ring) < 3:
            continue
        polygon = Polygon(ring)
        if not polygon.is_valid:
            polygon = polygon.buffer(0)
        if polygon.is_empty or polygon.area <= 0:
            continue
        if contour.is_hole:
            hole_polygons.append(polygon)
        else:
            exterior_polygons.append(polygon)

    polygons = []
    for exterior in exterior_polygons:
        holes = [
            list(hole.exterior.coords)
            for hole in hole_polygons
            if exterior.contains(hole.representative_point())
        ]
        polygon = Polygon(list(exterior.exterior.coords), holes)
        if not polygon.is_valid:
            polygon = polygon.buffer(0)
        if not polygon.is_empty and polygon.area > 0:
            polygons.append(polygon)

    if not polygons:
        raise ValueError("Vector extrusion did not find valid polygons.")

    merged = unary_union(polygons)
    merged_polygons = list(merged.geoms) if hasattr(merged, "geoms") else [merged]
    meshes = [
        trimesh.creation.extrude_polygon(polygon, height=extrusion_height)
        for polygon in merged_polygons
        if not polygon.is_empty and polygon.area > 0
    ]
    if not meshes:
        raise ValueError("Vector extrusion did not create any meshes.")

    mesh = trimesh.util.concatenate(meshes) if len(meshes) > 1 else meshes[0]
    mesh.export(output_path)


def validate_stl_mesh(stl_path: Path) -> MeshReport:
    warnings: list[str] = []
    failures: list[str] = []
    stl_path = stl_path.resolve()

    exists = stl_path.exists()
    file_size_bytes = stl_path.stat().st_size if exists else 0
    if not exists:
        failures.append("STL file was not created.")
        return MeshReport(
            stl_path=str(stl_path),
            exists=False,
            file_size_bytes=0,
            vertex_count=0,
            face_count=0,
            bounding_box_mm=[],
            empty_mesh=True,
            invalid_bounds=True,
            warnings=warnings,
            failures=failures,
        )
    if file_size_bytes == 0:
        failures.append("STL file is empty.")

    vertex_count = 0
    face_count = 0
    bounding_box_mm: list[float] = []
    empty_mesh = True
    invalid_bounds = True

    try:
        mesh = trimesh.load_mesh(stl_path, force="mesh")
        vertex_count = int(len(mesh.vertices))
        face_count = int(len(mesh.faces))
        empty_mesh = bool(mesh.is_empty or vertex_count == 0 or face_count == 0)
        if empty_mesh:
            failures.append("Mesh has no vertices or faces.")

        bounds = np.asarray(mesh.bounds, dtype=float)
        if bounds.shape == (2, 3) and np.all(np.isfinite(bounds)):
            dimensions = bounds[1] - bounds[0]
            if np.all(np.isfinite(dimensions)) and np.all(dimensions > 0):
                invalid_bounds = False
                bounding_box_mm = [round(float(value), 4) for value in dimensions]
            else:
                failures.append("Mesh bounds have non-positive dimensions.")
        else:
            failures.append("Mesh bounds are missing or invalid.")

        if not bool(getattr(mesh, "is_watertight", False)):
            warnings.append("Mesh is not watertight.")
    except Exception as error:
        failures.append(f"Could not load STL for validation: {error}")

    return MeshReport(
        stl_path=str(stl_path),
        exists=exists,
        file_size_bytes=file_size_bytes,
        vertex_count=vertex_count,
        face_count=face_count,
        bounding_box_mm=bounding_box_mm,
        empty_mesh=empty_mesh,
        invalid_bounds=invalid_bounds,
        warnings=warnings,
        failures=failures,
    )


def write_mesh_report(report: MeshReport, output_path: Path) -> None:
    output_path.write_text(json.dumps(asdict(report), indent=2) + "\n", encoding="utf-8")


def _prepare_product_mask(mask: np.ndarray, config: StlConfig) -> np.ndarray:
    product_mask = mask.copy()

    if config.product_mode == "keychain" and config.add_keychain_hole:
        product_mask = _add_keychain_loop(product_mask, config)

    if config.product_mode not in {"flat_relief", "keychain", "wall_art"}:
        raise ValueError(f"Unsupported product_mode: {config.product_mode}")

    return product_mask


def _add_keychain_loop(mask: np.ndarray, config: StlConfig) -> np.ndarray:
    height, width = mask.shape
    scale = config.output_scale_mm / width
    outer_radius_px = max(3, int((config.keychain_loop_outer_diameter_mm / 2) / scale))
    inner_radius_px = max(2, int((config.keychain_hole_diameter_mm / 2) / scale))
    padding = max(2, outer_radius_px // 4)

    expanded = np.zeros((height + outer_radius_px + padding, width), dtype=np.uint8)
    expanded[outer_radius_px + padding :, :] = mask.astype(np.uint8)

    component_points = np.argwhere(mask)
    if len(component_points) == 0:
        return mask

    min_y, min_x = component_points.min(axis=0)
    max_y, max_x = component_points.max(axis=0)
    center_x = int((min_x + max_x) / 2)
    center_y = outer_radius_px + padding

    cv2.circle(expanded, (center_x, center_y), outer_radius_px, 1, thickness=-1)
    cv2.circle(expanded, (center_x, center_y), inner_radius_px, 0, thickness=-1)
    cv2.rectangle(
        expanded,
        (max(0, center_x - outer_radius_px // 2), center_y),
        (min(width - 1, center_x + outer_radius_px // 2), outer_radius_px + padding + int(min_y) + 2),
        1,
        thickness=-1,
    )
    cv2.circle(expanded, (center_x, center_y), inner_radius_px, 0, thickness=-1)
    return expanded > 0


def _mask_for_stl(analysis: ImageAnalysis | np.ndarray, config: StlConfig) -> np.ndarray:
    if not isinstance(analysis, ImageAnalysis):
        return analysis
    if analysis.vector_contours:
        return _vector_mask_for_stl(analysis.vector_contours, analysis.final_mask.shape, config.curve_sample_resolution)
    if config.detail_mode == "silhouette_only":
        return analysis.final_mask
    if config.detail_mode == "raised_details":
        return analysis.body_mask | analysis.detail_mask
    if config.detail_mode == "engraved_details":
        return analysis.body_mask
    if config.detail_mode == "layered_color_relief":
        return analysis.body_mask | analysis.detail_mask
    return analysis.body_mask


def _vector_mask_for_stl(
    contours: list[VectorContour],
    shape: tuple[int, int],
    sample_resolution: int,
) -> np.ndarray:
    scale = max(1, int(sample_resolution))
    height, width = shape
    canvas = np.zeros((height * scale, width * scale), dtype=np.uint8)
    for contour in contours:
        points = np.round(contour.points * scale).astype(np.int32).reshape(-1, 1, 2)
        cv2.drawContours(canvas, [points], -1, 0 if contour.is_hole else 255, thickness=-1)
    return canvas > 127


def _contour_to_mm_ring(points: np.ndarray, scale: float, depth_mm: float) -> list[tuple[float, float]]:
    ring: list[tuple[float, float]] = []
    previous: tuple[float, float] | None = None
    for point in points:
        current = (float(point[0]) * scale, depth_mm - (float(point[1]) * scale))
        if current != previous:
            ring.append(current)
            previous = current
    if len(ring) > 1 and ring[0] == ring[-1]:
        ring.pop()
    return ring


def _resize_analysis_masks(
    analysis: ImageAnalysis | np.ndarray,
    mask: np.ndarray,
    config: StlConfig,
) -> tuple[np.ndarray, np.ndarray | None, list[np.ndarray]]:
    original_pixels = mask.shape[0] * mask.shape[1]
    resized_mask = _resize_for_mesh(mask, config.max_mesh_pixels)
    if not isinstance(analysis, ImageAnalysis):
        return resized_mask, None, []

    size = (resized_mask.shape[1], resized_mask.shape[0])
    detail_mask = cv2.resize(
        analysis.detail_mask.astype(np.uint8),
        size,
        interpolation=cv2.INTER_AREA,
    ) > 0
    color_masks = [
        cv2.resize(color_mask.astype(np.uint8), size, interpolation=cv2.INTER_AREA) > 0
        for color_mask in analysis.color_region_masks
    ]
    return resized_mask, detail_mask, color_masks


def _top_heights(
    mask: np.ndarray,
    config: StlConfig,
    detail_mask: np.ndarray | None = None,
    color_masks: list[np.ndarray] | None = None,
) -> np.ndarray:
    mode_height_multiplier = {
        "flat_relief": 1.0,
        "keychain": 1.15,
        "wall_art": 1.6,
    }.get(config.product_mode, 1.0)
    relief_height = config.extrusion_height_mm * mode_height_multiplier
    heights = np.where(mask, config.base_height_mm + relief_height, config.base_height_mm)

    if detail_mask is not None and detail_mask.shape == mask.shape and np.any(detail_mask):
        detail_pixels = detail_mask & mask
        if config.detail_mode == "raised_details":
            heights = np.where(detail_pixels, heights + config.detail_height_mm, heights)
        elif config.detail_mode == "engraved_details":
            engraved = max(config.base_height_mm, config.base_height_mm + relief_height - config.engraving_depth_mm)
            heights = np.where(detail_pixels, engraved, heights)
        elif config.detail_mode == "layered_color_relief":
            heights = np.where(detail_pixels, heights + config.detail_height_mm, heights)

    if config.detail_mode == "layered_color_relief" and color_masks:
        for index, color_mask in enumerate(color_masks[:3], start=1):
            if color_mask.shape == mask.shape:
                heights = np.where(color_mask & mask, heights + index * (config.detail_height_mm / 2.0), heights)

    if config.bevel_enabled and config.bevel_pixels > 0:
        distance = cv2.distanceTransform(mask.astype(np.uint8), cv2.DIST_L2, 3)
        bevel = np.clip(distance / float(config.bevel_pixels), 0.0, 1.0)
        heights = np.where(mask, config.base_height_mm + relief_height * bevel, config.base_height_mm)

    return heights


def _vertex_height(top_heights: np.ndarray, y: int, x: int, base_height: float) -> float:
    samples: list[float] = []
    for sample_y in (y - 1, y):
        for sample_x in (x - 1, x):
            if 0 <= sample_y < top_heights.shape[0] and 0 <= sample_x < top_heights.shape[1]:
                samples.append(float(top_heights[sample_y, sample_x]))
    if not samples:
        return base_height
    return max(samples)


def _resize_for_mesh(mask: np.ndarray, max_pixels: int) -> np.ndarray:
    height, width = mask.shape
    current_pixels = height * width
    if current_pixels <= max_pixels:
        return mask

    scale = (max_pixels / current_pixels) ** 0.5
    new_width = max(1, int(width * scale))
    new_height = max(1, int(height * scale))
    resized = cv2.resize(
        mask.astype(np.uint8),
        (new_width, new_height),
        interpolation=cv2.INTER_AREA,
    )
    return resized > 0
