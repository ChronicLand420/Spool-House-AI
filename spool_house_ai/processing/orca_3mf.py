from __future__ import annotations

import json
import math
import os
import re
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

import numpy as np
import trimesh

from spool_house_ai.processing.filament_layers import layer_top_z
from spool_house_ai.processing.generic_3mf import (
    CORE_NS,
    FIXED_ZIP_TIMESTAMP,
    MODEL_CONTENT_TYPE,
    MODEL_REL_TYPE,
    RELS_CONTENT_TYPE,
    RELS_NS,
    TYPES_NS,
)


ORCA_PROJECT_3MF_EXPORTER_VERSION = 1
ORCA_REFERENCE_APP_VERSION = "02.06.00.51"
ORCA_REFERENCE_SLICER_VERSION = "2.4.2"
ORCA_PROJECT_NOTICE = (
    "This is an OrcaSlicer project 3MF. It includes manual filament-change project markers "
    "for OrcaSlicer, but it does not contain sliced toolpaths, printer-bound G-code, or print commands."
)
CONTENT_TYPES_XML = "[Content_Types].xml"
ROOT_RELS_XML = "_rels/.rels"
ROOT_MODEL_XML = "3D/3dmodel.model"
MODEL_RELS_XML = "3D/_rels/3dmodel.model.rels"
PROJECT_SETTINGS = "Metadata/project_settings.config"
MODEL_SETTINGS = "Metadata/model_settings.config"
SLICE_INFO = "Metadata/slice_info.config"
CUSTOM_GCODE = "Metadata/custom_gcode_per_layer.xml"
PLATE_JSON = "Metadata/plate_1.json"
FILAMENT_SEQUENCE = "Metadata/filament_sequence.json"
BAMBU_NS = "http://schemas.bambulab.com/package/2021"
PRODUCTION_NS = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
DEFAULT_PLATE_SIZE_MM = 220.0


@dataclass(frozen=True)
class OrcaProjectValidationResult:
    passed: bool
    errors: list[str]
    archive_entries: list[str]
    source_mesh_bounds: list[float]
    project_mesh_bounds: list[float]
    bounds_match: bool
    tool_change_count: int
    tool_change_z_mm: list[float]


def export_orca_project_3mf(
    mesh: trimesh.Trimesh,
    output_path: Path,
    *,
    title: str,
    color_plan: dict[str, Any],
    bounds_tolerance: float = 0.001,
) -> dict[str, Any]:
    vertices, faces = _validated_mesh_arrays(mesh)
    slots = _filament_slots(color_plan)
    tool_changes = _tool_change_events(color_plan, slots)
    if len(slots) < 1:
        raise ValueError("Orca project export requires at least one filament slot.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)
    temp_path = output_path.with_name(f"{output_path.name}.tmp")
    temp_path.unlink(missing_ok=True)

    object_model_path = f"3D/Objects/{_safe_part_name(title)}.model"
    package_entries = _archive_entry_order(object_model_path)
    try:
        _write_orca_project_package(
            temp_path,
            title=title,
            object_model_path=object_model_path,
            vertices=vertices,
            faces=faces,
            color_plan=color_plan,
            slots=slots,
            tool_changes=tool_changes,
        )
        validation = validate_orca_project_3mf(
            temp_path,
            mesh,
            color_plan=color_plan,
            expected_entries=package_entries,
            bounds_tolerance=bounds_tolerance,
        )
        if not validation.passed:
            raise ValueError("; ".join(validation.errors))
        temp_path.replace(output_path)
        return {
            "orca_project_3mf_enabled": True,
            "orca_project_3mf_created": True,
            "orca_project_3mf_path": str(output_path),
            "orca_project_3mf_validation_passed": True,
            "orca_project_3mf_validation_errors": [],
            "orca_project_3mf_bounds": validation.project_mesh_bounds,
            "source_mesh_bounds": validation.source_mesh_bounds,
            "orca_project_bounds_match": validation.bounds_match,
            "orca_project_archive_entries": validation.archive_entries,
            "orca_project_tool_change_count": validation.tool_change_count,
            "orca_project_tool_change_z_mm": validation.tool_change_z_mm,
            "orca_project_filament_slots": slots,
            "orca_project_exporter_version": ORCA_PROJECT_3MF_EXPORTER_VERSION,
            "orca_project_notice": ORCA_PROJECT_NOTICE,
        }
    except Exception as error:
        temp_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)
        return {
            "orca_project_3mf_enabled": True,
            "orca_project_3mf_created": False,
            "orca_project_3mf_path": "",
            "orca_project_3mf_validation_passed": False,
            "orca_project_3mf_validation_errors": [str(error)],
            "orca_project_3mf_bounds": [],
            "source_mesh_bounds": _mesh_dimensions_from_vertices(vertices),
            "orca_project_bounds_match": False,
            "orca_project_archive_entries": [],
            "orca_project_tool_change_count": 0,
            "orca_project_tool_change_z_mm": [],
            "orca_project_filament_slots": slots,
            "orca_project_exporter_version": ORCA_PROJECT_3MF_EXPORTER_VERSION,
            "orca_project_notice": ORCA_PROJECT_NOTICE,
        }


