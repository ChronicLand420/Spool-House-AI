from __future__ import annotations

import math
import os
import zipfile
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

import numpy as np
import trimesh


CONTENT_TYPES_XML = "[Content_Types].xml"
ROOT_RELS_XML = "_rels/.rels"
MODEL_XML = "3D/3dmodel.model"
CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
MODEL_REL_TYPE = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"
MODEL_CONTENT_TYPE = "application/vnd.ms-package.3dmanufacturing-3dmodel+xml"
RELS_CONTENT_TYPE = "application/vnd.openxmlformats-package.relationships+xml"
GENERIC_3MF_EXPORTER_VERSION = 1
GENERIC_3MF_NOTICE = (
    "This is a generic 3MF model. Manual filament-change instructions are stored separately "
    "and are not embedded as slicer markers."
)
ARCHIVE_ENTRY_ORDER = (CONTENT_TYPES_XML, ROOT_RELS_XML, MODEL_XML)
FIXED_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


@dataclass(frozen=True)
class Generic3mfValidationResult:
    passed: bool
    errors: list[str]
    units: str
    bounds: list[float]
    source_mesh_bounds: list[float]
    bounds_match: bool
    archive_entries: list[str]


def export_generic_3mf(
    mesh: trimesh.Trimesh,
    output_path: Path,
    *,
    title: str,
    application: str,
    description: str,
    bounds_tolerance: float = 0.001,
) -> dict[str, Any]:
    vertices, faces = _validated_mesh_arrays(mesh)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)
    temp_path = output_path.with_name(f"{output_path.name}.tmp")
    temp_path.unlink(missing_ok=True)

    try:
        _write_package(
            temp_path,
            vertices=vertices,
            faces=faces,
            title=title,
            application=application,
            description=description,
        )
        validation = validate_generic_3mf(temp_path, mesh, bounds_tolerance=bounds_tolerance)
        if not validation.passed:
            raise ValueError("; ".join(validation.errors))
        temp_path.replace(output_path)
        return {
            "generic_3mf_enabled": True,
            "generic_3mf_created": True,
            "generic_3mf_path": str(output_path),
            "generic_3mf_validation_passed": True,
            "generic_3mf_validation_errors": [],
            "generic_3mf_units": validation.units,
            "generic_3mf_bounds": validation.bounds,
            "source_mesh_bounds": validation.source_mesh_bounds,
            "bounds_match": validation.bounds_match,
            "archive_entries": validation.archive_entries,
            "exporter_version": GENERIC_3MF_EXPORTER_VERSION,
            "generic_export_notice": GENERIC_3MF_NOTICE,
        }
    except Exception:
        temp_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)
        raise


def validate_generic_3mf(
    path: Path,
    source_mesh: trimesh.Trimesh | None = None,
    *,
    bounds_tolerance: float = 0.001,
) -> Generic3mfValidationResult:
    errors: list[str] = []
    units = ""
    bounds: list[float] = []
    source_bounds: list[float] = []
    bounds_match = False
    entries: list[str] = []

    if not path.exists():
        return Generic3mfValidationResult(False, [f"3MF file does not exist: {path}"], "", [], [], False, [])

    try:
        with zipfile.ZipFile(path, "r") as package:
            entries = package.namelist()
            if len(entries) != len(set(entries)):
                errors.append("3MF archive contains duplicate entries.")
            if entries != list(ARCHIVE_ENTRY_ORDER):
                errors.append(f"3MF archive entry order must be {list(ARCHIVE_ENTRY_ORDER)}; got {entries}.")
            missing = [entry for entry in ARCHIVE_ENTRY_ORDER if entry not in entries]
            if missing:
                errors.append(f"3MF archive is missing required entries: {missing}.")
            errors.extend(_validate_archive_safety(entries, package))

            content_root = _parse_xml(package, CONTENT_TYPES_XML, errors)
            rels_root = _parse_xml(package, ROOT_RELS_XML, errors)
            model_root = _parse_xml(package, MODEL_XML, errors)
            if content_root is not None:
                _validate_content_types(content_root, errors)
            if rels_root is not None:
                _validate_relationships(rels_root, errors)
            vertices: np.ndarray | None = None
            faces: np.ndarray | None = None
            if model_root is not None:
                units, vertices, faces = _validate_model_xml(model_root, errors)
                if vertices is not None and vertices.size:
                    bounds = _mesh_dimensions_from_vertices(vertices)
            if source_mesh is not None:
                source_vertices, _source_faces = _validated_mesh_arrays(source_mesh)
                source_bounds = _mesh_dimensions_from_vertices(source_vertices)
                if vertices is not None and vertices.size:
                    bounds_match = _bounds_match(vertices, source_vertices, bounds_tolerance)
                    if not bounds_match:
                        errors.append(
                            f"3MF mesh bounds {bounds} do not match source mesh bounds {source_bounds} "
                            f"within tolerance {bounds_tolerance}."
                        )
    except zipfile.BadZipFile as error:
        errors.append(f"3MF archive is not a valid ZIP file: {error}")
    except Exception as error:
        errors.append(f"Could not validate 3MF archive: {error}")

    return Generic3mfValidationResult(
        passed=not errors,
        errors=errors,
        units=units,
        bounds=bounds,
        source_mesh_bounds=source_bounds,
        bounds_match=bounds_match if source_mesh is not None else False,
        archive_entries=entries,
    )


