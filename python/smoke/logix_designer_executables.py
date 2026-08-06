"""Read-only Logix Designer SDK open-project probe using a disposable copy."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any, Iterable

PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from common import exit_codes  # noqa: E402
from common.logging_config import configure_logging  # noqa: E402
from common.result import summarize_public_attributes, write_json_result  # noqa: E402


LOGGER = logging.getLogger("logix_designer_open_project")
DEFAULT_OUTPUT = PYTHON_ROOT / "artifacts" / "logix-designer-executables.json"
DEFAULT_WORK_ROOT = PYTHON_ROOT / "artifacts" / "logix-designer-work"
SUPPORTED_PROJECT_EXTENSIONS = {".acd", ".l5x"}


class ProjectCloseError(RuntimeError):
    """Raised when the SDK project cannot be closed cleanly."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Open a disposable ACD/L5X copy with the Logix Designer SDK."
        )
    )
    parser.add_argument(
        "--project",
        required=True,
        type=Path,
        help="Source ACD or L5X project. The SDK opens a temporary copy, never this file.",
    )
    parser.add_argument(
        "--enumerate-executables",
        action="store_true",
        help=(
            "Read executable metadata from the disposable copy after it opens. "
            "Disabled by default."
        ),
    )
    parser.add_argument(
        "--project-inventory",
        action="store_true",
        help=(
            "Read the communications path and executable inventory from the "
            "disposable copy. Implies --enumerate-executables. Disabled by default."
        ),
    )
    parser.add_argument(
        "--build-validation",
        action="store_true",
        help=(
            "Build the disposable copy using the SDK default target without saving it. "
            "Disabled by default."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="JSON artifact path. Defaults to python/artifacts/logix-designer-executables.json.",
    )
    parser.add_argument(
        "--work-root",
        type=Path,
        default=DEFAULT_WORK_ROOT,
        help="Parent folder for the disposable project copy.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=180.0,
        help="Maximum duration for the SDK open/close operation. Defaults to 180 seconds.",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be greater than zero")
    if args.timeout_seconds > 600:
        parser.error("--timeout-seconds cannot exceed 600 seconds")
    return args


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def summarize_executables(executables: Iterable[Any]) -> list[dict[str, object]]:
    """Return deterministic, secret-filtered executable summaries."""

    summaries: list[dict[str, object]] = []
    for executable in executables:
        if executable is None or isinstance(executable, (bool, float, int, str)):
            summary: dict[str, object] = {
                "type": type(executable).__name__,
                "value": executable,
            }
        else:
            summary = {
                "type": type(executable).__name__,
                "attributes": summarize_public_attributes(executable),
            }
        summaries.append(summary)

    return sorted(
        summaries,
        key=lambda value: json.dumps(value, default=str, sort_keys=True),
    )


def base_result() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "smoke_test": "logix_designer_read_only_open_project",
        "read_only": True,
        "working_copy_used": True,
        "executable_enumeration_enabled": False,
        "project_inventory_enabled": False,
        "build_validation_enabled": False,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "host": platform.node(),
        "python_version": platform.python_version(),
        "checks": {
            "input_validation": "not_run",
            "sdk_import": "not_run",
            "working_copy": "not_run",
            "project_open_started": "not_run",
            "project_open_completed": "not_run",
            "get_communications_path_started": "not_run",
            "get_communications_path_completed": "not_run",
            "get_all_executables_started": "not_run",
            "get_all_executables_completed": "not_run",
            "build_started": "not_run",
            "build_completed": "not_run",
            "project_close_started": "not_run",
            "project_close_completed": "not_run",
            "source_unchanged": "not_run",
            "working_copy_cleanup": "not_run",
        },
    }


def _normalise_seconds(value: float) -> int | float:
    return int(value) if value.is_integer() else value


def operation_description(operation: str) -> str:
    """Return a stable SDK operation label for failure artifacts."""

    labels = {
        "open_logix_project": "LogixProject.open_logix_project",
        "get_communications_path": "LogixProject.get_communications_path",
        "get_all_executables": "LogixProject.get_all_executables",
        "build": "LogixProject.build",
        "close_project": "LogixProject.close",
    }
    return labels.get(operation, operation)


def capture_windows_diagnostics(
    source_path: Path,
    working_copy: Path,
    current_operation: str,
) -> dict[str, Any]:
    """Capture bounded Windows evidence for a stalled SDK operation."""

    diagnostics: dict[str, Any] = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "current_operation": current_operation,
        "source_project_path": str(source_path),
        "working_copy_path": str(working_copy),
    }
    if sys.platform != "win32":
        diagnostics["capture_status"] = "unsupported_platform"
        return diagnostics

    powershell_script = r"""
$ErrorActionPreference = 'SilentlyContinue'
$currentProcess = Get-Process -Id $PID
$serverProcesses = @(
    Get-CimInstance Win32_Process -Filter "Name = 'LdSdkServer.exe'" |
        ForEach-Object {
            $runtime = Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue
            [pscustomobject]@{
                pid = [int]$_.ProcessId
                parent_pid = [int]$_.ParentProcessId
                executable_path = $_.ExecutablePath
                command_line = $_.CommandLine
                session_id = if ($runtime) { [int]$runtime.SessionId } else { $null }
                start_time_utc = if ($runtime) { $runtime.StartTime.ToUniversalTime().ToString('o') } else { $null }
                cpu_seconds = if ($runtime) { $runtime.CPU } else { $null }
                working_set_bytes = if ($runtime) { [int64]$runtime.WorkingSet64 } else { $null }
                responding = if ($runtime) { [bool]$runtime.Responding } else { $null }
            }
        }
)
$connections = @(
    Get-NetTCPConnection -LocalPort 53204 -ErrorAction SilentlyContinue |
        ForEach-Object {
            [pscustomobject]@{
                local_address = $_.LocalAddress
                local_port = [int]$_.LocalPort
                remote_address = $_.RemoteAddress
                remote_port = [int]$_.RemotePort
                state = [string]$_.State
                owning_pid = [int]$_.OwningProcess
            }
        }
)
$versions = @(
    Get-ItemProperty `
        'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*', `
        'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*' |
        Where-Object { $_.DisplayName -match 'Studio 5000|Logix Designer' } |
        ForEach-Object {
            [pscustomobject]@{
                display_name = $_.DisplayName
                version = $_.DisplayVersion
                install_location = $_.InstallLocation
            }
        }
)
[pscustomobject]@{
    windows_identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    session_name = $env:SESSIONNAME
    session_id = [int]$currentProcess.SessionId
    ld_sdk_servers = $serverProcesses
    tcp_53204_connections = $connections
    installed_logix_designer_versions = $versions
} | ConvertTo-Json -Depth 6 -Compress
"""

    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", powershell_script],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if completed.returncode != 0:
            diagnostics["capture_status"] = "failed"
            diagnostics["capture_error"] = completed.stderr.strip()
            return diagnostics

        captured = json.loads(completed.stdout)
        if isinstance(captured, dict):
            diagnostics.update(captured)
        diagnostics["capture_status"] = "passed"
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        diagnostics["capture_status"] = "failed"
        diagnostics["capture_error"] = f"{type(error).__name__}: {error}"
    return diagnostics