def disabled_orca_project_metadata(output_path: Path | None = None) -> dict[str, Any]:
    return {
        "orca_project_3mf_enabled": False,
        "orca_project_3mf_created": False,
        "orca_project_3mf_path": str(output_path) if output_path is not None else "",
        "orca_project_3mf_validation_passed": False,
        "orca_project_3mf_validation_errors": [],
        "orca_project_3mf_bounds": [],
        "orca_project_bounds_match": False,
        "orca_project_archive_entries": [],
        "orca_project_tool_change_count": 0,
        "orca_project_tool_change_z_mm": [],
        "orca_project_filament_slots": [],
        "orca_project_exporter_version": ORCA_PROJECT_3MF_EXPORTER_VERSION,
        "orca_project_notice": ORCA_PROJECT_NOTICE,
    }


def validate_orca_project_3mf(
    path: Path,
    source_mesh: trimesh.Trimesh | None = None,
    *,
    color_plan: dict[str, Any] | None = None,
    expected_entries: list[str] | None = None,
    bounds_tolerance: float = 0.001,
) -> OrcaProjectValidationResult:
    errors: list[str] = []
    entries: list[str] = []
    source_bounds: list[float] = []
    project_bounds: list[float] = []
    bounds_match = False
    tool_change_z: list[float] = []

    if not path.exists():
        return OrcaProjectValidationResult(False, [f"Orca project 3MF does not exist: {path}"], [], [], [], False, 0, [])

    try:
        with zipfile.ZipFile(path, "r") as package:
            entries = package.namelist()
            expected = expected_entries or _detect_expected_entries(entries)
            if len(entries) != len(set(entries)):
                errors.append("Orca project archive contains duplicate entries.")
            if expected and entries != expected:
                errors.append(f"Orca project archive entry order must be {expected}; got {entries}.")
            missing = [entry for entry in expected if entry not in entries]
            if missing:
                errors.append(f"Orca project archive is missing required entries: {missing}.")
            errors.extend(_validate_archive_safety(entries, package))

            _parse_xml(package, CONTENT_TYPES_XML, errors)
            root_rels = _parse_xml(package, ROOT_RELS_XML, errors)
            root_model = _parse_xml(package, ROOT_MODEL_XML, errors)
            _parse_xml(package, MODEL_RELS_XML, errors)
            model_settings = _parse_xml(package, MODEL_SETTINGS, errors)
            custom_gcode = _parse_xml(package, CUSTOM_GCODE, errors)
            try:
                json.loads(package.read(PROJECT_SETTINGS).decode("utf-8"))
            except Exception as error:
                errors.append(f"{PROJECT_SETTINGS} is not valid JSON: {error}")
            try:
                json.loads(package.read(PLATE_JSON).decode("utf-8"))
            except Exception as error:
                errors.append(f"{PLATE_JSON} is not valid JSON: {error}")
            try:
                json.loads(package.read(FILAMENT_SEQUENCE).decode("utf-8"))
            except Exception as error:
                errors.append(f"{FILAMENT_SEQUENCE} is not valid JSON: {error}")

            object_model_path = _object_model_path_from_entries(entries)
            object_model = _parse_xml(package, object_model_path, errors) if object_model_path else None
            if root_rels is not None:
                _validate_root_relationships(root_rels, errors)
            if root_model is not None:
                _validate_root_model(root_model, object_model_path, errors)
            vertices: np.ndarray | None = None
            if object_model is not None:
                vertices, _faces = _validate_component_model(object_model, errors)
                if vertices is not None and vertices.size:
                    project_bounds = _mesh_dimensions_from_vertices(vertices)
            if model_settings is not None:
                _validate_model_settings(model_settings, errors)
            if custom_gcode is not None:
                tool_change_z = _validate_custom_gcode(custom_gcode, color_plan, errors)
            if source_mesh is not None:
                source_vertices, _source_faces = _validated_mesh_arrays(source_mesh)
                source_bounds = _mesh_dimensions_from_vertices(source_vertices)
                if vertices is not None and vertices.size:
                    bounds_match = _dimensions_match(project_bounds, source_bounds, bounds_tolerance)
                    if not bounds_match:
                        errors.append(
                            f"Orca project mesh bounds {project_bounds} do not match source mesh bounds "
                            f"{source_bounds} within tolerance {bounds_tolerance}."
                        )
    except zipfile.BadZipFile as error:
        errors.append(f"Orca project 3MF is not a valid ZIP file: {error}")
    except Exception as error:
        errors.append(f"Could not validate Orca project 3MF: {error}")

    return OrcaProjectValidationResult(
        passed=not errors,
        errors=errors,
        archive_entries=entries,
        source_mesh_bounds=source_bounds,
        project_mesh_bounds=project_bounds,
        bounds_match=bounds_match,
        tool_change_count=len(tool_change_z),
        tool_change_z_mm=tool_change_z,
    )