def validation_result_to_dict(result: Generic3mfValidationResult) -> dict[str, Any]:
    return asdict(result)


def _write_package(
    output_path: Path,
    *,
    vertices: np.ndarray,
    faces: np.ndarray,
    title: str,
    application: str,
    description: str,
) -> None:
    files = {
        CONTENT_TYPES_XML: _content_types_xml(),
        ROOT_RELS_XML: _root_rels_xml(),
        MODEL_XML: _model_xml(vertices, faces, title=title, application=application, description=description),
    }
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as package:
        for name in ARCHIVE_ENTRY_ORDER:
            info = zipfile.ZipInfo(name, date_time=FIXED_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            package.writestr(info, files[name].encode("utf-8"))


def _content_types_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<Types xmlns="{TYPES_NS}">\n'
        f'  <Default Extension="rels" ContentType="{RELS_CONTENT_TYPE}"/>\n'
        f'  <Override PartName="/{MODEL_XML}" ContentType="{MODEL_CONTENT_TYPE}"/>\n'
        "</Types>\n"
    )


def _root_rels_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<Relationships xmlns="{RELS_NS}">\n'
        f'  <Relationship Id="rel0" Type="{MODEL_REL_TYPE}" Target="/{MODEL_XML}"/>\n'
        "</Relationships>\n"
    )


def _model_xml(
    vertices: np.ndarray,
    faces: np.ndarray,
    *,
    title: str,
    application: str,
    description: str,
) -> str:
    vertex_lines = "\n".join(
        f'          <vertex x="{_format_number(vertex[0])}" y="{_format_number(vertex[1])}" z="{_format_number(vertex[2])}"/>'
        for vertex in vertices
    )
    triangle_lines = "\n".join(
        f'          <triangle v1="{int(face[0])}" v2="{int(face[1])}" v3="{int(face[2])}"/>'
        for face in faces
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<model unit="millimeter" xml:lang="en-US" xmlns="{CORE_NS}">\n'
        f'  <metadata name="Title">{escape(title)}</metadata>\n'
        f'  <metadata name="Application">{escape(application)}</metadata>\n'
        f'  <metadata name="Description">{escape(description)}</metadata>\n'
        "  <resources>\n"
        '    <object id="1" type="model">\n'
        "      <mesh>\n"
        "        <vertices>\n"
        f"{vertex_lines}\n"
        "        </vertices>\n"
        "        <triangles>\n"
        f"{triangle_lines}\n"
        "        </triangles>\n"
        "      </mesh>\n"
        "    </object>\n"
        "  </resources>\n"
        "  <build>\n"
        '    <item objectid="1"/>\n'
        "  </build>\n"
        "</model>\n"
    )


def _validated_mesh_arrays(mesh: trimesh.Trimesh) -> tuple[np.ndarray, np.ndarray]:
    vertices = np.asarray(mesh.vertices, dtype=float)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    if vertices.ndim != 2 or vertices.shape[1] != 3 or len(vertices) == 0:
        raise ValueError("Generic 3MF export requires a mesh with vertices.")
    if faces.ndim != 2 or faces.shape[1] != 3 or len(faces) == 0:
        raise ValueError("Generic 3MF export requires a mesh with triangular faces.")
    if not np.all(np.isfinite(vertices)):
        raise ValueError("Generic 3MF export requires finite vertex coordinates.")
    if np.any(faces < 0):
        raise ValueError("Generic 3MF export found negative triangle indices.")
    if np.any(faces >= len(vertices)):
        raise ValueError("Generic 3MF export found triangle indices outside the vertex array.")
    repeated = (faces[:, 0] == faces[:, 1]) | (faces[:, 0] == faces[:, 2]) | (faces[:, 1] == faces[:, 2])
    if np.any(repeated):
        raise ValueError("Generic 3MF export found degenerate triangles with repeated vertex indices.")
    return vertices.copy(), faces.copy()