def finish(
    result: dict[str, Any],
    output_path: Path,
    exit_code: int,
    started: float,
) -> int:
    result["exit_code"] = exit_code
    result["status"] = "passed" if exit_code == exit_codes.SUCCESS else "failed"
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    result["finished_at_utc"] = datetime.now(timezone.utc).isoformat()

    try:
        write_json_result(output_path.resolve(), result)
    except OSError as error:
        LOGGER.exception(
            "Unable to write the JSON result artifact",
            extra={"event": "result_write_failed", "exit_code": exit_codes.CONFIGURATION_ERROR},
        )
        result["status"] = "failed"
        result["exit_code"] = exit_codes.CONFIGURATION_ERROR
        result["error"] = {"type": type(error).__name__, "message": str(error)}
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
        return exit_codes.CONFIGURATION_ERROR

    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    LOGGER.info(
        "Logix Designer open-project smoke test finished",
        extra={
            "event": "smoke_finished",
            "exit_code": exit_code,
            "elapsed_seconds": result["elapsed_seconds"],
        },
    )
    return exit_code


async def run_read_only_probe(
    logix_project_type: Any,
    event_logger_type: Any,
    project_copy: Path,
    checks: dict[str, str],
    operation_state: dict[str, str],
    enumerate_executables: bool,
    project_inventory: bool,
    build_validation: bool,
) -> dict[str, Any]:
    project = None
    operation_state["current_operation"] = "open_logix_project"
    checks["project_open_started"] = "passed"
    LOGGER.info(
        "Opening disposable project copy", extra={"event": "project_open_started"}
    )
    try:
        project = await logix_project_type.open_logix_project(
            str(project_copy), event_logger_type()
        )
        checks["project_open_completed"] = "passed"
        operation_state["current_operation"] = "project_opened"
        inventory: dict[str, Any] = {}
        if project_inventory:
            operation_state["current_operation"] = "get_communications_path"
            checks["get_communications_path_started"] = "passed"
            LOGGER.info(
                "Reading disposable project communications path",
                extra={"event": "get_communications_path_started"},
            )
            inventory["communications_path"] = await project.get_communications_path()
            checks["get_communications_path_completed"] = "passed"
            operation_state["current_operation"] = "project_opened"
        if enumerate_executables:
            operation_state["current_operation"] = "get_all_executables"
            checks["get_all_executables_started"] = "passed"
            LOGGER.info(
                "Reading disposable project executables",
                extra={"event": "get_all_executables_started"},
            )
            executables = await project.get_all_executables()
            checks["get_all_executables_completed"] = "passed"
            operation_state["current_operation"] = "project_opened"
            inventory["executables"] = summarize_executables(executables)
        if build_validation:
            operation_state["current_operation"] = "build"
            checks["build_started"] = "passed"
            LOGGER.info(
                "Building disposable project copy",
                extra={"event": "build_started"},
            )
            await project.build()
            checks["build_completed"] = "passed"
            operation_state["current_operation"] = "project_opened"
        return inventory
    finally:
        if project is not None:
            interrupted_operation = operation_state["current_operation"]
            operation_state["current_operation"] = "close_project"
            checks["project_close_started"] = "passed"
            LOGGER.info(
                "Closing disposable project", extra={"event": "project_close_started"}
            )
            try:
                project.close()
                checks["project_close_completed"] = "passed"
                operation_state["current_operation"] = (
                    "completed"
                    if interrupted_operation == "project_opened"
                    else interrupted_operation
                )
            except Exception as error:
                checks["project_close_completed"] = "failed"
                raise ProjectCloseError(str(error)) from error


