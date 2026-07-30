from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import trimesh

from spool_house_ai.processing.filament_layers import calculate_filament_swap_plan
from spool_house_ai.processing.orca_3mf import (
    CUSTOM_GCODE,
    ORCA_PROJECT_NOTICE,
    export_orca_project_3mf,
    validate_orca_project_3mf,
)


class OrcaProject3mfTests(unittest.TestCase):
    def test_exports_orca_project_with_color_change_markers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            mesh = trimesh.creation.box(extents=(40.0, 20.0, 4.4))
            mesh.apply_translation((20.0, 10.0, 2.2))
            color_plan = _solid_base_plan()
            output_path = temp_path / "orca_project.3mf"

            metadata = export_orca_project_3mf(mesh, output_path, title="test sign", color_plan=color_plan)

            self.assertTrue(metadata["orca_project_3mf_created"])
            self.assertTrue(metadata["orca_project_3mf_validation_passed"])
            self.assertEqual(metadata["orca_project_tool_change_z_mm"], [2.2, 3.0, 3.8])
            self.assertEqual(metadata["orca_project_tool_change_count"], 3)
            self.assertIn("OrcaSlicer project", metadata["orca_project_notice"])
            self.assertTrue(output_path.exists())

            validation = validate_orca_project_3mf(output_path, mesh, color_plan=color_plan)
            self.assertTrue(validation.passed, validation.errors)
            self.assertEqual(validation.source_mesh_bounds, [40.0, 20.0, 4.4])
            self.assertEqual(validation.project_mesh_bounds, [40.0, 20.0, 4.4])
            self.assertTrue(validation.bounds_match)

    def test_archive_shape_is_orca_project_not_generic_color_3mf(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "project.3mf"
            mesh = trimesh.creation.box(extents=(10.0, 8.0, 2.0))
            mesh.apply_translation((5.0, 4.0, 1.0))

            export_orca_project_3mf(mesh, output_path, title="project", color_plan=_no_base_plan())

            with zipfile.ZipFile(output_path, "r") as package:
                entries = package.namelist()
                self.assertIn("3D/3dmodel.model", entries)
                self.assertTrue(any(entry.startswith("3D/Objects/") for entry in entries))
                self.assertIn("Metadata/project_settings.config", entries)
                self.assertIn("Metadata/model_settings.config", entries)
                self.assertIn("Metadata/filament_sequence.json", entries)
                self.assertIn(CUSTOM_GCODE, entries)
                self.assertFalse(any(entry.lower().endswith((".stl", ".gcode", ".gco", ".png")) for entry in entries))

                settings = json.loads(package.read("Metadata/project_settings.config"))
                self.assertEqual(settings["version"], "02.06.00.51")
                self.assertEqual(settings["curr_bed_type"], "High Temp Plate")
                self.assertEqual(settings["manual_filament_change"], "1")
                self.assertEqual(settings["single_extruder_multi_material"], "1")
                self.assertEqual(settings["filament_colour"], ["#FFFFFF", "#FF0000", "#000000"])
                self.assertEqual(settings["layer_height"], "0.2")
                self.assertEqual(
                    json.loads(package.read("Metadata/filament_sequence.json")),
                    {"plate_1": {"nozzle_sequence": [], "optimal_assignment": [], "sequence": []}},
                )

                root_model = ET.fromstring(package.read("3D/3dmodel.model"))
                ns = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
                item = root_model.find(".//m:build/m:item", ns)
                self.assertIsNotNone(item)
                self.assertEqual(item.attrib["transform"], "1 0 0 0 1 0 0 0 1 110 110 1")

                custom = ET.fromstring(package.read(CUSTOM_GCODE))
                layers = custom.findall(".//layer")
                self.assertEqual([layer.attrib["top_z"] for layer in layers], ["1", "1.4"])
                self.assertEqual([layer.attrib["extruder"] for layer in layers], ["2", "3"])
                self.assertEqual([layer.attrib["gcode"] for layer in layers], ["tool_change", "tool_change"])

    def test_export_is_byte_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            mesh = trimesh.creation.box(extents=(12.0, 6.0, 2.0))
            mesh.apply_translation((6.0, 3.0, 1.0))
            first = temp_path / "first.3mf"
            second = temp_path / "second.3mf"

            export_orca_project_3mf(mesh, first, title="same", color_plan=_no_base_plan())
            export_orca_project_3mf(mesh, second, title="same", color_plan=_no_base_plan())

            self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_invalid_mesh_failure_does_not_leave_project_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "invalid.3mf"
            mesh = trimesh.Trimesh(vertices=[[0.0, 0.0, float("nan")]], faces=[[0, 0, 0]], process=False)

            with self.assertRaises(ValueError):
                export_orca_project_3mf(mesh, output_path, title="bad", color_plan=_no_base_plan())

            self.assertFalse(output_path.exists())

    def test_validator_rejects_printer_bound_gcode_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "project.3mf"
            mesh = trimesh.creation.box(extents=(10.0, 8.0, 2.0))
            mesh.apply_translation((5.0, 4.0, 1.0))
            export_orca_project_3mf(mesh, output_path, title="project", color_plan=_no_base_plan())

            unsafe_path = Path(temp_dir) / "unsafe.3mf"
            with zipfile.ZipFile(output_path, "r") as src, zipfile.ZipFile(unsafe_path, "w") as dst:
                for name in src.namelist():
                    data = src.read(name)
                    if name == "Metadata/project_settings.config":
                        data += b'\n{"machine_start_gcode":"G28"}'
                    dst.writestr(name, data)

            validation = validate_orca_project_3mf(unsafe_path, mesh, color_plan=_no_base_plan())
            self.assertFalse(validation.passed)
            self.assertTrue(any("printer-bound G-code" in error for error in validation.errors))


def _colors() -> list[dict[str, object]]:
    return [
        {"index": 1, "cluster_label": 0, "hex": "#FFFFFF", "suggested_color_name": "white"},
        {"index": 2, "cluster_label": 1, "hex": "#FF0000", "suggested_color_name": "red"},
        {"index": 3, "cluster_label": 2, "hex": "#000000", "suggested_color_name": "black"},
    ]


def _no_base_plan() -> dict:
    return calculate_filament_swap_plan(
        _colors(),
        base_height_mm=0.8,
        layer_step_mm=0.4,
        first_layer_height_mm=0.2,
        layer_height_mm=0.2,
        height_alignment_mode="snap_up",
        height_alignment_tolerance_mm=0.001,
        palette_order="light_to_dark",
    )


def _solid_base_plan() -> dict:
    return calculate_filament_swap_plan(
        _colors(),
        base_height_mm=0.8,
        layer_step_mm=0.4,
        first_layer_height_mm=0.2,
        layer_height_mm=0.2,
        height_alignment_mode="snap_up",
        height_alignment_tolerance_mm=0.001,
        min_model_thickness_mm=2.0,
        solid_base_enabled=True,
        solid_base_thickness_mm=2.0,
        solid_base_color_band_height_mm=0.8,
        palette_order="light_to_dark",
    )


if __name__ == "__main__":
    unittest.main()