def validation_result_to_dict(result: OrcaProjectValidationResult) -> dict[str, Any]:
    return asdict(result)


def _write_orca_project_package(
    output_path: Path,
    *,
    title: str,
    object_model_path: str,
    vertices: np.ndarray,
    faces: np.ndarray,
    color_plan: dict[str, Any],
    slots: list[dict[str, Any]],
    tool_changes: list[dict[str, Any]],
) -> None:
    source_bounds = _bounds_from_vertices(vertices)
    shifted_vertices = _center_project_vertices(vertices)
    files = {
        CONTENT_TYPES_XML: _content_types_xml(),
        ROOT_RELS_XML: _root_rels_xml(),
        ROOT_MODEL_XML: _root_model_xml(title, object_model_path, source_bounds),
        MODEL_RELS_XML: _model_rels_xml(object_model_path),
        object_model_path: _component_model_xml(shifted_vertices, faces),
        PROJECT_SETTINGS: _project_settings_json(color_plan, slots),
        MODEL_SETTINGS: _model_settings_xml(title, slots, source_bounds),
        SLICE_INFO: _slice_info_xml(),
        CUSTOM_GCODE: _custom_gcode_xml(tool_changes),
        PLATE_JSON: _plate_json(source_bounds, slots),
        FILAMENT_SEQUENCE: _filament_sequence_json(),
    }
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as package:
        for name in _archive_entry_order(object_model_path):
            info = zipfile.ZipInfo(name, date_time=FIXED_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            package.writestr(info, files[name].encode("utf-8"))


def _archive_entry_order(object_model_path: str) -> list[str]:
    return [
        CONTENT_TYPES_XML,
        ROOT_RELS_XML,
        ROOT_MODEL_XML,
        MODEL_RELS_XML,
        object_model_path,
        PROJECT_SETTINGS,
        MODEL_SETTINGS,
        SLICE_INFO,
        CUSTOM_GCODE,
        PLATE_JSON,
        FILAMENT_SEQUENCE,
    ]


def _content_types_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<Types xmlns="{TYPES_NS}">\n'
        f'  <Default Extension="rels" ContentType="{RELS_CONTENT_TYPE}"/>\n'
        f'  <Default Extension="model" ContentType="{MODEL_CONTENT_TYPE}"/>\n'
        '  <Default Extension="config" ContentType="application/octet-stream"/>\n'
        '  <Default Extension="json" ContentType="application/json"/>\n'
        '  <Default Extension="xml" ContentType="application/xml"/>\n'
        f'  <Override PartName="/{ROOT_MODEL_XML}" ContentType="{MODEL_CONTENT_TYPE}"/>\n'
        "</Types>\n"
    )


def _root_rels_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<Relationships xmlns="{RELS_NS}">\n'
        f'  <Relationship Id="rel-1" Type="{MODEL_REL_TYPE}" Target="/{ROOT_MODEL_XML}"/>\n'
        "</Relationships>\n"
    )


def _model_rels_xml(object_model_path: str) -> str:
    target = "/" + object_model_path
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<Relationships xmlns="{RELS_NS}">\n'
        f'  <Relationship Target="{escape(target)}" Id="rel-1" Type="{MODEL_REL_TYPE}"/>\n'
        "</Relationships>\n"
    )