def _validate_archive_safety(entries: list[str], package: zipfile.ZipFile) -> list[str]:
    errors: list[str] = []
    allowed = set(ARCHIVE_ENTRY_ORDER)
    disallowed_tokens = ("orcaslicer", "bambustudio", "bambulab", "metadata/", "3d/objects/")
    for entry in entries:
        normalized = entry.replace("\\", "/")
        lower = normalized.lower()
        if normalized not in allowed:
            errors.append(f"3MF archive contains unsupported generic export entry: {entry}")
        if os.path.isabs(normalized) or ":" in normalized:
            errors.append(f"3MF archive entry looks like an absolute path: {entry}")
        if lower.endswith((".stl", ".gcode", ".gco")):
            errors.append(f"3MF archive must not embed STL or G-code entries: {entry}")
        if lower.endswith((".config", ".ini")) or "thumbnail" in lower or lower.endswith((".png", ".jpg", ".jpeg")):
            errors.append(f"3MF archive contains slicer project or thumbnail content: {entry}")
        if any(token in lower for token in disallowed_tokens):
            errors.append(f"3MF archive contains slicer/vendor-specific entry: {entry}")
        try:
            text = package.read(entry).decode("utf-8", errors="ignore").lower()
        except Exception:
            text = ""
        if any(token in text for token in ("orcaslicer", "bambustudio", "bambulab", "m600", "g-code", "gcode")):
            errors.append(f"3MF archive contains slicer/vendor/G-code text in {entry}.")
        if any(token in text for token in ("c:\\", "c:/", "\\users\\", "/users/", "appdata\\local\\temp", "appdata/local/temp")):
            errors.append(f"3MF archive contains local or temporary path text in {entry}.")
    return errors


def _parse_xml(package: zipfile.ZipFile, entry: str, errors: list[str]) -> ET.Element | None:
    if entry not in package.namelist():
        return None
    try:
        return ET.fromstring(package.read(entry))
    except ET.ParseError as error:
        errors.append(f"{entry} is not valid XML: {error}")
        return None


def _validate_content_types(root: ET.Element, errors: list[str]) -> None:
    if root.tag != f"{{{TYPES_NS}}}Types":
        errors.append("[Content_Types].xml root namespace is invalid.")
    defaults = {
        child.attrib.get("Extension"): child.attrib.get("ContentType")
        for child in root
        if child.tag == f"{{{TYPES_NS}}}Default"
    }
    overrides = {
        child.attrib.get("PartName"): child.attrib.get("ContentType")
        for child in root
        if child.tag == f"{{{TYPES_NS}}}Override"
    }
    if defaults.get("rels") != RELS_CONTENT_TYPE:
        errors.append("[Content_Types].xml is missing the .rels content type.")
    if overrides.get(f"/{MODEL_XML}") != MODEL_CONTENT_TYPE:
        errors.append("[Content_Types].xml is missing the 3D model override.")


def _validate_relationships(root: ET.Element, errors: list[str]) -> None:
    if root.tag != f"{{{RELS_NS}}}Relationships":
        errors.append("_rels/.rels root namespace is invalid.")
    relationships = [child for child in root if child.tag == f"{{{RELS_NS}}}Relationship"]
    model_relationships = [
        child for child in relationships if child.attrib.get("Type") == MODEL_REL_TYPE and child.attrib.get("Target") == f"/{MODEL_XML}"
    ]
    if len(model_relationships) != 1:
        errors.append("_rels/.rels must contain exactly one relationship to /3D/3dmodel.model.")
    elif model_relationships[0].attrib.get("Id") != "rel0":
        errors.append("_rels/.rels model relationship ID must be rel0.")


