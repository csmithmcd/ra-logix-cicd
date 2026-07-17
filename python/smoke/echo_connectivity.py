"""Read-only connectivity smoke test for FactoryTalk Logix Echo 4.0."""

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
from common.result import summarize_public_attributes, write_json_result  # noqa: E402


LOGGER = logging.getLogger("echo_connectivity")
EMPTY_GUID = "00000000-0000-0000-0000-000000000000"
DEFAULT_OUTPUT = PYTHON_ROOT / "artifacts" / "echo-connectivity.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run read-only connectivity checks against FactoryTalk Logix Echo."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="JSON artifact path. Defaults to python/artifacts/echo-connectivity.json.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=30.0,
        help="Maximum duration for all Echo API calls. Defaults to 30 seconds.",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be greater than zero")
    return args


def base_result() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "smoke_test": "factorytalk_logix_echo_read_only_connectivity",
        "read_only": True,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "host": platform.node(),
        "python_version": platform.python_version(),
        "checks": {},
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
    except OSError as error:
        LOGGER.exception(
            "Unable to write the JSON result artifact",
            extra={"event": "result_write_failed", "exit_code": exit_codes.CONFIGURATION_ERROR},
        )
        result["status"] = "failed"
        result["exit_code"] = exit_codes.CONFIGURATION_ERROR
        result["error"] = {
            "type": type(error).__name__,
            "message": str(error),
        }
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
        return exit_codes.CONFIGURATION_ERROR

    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    LOGGER.info(
        "Echo connectivity smoke test finished",
        extra={
            "event": "smoke_finished",
            "exit_code": exit_code,
            "elapsed_seconds": result["elapsed_seconds"],
        },
    )
    return exit_code


async def run_read_only_probe(service_api_client_type: Any) -> dict[str, Any]:
    LOGGER.info("Creating Echo service client", extra={"event": "service_client_create"})
    service_client = service_api_client_type.create()

    LOGGER.info("Reading Echo product information", extra={"event": "product_info_read"})
    product_info = await service_client.get_product_info()

    LOGGER.info("Reading Echo license status", extra={"event": "license_info_read"})
    license_info = await service_client.get_license_info()

    LOGGER.info("Listing Echo chassis", extra={"event": "chassis_list_read"})
    chassis = list(await service_client.list_chassis())

    LOGGER.info("Listing chassis-less controllers", extra={"event": "compact_controller_list_read"})
    compact_controllers = list(await service_client.list_controllers(EMPTY_GUID))

    chassis_controller_count = 0
    for chassis_item in chassis:
        controllers = await service_client.list_controllers(chassis_item.chassis_guid)
        chassis_controller_count += len(list(controllers))

    return {
        "product_info": summarize_public_attributes(product_info),
        "license_info": summarize_public_attributes(license_info),
        "inventory_counts": {
            "chassis": len(chassis),
            "chassis_less_controllers": len(compact_controllers),
            "controllers_in_chassis": chassis_controller_count,
            "controllers_total": len(compact_controllers) + chassis_controller_count,
        },
    }


def main() -> int:
    args = parse_args()
    configure_logging(args.verbose)
    started = time.monotonic()
    result = base_result()

    if not ((3, 12) <= sys.version_info[:2] < (3, 14)):
        result["error"] = {
            "type": "UnsupportedPythonVersion",
            "message": "ftecho_sdk 4.0.0 requires Python 3.12 or 3.13; Python 3.12 is recommended.",
        }
        return finish(result, args.output, exit_codes.CONFIGURATION_ERROR, started)

    try:
        from ftecho_sdk import FTEchoSdkError, ServiceApiClientV2

        result["sdk_version"] = metadata.version("ftecho_sdk")
        result["checks"]["sdk_import"] = "passed"
    except Exception as error:
        LOGGER.exception("Unable to import the Echo SDK", extra={"event": "sdk_import_failed"})
        result["checks"]["sdk_import"] = "failed"
        result["error"] = {"type": type(error).__name__, "message": str(error)}
        return finish(result, args.output, exit_codes.DEPENDENCY_ERROR, started)

    try:
        probe_result = asyncio.run(
            asyncio.wait_for(
                run_read_only_probe(ServiceApiClientV2),
                timeout=args.timeout_seconds,
            )
        )
        result["checks"].update(
            {
                "service_connectivity": "passed",
                "product_info_read": "passed",
                "license_info_read": "passed",
                "inventory_read": "passed",
            }
        )
        result.update(probe_result)
        return finish(result, args.output, exit_codes.SUCCESS, started)
    except TimeoutError as error:
        LOGGER.exception("Echo API calls timed out", extra={"event": "service_timeout"})
        result["checks"]["service_connectivity"] = "failed"
        result["error"] = {"type": type(error).__name__, "message": str(error)}
        return finish(result, args.output, exit_codes.SERVICE_UNAVAILABLE, started)
    except FTEchoSdkError as error:
        LOGGER.exception("Echo SDK reported an error", extra={"event": "service_error"})
        result["checks"]["service_connectivity"] = "failed"
        result["error"] = {"type": type(error).__name__, "message": str(error)}
        return finish(result, args.output, exit_codes.SERVICE_UNAVAILABLE, started)
    except Exception as error:
        LOGGER.exception("Unexpected smoke-test failure", extra={"event": "unexpected_error"})
        result["error"] = {"type": type(error).__name__, "message": str(error)}
        return finish(result, args.output, exit_codes.TEST_FAILURE, started)


if __name__ == "__main__":
    sys.exit(main())