def _root_model_xml(title: str, object_model_path: str, bounds: dict[str, float]) -> str:
    transform = (
        "1 0 0 0 1 0 0 0 1 "
        f"{_format_number(DEFAULT_PLATE_SIZE_MM / 2.0)} "
        f"{_format_number(DEFAULT_PLATE_SIZE_MM / 2.0)} "
        f"{_format_number(bounds['depth'] / 2.0)}"
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<model unit="millimeter" xml:lang="en-US" xmlns="{CORE_NS}" '
        f'xmlns:BambuStudio="{BAMBU_NS}" xmlns:p="{PRODUCTION_NS}" requiredextensions="p">\n'
        f'  <metadata name="Application">BambuStudio-{ORCA_REFERENCE_APP_VERSION}</metadata>\n'
        f'  <metadata name="OrcaSlicer">{ORCA_REFERENCE_SLICER_VERSION}</metadata>\n'
        '  <metadata name="BambuStudio:3mfVersion">1</metadata>\n'
        f'  <metadata name="Title">{escape(title)}</metadata>\n'
        f'  <metadata name="Description">{escape(ORCA_PROJECT_NOTICE)}</metadata>\n'
        "  <resources>\n"
        '    <object id="2" type="model" p:UUID="00000000-0000-0000-0000-000000000002">\n'
        "      <components>\n"
        f'        <component p:path="/{escape(object_model_path)}" objectid="1" '
        'p:UUID="00000000-0000-0000-0000-000000000001" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>\n'
        "      </components>\n"
        "    </object>\n"
        "  </resources>\n"
        "  <build>\n"
        f'    <item objectid="2" transform="{transform}" printable="1" auto_drop="1"/>\n'
        "  </build>\n"
        "</model>\n"
    )


def _component_model_xml(vertices: np.ndarray, faces: np.ndarray) -> str:
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
        f'<model unit="millimeter" xml:lang="en-US" xmlns="{CORE_NS}" xmlns:p="{PRODUCTION_NS}" requiredextensions="p">\n'
        "  <resources>\n"
        '    <object id="1" type="model" p:UUID="00000000-0000-0000-0000-000000000001">\n'
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
        "  <build/>\n"
        "</model>\n"
    )


def _project_settings_json(color_plan: dict[str, Any], slots: list[dict[str, Any]]) -> str:
    layer_settings = color_plan.get("layer_settings") or {}
    colors = [slot["hex"] for slot in slots]
    slot_count = len(colors)
    flush_matrix = ["0" if row == col else "140" for row in range(slot_count) for col in range(slot_count)]
    data = {
        "name": "project_settings",
        "version": ORCA_REFERENCE_APP_VERSION,
        "printer_model": "Creality Ender-5 S1",
        "printer_variant": "0.4",
        "printer_settings_id": "Ender-5 S1",
        "print_settings_id": "0.2mm Standard",
        "printable_area": ["0x0", "220x0", "220x220", "0x220"],
        "curr_bed_type": "High Temp Plate",
        "nozzle_diameter": ["0.4"],
        "layer_height": str(layer_settings.get("layer_height_mm", 0.2)),
        "initial_layer_print_height": str(layer_settings.get("first_layer_height_mm", 0.2)),
        "manual_filament_change": "1",
        "single_extruder_multi_material": "1",
        "change_filament_gcode": "COLOR_CHANGE",
        "filament_colour": colors,
        "filament_multi_colour": colors,
        "filament_type": ["PLA"] * slot_count,
        "filament_settings_id": ["Generic PLA"] * slot_count,
        "filament_ids": [""] * slot_count,
        "filament_self_index": [str(index) for index in range(slot_count)],
        "filament_colour_type": ["1"] * slot_count,
        "filament_map": ["1"] * slot_count,
        "filament_map_mode": "Auto For Flush",
        "flush_volumes_matrix": flush_matrix,
        "flush_volumes_vector": ["140"] * slot_count,
        "default_filament_profile": ["Generic PLA"],
        "orca_project_export_notice": ORCA_PROJECT_NOTICE,
    }
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def _model_settings_xml(title: str, slots: list[dict[str, Any]], bounds: dict[str, float]) -> str:
    source_file = f"{title}.stl"
    filament_maps = " ".join("1" for _slot in slots)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<config>\n'
        '  <object id="2">\n'
        f'    <metadata key="name" value="{escape(title)}"/>\n'
        f'    <metadata key="source_file" value="{escape(source_file)}"/>\n'
        '    <metadata key="extruder" value="1"/>\n'
        '    <part id="1" subtype="normal_part">\n'
        f'      <metadata key="name" value="{escape(source_file)}"/>\n'
        f'      <metadata key="source_file" value="{escape(source_file)}"/>\n'
        '      <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>\n'
        '      <metadata key="source_object_id" value="0"/>\n'
        '      <metadata key="source_volume_id" value="0"/>\n'
        f'      <metadata key="source_offset_x" value="{_format_number(bounds["width"] / 2.0)}"/>\n'
        f'      <metadata key="source_offset_y" value="{_format_number(bounds["height"] / 2.0)}"/>\n'
        f'      <metadata key="source_offset_z" value="{_format_number(bounds["depth"] / 2.0)}"/>\n'
        '      <mesh_stat edges_fixed="0" degenerate_facets="0" facets_removed="0" facets_reversed="0" backwards_edges="0"/>\n'
        "    </part>\n"
        "  </object>\n"
        '  <plate>\n'
        '    <metadata key="plater_id" value="1"/>\n'
        '    <metadata key="plater_name" value="plate-1"/>\n'
        f'    <metadata key="filament_map_mode" value="Auto For Flush"/>\n'
        f'    <metadata key="filament_maps" value="{filament_maps}"/>\n'
        '    <model_instance>\n'
        '      <metadata key="object_id" value="2"/>\n'
        '      <metadata key="instance_id" value="0"/>\n'
        '      <metadata key="identify_id" value="1"/>\n'
        '    </model_instance>\n'
        '  </plate>\n'
        '</config>\n'
    )


