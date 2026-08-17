"""
Convert an L5X file to an ACD file using the Logix Designer SDK.

Runs inside the interactive desktop session via the LD-SDK-Run scheduled task.
The L5X is generated build output (from l5xplode implode), so it is opened
directly -- no disposable copy is needed.

Runs under the logged-in user context (BMCD\\csmith or logix-runner) because
the Logix Designer SDK requires an active interactive Windows session.

Exit codes:
    0  SUCCESS             -- ACD written and verified non-empty
    1  TEST_FAILURE        -- SDK error during open or save
    2  CONFIGURATION_ERROR -- bad arguments, missing file, wrong Python version
    3  DEPENDENCY_ERROR    -- SDK import failed
    4  SERVICE_UNAVAILABLE -- SDK operation timed out
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import platform
import sys
import time
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any

PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from common import exit_codes  # noqa: E402
from common.logging_config import configure_logging  # noqa: E402
from common.result import write_json_result  # noqa: E402

LOGGER = logging.getLogger("l5x_to_acd")
DEFAULT_OUTPUT = PYTHON_ROOT / "artifacts" / "l5x-to-acd.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert an L5X file to an ACD file using the Logix Designer SDK."
    )
    parser.add_argument(
        "--l5x",
        required=True,
        type=Path,
        help="Input L5X file path.",
    )
    parser.add_argument(
        "--acd",
        required=True,
        type=Path,
        help="Output ACD file path. Parent directories are created. Overwritten if it exists.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="JSON artifact path. Default: python/artifacts/l5x-to-acd.json.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=300.0,
        help="Maximum seconds for the open + save_as operation. Default: 300.",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be greater than zero")
    if args.timeout_seconds > 600:
        parser.error("--timeout-seconds cannot exceed 600 seconds")
    return args


def base_result() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "operation": "l5x_to_acd",
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "host": platform.node(),
        "python_version": platform.python_version(),
        "checks": {
            "input_validation": "not_run",
            "sdk_import": "not_run",
            "project_open": "not_run",
            "project_save": "not_run",
            "acd_written": "not_run",
            "project_close": "not_run",
        },
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
        LOGGER.exception("Unable to write JSON result artifact")
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
        return exit_codes.CONFIGURATION_ERROR
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    LOGGER.info(
        "l5x_to_acd finished",
        extra={
            "event": "finished",
            "exit_code": exit_code,
            "elapsed_seconds": result["elapsed_seconds"],
        },
    )
    return exit_code


async def convert(
    logix_project_type: Any,
    l5x_path: Path,
    acd_path: Path,
    checks: dict[str, str],
) -> None:
    """Open the L5X and save as ACD. Closes the project in a finally block."""
    project = None
    try:
        LOGGER.info(
            "Opening L5X",
            extra={"event": "project_open_started", "l5x_path": str(l5x_path)},
        )
        project = await logix_project_type.open_logix_project(str(l5x_path))
        checks["project_open"] = "passed"

        acd_path.parent.mkdir(parents=True, exist_ok=True)
        LOGGER.info(
            "Saving as ACD",
            extra={"event": "project_save_started", "acd_path": str(acd_path)},
        )
        await project.save_as(str(acd_path), force=True)
        checks["project_save"] = "passed"

        acd_size = acd_path.stat().st_size if acd_path.exists() else 0
        if acd_size == 0:
            raise RuntimeError(
                f"ACD was written but is empty at '{acd_path}' -- "
                "conversion may have failed silently."
            )
        checks["acd_written"] = "passed"
    finally:
        if project is not None:
            LOGGER.info("Closing project", extra={"event": "project_close_started"})
            try:
                project.close()
                checks["project_close"] = "passed"
            except Exception as err:
                checks["project_close"] = "failed"
                LOGGER.warning("Project close failed: %s", err)


def main() -> int:
    args = parse_args()
    configure_logging(args.verbose)
    started = time.monotonic()
    result = base_result()
    checks: dict[str, str] = result["checks"]

    # ---- input validation ----
    try:
        l5x_path = args.l5x.resolve(strict=True)
        if l5x_path.suffix.lower() != ".l5x":
            raise ValueError(
                f"--l5x must be an .l5x file, got suffix '{l5x_path.suffix}'"
            )
        acd_path = args.acd.resolve()
        if acd_path.suffix.lower() != ".acd":
            raise ValueError(
                f"--acd must be an .acd file, got suffix '{acd_path.suffix}'"
            )
        result["l5x_path"] = str(l5x_path)
        result["l5x_size_bytes"] = l5x_path.stat().st_size
        result["acd_path"] = str(acd_path)
        checks["input_validation"] = "passed"
    except (OSError, ValueError) as err:
        checks["input_validation"] = "failed"
        result["error"] = {"type": type(err).__name__, "message": str(err)}
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

    # ---- SDK import ----
    try:
        from logix_designer_sdk import LogixProject
        from logix_designer_sdk.exceptions import LogixSdkError

        result["sdk_version"] = metadata.version("logix-designer-sdk")
        checks["sdk_import"] = "passed"
    except Exception as err:
        LOGGER.exception("Logix Designer SDK import failed")
        checks["sdk_import"] = "failed"
        result["error"] = {"type": type(err).__name__, "message": str(err)}
        return finish(result, args.output, exit_codes.DEPENDENCY_ERROR, started)

    # ---- conversion ----
    exit_code = exit_codes.SUCCESS
    try:
        asyncio.run(
            asyncio.wait_for(
                convert(LogixProject, l5x_path, acd_path, checks),
                timeout=args.timeout_seconds,
            )
        )
        result["acd_size_bytes"] = acd_path.stat().st_size if acd_path.exists() else 0
    except TimeoutError:
        LOGGER.exception("SDK operation timed out")
        result["error"] = {
            "type": "TimeoutError",
            "message": (
                f"Timed out after {int(args.timeout_seconds)}s waiting for "
                "LogixProject.open_logix_project / save_as to complete."
            ),
        }
        exit_code = exit_codes.SERVICE_UNAVAILABLE
    except LogixSdkError as err:
        LOGGER.exception("Logix Designer SDK reported an error")
        result["error"] = {"type": type(err).__name__, "message": str(err)}
        exit_code = exit_codes.TEST_FAILURE
    except Exception as err:
        LOGGER.exception("Unexpected error during L5X->ACD conversion")
        result["error"] = {"type": type(err).__name__, "message": str(err)}
        exit_code = exit_codes.TEST_FAILURE

    return finish(result, args.output, exit_code, started)


if __name__ == "__main__":
    sys.exit(main())
