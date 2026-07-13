from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
import trimesh

from spool_house_ai.processing.generic_3mf import (
    ARCHIVE_ENTRY_ORDER,
    CORE_NS,
    GENERIC_3MF_NOTICE,
    MODEL_XML,
    export_generic_3mf,
    validate_generic_3mf,
)


class Generic3mfTests(unittest.TestCase):
    def test_export_writes_minimal_valid_package(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            mesh = _disconnected_mesh()
            original_vertices = np.asarray(mesh.vertices).copy()
            original_faces = np.asarray(mesh.faces).copy()
            output_path = temp_path / "generic.3mf"

            metadata = export_generic_3mf(
                mesh,
                output_path,
                title="Generic Test",
                application="Spool House Studio",
                description=GENERIC_3MF_NOTICE,
            )

            self.assertTrue(output_path.exists())
            self.assertTrue(metadata["generic_3mf_created"])
            self.assertTrue(metadata["generic_3mf_validation_passed"])
            self.assertEqual(metadata["archive_entries"], list(ARCHIVE_ENTRY_ORDER))
            self.assertEqual(metadata["generic_3mf_units"], "millimeter")
            self.assertTrue(metadata["bounds_match"])
            np.testing.assert_allclose(mesh.vertices, original_vertices)
            np.testing.assert_array_equal(mesh.faces, original_faces)

            with zipfile.ZipFile(output_path, "r") as package:
                self.assertEqual(package.namelist(), list(ARCHIVE_ENTRY_ORDER))
                model_root = ET.fromstring(package.read(MODEL_XML))
            self.assertEqual(model_root.attrib["unit"], "millimeter")
            objects = model_root.findall(f".//{{{CORE_NS}}}object")
            self.assertEqual([obj.attrib["id"] for obj in objects], ["1"])
            build_items = model_root.findall(f".//{{{CORE_NS}}}build/{{{CORE_NS}}}item")
            self.assertEqual(len(build_items), 1)
            self.assertEqual(build_items[0].attrib["objectid"], "1")
            self.assertNotIn("transform", build_items[0].attrib)

    def test_deterministic_export_is_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            mesh = _disconnected_mesh()
            first = temp_path / "first.3mf"
            second = temp_path / "second.3mf"

            for path in (first, second):
                export_generic_3mf(
                    mesh,
                    path,
                    title="Deterministic",
                    application="Spool House Studio",
                    description=GENERIC_3MF_NOTICE,
                )

            self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_validator_rejects_missing_model_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "missing_model.3mf"
            with zipfile.ZipFile(path, "w") as package:
                package.writestr("[Content_Types].xml", "<Types/>")
                package.writestr("_rels/.rels", "<Relationships/>")

            result = validate_generic_3mf(path)

            self.assertFalse(result.passed)
            self.assertTrue(any("missing required entries" in error for error in result.errors))

    def test_validator_rejects_bounds_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source = trimesh.creation.box(extents=(10.0, 8.0, 2.0))
            different = trimesh.creation.box(extents=(12.0, 8.0, 2.0))
            output_path = temp_path / "bounds.3mf"

            export_generic_3mf(
                source,
                output_path,
                title="Bounds",
                application="Spool House Studio",
                description=GENERIC_3MF_NOTICE,
            )
            result = validate_generic_3mf(output_path, different)

            self.assertFalse(result.passed)
            self.assertFalse(result.bounds_match)
            self.assertTrue(any("do not match source mesh bounds" in error for error in result.errors))

    def test_invalid_meshes_fail_before_writing_successful_export(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            empty = trimesh.Trimesh(vertices=np.empty((0, 3)), faces=np.empty((0, 3)), process=False)
            nan_mesh = trimesh.Trimesh(
                vertices=np.array([[0.0, 0.0, 0.0], [1.0, np.nan, 0.0], [0.0, 1.0, 0.0]]),
                faces=np.array([[0, 1, 2]]),
                process=False,
            )
            bad_face_mesh = trimesh.Trimesh(
                vertices=np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]),
                faces=np.array([[0, 1, 4]]),
                process=False,
            )

            for index, mesh in enumerate((empty, nan_mesh, bad_face_mesh)):
                output_path = temp_path / f"invalid_{index}.3mf"
                with self.assertRaises(ValueError):
                    export_generic_3mf(
                        mesh,
                        output_path,
                        title="Invalid",
                        application="Spool House Studio",
                        description=GENERIC_3MF_NOTICE,
                    )
                self.assertFalse(output_path.exists())

    def test_validator_rejects_embedded_stl_and_vendor_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "vendor.3mf"
            with zipfile.ZipFile(path, "w") as package:
                package.writestr("[Content_Types].xml", "<Types/>")
                package.writestr("_rels/.rels", "<Relationships/>")
                package.writestr("3D/3dmodel.model", "<model/>")
                package.writestr("Metadata/project_settings.config", "BambuStudio")
                package.writestr("3D/Objects/source.stl", "solid test")

            result = validate_generic_3mf(path)

            self.assertFalse(result.passed)
            self.assertTrue(any("STL or G-code" in error for error in result.errors))
            self.assertTrue(any("slicer/vendor-specific" in error for error in result.errors))


def _disconnected_mesh() -> trimesh.Trimesh:
    left = trimesh.creation.box(extents=(10.0, 8.0, 2.0))
    left.apply_translation((5.0, 4.0, 1.0))
    right = trimesh.creation.box(extents=(2.0, 2.0, 1.0))
    right.apply_translation((14.0, 4.0, 0.5))
    mesh = trimesh.util.concatenate([left, right])
    return trimesh.Trimesh(vertices=mesh.vertices.copy(), faces=mesh.faces.copy(), process=False)


if __name__ == "__main__":
    unittest.main()