def _slice_info_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<config>\n'
        '  <header>\n'
        '    <header_item key="X-BBL-Client-Type" value="slicer"/>\n'
        f'    <header_item key="X-BBL-Client-Version" value="{ORCA_REFERENCE_APP_VERSION}"/>\n'
        f'    <header_item key="OrcaSlicer-Version" value="{ORCA_REFERENCE_SLICER_VERSION}"/>\n'
        '  </header>\n'
        '</config>\n'
    )


def _custom_gcode_xml(tool_changes: list[dict[str, Any]]) -> str:
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        "<custom_gcodes_per_layer>",
        "<plate>",
        '<plate_info id="1"/>',
    ]
    for event in tool_changes:
        lines.append(
            f'<layer top_z="{_format_number(float(event["orca_marker_top_z_mm"]))}" type="2" '
            f'extruder="{int(event["extruder"])}" color="{escape(str(event["hex"]))}" extra="" gcode="tool_change"/>'
        )
    lines.extend(
        [
            '<mode value="MultiAsSingle"/>',
            "</plate>",
            "</custom_gcodes_per_layer>",
            "",
        ]
    )
    return "\n".join(lines)


def _plate_json(bounds: dict[str, float], slots: list[dict[str, Any]]) -> str:
    center = DEFAULT_PLATE_SIZE_MM / 2.0
    half_width = bounds["width"] / 2.0
    half_height = bounds["height"] / 2.0
    data = {
        "version": 2,
        "bed_type": "hot_plate",
        "nozzle_diameter": 0.4,
        "first_extruder": 0,
        "filament_colors": [],
        "filament_ids": [],
        "bbox_all": [
            _round_json(center - half_width),
            _round_json(center - half_height),
            _round_json(center + half_width),
            _round_json(center + half_height),
        ],
        "bbox_objects": [
            {
                "area": _round_json(bounds["width"] * bounds["height"]),
                "bbox": [
                    _round_json(center - half_width),
                    _round_json(center - half_height),
                    _round_json(center + half_width),
                    _round_json(center + half_height),
                ],
                "id": 1,
                "layer_height": 0.2,
                "name": "",
            }
        ],
    }
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def _filament_sequence_json() -> str:
    return json.dumps({"plate_1": {"nozzle_sequence": [], "optimal_assignment": [], "sequence": []}}, sort_keys=True) + "\n"


