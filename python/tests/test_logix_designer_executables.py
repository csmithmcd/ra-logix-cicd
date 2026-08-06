"""Offline regression tests for the Logix Designer open-project probe."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from common import exit_codes  # noqa: E402
from smoke import logix_designer_executables  # noqa: E402


class FakeLogixSdkError(Exception):
    """Stand-in for the Rockwell SDK exception type."""


class FakeStdOutEventLogger:
    """Stand-in for the Rockwell SDK event logger."""


class FakeExecutable:
    def __init__(self, name: str, executable_type: str) -> None:
        self.name = name
        self.executable_type = executable_type
        self.password = "must-not-be-serialized"
        self.opaque = object()


class FakeOpenedProject:
    calls: list[str] = []
    build_error: Exception | None = None
    close_error: Exception | None = None
    executables: list[FakeExecutable] = []

    async def get_all_executables(self) -> list[FakeExecutable]:
        self.calls.append("get_all_executables")
        return self.executables

    async def get_communications_path(self) -> str:
        self.calls.append("get_communications_path")
        return "EmulateEthernet\\127.0.0.1"

    async def build(self) -> None:
        self.calls.append("build")
        if self.build_error is not None:
            raise self.build_error

    def close(self) -> None:
        self.calls.append("close")
        if self.close_error is not None:
            raise self.close_error


class FakeLogixProject:
    opened_paths: list[Path] = []

    @classmethod
    async def open_logix_project(
        cls, project_path: str, logger: FakeStdOutEventLogger
    ) -> FakeOpenedProject:
        del logger
        copied_path = Path(project_path)
        if not copied_path.is_file():
            raise AssertionError("The working copy must exist before the SDK opens it")
        cls.opened_paths.append(copied_path)
        return FakeOpenedProject()


class FakeHangingLogixProject:
    @classmethod
    async def open_logix_project(
        cls, project_path: str, logger: FakeStdOutEventLogger
    ) -> FakeOpenedProject:
        del project_path, logger
        await asyncio.sleep(3600)
        raise AssertionError("The timeout should cancel the hanging open operation")


def fake_sdk_modules(
    logix_project_type: type = FakeLogixProject,
) -> dict[str, types.ModuleType]:
    sdk_module = types.ModuleType("logix_designer_sdk")
    sdk_module.LogixProject = logix_project_type
    sdk_module.StdOutEventLogger = FakeStdOutEventLogger

    exceptions_module = types.ModuleType("logix_designer_sdk.exceptions")
    exceptions_module.LogixSdkError = FakeLogixSdkError
    return {
        "logix_designer_sdk": sdk_module,
        "logix_designer_sdk.exceptions": exceptions_module,
    }


class LogixDesignerExecutablesTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeLogixProject.opened_paths = []
        FakeOpenedProject.calls = []
        FakeOpenedProject.build_error = None
        FakeOpenedProject.close_error = None
        FakeOpenedProject.executables = []

    def test_success_uses_a_copy_closes_project_and_removes_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source_path = root / "ExampleForCICD_L85E.L5X"
            source_contents = "<RSLogix5000Content />\n"
            source_path.write_text(source_contents, encoding="utf-8")
            output_path = root / "result.json"
            argv = [
                "logix_designer_executables.py",
                "--project",
                str(source_path),
                "--output",
                str(output_path),
            ]

            with (
                patch.dict(sys.modules, fake_sdk_modules()),
                patch.object(
                    logix_designer_executables.metadata,
                    "version",
                    return_value="2.0.2",
                ),
                patch.object(sys, "argv", argv),
                redirect_stdout(StringIO()),
            ):
                exit_code = logix_designer_executables.main()

            self.assertEqual(exit_codes.SUCCESS, exit_code)
            self.assertEqual(source_contents, source_path.read_text(encoding="utf-8"))
            self.assertEqual(["close"], FakeOpenedProject.calls)
            self.assertEqual(1, len(FakeLogixProject.opened_paths))
            self.assertNotEqual(source_path, FakeLogixProject.opened_paths[0])
            self.assertFalse(FakeLogixProject.opened_paths[0].exists())

            result_text = output_path.read_text(encoding="utf-8")
            result = json.loads(result_text)
            self.assertEqual("passed", result["status"])
            self.assertTrue(result["read_only"])
            self.assertTrue(result["working_copy_used"])
            self.assertFalse(result["executable_enumeration_enabled"])
            self.assertFalse(result["project_inventory_enabled"])
            self.assertFalse(result["build_validation_enabled"])
            self.assertEqual("2.0.2", result["sdk_version"])
            self.assertEqual(
                "logix_designer_read_only_open_project", result["smoke_test"]
            )
            self.assertNotIn("executables", result)
            self.assertEqual(
                {
                    "input_validation": "passed",
                    "sdk_import": "passed",
                    "source_unchanged": "passed",
                    "working_copy": "passed",
                    "project_open_started": "passed",
                    "project_open_completed": "passed",
                    "get_communications_path_started": "not_run",
                    "get_communications_path_completed": "not_run",
                    "get_all_executables_started": "not_run",
                    "get_all_executables_completed": "not_run",
                    "build_started": "not_run",
                    "build_completed": "not_run",
                    "project_close_started": "passed",
                    "project_close_completed": "passed",
                    "working_copy_cleanup": "passed",
                },
                result["checks"],
            )
            self.assertNotIn("password", result_text)
            self.assertNotIn(" at 0x", result_text)

    def test_build_validation_is_opt_in_and_does_not_save(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source_path = root / "Project.ACD"
            source_path.write_bytes(b"test project")
            output_path = root / "result.json"
            argv = [
                "logix_designer_executables.py",
                "--project",
                str(source_path),
                "--output",
                str(output_path),
                "--build-validation",
            ]

            with (
                patch.dict(sys.modules, fake_sdk_modules()),
                patch.object(
                    logix_designer_executables.metadata,
                    "version",
                    return_value="2.0.2",
                ),
                patch.object(sys, "argv", argv),
                redirect_stdout(StringIO()),
            ):
                exit_code = logix_designer_executables.main()

            result = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(exit_codes.SUCCESS, exit_code)
            self.assertEqual(["build", "close"], FakeOpenedProject.calls)
            self.assertTrue(result["build_validation_enabled"])
            self.assertEqual(
                "logix_designer_offline_build_validation", result["smoke_test"]
            )
            self.assertEqual("passed", result["checks"]["build_started"])
            self.assertEqual("passed", result["checks"]["build_completed"])
            self.assertNotIn("save", FakeOpenedProject.calls)

    def test_build_failure_preserves_operation_and_closes_project(self) -> None:
        FakeOpenedProject.build_error = FakeLogixSdkError(
            "Operation not supported on Logix Designer version 36.4."
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source_path = root / "Project.ACD"
            source_path.write_bytes(b"test project")
            output_path = root / "result.json"
            argv = [
                "logix_designer_executables.py",
                "--project",
                str(source_path),
                "--output",
                str(output_path),
                "--build-validation",
            ]

            with (
                patch.dict(sys.modules, fake_sdk_modules()),
                patch.object(
                    logix_designer_executables.metadata,
                    "version",
                    return_value="2.0.2",
                ),
                patch.object(sys, "argv", argv),
                redirect_stdout(StringIO()),
            ):
                exit_code = logix_designer_executables.main()

            result = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(exit_codes.SERVICE_UNAVAILABLE, exit_code)
            self.assertEqual(["build", "close"], FakeOpenedProject.calls)
            self.assertEqual("failed", result["checks"]["build_completed"])
            self.assertEqual("passed", result["checks"]["project_close_completed"])
            self.assertEqual("build", result["error"]["operation"])
            self.assertEqual("passed", result["checks"]["source_unchanged"])
            self.assertEqual("passed", result["checks"]["working_copy_cleanup"])

    def test_project_inventory_is_opt_in_and_includes_communications_path(self) -> None:
        FakeOpenedProject.executables = [FakeExecutable("Main", "Routine")]

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source_path = root / "Project.ACD"
            source_path.write_bytes(b"test project")
            output_path = root / "result.json"
            argv = [
                "logix_designer_executables.py",
                "--project",
                str(source_path),
                "--output",
                str(output_path),
                "--project-inventory",
            ]

            with (
                patch.dict(sys.modules, fake_sdk_modules()),
                patch.object(
                    logix_designer_executables.metadata,
                    "version",
                    return_value="2.0.2",
                ),
                patch.object(sys, "argv", argv),
                redirect_stdout(StringIO()),
            ):
                exit_code = logix_designer_executables.main()

            result = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(exit_codes.SUCCESS, exit_code)
            self.assertEqual(
                ["get_communications_path", "get_all_executables", "close"],
                FakeOpenedProject.calls,
            )
            self.assertTrue(result["project_inventory_enabled"])
            self.assertTrue(result["executable_enumeration_enabled"])
            self.assertEqual(
                "logix_designer_read_only_project_inventory", result["smoke_test"]
            )
            self.assertEqual("EmulateEthernet\\127.0.0.1", result["communications_path"])
            self.assertEqual(1, result["executables_count"])
            self.assertEqual("passed", result["checks"]["get_communications_path_started"])
            self.assertEqual("passed", result["checks"]["get_communications_path_completed"])

    def test_enumeration_is_opt_in_and_secret_filtered(self) -> None:
        FakeOpenedProject.executables = [
            FakeExecutable("Zulu", "Routine"),
            FakeExecutable("Alpha", "AOI"),
        ]

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source_path = root / "Project.ACD"
            source_path.write_bytes(b"test project")
            output_path = root / "result.json"
            argv = [
                "logix_designer_executables.py",
                "--project",
                str(source_path),
                "--output",
                str(output_path),
                "--enumerate-executables",
            ]

            with (
                patch.dict(sys.modules, fake_sdk_modules()),
                patch.object(
                    logix_designer_executables.metadata,
                    "version",
                    return_value="2.0.2",
                ),
                patch.object(sys, "argv", argv),
                redirect_stdout(StringIO()),
            ):
                exit_code = logix_designer_executables.main()

            result_text = output_path.read_text(encoding="utf-8")
            result = json.loads(result_text)
            self.assertEqual(exit_codes.SUCCESS, exit_code)
            self.assertEqual(["get_all_executables", "close"], FakeOpenedProject.calls)
            self.assertTrue(result["executable_enumeration_enabled"])
            self.assertEqual(
                "logix_designer_read_only_get_all_executables",
                result["smoke_test"],
            )
            self.assertEqual("passed", result["checks"]["get_all_executables_started"])
            self.assertEqual("passed", result["checks"]["get_all_executables_completed"])
            self.assertEqual(2, result["executables_count"])
            self.assertEqual("Alpha", result["executables"][0]["attributes"]["name"])
            self.assertNotIn("password", result_text)
            self.assertNotIn("must-not-be-serialized", result_text)
            self.assertNotIn(" at 0x", result_text)

    def test_close_failure_returns_cleanup_exit_code(self) -> None:
        FakeOpenedProject.close_error = RuntimeError("close failed")

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source_path = root / "Project.ACD"
            source_path.write_bytes(b"test project")
            output_path = root / "result.json"
            argv = [
                "logix_designer_executables.py",
                "--project",
                str(source_path),
                "--output",
                str(output_path),
            ]

            with (
                patch.dict(sys.modules, fake_sdk_modules()),
                patch.object(
                    logix_designer_executables.metadata,
                    "version",
                    return_value="2.0.2",
                ),
                patch.object(sys, "argv", argv),
                redirect_stdout(StringIO()),
            ):
                exit_code = logix_designer_executables.main()

            result = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(exit_codes.CLEANUP_FAILURE, exit_code)
            self.assertEqual("failed", result["status"])
            self.assertEqual("passed", result["checks"]["project_close_started"])
            self.assertEqual("failed", result["checks"]["project_close_completed"])
            self.assertEqual("passed", result["checks"]["working_copy_cleanup"])

    def test_timeout_identifies_open_operation_and_captures_diagnostics(self) -> None:
        diagnostics = {
            "windows_identity": "LAB\\csmith",
            "session_name": "RDP-Tcp#1",
            "session_id": 2,
            "ld_sdk_servers": [{"pid": 3352, "session_id": 0}],
            "tcp_53204_connections": [{"state": "Established"}],
            "installed_logix_designer_versions": [{"version": "36.00"}],
        }

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source_path = root / "Project.ACD"
            source_path.write_bytes(b"test project")
            output_path = root / "result.json"
            argv = [
                "logix_designer_executables.py",
                "--project",
                str(source_path),
                "--output",
                str(output_path),
                "--timeout-seconds",
                "0.01",
            ]

            with (
                patch.dict(sys.modules, fake_sdk_modules(FakeHangingLogixProject)),
                patch.object(
                    logix_designer_executables.metadata,
                    "version",
                    return_value="2.0.2",
                ),
                patch.object(
                    logix_designer_executables,
                    "capture_windows_diagnostics",
                    return_value=diagnostics,
                ),
                patch.object(sys, "argv", argv),
                redirect_stdout(StringIO()),
            ):
                exit_code = logix_designer_executables.main()

            result = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(exit_codes.SERVICE_UNAVAILABLE, exit_code)
            self.assertEqual("failed", result["status"])
            self.assertEqual("passed", result["checks"]["project_open_started"])
            self.assertEqual("failed", result["checks"]["project_open_completed"])
            self.assertEqual(
                "not_run", result["checks"]["get_all_executables_started"]
            )
            self.assertEqual("open_logix_project", result["error"]["operation"])
            self.assertEqual(0.01, result["error"]["timeout_seconds"])
            self.assertEqual(
                "Timed out after 0.01 seconds while waiting for "
                "LogixProject.open_logix_project.",
                result["error"]["message"],
            )
            self.assertEqual(diagnostics, result["diagnostics"])
            self.assertEqual("passed", result["checks"]["source_unchanged"])
            self.assertEqual("passed", result["checks"]["working_copy_cleanup"])

    def test_missing_project_is_a_configuration_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output_path = root / "result.json"
            argv = [
                "logix_designer_executables.py",
                "--project",
                str(root / "missing.ACD"),
                "--output",
                str(output_path),
            ]

            with (
                patch.object(sys, "argv", argv),
                redirect_stdout(StringIO()),
            ):
                exit_code = logix_designer_executables.main()

            result = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(exit_codes.CONFIGURATION_ERROR, exit_code)
            self.assertEqual("failed", result["status"])
            self.assertEqual("failed", result["checks"]["input_validation"])
            self.assertEqual([], FakeLogixProject.opened_paths)


if __name__ == "__main__":
    unittest.main()
