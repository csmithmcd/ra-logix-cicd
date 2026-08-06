"""Compare routine logic exported from two Git revisions of a Logix project."""

from __future__ import annotations

import argparse
import asyncio
import difflib
import hashlib
import json
import logging
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path, PurePosixPath
from typing import Any

PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from common import exit_codes  # noqa: E402
from common.logging_config import configure_logging  # noqa: E402
from common.result import write_json_result  # noqa: E402


LOGGER = logging.getLogger("logix_code_compare")
DEFAULT_JSON_OUTPUT = PYTHON_ROOT / "artifacts" / "logix-code-compare.json"
DEFAULT_MARKDOWN_OUTPUT = PYTHON_ROOT / "artifacts" / "logix-code-compare.md"
DEFAULT_WORK_ROOT = PYTHON_ROOT / "artifacts" / "logix-code-compare-work"
SUPPORTED_PROJECT_EXTENSIONS = {".acd", ".l5x"}
VOLATILE_XML_ATTRIBUTES = {"exportdate"}


class GitCommandError(RuntimeError):
    """Raised when Git cannot resolve or materialize a requested object."""


class ProjectCloseError(RuntimeError):
    """Raised when an SDK project cannot be closed cleanly."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare normalized routine L5X exports from two Git revisions of "
            "the same ACD/L5X project. Differences are reported but do not fail."
        )
    )
    parser.add_argument(
        "--repository",
        required=True,
        type=Path,
        help="Git repository containing the Logix project.",
    )
    parser.add_argument(
        "--project-path",
        required=True,
        help="Repository-relative ACD/L5X path using forward slashes.",
    )
    parser.add_argument(
        "--base-revision",
        default="HEAD^",
        help="Older Git revision. Defaults to HEAD^.",
    )
    parser.add_argument(
        "--current-revision",
        default="HEAD",
        help="Newer Git revision. Defaults to HEAD.",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=DEFAULT_JSON_OUTPUT,
        help="JSON comparison artifact path.",
    )
    parser.add_argument(
        "--output-markdown",
        type=Path,
        default=DEFAULT_MARKDOWN_OUTPUT,
        help="Human-readable Markdown comparison artifact path.",
    )
    parser.add_argument(
        "--work-root",
        type=Path,
        default=DEFAULT_WORK_ROOT,
        help="Ignored or external parent folder for disposable Git blobs and exports.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=600.0,
        help="Maximum total SDK comparison duration. Defaults to 600 seconds.",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be greater than zero")
    if args.timeout_seconds > 1200:
        parser.error("--timeout-seconds cannot exceed 1200 seconds")
    return args


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest().upper()


def _run_git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip()
        raise GitCommandError(
            f"git {' '.join(arguments)} failed with exit code "
            f"{completed.returncode}: {message}"
        )
    return completed.stdout.strip()


def resolve_repository(path: Path) -> Path:
    repository = path.resolve(strict=True)
    root = Path(_run_git(repository, "rev-parse", "--show-toplevel")).resolve()
    if root != repository:
        raise ValueError(f"--repository must be the Git root: '{root}'")
    return repository


def validate_project_path(value: str) -> str:
    if "\\" in value:
        raise ValueError("--project-path must use forward slashes")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError("--project-path must be a safe repository-relative path")
    if path.suffix.lower() not in SUPPORTED_PROJECT_EXTENSIONS:
        raise ValueError("--project-path must reference an ACD or L5X file")
    return path.as_posix()


def resolve_commit(repository: Path, revision: str) -> str:
    return _run_git(repository, "rev-parse", "--verify", f"{revision}^{{commit}}")


def resolve_blob(repository: Path, commit: str, project_path: str) -> str:
    return _run_git(
        repository,
        "rev-parse",
        "--verify",
        f"{commit}:{project_path}",
    )


def repository_status(repository: Path) -> str:
    return _run_git(
        repository,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    )


def _path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def validate_generated_path(repository: Path, path: Path, option: str) -> Path:
    resolved = path.resolve()
    if not _path_is_within(resolved, repository):
        return resolved

    relative = resolved.relative_to(repository).as_posix()
    completed = subprocess.run(
        ["git", "-C", str(repository), "check-ignore", "--quiet", "--", relative],
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise ValueError(
            f"{option} must be outside the repository or ignored by Git: "
            f"'{resolved}'"
        )
    return resolved


def materialize_git_blob(
    repository: Path,
    commit: str,
    project_path: str,
    destination: Path,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as stream:
        completed = subprocess.run(
            ["git", "-C", str(repository), "show", f"{commit}:{project_path}"],
            check=False,
            stdout=stream,
            stderr=subprocess.PIPE,
        )
    if completed.returncode != 0:
        destination.unlink(missing_ok=True)
        message = completed.stderr.decode("utf-8", errors="replace").strip()
        raise GitCommandError(
            f"Unable to materialize '{project_path}' from {commit}: {message}"
        )


def _local_name(name: str) -> str:
    return name.rsplit("}", 1)[-1].lower()


def normalize_l5x(path: Path) -> str:
    """Return readable, deterministic XML with volatile export metadata removed."""

    tree = ET.parse(path)
    root = tree.getroot()
    for element in root.iter():
        attributes = [
            (name, value)
            for name, value in element.attrib.items()
            if _local_name(name) not in VOLATILE_XML_ATTRIBUTES
        ]
        element.attrib.clear()
        element.attrib.update(sorted(attributes))

    ET.indent(tree, space="  ")
    rendered = ET.tostring(
        root,
        encoding="unicode",
        short_empty_elements=True,
    )
    return rendered.replace("\r\n", "\n").rstrip() + "\n"


def _export_file_name(x_path: str) -> str:
    names = re.findall(r"@Name=['\"]([^'\"]+)['\"]", x_path)
    label = names[-1] if names else "component"
    label = re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip("-") or "component"
    suffix = hashlib.sha256(x_path.encode("utf-8")).hexdigest()[:12]
    return f"{label}-{suffix}.L5X"


def _validate_executable_paths(values: Any) -> list[str]:
    if values is None:
        return []
    paths = list(values)
    if not all(isinstance(value, str) and value for value in paths):
        raise TypeError("LogixProject.get_all_executables returned a non-string path")
    return sorted(set(paths))


async def export_revision(
    logix_project_type: Any,
    event_logger_type: Any,
    project_path: Path,
    export_directory: Path,
    side: str,
    checks: dict[str, str],
    operation_state: dict[str, str],
) -> dict[str, dict[str, Any]]:
    project = None
    open_operation = f"{side}_project_open"
    enumerate_operation = f"{side}_executable_enumeration"
    export_operation = f"{side}_partial_export"
    complete_operation = f"{side}_complete"
    operation_state["current_operation"] = open_operation
    checks[f"{side}_project_open_started"] = "passed"
    LOGGER.info(
        "Opening disposable Git project",
        extra={"event": f"{side}_project_open_started", "revision_side": side},
    )

    try:
        project = await logix_project_type.open_logix_project(
            str(project_path), event_logger_type()
        )
        checks[f"{side}_project_open_completed"] = "passed"
        operation_state["current_operation"] = enumerate_operation
        checks[f"{side}_executable_enumeration_started"] = "passed"
        executable_paths = _validate_executable_paths(
            await project.get_all_executables()
        )
        checks[f"{side}_executable_enumeration_completed"] = "passed"

        export_directory.mkdir(parents=True, exist_ok=True)
        snapshots: dict[str, dict[str, Any]] = {}
        checks[f"{side}_partial_export_started"] = "passed"
        operation_state["current_operation"] = export_operation
        for x_path in executable_paths:
            export_path = export_directory / _export_file_name(x_path)
            await project.partial_export_to_xml_file(x_path, str(export_path))
            if not export_path.is_file() or export_path.stat().st_size == 0:
                raise OSError(f"SDK did not create a non-empty export for '{x_path}'")
            normalized_xml = normalize_l5x(export_path)
            snapshots[x_path] = {
                "normalized_xml": normalized_xml,
                "normalized_sha256": text_sha256(normalized_xml),
                "normalized_size_bytes": len(normalized_xml.encode("utf-8")),
            }
        checks[f"{side}_partial_export_completed"] = "passed"
        operation_state["current_operation"] = complete_operation
        return snapshots
    finally:
        if project is not None:
            interrupted_operation = operation_state["current_operation"]
            operation_state["current_operation"] = f"{side}_project_close"
            checks[f"{side}_project_close_started"] = "passed"
            LOGGER.info(
                "Closing disposable Git project",
                extra={"event": f"{side}_project_close_started", "revision_side": side},
            )
            try:
                project.close()
                checks[f"{side}_project_close_completed"] = "passed"
                operation_state["current_operation"] = (
                    f"{side}_completed"
                    if interrupted_operation == complete_operation
                    else interrupted_operation
                )
            except Exception as error:
                checks[f"{side}_project_close_completed"] = "failed"
                raise ProjectCloseError(f"Unable to close {side} project: {error}") from error


async def export_both_revisions(
    logix_project_type: Any,
    event_logger_type: Any,
    base_project: Path,
    current_project: Path,
    work_directory: Path,
    checks: dict[str, str],
    operation_state: dict[str, str],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    base = await export_revision(
        logix_project_type,
        event_logger_type,
        base_project,
        work_directory / "base-exports",
        "base",
        checks,
        operation_state,
    )
    current = await export_revision(
        logix_project_type,
        event_logger_type,
        current_project,
        work_directory / "current-exports",
        "current",
        checks,
        operation_state,
    )
    return base, current


def compare_snapshots(
    base: dict[str, dict[str, Any]],
    current: dict[str, dict[str, Any]],
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    changes: list[dict[str, Any]] = []
    unchanged = 0
    for x_path in sorted(set(base) | set(current)):
        if x_path not in base:
            changes.append(
                {
                    "change_type": "added",
                    "x_path": x_path,
                    "base_sha256": None,
                    "current_sha256": current[x_path]["normalized_sha256"],
                    "diff": [],
                }
            )
            continue
        if x_path not in current:
            changes.append(
                {
                    "change_type": "removed",
                    "x_path": x_path,
                    "base_sha256": base[x_path]["normalized_sha256"],
                    "current_sha256": None,
                    "diff": [],
                }
            )
            continue
        if base[x_path]["normalized_sha256"] == current[x_path]["normalized_sha256"]:
            unchanged += 1
            continue

        difference = list(
            difflib.unified_diff(
                base[x_path]["normalized_xml"].splitlines(),
                current[x_path]["normalized_xml"].splitlines(),
                fromfile=f"base:{x_path}",
                tofile=f"current:{x_path}",
                lineterm="",
            )
        )
        changes.append(
            {
                "change_type": "modified",
                "x_path": x_path,
                "base_sha256": base[x_path]["normalized_sha256"],
                "current_sha256": current[x_path]["normalized_sha256"],
                "diff": difference,
            }
        )

    summary = {
        "added": sum(change["change_type"] == "added" for change in changes),
        "removed": sum(change["change_type"] == "removed" for change in changes),
        "modified": sum(change["change_type"] == "modified" for change in changes),
        "unchanged": unchanged,
        "base_total": len(base),
        "current_total": len(current),
    }
    return summary, changes


def public_snapshot_list(snapshots: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "x_path": x_path,
            "normalized_sha256": snapshots[x_path]["normalized_sha256"],
            "normalized_size_bytes": snapshots[x_path]["normalized_size_bytes"],
        }
        for x_path in sorted(snapshots)
    ]


def base_result() -> dict[str, Any]:
    checks = {
        "input_validation": "not_run",
        "sdk_import": "not_run",
        "base_git_blob_materialized": "not_run",
        "current_git_blob_materialized": "not_run",
        "comparison_completed": "not_run",
        "repository_unchanged": "not_run",
        "working_copy_cleanup": "not_run",
    }
    for side in ("base", "current"):
        for operation in (
            "project_open_started",
            "project_open_completed",
            "executable_enumeration_started",
            "executable_enumeration_completed",
            "partial_export_started",
            "partial_export_completed",
            "project_close_started",
            "project_close_completed",
        ):
            checks[f"{side}_{operation}"] = "not_run"
    return {
        "schema_version": 1,
        "compare": "logix_git_routine_code_compare",
        "read_only": True,
        "controller_interaction": False,
        "differences_are_failure": False,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "host": platform.node(),
        "python_version": platform.python_version(),
        "checks": checks,
    }


def render_markdown(result: dict[str, Any]) -> str:
    lines = ["# Logix code comparison", ""]
    if result.get("status") != "passed":
        error = result.get("error", {})
        lines.extend(
            [
                "Comparison failed.",
                "",
                f"- Error: `{error.get('type', 'UnknownError')}`",
                f"- Operation: `{error.get('operation', 'unknown')}`",
                f"- Message: {error.get('message', 'No message')}",
                "",
            ]
        )
        return "\n".join(lines)

    base = result["base"]
    current = result["current"]
    summary = result["summary"]
    lines.extend(
        [
            f"- Project: `{result['project_path']}`",
            f"- Base: `{base['revision']}` (`{base['commit']}`)",
            f"- Current: `{current['revision']}` (`{current['commit']}`)",
            f"- Result: **{result['comparison_status']}**",
            "",
            "| Added | Removed | Modified | Unchanged |",
            "|---:|---:|---:|---:|",
            (
                f"| {summary['added']} | {summary['removed']} | "
                f"{summary['modified']} | {summary['unchanged']} |"
            ),
            "",
        ]
    )
    if not result["changes"]:
        lines.extend(["No routine logic differences were found.", ""])
        return "\n".join(lines)

    lines.extend(["## Changes", ""])
    for change in result["changes"]:
        lines.extend(
            [
                f"### {change['change_type'].title()}: `{change['x_path']}`",
                "",
                f"- Base SHA-256: `{change['base_sha256'] or 'n/a'}`",
                f"- Current SHA-256: `{change['current_sha256'] or 'n/a'}`",
                "",
            ]
        )
        if change["diff"]:
            lines.extend(["```diff", *change["diff"], "```", ""])
    return "\n".join(lines)


def finish(
    result: dict[str, Any],
    output_json: Path,
    output_markdown: Path,
    exit_code: int,
    started: float,
) -> int:
    result["exit_code"] = exit_code
    result["status"] = "passed" if exit_code == exit_codes.SUCCESS else "failed"
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    result["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    try:
        write_json_result(output_json, result)
        output_markdown.parent.mkdir(parents=True, exist_ok=True)
        temporary_markdown = output_markdown.with_suffix(output_markdown.suffix + ".tmp")
        temporary_markdown.write_text(render_markdown(result), encoding="utf-8")
        temporary_markdown.replace(output_markdown)
    except OSError as error:
        result["status"] = "failed"
        result["exit_code"] = exit_codes.CONFIGURATION_ERROR
        result["error"] = {"type": type(error).__name__, "message": str(error)}
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
        return exit_codes.CONFIGURATION_ERROR

    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    LOGGER.info(
        "Logix code comparison finished",
        extra={
            "event": "compare_finished",
            "exit_code": exit_code,
            "comparison_status": result.get("comparison_status", "failed"),
        },
    )
    return exit_code


def _mark_failed_operation(checks: dict[str, str], operation: str) -> None:
    operation_to_check = {
        "base_project_open": "base_project_open_completed",
        "base_executable_enumeration": "base_executable_enumeration_completed",
        "base_partial_export": "base_partial_export_completed",
        "current_project_open": "current_project_open_completed",
        "current_executable_enumeration": "current_executable_enumeration_completed",
        "current_partial_export": "current_partial_export_completed",
    }
    check = operation_to_check.get(operation)
    if check:
        checks[check] = "failed"


def main() -> int:
    args = parse_args()
    configure_logging(args.verbose)
    started = time.monotonic()
    result = base_result()
    checks: dict[str, str] = result["checks"]
    operation_state = {"current_operation": "input_validation"}
    repository: Path | None = None
    status_before: str | None = None
    work_directory: Path | None = None
    output_json = DEFAULT_JSON_OUTPUT.resolve()
    output_markdown = DEFAULT_MARKDOWN_OUTPUT.resolve()
    exit_code = exit_codes.SUCCESS

    try:
        repository = resolve_repository(args.repository)
        project_path = validate_project_path(args.project_path)
        output_json = validate_generated_path(repository, args.output_json, "--output-json")
        output_markdown = validate_generated_path(
            repository, args.output_markdown, "--output-markdown"
        )
        work_root = validate_generated_path(repository, args.work_root, "--work-root")
        status_before = repository_status(repository)
        base_commit = resolve_commit(repository, args.base_revision)
        current_commit = resolve_commit(repository, args.current_revision)
        base_blob = resolve_blob(repository, base_commit, project_path)
        current_blob = resolve_blob(repository, current_commit, project_path)
        checks["input_validation"] = "passed"
        result["repository"] = str(repository)
        result["project_path"] = project_path
        result["base"] = {
            "revision": args.base_revision,
            "commit": base_commit,
            "blob": base_blob,
        }
        result["current"] = {
            "revision": args.current_revision,
            "commit": current_commit,
            "blob": current_blob,
        }
    except (GitCommandError, OSError, ValueError) as error:
        checks["input_validation"] = "failed"
        result["error"] = {"type": type(error).__name__, "message": str(error)}
        return finish(result, output_json, output_markdown, exit_codes.CONFIGURATION_ERROR, started)

    if not ((3, 12) <= sys.version_info[:2] < (3, 14)):
        result["error"] = {
            "type": "UnsupportedPythonVersion",
            "message": (
                "logix-designer-sdk 2.0.2 requires Python 3.12 or 3.13; "
                "Python 3.13 is the validated baseline."
            ),
        }
        return finish(result, output_json, output_markdown, exit_codes.CONFIGURATION_ERROR, started)

    try:
        from logix_designer_sdk import LogixProject, StdOutEventLogger
        from logix_designer_sdk.exceptions import LogixSdkError

        result["sdk_version"] = metadata.version("logix-designer-sdk")
        checks["sdk_import"] = "passed"
    except Exception as error:
        checks["sdk_import"] = "failed"
        result["error"] = {"type": type(error).__name__, "message": str(error)}
        return finish(result, output_json, output_markdown, exit_codes.DEPENDENCY_ERROR, started)

    try:
        operation_state["current_operation"] = "working_copy"
        work_root.mkdir(parents=True, exist_ok=True)
        work_directory = Path(
            tempfile.mkdtemp(prefix="logix-code-compare-", dir=work_root)
        )
        project_name = PurePosixPath(project_path).name
        base_project = work_directory / "base" / project_name
        current_project = work_directory / "current" / project_name

        materialize_git_blob(
            repository, result["base"]["commit"], project_path, base_project
        )
        checks["base_git_blob_materialized"] = "passed"
        materialize_git_blob(
            repository, result["current"]["commit"], project_path, current_project
        )
        checks["current_git_blob_materialized"] = "passed"
        result["base"]["project_sha256"] = file_sha256(base_project)
        result["current"]["project_sha256"] = file_sha256(current_project)

        base_snapshots, current_snapshots = asyncio.run(
            asyncio.wait_for(
                export_both_revisions(
                    LogixProject,
                    StdOutEventLogger,
                    base_project,
                    current_project,
                    work_directory,
                    checks,
                    operation_state,
                ),
                timeout=args.timeout_seconds,
            )
        )
        operation_state["current_operation"] = "comparison"
        summary, changes = compare_snapshots(base_snapshots, current_snapshots)
        checks["comparison_completed"] = "passed"
        result["base"]["routines"] = public_snapshot_list(base_snapshots)
        result["current"]["routines"] = public_snapshot_list(current_snapshots)
        result["summary"] = summary
        result["changes"] = changes
        result["differences_found"] = bool(changes)
        result["comparison_status"] = "different" if changes else "identical"
    except TimeoutError as error:
        operation = operation_state["current_operation"]
        _mark_failed_operation(checks, operation)
        result["error"] = {
            "type": type(error).__name__,
            "message": f"Timed out during {operation} after {args.timeout_seconds} seconds.",
            "operation": operation,
        }
        exit_code = exit_codes.SERVICE_UNAVAILABLE
    except ProjectCloseError as error:
        result["error"] = {
            "type": type(error).__name__,
            "message": str(error),
            "operation": operation_state["current_operation"],
        }
        exit_code = exit_codes.CLEANUP_FAILURE
    except LogixSdkError as error:
        operation = operation_state["current_operation"]
        _mark_failed_operation(checks, operation)
        result["error"] = {
            "type": type(error).__name__,
            "message": str(error),
            "operation": operation,
        }
        exit_code = exit_codes.SERVICE_UNAVAILABLE
    except (ET.ParseError, GitCommandError, OSError, TypeError, ValueError) as error:
        operation = operation_state["current_operation"]
        _mark_failed_operation(checks, operation)
        result["error"] = {
            "type": type(error).__name__,
            "message": str(error),
            "operation": operation,
        }
        exit_code = exit_codes.TEST_FAILURE
    except Exception as error:
        operation = operation_state["current_operation"]
        _mark_failed_operation(checks, operation)
        LOGGER.exception(
            "Unexpected Logix code comparison failure",
            extra={"event": "unexpected_error", "operation": operation},
        )
        result["error"] = {
            "type": type(error).__name__,
            "message": str(error),
            "operation": operation,
        }
        exit_code = exit_codes.TEST_FAILURE
    finally:
        if work_directory is not None:
            try:
                shutil.rmtree(work_directory)
                checks["working_copy_cleanup"] = "passed"
            except OSError as error:
                checks["working_copy_cleanup"] = "failed"
                result["error"] = {"type": type(error).__name__, "message": str(error)}
                exit_code = exit_codes.CLEANUP_FAILURE

        if repository is not None and status_before is not None:
            try:
                if repository_status(repository) == status_before:
                    checks["repository_unchanged"] = "passed"
                else:
                    checks["repository_unchanged"] = "failed"
                    result["error"] = {
                        "type": "RepositoryChanged",
                        "message": "The repository status changed during comparison.",
                    }
                    exit_code = exit_codes.TEST_FAILURE
            except (GitCommandError, OSError) as error:
                checks["repository_unchanged"] = "failed"
                result["error"] = {"type": type(error).__name__, "message": str(error)}
                exit_code = exit_codes.TEST_FAILURE

    return finish(result, output_json, output_markdown, exit_code, started)


if __name__ == "__main__":
    sys.exit(main())