def _filament_slots(color_plan: dict[str, Any]) -> list[dict[str, Any]]:
    colors = list(color_plan.get("colors") or [])
    solid_base = color_plan.get("solid_base_plate") or {}
    slots: list[dict[str, Any]] = []
    if solid_base.get("enabled"):
        slots.append(
            {
                "slot": 1,
                "role": "solid_base",
                "hex": _normalize_hex(solid_base.get("color_hex") or "#808080"),
                "label": "Solid base",
            }
        )
        for index, color in enumerate(colors, start=2):
            slots.append(
                {
                    "slot": index,
                    "role": "artwork_color",
                    "hex": _normalize_hex(color.get("hex") or "#808080"),
                    "label": color.get("suggested_color_name") or f"Color {index - 1}",
                    "order": color.get("order", index - 1),
                }
            )
    else:
        for index, color in enumerate(colors, start=1):
            slots.append(
                {
                    "slot": index,
                    "role": "artwork_color",
                    "hex": _normalize_hex(color.get("hex") or "#808080"),
                    "label": color.get("suggested_color_name") or f"Color {index}",
                    "order": color.get("order", index),
                }
            )
    return slots


def _tool_change_events(color_plan: dict[str, Any], slots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    colors = list(color_plan.get("colors") or [])
    solid_base = color_plan.get("solid_base_plate") or {}
    events: list[dict[str, Any]] = []
    if solid_base.get("enabled"):
        for color_index, color in enumerate(colors, start=2):
            events.append(
                {
                    "transition_z_mm": float(color.get("aligned_start_z_mm", 0.0)),
                    "orca_marker_top_z_mm": _orca_marker_top_z(color_plan, color),
                    "extruder": color_index,
                    "hex": _normalize_hex(color.get("hex") or "#808080"),
                    "change_before_layer": color.get("change_before_layer"),
                }
            )
    else:
        for color_index, color in enumerate(colors[1:], start=2):
            events.append(
                {
                    "transition_z_mm": float(color.get("aligned_start_z_mm", 0.0)),
                    "orca_marker_top_z_mm": _orca_marker_top_z(color_plan, color),
                    "extruder": color_index,
                    "hex": _normalize_hex(color.get("hex") or "#808080"),
                    "change_before_layer": color.get("change_before_layer"),
                }
            )
    return events


def _orca_marker_top_z(color_plan: dict[str, Any], color: dict[str, Any]) -> float:
    layer_number = color.get("change_before_layer")
    if layer_number in (None, ""):
        return float(color.get("aligned_start_z_mm", 0.0))
    layer_settings = color_plan.get("layer_settings") or {}
    first_layer_height = float(layer_settings.get("first_layer_height_mm", 0.2))
    normal_layer_height = float(layer_settings.get("layer_height_mm", 0.2))
    return layer_top_z(int(layer_number), first_layer_height, normal_layer_height)


def _validated_mesh_arrays(mesh: trimesh.Trimesh) -> tuple[np.ndarray, np.ndarray]:
    vertices = np.asarray(mesh.vertices, dtype=float)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    if vertices.ndim != 2 or vertices.shape[1] != 3 or len(vertices) == 0:
        raise ValueError("Orca project export requires a mesh with vertices.")
    if faces.ndim != 2 or faces.shape[1] != 3 or len(faces) == 0:
        raise ValueError("Orca project export requires a mesh with triangular faces.")
    if not np.all(np.isfinite(vertices)):
        raise ValueError("Orca project export requires finite vertex coordinates.")
    if np.any(faces < 0) or np.any(faces >= len(vertices)):
        raise ValueError("Orca project export found triangle indices outside the vertex array.")
    repeated = (faces[:, 0] == faces[:, 1]) | (faces[:, 0] == faces[:, 2]) | (faces[:, 1] == faces[:, 2])
    if np.any(repeated):
        raise ValueError("Orca project export found degenerate triangles with repeated vertex indices.")
    return vertices.copy(), faces.copy()


def _center_project_vertices(vertices: np.ndarray) -> np.ndarray:
    shifted = vertices.copy()
    mins = np.min(shifted, axis=0)
    maxs = np.max(shifted, axis=0)
    shifted[:, 0] -= (mins[0] + maxs[0]) / 2.0
    shifted[:, 1] -= (mins[1] + maxs[1]) / 2.0
    shifted[:, 2] -= (mins[2] + maxs[2]) / 2.0
    return shifted


def _bounds_from_vertices(vertices: np.ndarray) -> dict[str, float]:
    mins = np.min(vertices, axis=0)
    maxs = np.max(vertices, axis=0)
    return {
        "width": float(maxs[0] - mins[0]),
        "height": float(maxs[1] - mins[1]),
        "depth": float(maxs[2] - mins[2]),
    }


def _mesh_dimensions_from_vertices(vertices: np.ndarray) -> list[float]:
    bounds = _bounds_from_vertices(vertices)
    return [round(bounds["width"], 4), round(bounds["height"], 4), round(bounds["depth"], 4)]


def _dimensions_match(left: list[float], right: list[float], tolerance: float) -> bool:
    if len(left) != 3 or len(right) != 3:
        return False
    return all(abs(float(a) - float(b)) <= tolerance for a, b in zip(left, right))


def _validate_archive_safety(entries: list[str], package: zipfile.ZipFile) -> list[str]:
    errors: list[str] = []
    allowed_prefixes = ("3D/", "3D/Objects/", "3D/_rels/", "Metadata/", "_rels/")
    for entry in entries:
        normalized = entry.replace("\\", "/")
        lower = normalized.lower()
        if os.path.isabs(normalized) or ":" in normalized:
            errors.append(f"Orca project archive entry looks like an absolute path: {entry}")
        if lower.endswith((".stl", ".gcode", ".gco", ".bgcode")):
            errors.append(f"Orca project archive must not embed STL or sliced G-code entries: {entry}")
        if lower.endswith((".png", ".jpg", ".jpeg")):
            errors.append(f"Orca project export intentionally omits thumbnails; found: {entry}")
        if lower.startswith("metadata/") and lower not in {
            PROJECT_SETTINGS.lower(),
            MODEL_SETTINGS.lower(),
            SLICE_INFO.lower(),
            CUSTOM_GCODE.lower(),
            PLATE_JSON.lower(),
            FILAMENT_SEQUENCE.lower(),
        }:
            errors.append(f"Orca project archive contains unsupported metadata entry: {entry}")
        if not (
            normalized == CONTENT_TYPES_XML
            or normalized == ROOT_RELS_XML
            or normalized.startswith(allowed_prefixes)
        ):
            errors.append(f"Orca project archive contains unexpected entry: {entry}")
        text = ""
        try:
            text = package.read(entry).decode("utf-8", errors="ignore").lower()
        except Exception:
            pass
        if any(token in text for token in ("c:\\", "c:/", "\\users\\", "/users/", "appdata\\local\\temp", "appdata/local/temp")):
            errors.append(f"Orca project archive contains local or temporary path text in {entry}.")
        if any(token in text for token in ("m104", "m109", "m140", "m190", "g28", "g1 ", "g0 ", "start_print", "print_start")):
            errors.append(f"Orca project archive contains printer-bound G-code text in {entry}.")
    return errors


def _parse_xml(package: zipfile.ZipFile, entry: str, errors: list[str]) -> ET.Element | None:
    if entry not in package.namelist():
        errors.append(f"Orca project archive is missing {entry}.")
        return None
    try:
        return ET.fromstring(package.read(entry))
    except ET.ParseError as error:
        errors.append(f"{entry} is not valid XML: {error}")
        return None


def _validate_root_relationships(root: ET.Element, errors: list[str]) -> None:
    if root.tag != f"{{{RELS_NS}}}Relationships":
        errors.append("_rels/.rels root namespace is invalid.")
    relationships = [child for child in root if child.tag == f"{{{RELS_NS}}}Relationship"]
    model_rels = [child for child in relationships if child.attrib.get("Type") == MODEL_REL_TYPE]
    if len(model_rels) != 1 or model_rels[0].attrib.get("Target") != f"/{ROOT_MODEL_XML}":
        errors.append("_rels/.rels must point to /3D/3dmodel.model.")


def _validate_root_model(root: ET.Element, object_model_path: str | None, errors: list[str]) -> None:
    if root.tag != f"{{{CORE_NS}}}model":
        errors.append("Root 3D model namespace is invalid.")
    if root.attrib.get("unit") != "millimeter":
        errors.append("Root 3D model unit must be millimeter.")
    resources = root.find(f"{{{CORE_NS}}}resources")
    build = root.find(f"{{{CORE_NS}}}build")
    if resources is None or build is None:
        errors.append("Root model must contain resources and build.")
        return
    objects = resources.findall(f"{{{CORE_NS}}}object")
    if [obj.attrib.get("id") for obj in objects] != ["2"]:
        errors.append("Root project model must contain deterministic object ID 2.")
    component = objects[0].find(f".//{{{CORE_NS}}}component") if objects else None
    if component is None:
        errors.append("Root project object must reference a component model.")
    elif object_model_path and component.attrib.get(f"{{{PRODUCTION_NS}}}path") != f"/{object_model_path}":
        errors.append("Root project component path does not reference the object model file.")
    items = build.findall(f"{{{CORE_NS}}}item")
    if len(items) != 1 or items[0].attrib.get("objectid") != "2":
        errors.append("Root project build must contain one item referencing object ID 2.")


def _validate_component_model(root: ET.Element, errors: list[str]) -> tuple[np.ndarray | None, np.ndarray | None]:
    if root.tag != f"{{{CORE_NS}}}model":
        errors.append("Component model namespace is invalid.")
    if root.attrib.get("unit") != "millimeter":
        errors.append("Component model unit must be millimeter.")
    resources = root.find(f"{{{CORE_NS}}}resources")
    if resources is None:
        errors.append("Component model is missing resources.")
        return None, None
    objects = resources.findall(f"{{{CORE_NS}}}object")
    if [obj.attrib.get("id") for obj in objects] != ["1"]:
        errors.append("Component model must contain deterministic object ID 1.")
    mesh = objects[0].find(f"{{{CORE_NS}}}mesh") if objects else None
    if mesh is None:
        errors.append("Component object 1 is missing a mesh.")
        return None, None
    vertices = _parse_vertices(mesh.find(f"{{{CORE_NS}}}vertices"), errors)
    faces = _parse_triangles(mesh.find(f"{{{CORE_NS}}}triangles"), len(vertices) if vertices is not None else 0, errors)
    return vertices, faces


def _parse_vertices(vertices_element: ET.Element | None, errors: list[str]) -> np.ndarray | None:
    if vertices_element is None:
        errors.append("Mesh is missing vertices.")
        return None
    vertices = []
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
    faces = []
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


def _validate_model_settings(root: ET.Element, errors: list[str]) -> None:
    if root.tag != "config":
        errors.append("model_settings.config root must be <config>.")
    if root.find("object") is None:
        errors.append("model_settings.config is missing object metadata.")
    if root.find("plate") is None:
        errors.append("model_settings.config is missing plate metadata.")


def _validate_custom_gcode(
    root: ET.Element,
    color_plan: dict[str, Any] | None,
    errors: list[str],
) -> list[float]:
    if root.tag != "custom_gcodes_per_layer":
        errors.append("custom_gcode_per_layer.xml root is invalid.")
    layers = root.findall(".//layer")
    z_values: list[float] = []
    for layer in layers:
        if layer.attrib.get("gcode") != "tool_change":
            errors.append("Orca project only permits tool_change custom layer markers.")
        try:
            z_values.append(round(float(layer.attrib.get("top_z", "")), 4))
            int(layer.attrib.get("extruder", ""))
        except ValueError:
            errors.append("Custom layer marker has invalid top_z or extruder value.")
        if not _is_hex_color(layer.attrib.get("color", "")):
            errors.append("Custom layer marker color is not a hex color.")
    if color_plan is not None:
        expected = [
            round(float(event["orca_marker_top_z_mm"]), 4)
            for event in _tool_change_events(color_plan, _filament_slots(color_plan))
        ]
        if z_values != expected:
            errors.append(f"Custom layer marker Z values {z_values} do not match expected Orca layer-top markers {expected}.")
    return z_values


def _detect_expected_entries(entries: list[str]) -> list[str]:
    object_path = _object_model_path_from_entries(entries)
    return _archive_entry_order(object_path) if object_path else []


def _object_model_path_from_entries(entries: list[str]) -> str | None:
    object_entries = [entry for entry in entries if entry.startswith("3D/Objects/") and entry.endswith(".model")]
    return object_entries[0] if len(object_entries) == 1 else None


def _safe_part_name(title: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "_", str(title).strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if not cleaned:
        cleaned = "spool_house_model"
    return f"{cleaned}_1"


def _normalize_hex(value: Any) -> str:
    text = str(value or "#808080").strip().upper()
    if not text.startswith("#"):
        text = f"#{text}"
    if not _is_hex_color(text):
        return "#808080"
    return text


def _is_hex_color(value: str) -> bool:
    return bool(re.fullmatch(r"#[0-9A-Fa-f]{6}", str(value or "")))


def _format_number(value: float) -> str:
    if not math.isfinite(float(value)):
        raise ValueError("Cannot serialize non-finite coordinate.")
    selected = f"{float(value):.8f}".rstrip("0").rstrip(".")
    return selected if selected not in {"", "-0"} else "0"


def _round_json(value: float) -> float:
    return round(float(value), 6)
