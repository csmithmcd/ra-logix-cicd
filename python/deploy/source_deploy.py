"""
Source-to-Echo pipeline -- Phase 9.

Orchestrates three sequential steps from a single process running as
NT AUTHORITY/SYSTEM:

  Step 1 -- Implode  (l5xplode, no SDK required)
      Reads the exploded XML source tree from --source-dir and writes
      python/artifacts/build/Controller.L5X.

  Step 2 -- L5X -> ACD  (Logix Designer SDK, interactive session required)
      Writes a request to C:\\data\\ld-runner\\request.json and triggers the
      "LD-SDK-Run" Windows Scheduled Task, which runs l5x_to_acd.py in the
      active console session. Polls for result.exitcode. On success,
      python/artifacts/build/Controller.ACD exists.

  Step 3 -- Echo deploy  (ftecho_sdk, no interactive session required)
      Calls echo_deploy.py via subprocess to create CICD_TEST_01, download
      Controller.ACD, verify HARD_RUN, and delete the controller and chassis.

Exit codes:
    0  SUCCESS             -- all three steps passed
    1  TEST_FAILURE        -- step failed (non-zero subprocess exit)
    2  CONFIGURATION_ERROR -- bad arguments or missing prerequisites
    5  CLEANUP_FAILURE     -- echo_deploy.py exited 5 (manual intervention needed)
"""

from __future__ import annotations

import argparse
import json
import logging
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from common import exit_codes  # noqa: E402
from common.logging_config import configure_logging  # noqa: E402
from common.result import write_json_result  # noqa: E402

LOGGER = logging.getLogger("source_deploy")

# LD-SDK-Run runner directory (shared with invoke_ld_sdk_*_interactive.ps1)
RUNNER_DIR = Path(r"C:\data\ld-runner")
REQUEST_FILE = RUNNER_DIR / "request.json"
RESULT_FILE = RUNNER_DIR / "result.exitcode"

# Default paths derived from script location
_HERE = Path(__file__).resolve().parent
DEFAULT_L5X_TO_ACD_SCRIPT = _HERE.parent / "build" / "l5x_to_acd.py"
DEFAULT_ECHO_DEPLOY_SCRIPT = _HERE / "echo_deploy.py"