def _validate_model_xml(root: ET.Element, errors: list[str]) -> tuple[str, np.ndarray | None, np.ndarray | None]:
    if root.tag != f"{{{CORE_NS}}}model":
        errors.append("3D/3dmodel.model root namespace is invalid.")
    units = root.attrib.get("unit", "")
    if units != "millimeter":
        errors.append("3D model unit must be millimeter.")
    if any("bambu" in key.lower() or "orca" in key.lower() for key in root.attrib):
        errors.append("3D model contains slicer/vendor-specific attributes.")

    resources = root.find(f"{{{CORE_NS}}}resources")
    build = root.find(f"{{{CORE_NS}}}build")
    if resources is None:
        errors.append("3D model is missing resources.")
        return units, None, None
    if build is None:
        errors.append("3D model is missing build section.")

    objects = resources.findall(f"{{{CORE_NS}}}object")
    object_ids: list[int] = []
    for obj in objects:
        try:
            object_ids.append(int(obj.attrib.get("id", "")))
        except ValueError:
            errors.append(f"Object ID is not an integer: {obj.attrib.get('id')!r}")
    if len(object_ids) != len(set(object_ids)):
        errors.append("Object IDs are not unique.")
    if object_ids != [1]:
        errors.append(f"Generic 3MF must contain only deterministic object ID 1; got {object_ids}.")

    obj = objects[0] if objects else None
    mesh = obj.find(f"{{{CORE_NS}}}mesh") if obj is not None else None
    if obj is None or mesh is None:
        errors.append("Object 1 is missing a mesh.")
        return units, None, None
    vertices_element = mesh.find(f"{{{CORE_NS}}}vertices")
    triangles_element = mesh.find(f"{{{CORE_NS}}}triangles")
    vertices = _parse_vertices(vertices_element, errors)
    faces = _parse_triangles(triangles_element, len(vertices) if vertices is not None else 0, errors)

    if build is not None:
        items = build.findall(f"{{{CORE_NS}}}item")
        if len(items) != 1:
            errors.append("Build section must contain exactly one item.")
        elif items[0].attrib.get("objectid") != "1":
            errors.append("Build item must reference object ID 1.")
        if items and "transform" in items[0].attrib:
            _parse_transform(items[0].attrib["transform"], errors)
    return units, vertices, faces


def _parse_vertices(vertices_element: ET.Element | None, errors: list[str]) -> np.ndarray | None:
    if vertices_element is None:
        errors.append("Mesh is missing vertices.")
        return None
    vertices: list[tuple[float, float, float]] = []
    for vertex in vertices_element.findall(f"{{{CORE_NS}}}vertex"):
        try:
            row = (float(vertex.attrib["x"]), float(vertex.attrib["y"]), float(vertex.attrib["z"]))
        except (KeyError, ValueError) as error:
            errors.append(f"Vertex has invalid coordinates: {error}")
            continue
        if not all(math.isfinite(value) for value in row):
            errors.append("Vertex has non-finite coordinates.")
        vertices.append(row)
    if not vertices:
        errors.append("Mesh has no vertices.")
    return np.asarray(vertices, dtype=float)


def _parse_triangles(triangles_element: ET.Element | None, vertex_count: int, errors: list[str]) -> np.ndarray | None:
    if triangles_element is None:
        errors.append("Mesh is missing triangles.")
        return None
    faces: list[tuple[int, int, int]] = []
    for triangle in triangles_element.findall(f"{{{CORE_NS}}}triangle"):
        try:
            face = (int(triangle.attrib["v1"]), int(triangle.attrib["v2"]), int(triangle.attrib["v3"]))
        except (KeyError, ValueError) as error:
            errors.append(f"Triangle has invalid indices: {error}")
            continue
        if any(index < 0 for index in face):
            errors.append("Triangle has negative indices.")
        if any(index >= vertex_count for index in face):
            errors.append("Triangle references an out-of-range vertex.")
        faces.append(face)
    if not faces:
        errors.append("Mesh has no triangles.")
    return np.asarray(faces, dtype=np.int64)


def _parse_transform(value: str, errors: list[str]) -> None:
    try:
        numbers = [float(part) for part in value.split()]
    except ValueError:
        errors.append("Build item transform contains non-numeric values.")
        return
    if len(numbers) != 12 or not all(math.isfinite(number) for number in numbers):
        errors.append("Build item transform must contain 12 finite numbers.")


def _mesh_dimensions_from_vertices(vertices: np.ndarray) -> list[float]:
    mins = np.min(vertices, axis=0)
    maxs = np.max(vertices, axis=0)
    return [round(float(value), 4) for value in (maxs - mins)]


def _bounds_match(vertices: np.ndarray, source_vertices: np.ndarray, tolerance: float) -> bool:
    mins = np.min(vertices, axis=0)
    maxs = np.max(vertices, axis=0)
    source_mins = np.min(source_vertices, axis=0)
    source_maxs = np.max(source_vertices, axis=0)
    dims = maxs - mins
    source_dims = source_maxs - source_mins
    return (
        np.allclose(mins, source_mins, atol=tolerance)
        and np.allclose(maxs, source_maxs, atol=tolerance)
        and np.allclose(dims, source_dims, atol=tolerance)
    )


def _format_number(value: float) -> str:
    if not math.isfinite(float(value)):
        raise ValueError("Cannot serialize non-finite coordinate.")
    selected = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return selected if selected not in {"", "-0"} else "0"