def main() -> int:
    args = parse_args()
    configure_logging(args.verbose)
    started = time.monotonic()
    result = base_result()
    enumerate_executables = args.enumerate_executables or args.project_inventory
    result["executable_enumeration_enabled"] = enumerate_executables
    result["project_inventory_enabled"] = args.project_inventory
    result["build_validation_enabled"] = args.build_validation
    if args.build_validation:
        result["smoke_test"] = "logix_designer_offline_build_validation"
    elif args.project_inventory:
        result["smoke_test"] = "logix_designer_read_only_project_inventory"
    elif args.enumerate_executables:
        result["smoke_test"] = "logix_designer_read_only_get_all_executables"
    checks: dict[str, str] = result["checks"]
    operation_state = {"current_operation": "input_validation"}

    try:
        source_path = args.project.resolve(strict=True)
        if not source_path.is_file():
            raise FileNotFoundError(f"Project is not a file: '{source_path}'")
        if source_path.suffix.lower() not in SUPPORTED_PROJECT_EXTENSIONS:
            raise ValueError("--project must reference an ACD or L5X file")
        source_sha256 = file_sha256(source_path)
        source_size = source_path.stat().st_size
        checks["input_validation"] = "passed"
        result["project"] = {
            "file_name": source_path.name,
            "file_type": source_path.suffix.upper().lstrip("."),
            "sha256": source_sha256,
            "size_bytes": source_size,
        }
    except (OSError, ValueError) as error:
        checks["input_validation"] = "failed"
        result["error"] = {"type": type(error).__name__, "message": str(error)}
        return finish(result, args.output, exit_codes.CONFIGURATION_ERROR, started)

    if not ((3, 12) <= sys.version_info[:2] < (3, 14)):
        result["error"] = {
            "type": "UnsupportedPythonVersion",
            "message": (
                "logix-designer-sdk 2.0.2 requires Python 3.12 or 3.13; "
                "Python 3.13 is the validated baseline."
            ),
        }
        return finish(result, args.output, exit_codes.CONFIGURATION_ERROR, started)

    try:
        from logix_designer_sdk import LogixProject, StdOutEventLogger
        from logix_designer_sdk.exceptions import LogixSdkError

        result["sdk_version"] = metadata.version("logix-designer-sdk")
        checks["sdk_import"] = "passed"
    except Exception as error:
        LOGGER.exception(
            "Unable to import the Logix Designer SDK",
            extra={"event": "sdk_import_failed"},
        )
        checks["sdk_import"] = "failed"
        result["error"] = {"type": type(error).__name__, "message": str(error)}
        return finish(result, args.output, exit_codes.DEPENDENCY_ERROR, started)

    exit_code = exit_codes.SUCCESS
    working_directory: Path | None = None
    project_copy: Path | None = None
    try:
        operation_state["current_operation"] = "working_copy"
        work_root = args.work_root.resolve()
        work_root.mkdir(parents=True, exist_ok=True)
        working_directory = Path(
            tempfile.mkdtemp(prefix="logix-designer-smoke-", dir=work_root)
        )
        project_copy = working_directory / source_path.name
        shutil.copyfile(source_path, project_copy)
        if file_sha256(project_copy) != source_sha256:
            raise OSError("The disposable project copy failed SHA256 verification")
        checks["working_copy"] = "passed"

        inventory = asyncio.run(
            asyncio.wait_for(
                run_read_only_probe(
                    LogixProject,
                    StdOutEventLogger,
                    project_copy,
                    checks,
                    operation_state,
                    enumerate_executables,
                    args.project_inventory,
                    args.build_validation,
                ),
                timeout=args.timeout_seconds,
            )
        )
        if "communications_path" in inventory:
            result["communications_path"] = inventory["communications_path"]
        if "executables" in inventory:
            result["executables"] = inventory["executables"]
            result["executables_count"] = len(inventory["executables"])
    except TimeoutError as error:
        LOGGER.exception(
            "Logix Designer SDK operation timed out", extra={"event": "sdk_timeout"}
        )
        operation = operation_state["current_operation"]
        if operation == "open_logix_project":
            checks["project_open_completed"] = "failed"
        elif operation == "get_communications_path":
            checks["get_communications_path_completed"] = "failed"
        elif operation == "get_all_executables":
            checks["get_all_executables_completed"] = "failed"
        elif operation == "build":
            checks["build_completed"] = "failed"
        timeout_seconds = _normalise_seconds(args.timeout_seconds)
        result["error"] = {
            "type": type(error).__name__,
            "message": (
                f"Timed out after {timeout_seconds} seconds while waiting for "
                f"{operation_description(operation)}."
            ),
            "operation": operation,
            "timeout_seconds": timeout_seconds,
        }
        if project_copy is not None:
            result["diagnostics"] = capture_windows_diagnostics(
                source_path,
                project_copy,
                operation,
            )
        exit_code = exit_codes.SERVICE_UNAVAILABLE
    except ProjectCloseError as error:
        LOGGER.exception(
            "Logix Designer project cleanup failed",
            extra={"event": "project_close_failed"},
        )
        result["error"] = {"type": type(error).__name__, "message": str(error)}
        exit_code = exit_codes.CLEANUP_FAILURE
    except LogixSdkError as error:
        LOGGER.exception(
            "Logix Designer SDK reported an error", extra={"event": "sdk_error"}
        )
        operation = operation_state["current_operation"]
        if operation == "open_logix_project":
            checks["project_open_completed"] = "failed"
        elif operation == "get_communications_path":
            checks["get_communications_path_completed"] = "failed"
        elif operation == "get_all_executables":
            checks["get_all_executables_completed"] = "failed"
        elif operation == "build":
            checks["build_completed"] = "failed"
        result["error"] = {
            "type": type(error).__name__,
            "message": str(error),
            "operation": operation,
        }
        if "timed out" in str(error).lower() and project_copy is not None:
            result["diagnostics"] = capture_windows_diagnostics(
                source_path,
                project_copy,
                operation,
            )
        exit_code = exit_codes.SERVICE_UNAVAILABLE
    except OSError as error:
        LOGGER.exception(
            "Unable to create or verify the disposable project copy",
            extra={"event": "working_copy_failed"},
        )
        checks["working_copy"] = "failed"
        result["error"] = {"type": type(error).__name__, "message": str(error)}
        exit_code = exit_codes.CONFIGURATION_ERROR
    except Exception as error:
        LOGGER.exception(
            "Unexpected Logix Designer smoke-test failure",
            extra={"event": "unexpected_error"},
        )
        operation = operation_state["current_operation"]
        if operation == "open_logix_project":
            checks["project_open_completed"] = "failed"
        elif operation == "get_communications_path":
            checks["get_communications_path_completed"] = "failed"
        elif operation == "get_all_executables":
            checks["get_all_executables_completed"] = "failed"
        elif operation == "build":
            checks["build_completed"] = "failed"
        result["error"] = {
            "type": type(error).__name__,
            "message": str(error),
            "operation": operation,
        }
        exit_code = exit_codes.TEST_FAILURE
    finally:
        try:
            if file_sha256(source_path) == source_sha256:
                checks["source_unchanged"] = "passed"
            else:
                checks["source_unchanged"] = "failed"
                result["error"] = {
                    "type": "SourceProjectChanged",
                    "message": "The source project SHA256 changed during the smoke test.",
                }
                exit_code = exit_codes.TEST_FAILURE
        except OSError as error:
            checks["source_unchanged"] = "failed"
            result["error"] = {"type": type(error).__name__, "message": str(error)}
            exit_code = exit_codes.TEST_FAILURE

        if working_directory is not None:
            try:
                shutil.rmtree(working_directory)
                checks["working_copy_cleanup"] = "passed"
            except OSError as error:
                checks["working_copy_cleanup"] = "failed"
                result["error"] = {"type": type(error).__name__, "message": str(error)}
                exit_code = exit_codes.CLEANUP_FAILURE

    return finish(result, args.output, exit_code, started)


if __name__ == "__main__":
    sys.exit(main())
