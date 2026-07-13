from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from spool_house_ai.output_paths import build_job_output_paths_for_stem
from spool_house_ai.slicer_integration import (
    build_slicer_launch_plan,
    discover_slicer,
    launch_slicer_plan,
    normalize_preferred_slicer,
    safe_slicer_info_diagnostic,
    select_slicer_input,
)


class SlicerIntegrationTests(unittest.TestCase):
    def _paths(self, temp_dir: str):
        paths = build_job_output_paths_for_stem(Path(temp_dir), "sample model")
        paths.create_directories()
        return paths

    def _successful_status(self, paths):
        return {
            "failures": [],
            "mesh_summary": {"exists": True, "failures": []},
            "generic_3mf_summary": {
                "generic_3mf_created": True,
                "generic_3mf_validation_passed": True,
                "generic_3mf_path": str(paths.generic_3mf_path),
            },
        }

    def test_validated_3mf_is_preferred(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = self._paths(temp_dir)
            paths.stl_path.write_text("solid model", encoding="utf-8")
            paths.generic_3mf_path.write_bytes(b"3mf")
            selection = select_slicer_input(paths, self._successful_status(paths))
            self.assertTrue(selection.success)
            self.assertEqual(selection.path, paths.generic_3mf_path)
            self.assertTrue(selection.used_generic_3mf)

    def test_failed_3mf_is_ignored_for_stl_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = self._paths(temp_dir)
            paths.stl_path.write_text("solid model", encoding="utf-8")
            paths.generic_3mf_path.write_bytes(b"bad")
            status = self._successful_status(paths)
            status["generic_3mf_summary"]["generic_3mf_validation_passed"] = False
            selection = select_slicer_input(paths, status)
            self.assertTrue(selection.success)
            self.assertEqual(selection.path, paths.stl_path)
            self.assertTrue(selection.fallback_used)

    def test_missing_3mf_falls_back_to_stl(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = self._paths(temp_dir)
            paths.stl_path.write_text("solid model", encoding="utf-8")
            selection = select_slicer_input(paths, {"failures": []})
            self.assertEqual(selection.path, paths.stl_path)
            self.assertTrue(selection.fallback_used)

    def test_prefer_generic_false_selects_stl(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = self._paths(temp_dir)
            paths.stl_path.write_text("solid model", encoding="utf-8")
            paths.generic_3mf_path.write_bytes(b"3mf")
            selection = select_slicer_input(paths, self._successful_status(paths), prefer_generic_3mf=False)
            self.assertEqual(selection.path, paths.stl_path)
            self.assertFalse(selection.used_generic_3mf)

    def test_missing_both_returns_clear_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = self._paths(temp_dir)
            selection = select_slicer_input(paths, {})
            self.assertFalse(selection.success)
            self.assertIn("No successful STL", selection.error)

    def test_preferred_slicer_normalization(self) -> None:
        self.assertEqual(normalize_preferred_slicer("OrcaSlicer"), "orca")
        self.assertEqual(normalize_preferred_slicer("bambu studio"), "bambu")
        self.assertEqual(normalize_preferred_slicer("nonsense"), "system_default")

    def test_configured_discovery_path_wins(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            exe = Path(temp_dir) / "orca-slicer.exe"
            exe.write_text("", encoding="utf-8")
            result = discover_slicer("orca", configured_path=str(exe))
            self.assertTrue(result.found)
            self.assertEqual(result.discovery_method, "configured")
            self.assertEqual(result.executable_path, exe.resolve())

    def test_common_path_and_path_lookup_discovery_are_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            common = Path(temp_dir) / "Bambu Studio" / "bambu-studio.exe"
            common.parent.mkdir()
            common.write_text("", encoding="utf-8")
            result = discover_slicer("bambu", common_paths=[common])
            self.assertTrue(result.found)
            self.assertEqual(result.discovery_method, "common_path")

            path_exe = Path(temp_dir) / "orca-slicer.exe"
            path_exe.write_text("", encoding="utf-8")
            result = discover_slicer("orca", common_paths=[], path_lookup=lambda _name: str(path_exe))
            self.assertTrue(result.found)
            self.assertEqual(result.discovery_method, "path")

    def test_missing_and_wrong_executable_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            wrong = Path(temp_dir) / "notepad.exe"
            wrong.write_text("", encoding="utf-8")
            result = discover_slicer("orca", configured_path=str(wrong))
            self.assertFalse(result.found)
            self.assertEqual(result.validation_result, "wrong_app")
            missing = discover_slicer("bambu", configured_path=str(Path(temp_dir) / "bambu-studio.exe"))
            self.assertFalse(missing.found)

    def test_launch_system_default_uses_selected_file_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = self._paths(temp_dir)
            paths.stl_path.write_text("solid model", encoding="utf-8")
            selection = select_slicer_input(paths, {"failures": []})
            plan = build_slicer_launch_plan(selection, preferred_slicer="system_default")
            opened: list[Path] = []
            result = launch_slicer_plan(plan, system_default_launcher=lambda path: opened.append(path) is None)
            self.assertTrue(result.launched)
            self.assertEqual(opened, [paths.stl_path])

    def test_orca_and_bambu_launch_use_argument_lists_without_slicing_flags(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = self._paths(temp_dir)
            paths.stl_path.write_text("solid model", encoding="utf-8")
            orca = Path(temp_dir) / "orca-slicer.exe"
            bambu = Path(temp_dir) / "bambu-studio.exe"
            orca.write_text("", encoding="utf-8")
            bambu.write_text("", encoding="utf-8")
            selection = select_slicer_input(paths, {"failures": []})

            launched: list[list[str]] = []

            def fake_popen(args, **_kwargs):
                launched.append(args)
                return object()

            orca_plan = build_slicer_launch_plan(selection, preferred_slicer="orca", orca_executable_path=str(orca))
            bambu_plan = build_slicer_launch_plan(selection, preferred_slicer="bambu", bambu_executable_path=str(bambu))
            self.assertTrue(launch_slicer_plan(orca_plan, popen_factory=fake_popen).launched)
            self.assertTrue(launch_slicer_plan(bambu_plan, popen_factory=fake_popen).launched)
            for args in launched:
                self.assertEqual(len(args), 2)
                self.assertNotIn("--slice", args)
                self.assertNotIn("--export-gcode", args)
                self.assertEqual(args[1], str(paths.stl_path))

    def test_bambu_safe_info_diagnostic_captures_output(self) -> None:
        def runner(command, **_kwargs):
            return subprocess.CompletedProcess(command, 0, stdout="dimensions: ok", stderr="")

        result = safe_slicer_info_diagnostic("bambu", Path("bambu-studio.exe"), Path("model.3mf"), runner=runner)
        self.assertTrue(result.supported)
        self.assertTrue(result.success)
        self.assertEqual(result.command[1], "--info")
        self.assertIn("dimensions", result.stdout)

    def test_cli_diagnostic_timeout_and_unsupported(self) -> None:
        def timeout_runner(command, **_kwargs):
            raise subprocess.TimeoutExpired(command, 1, output="partial", stderr="late")

        timed_out = safe_slicer_info_diagnostic(
            "bambu",
            Path("bambu-studio.exe"),
            Path("model.3mf"),
            timeout_seconds=1,
            runner=timeout_runner,
        )
        self.assertTrue(timed_out.timed_out)
        unsupported = safe_slicer_info_diagnostic("orca", Path("orca-slicer.exe"), Path("model.3mf"))
        self.assertFalse(unsupported.supported)


if __name__ == "__main__":
    unittest.main()
