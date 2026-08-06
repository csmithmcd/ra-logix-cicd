"""Tests for Git-aware Logix routine comparison."""

from __future__ import annotations

import json
import subprocess
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
from compare import logix_code_compare  # noqa: E402


class FakeLogixSdkError(Exception):
    """Stand-in for the Rockwell SDK base exception."""


class FakeStdOutEventLogger:
    """Stand-in for the Rockwell SDK event logger."""


class FakeOpenedProject:
    calls: list[tuple[str, str]] = []
    close_error_markers: set[str] = set()
    exports_by_marker: dict[str, dict[str, str]] = {}

    def __init__(self, marker: str) -> None:
        self.marker = marker

    async def get_all_executables(self) -> list[str]:
        self.calls.append((self.marker, "get_all_executables"))
        return list(self.exports_by_marker[self.marker])

    async def partial_export_to_xml_file(self, x_path: str, file_path: str) -> None:
        self.calls.append((self.marker, f"partial_export:{x_path}"))
        Path(file_path).write_text(
            self.exports_by_marker[self.marker][x_path],
            encoding="utf-8",
        )

    def close(self) -> None:
        self.calls.append((self.marker, "close"))
        if self.marker in self.close_error_markers:
            raise RuntimeError(f"close failed for {self.marker}")


class FakeLogixProject:
    @classmethod
    async def open_logix_project(
        cls, project_path: str, logger: FakeStdOutEventLogger
    ) -> FakeOpenedProject:
        del logger
        marker = Path(project_path).read_text(encoding="utf-8")
        FakeOpenedProject.calls.append((marker, "open"))
        return FakeOpenedProject(marker)


def fake_sdk_modules() -> dict[str, types.ModuleType]:
    sdk_module = types.ModuleType("logix_designer_sdk")
    sdk_module.LogixProject = FakeLogixProject
    sdk_module.StdOutEventLogger = FakeStdOutEventLogger

    exceptions_module = types.ModuleType("logix_designer_sdk.exceptions")
    exceptions_module.LogixSdkError = FakeLogixSdkError
    return {
        "logix_designer_sdk": sdk_module,
        "logix_designer_sdk.exceptions": exceptions_module,
    }


def run_git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def initialize_repository(root: Path) -> tuple[Path, str, str, str]:
    repository = root / "repository"
    repository.mkdir()
    run_git(repository, "init", "--quiet")
    run_git(repository, "config", "user.name", "Logix Compare Test")
    run_git(repository, "config", "user.email", "logix-compare@example.invalid")

    project_path = "projects/Example.ACD"
    project = repository / "projects" / "Example.ACD"
    project.parent.mkdir(parents=True)
    project.write_text("base", encoding="utf-8")
    run_git(repository, "add", project_path)
    run_git(repository, "commit", "--quiet", "-m", "Add base project")
    base_commit = run_git(repository, "rev-parse", "HEAD")

    project.write_text("current", encoding="utf-8")
    run_git(repository, "add", project_path)
    run_git(repository, "commit", "--quiet", "-m", "Update project")
    current_commit = run_git(repository, "rev-parse", "HEAD")
    return repository, project_path, base_commit, current_commit


def routine_xml(name: str, logic: str, export_date: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<RSLogix5000Content ExportDate="{export_date}" '
        'SchemaRevision="1.0" TargetType="Routine">\n'
        f'  <Routine Name="{name}" Type="RLL">\n'
        "    <RLLContent>\n"
        f'      <Rung Number="0"><Text>{logic}</Text></Rung>\n'
        "    </RLLContent>\n"
        "  </Routine>\n"
        "</RSLogix5000Content>\n"
    )


class LogixCodeCompareTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeOpenedProject.calls = []
        FakeOpenedProject.close_error_markers = set()
        FakeOpenedProject.exports_by_marker = {}

    def run_compare(
        self,
        root: Path,
        repository: Path,
        project_path: str,
        base_commit: str,
        current_commit: str,
    ) -> tuple[int, dict[str, object], str, Path]:
        artifacts = root / "artifacts"
        output_json = artifacts / "compare.json"
        output_markdown = artifacts / "compare.md"
        work_root = root / "work"
        argv = [
            "logix_code_compare.py",
            "--repository",
            str(repository),
            "--project-path",
            project_path,
            "--base-revision",
            base_commit,
            "--current-revision",
            current_commit,
            "--output-json",
            str(output_json),
            "--output-markdown",
            str(output_markdown),
            "--work-root",
            str(work_root),
        ]

        with (
            patch.dict(sys.modules, fake_sdk_modules()),
            patch.object(logix_code_compare.metadata, "version", return_value="2.0.2"),
            patch.object(sys, "argv", argv),
            redirect_stdout(StringIO()),
        ):
            exit_code = logix_code_compare.main()

        result = json.loads(output_json.read_text(encoding="utf-8"))
        markdown = output_markdown.read_text(encoding="utf-8")
        return exit_code, result, markdown, work_root

    def test_identical_logic_ignores_export_date_and_preserves_repository(self) -> None:
        x_path = (
            "Controller/Programs/Program[@Name='P00']/Routines/"
            "Routine[@Name='R00_Main']"
        )
        FakeOpenedProject.exports_by_marker = {
            "base": {x_path: routine_xml("R00_Main", "XIC(Input)OTE(Output);", "A")},
            "current": {
                x_path: routine_xml("R00_Main", "XIC(Input)OTE(Output);", "B")
            },
        }

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            repository, project_path, base_commit, current_commit = (
                initialize_repository(root)
            )
            (repository / "local-notes.txt").write_text(
                "pre-existing untracked file\n", encoding="utf-8"
            )
            status_before = run_git(repository, "status", "--porcelain=v1")
            exit_code, result, markdown, work_root = self.run_compare(
                root, repository, project_path, base_commit, current_commit
            )

            self.assertEqual(exit_codes.SUCCESS, exit_code)
            self.assertEqual("passed", result["status"])
            self.assertEqual("identical", result["comparison_status"])
            self.assertFalse(result["differences_found"])
            self.assertEqual([], result["changes"])
            self.assertEqual(1, result["summary"]["unchanged"])
            self.assertEqual(
                "passed", result["checks"]["repository_unchanged"]
            )
            self.assertEqual(
                "passed", result["checks"]["working_copy_cleanup"]
            )
            self.assertEqual(status_before, run_git(repository, "status", "--porcelain=v1"))
            self.assertEqual([], list(work_root.iterdir()))
            self.assertIn("No routine logic differences were found.", markdown)
            self.assertEqual(
                [
                    ("base", "open"),
                    ("base", "get_all_executables"),
                    ("base", f"partial_export:{x_path}"),
                    ("base", "close"),
                    ("current", "open"),
                    ("current", "get_all_executables"),
                    ("current", f"partial_export:{x_path}"),
                    ("current", "close"),
                ],
                FakeOpenedProject.calls,
            )

    def test_reports_added_removed_and_modified_routines_without_failure(self) -> None:
        modified = "Controller/Programs/Program[@Name='P00']/Routines/Routine[@Name='Modified']"
        removed = "Controller/Programs/Program[@Name='P00']/Routines/Routine[@Name='Removed']"
        added = "Controller/Programs/Program[@Name='P00']/Routines/Routine[@Name='Added']"
        FakeOpenedProject.exports_by_marker = {
            "base": {
                modified: routine_xml("Modified", "XIC(A)OTE(B);", "A"),
                removed: routine_xml("Removed", "XIC(C)OTE(D);", "A"),
            },
            "current": {
                modified: routine_xml("Modified", "XIC(A)OTL(B);", "B"),
                added: routine_xml("Added", "XIC(E)OTE(F);", "B"),
            },
        }

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            repository, project_path, base_commit, current_commit = (
                initialize_repository(root)
            )
            exit_code, result, markdown, _ = self.run_compare(
                root, repository, project_path, base_commit, current_commit
            )

            self.assertEqual(exit_codes.SUCCESS, exit_code)
            self.assertEqual("different", result["comparison_status"])
            self.assertTrue(result["differences_found"])
            self.assertEqual(
                {
                    "added": 1,
                    "removed": 1,
                    "modified": 1,
                    "unchanged": 0,
                    "base_total": 2,
                    "current_total": 2,
                },
                result["summary"],
            )
            changes = {change["change_type"]: change for change in result["changes"]}
            self.assertEqual(added, changes["added"]["x_path"])
            self.assertEqual(removed, changes["removed"]["x_path"])
            self.assertEqual(modified, changes["modified"]["x_path"])
            self.assertTrue(changes["modified"]["diff"])
            self.assertIn("-        <Text>XIC(A)OTE(B);</Text>", markdown)
            self.assertIn("+        <Text>XIC(A)OTL(B);</Text>", markdown)

    def test_invalid_export_fails_and_still_closes_and_cleans_up(self) -> None:
        x_path = "Controller/Programs/Program[@Name='P00']/Routines/Routine[@Name='Bad']"
        FakeOpenedProject.exports_by_marker = {
            "base": {x_path: routine_xml("Bad", "XIC(A)OTE(B);", "A")},
            "current": {x_path: "<not-valid-xml"},
        }

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            repository, project_path, base_commit, current_commit = (
                initialize_repository(root)
            )
            exit_code, result, _, work_root = self.run_compare(
                root, repository, project_path, base_commit, current_commit
            )

            self.assertEqual(exit_codes.TEST_FAILURE, exit_code)
            self.assertEqual("failed", result["status"])
            self.assertEqual("ParseError", result["error"]["type"])
            self.assertEqual("current_partial_export", result["error"]["operation"])
            self.assertEqual(
                "failed", result["checks"]["current_partial_export_completed"]
            )
            self.assertEqual(
                "passed", result["checks"]["current_project_close_completed"]
            )
            self.assertEqual(
                "passed", result["checks"]["repository_unchanged"]
            )
            self.assertEqual([], list(work_root.iterdir()))

    def test_missing_project_in_base_revision_is_configuration_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            repository, _, base_commit, current_commit = initialize_repository(root)
            artifacts = root / "artifacts"
            argv = [
                "logix_code_compare.py",
                "--repository",
                str(repository),
                "--project-path",
                "projects/Missing.ACD",
                "--base-revision",
                base_commit,
                "--current-revision",
                current_commit,
                "--output-json",
                str(artifacts / "compare.json"),
                "--output-markdown",
                str(artifacts / "compare.md"),
                "--work-root",
                str(root / "work"),
            ]

            with (
                patch.object(sys, "argv", argv),
                redirect_stdout(StringIO()),
            ):
                exit_code = logix_code_compare.main()

            result = json.loads((artifacts / "compare.json").read_text(encoding="utf-8"))
            self.assertEqual(exit_codes.CONFIGURATION_ERROR, exit_code)
            self.assertEqual("failed", result["status"])
            self.assertEqual("failed", result["checks"]["input_validation"])
            self.assertEqual("GitCommandError", result["error"]["type"])

    def test_close_failure_returns_cleanup_failure_and_removes_working_copy(self) -> None:
        x_path = "Controller/Programs/Program[@Name='P00']/Routines/Routine[@Name='Main']"
        FakeOpenedProject.exports_by_marker = {
            "base": {x_path: routine_xml("Main", "XIC(A)OTE(B);", "A")},
            "current": {x_path: routine_xml("Main", "XIC(A)OTE(B);", "B")},
        }
        FakeOpenedProject.close_error_markers = {"current"}

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            repository, project_path, base_commit, current_commit = (
                initialize_repository(root)
            )
            exit_code, result, _, work_root = self.run_compare(
                root, repository, project_path, base_commit, current_commit
            )

            self.assertEqual(exit_codes.CLEANUP_FAILURE, exit_code)
            self.assertEqual("ProjectCloseError", result["error"]["type"])
            self.assertEqual(
                "failed", result["checks"]["current_project_close_completed"]
            )
            self.assertEqual(
                "passed", result["checks"]["repository_unchanged"]
            )
            self.assertEqual(
                "passed", result["checks"]["working_copy_cleanup"]
            )
            self.assertEqual([], list(work_root.iterdir()))


if __name__ == "__main__":
    unittest.main()