DEFAULT_SOURCE_DIR = PYTHON_ROOT.parent / "1-production-files" / "Source"
DEFAULT_BUILD_DIR = PYTHON_ROOT / "artifacts" / "build"
DEFAULT_OUTPUT = PYTHON_ROOT / "artifacts" / "source-deploy.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Implode exploded XML source to L5X, convert to ACD via Logix Designer SDK, "
            "then download to a disposable Echo controller."
        )
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=DEFAULT_SOURCE_DIR,
        help=(
            "Root of the exploded source tree (contains RSLogix5000Content/). "
            "Default: 1-production-files/Source."
        ),
    )
    parser.add_argument(
        "--l5xplode",
        type=Path,
        required=True,
        help="Path to l5xplode.exe.",
    )
    parser.add_argument(
        "--ld-python-exe",
        type=Path,
        required=True,
        help=(
            "Python 3.13 executable inside the logix-designer venv. "
            "Used by the LD-SDK-Run task to run l5x_to_acd.py."
        ),
    )
    parser.add_argument(
        "--l5x-to-acd-script",
        type=Path,
        default=DEFAULT_L5X_TO_ACD_SCRIPT,
        help=(
            "Path to l5x_to_acd.py. "
            "Default: python/build/l5x_to_acd.py relative to this script."
        ),
    )
    parser.add_argument(
        "--echo-deploy-script",
        type=Path,
        default=DEFAULT_ECHO_DEPLOY_SCRIPT,
        help=(
            "Path to echo_deploy.py. "
            "Default: echo_deploy.py in the same directory as source_deploy.py."
        ),
    )
    parser.add_argument(
        "--build-dir",
        type=Path,
        default=DEFAULT_BUILD_DIR,
        help=(
            "Directory for intermediate build artifacts (Controller.L5X, Controller.ACD). "
            "Default: python/artifacts/build."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="JSON pipeline summary artifact. Default: python/artifacts/source-deploy.json.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=300.0,
        help="Per-step timeout in seconds. Default: 300.",
    )
    parser.add_argument(
        "--task-name",
        default="LD-SDK-Run",
        help="Name of the LD-SDK-Run Windows Scheduled Task. Default: LD-SDK-Run.",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be greater than zero")
    return args


# --------------------------------------------------------------------------- #
# LD-SDK-Run helper
# --------------------------------------------------------------------------- #

def trigger_ld_sdk_task(
    python_exe: str,
    script_path: str,
    script_args: list[str],
    timeout_seconds: int,
    task_name: str,
) -> int:
    """
    Write request.json, fire the scheduled task, and poll for result.exitcode.

    Returns the integer exit code written by l5x_to_acd.py (0 = success).
    Raises RuntimeError if schtasks fails.
    Raises TimeoutError if the task does not complete within timeout_seconds + 120.
    """
    request = {
        "python_exe": python_exe,
        "script_path": script_path,
        "script_args": script_args,
        "timeout_seconds": timeout_seconds,
        "requested_at": datetime.now(timezone.utc).isoformat(),
    }
    RUNNER_DIR.mkdir(parents=True, exist_ok=True)
    REQUEST_FILE.write_text(
        json.dumps(request, indent=2, ensure_ascii=True), encoding="utf-8"
    )
    LOGGER.info(
        "LD-SDK request written",
        extra={"event": "ld_sdk_request_written", "task": task_name},
    )

    RESULT_FILE.unlink(missing_ok=True)

    proc = subprocess.run(
        ["schtasks.exe", "/run", "/tn", task_name],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"schtasks /run /tn '{task_name}' failed (exit {proc.returncode}): "
            f"{proc.stderr.strip()}"
        )
    LOGGER.info(
        "Scheduled task triggered; polling for result...",
        extra={"event": "ld_sdk_task_triggered"},
    )

    deadline = time.monotonic() + timeout_seconds + 120
    poll_interval = 5
    while time.monotonic() < deadline:
        time.sleep(poll_interval)
        if RESULT_FILE.exists():
            raw = RESULT_FILE.read_text(encoding="ascii").strip()
            code = int(raw)
            LOGGER.info(
                "LD-SDK task completed",
                extra={"event": "ld_sdk_task_completed", "exit_code": code},
            )
            return code
        poll_interval = min(poll_interval + 1, 15)

    raise TimeoutError(
        f"Timed out after {timeout_seconds + 120}s waiting for task "
        f"'{task_name}' to write result.exitcode."
    )


# --------------------------------------------------------------------------- #
# Result helpers
# --------------------------------------------------------------------------- #

def step_summary(
    status: str,
    exit_code: int,
    elapsed: float,
    **extra: Any,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "status": status,
        "exit_code": exit_code,
        "elapsed_seconds": round(elapsed, 3),
    }
    summary.update(extra)
    return summary


def base_result() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "pipeline": "source_deploy",
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "host": platform.node(),
        "python_version": platform.python_version(),
        "steps": {},
    }


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
    except OSError:
        LOGGER.exception("Unable to write pipeline summary JSON")
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
        return exit_codes.CONFIGURATION_ERROR
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    LOGGER.info(
        "source_deploy finished",
        extra={
            "event": "pipeline_finished",
            "exit_code": exit_code,
            "elapsed_seconds": result["elapsed_seconds"],
        },
    )
    return exit_code


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main() -> int:
    args = parse_args()
    configure_logging(args.verbose)
    started = time.monotonic()
    result = base_result()
    steps: dict[str, Any] = result["steps"]

    # ---- prerequisite paths ----
    try:
        source_dir = args.source_dir.resolve(strict=True)
        l5xplode = args.l5xplode.resolve(strict=True)
        ld_python_exe = args.ld_python_exe.resolve(strict=True)
        l5x_to_acd_script = args.l5x_to_acd_script.resolve(strict=True)
        echo_deploy_script = args.echo_deploy_script.resolve(strict=True)
        build_dir = args.build_dir.resolve()
        build_dir.mkdir(parents=True, exist_ok=True)
        result["source_dir"] = str(source_dir)
        result["build_dir"] = str(build_dir)
        result["l5xplode"] = str(l5xplode)
    except (OSError, FileNotFoundError) as err:
        result["error"] = {"type": type(err).__name__, "message": str(err)}
        LOGGER.error("Prerequisite check failed: %s", err)
        return finish(result, args.output, exit_codes.CONFIGURATION_ERROR, started)

    l5x_out = build_dir / "Controller.L5X"
    acd_out = build_dir / "Controller.ACD"
    l5x_to_acd_json = build_dir / "l5x-to-acd.json"
    echo_deploy_json = PYTHON_ROOT / "artifacts" / "echo-deploy.json"

    timeout = int(args.timeout_seconds)

    # ================================================================ Step 1: Implode
    LOGGER.info(
        "Step 1: implode",
        extra={"event": "step_implode_started", "source_dir": str(source_dir)},
    )
    step_start = time.monotonic()
    try:
        proc = subprocess.run(
            [
                str(l5xplode),
                "implode",
                "--dir", str(source_dir),
                "--l5x", str(l5x_out),
                "--force",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        if proc.stdout:
            LOGGER.info("[l5xplode] %s", proc.stdout.strip())
        if proc.stderr:
            LOGGER.warning("[l5xplode stderr] %s", proc.stderr.strip())
        implode_elapsed = time.monotonic() - step_start
        l5x_size = l5x_out.stat().st_size if l5x_out.exists() else 0
        if proc.returncode != 0:
            steps["implode"] = step_summary(
                "failed", proc.returncode, implode_elapsed,
                l5x_path=str(l5x_out),
                stderr=proc.stderr.strip(),
            )
            result["error"] = {
                "type": "ImplodeFailed",
                "message": f"l5xplode implode exited {proc.returncode}",
            }
            return finish(result, args.output, exit_codes.TEST_FAILURE, started)
        steps["implode"] = step_summary(
            "passed", 0, implode_elapsed,
            l5x_path=str(l5x_out),
            l5x_size_bytes=l5x_size,
        )
        LOGGER.info(
            "Implode succeeded",
            extra={"event": "step_implode_completed", "l5x_size_bytes": l5x_size},
        )
    except (subprocess.TimeoutExpired, OSError) as err:
        steps["implode"] = step_summary(
            "failed", exit_codes.TEST_FAILURE, time.monotonic() - step_start,
            error=str(err),
        )
        result["error"] = {"type": type(err).__name__, "message": str(err)}
        return finish(result, args.output, exit_codes.TEST_FAILURE, started)

    # ================================================================ Step 2: L5X -> ACD
    LOGGER.info(
        "Step 2: L5X -> ACD via LD-SDK-Run",
        extra={"event": "step_l5x_to_acd_started", "l5x_path": str(l5x_out)},
    )
    step_start = time.monotonic()
    try:
        script_args = [
            "--l5x", str(l5x_out),
            "--acd", str(acd_out),
            "--output", str(l5x_to_acd_json),
            "--timeout-seconds", str(timeout),
        ]
        task_exit = trigger_ld_sdk_task(
            python_exe=str(ld_python_exe),
            script_path=str(l5x_to_acd_script),
            script_args=script_args,
            timeout_seconds=timeout,
            task_name=args.task_name,
        )
        convert_elapsed = time.monotonic() - step_start
        acd_size = acd_out.stat().st_size if acd_out.exists() else 0
        if task_exit != 0:
            steps["l5x_to_acd"] = step_summary(
                "failed", task_exit, convert_elapsed,
                acd_path=str(acd_out),
            )
            result["error"] = {
                "type": "L5xToAcdFailed",
                "message": f"l5x_to_acd.py exited {task_exit} -- see {l5x_to_acd_json}",
            }
            return finish(result, args.output, exit_codes.TEST_FAILURE, started)
        steps["l5x_to_acd"] = step_summary(
            "passed", 0, convert_elapsed,
            acd_path=str(acd_out),
            acd_size_bytes=acd_size,
        )
        LOGGER.info(
            "L5X -> ACD succeeded",
            extra={"event": "step_l5x_to_acd_completed", "acd_size_bytes": acd_size},
        )
    except (RuntimeError, TimeoutError, OSError) as err:
        steps["l5x_to_acd"] = step_summary(
            "failed", exit_codes.SERVICE_UNAVAILABLE, time.monotonic() - step_start,
            error=str(err),
        )
        result["error"] = {"type": type(err).__name__, "message": str(err)}
        return finish(result, args.output, exit_codes.TEST_FAILURE, started)

    # ================================================================ Step 3: Echo deploy
    LOGGER.info(
        "Step 3: Echo deploy",
        extra={"event": "step_echo_deploy_started", "acd_path": str(acd_out)},
    )
    step_start = time.monotonic()
    try:
        proc = subprocess.run(
            [
                sys.executable,
                str(echo_deploy_script),
                "--acd", str(acd_out),
                "--output", str(echo_deploy_json),
                "--timeout-seconds", str(timeout),
            ],
            check=False,
            timeout=timeout + 120,
        )
        echo_elapsed = time.monotonic() - step_start
        steps["echo_deploy"] = step_summary(
            "passed" if proc.returncode == 0 else "failed",
            proc.returncode,
            echo_elapsed,
            echo_json=str(echo_deploy_json),
        )
        if proc.returncode not in (exit_codes.SUCCESS, exit_codes.CLEANUP_FAILURE):
            result["error"] = {
                "type": "EchoDeployFailed",
                "message": f"echo_deploy.py exited {proc.returncode} -- see {echo_deploy_json}",
            }
            return finish(result, args.output, exit_codes.TEST_FAILURE, started)
        LOGGER.info(
            "Echo deploy completed",
            extra={"event": "step_echo_deploy_completed", "exit_code": proc.returncode},
        )
        final_code = proc.returncode  # propagate CLEANUP_FAILURE (5) if present
    except (subprocess.TimeoutExpired, OSError) as err:
        steps["echo_deploy"] = step_summary(
            "failed", exit_codes.TEST_FAILURE, time.monotonic() - step_start,
            error=str(err),
        )
        result["error"] = {"type": type(err).__name__, "message": str(err)}
        return finish(result, args.output, exit_codes.TEST_FAILURE, started)

    return finish(result, args.output, final_code, started)


if __name__ == "__main__":
    sys.exit(main())
