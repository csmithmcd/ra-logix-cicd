"""
Echo controller lifecycle smoke test -- Phase 4.

Creates a disposable CICD_TEST_01 controller in FactoryTalk Logix Echo,
downloads ExampleForCICD_L85E.ACD to it, sets it to RUN mode, verifies the
controller is running, then deletes it unconditionally in a finally block.

Runs under NT AUTHORITY/SYSTEM (no interactive desktop required).
The Echo service is accessed via the SDK's TCP interface.

Exit codes:
    0  SUCCESS        -- controller created, downloaded, verified, deleted
    1  TEST_FAILURE   -- controller reached an unexpected state
    2  CONFIGURATION_ERROR -- bad arguments or missing ACD file
    3  DEPENDENCY_ERROR -- SDK import failed
    4  SERVICE_UNAVAILABLE -- cannot reach Echo service
    5  CLEANUP_FAILURE -- controller was not deleted; manual intervention required
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

LOGGER = logging.getLogger("echo_deploy")

CONTROLLER_NAME = "CICD_TEST_01"
CONTROLLER_DESCRIPTION = "Disposable CI/CD test controller -- deleted after each pipeline run"
CHASSIS_NAME = "CICD_TEST_CHASSIS"
CHASSIS_DESCRIPTION = "Disposable CI/CD test chassis -- deleted after each pipeline run"
EMPTY_GUID = "00000000-0000-0000-0000-000000000000"

DEFAULT_ACD = (
    PYTHON_ROOT.parent / "1-production-files" / "ACDs" / "ExampleForCICD_L85E.ACD"
)
DEFAULT_OUTPUT = PYTHON_ROOT / "artifacts" / "echo-deploy.json"
DOWNLOAD_POLL_INTERVAL = 3.0   # seconds between feedback checks
DOWNLOAD_MAX_WAIT = 300.0      # seconds before giving up on download
PROJECT_LOAD_TIMEOUT = 120.0   # seconds to wait for loaded_project_name to become non-empty
PROJECT_LOAD_POLL = 3.0        # seconds between loaded_project_name polls
MODE_SETTLE_WAIT = 5.0         # seconds to wait after setting RUN before reading back
DELETE_MAX_RETRIES = 4         # attempts before giving up on controller/chassis delete
DELETE_RETRY_DELAY = 8.0       # seconds between delete retries


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create a disposable Echo controller, download an ACD project, "
            "verify RUN mode, then delete the controller."
        )
    )
    parser.add_argument(
        "--acd",
        type=Path,
        default=DEFAULT_ACD,
        help="Path to the ACD project file to deploy. Defaults to ExampleForCICD_L85E.ACD.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="JSON artifact path.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=300.0,
        help="Maximum total lifecycle duration in seconds. Defaults to 300.",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be greater than zero")
    if args.timeout_seconds > 600:
        parser.error("--timeout-seconds cannot exceed 600")
    return args


def base_result(acd_path: Path) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "deploy_test": "echo_controller_lifecycle",
        "controller_name": CONTROLLER_NAME,
        "acd_path": str(acd_path),
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
    except OSError as err:
        LOGGER.exception(
            "Unable to write JSON artifact",
            extra={"event": "result_write_failed"},
        )
        result["error"] = {"type": type(err).__name__, "message": str(err)}
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
        return exit_codes.CONFIGURATION_ERROR

    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    LOGGER.info(
        "Echo deploy test finished",
        extra={"event": "deploy_finished", "exit_code": exit_code,
               "elapsed_seconds": result["elapsed_seconds"]},
    )
    return exit_code


def controller_data_summary(ctrl: Any) -> dict[str, Any]:
    """Extract serializable fields from a ControllerData object."""
    fw_ver = ctrl.firmware_package_version
    fw_str = (
        f"{fw_ver.major}.{fw_ver.minor:03d}"
        if fw_ver and fw_ver.major is not None
        else None
    )
    ip = ctrl.ip_configuration_data
    ip_addr = ip.address if ip else None
    mode = ctrl.controller_mode
    ks = ctrl.key_switch_position
    return {
        "controller_guid": ctrl.controller_guid,
        "controller_name": ctrl.controller_name,
        "chassis_guid": ctrl.chassis_guid,
        "chassis_name": ctrl.chassis_name,
        "slot": ctrl.slot,
        "firmware_package_guid": ctrl.firmware_package_guid,
        "firmware_version": fw_str,
        "ip_address": ip_addr,
        "is_enabled": ctrl.is_enabled,
        "is_in_run_mode": ctrl.is_in_run_mode,
        "is_faulted": ctrl.is_faulted,
        "controller_mode": str(mode) if mode else None,
        "key_switch_position": str(ks) if ks else None,
        "loaded_project_name": ctrl.loaded_project_name,
    }


async def find_cicd_test_chassis(client: Any) -> Any | None:
    """Return the ChassisData for CICD_TEST_CHASSIS if it exists, else None."""
    chassis_list = await client.list_chassis()
    for ch in chassis_list:
        if ch.name == CHASSIS_NAME:
            return ch
    return None


async def find_cicd_test_controller(client: Any) -> Any | None:
    """Return the ControllerData for CICD_TEST_01 if it exists anywhere, else None."""
    # Search chassis-less controllers
    chassis_less = await client.list_controllers(EMPTY_GUID)
    for c in chassis_less:
        if c.controller_name == CONTROLLER_NAME:
            return c

    # Search each chassis (including any leftover test chassis)
    chassis_list = await client.list_chassis()
    for ch in chassis_list:
        controllers = await client.list_controllers(ch.chassis_guid)
        for c in controllers:
            if c.controller_name == CONTROLLER_NAME:
                return c

    return None


async def safe_delete_chassis(client: Any, chassis_guid: str) -> bool:
    """
    Delete a chassis, retrying up to DELETE_MAX_RETRIES times.
    Retries handle the window after download where the controller/chassis is in a
    transitional state and the Echo service rejects API calls.
    Returns True on success, False on failure (logs but does not raise).
    Any controllers inside should already be deleted before calling this.
    """
    for attempt in range(1, DELETE_MAX_RETRIES + 1):
        try:
            await client.delete_chassis(chassis_guid)
            LOGGER.info(
                "Chassis deleted",
                extra={"event": "chassis_delete_ok",
                       "chassis_guid": chassis_guid, "attempt": attempt},
            )
            return True
        except Exception as err:
            if attempt < DELETE_MAX_RETRIES:
                LOGGER.warning(
                    "Delete chassis attempt %d/%d failed; retrying in %.0fs: %s",
                    attempt, DELETE_MAX_RETRIES, DELETE_RETRY_DELAY, err,
                    extra={"event": "chassis_delete_retry", "attempt": attempt},
                )
                await asyncio.sleep(DELETE_RETRY_DELAY)
            else:
                LOGGER.error(
                    "Failed to delete chassis after %d attempts -- manual cleanup required: %s",
                    DELETE_MAX_RETRIES, err,
                    extra={"event": "chassis_delete_failed", "chassis_guid": chassis_guid},
                )
    return False


async def safe_delete_controller(client: Any, controller_guid: str) -> bool:
    """
    Disable the controller then delete it, retrying up to DELETE_MAX_RETRIES times.
    Retries handle the window after download where the controller is in a transitional
    state and rejects API calls.
    Returns True if deleted, False if all attempts failed (logs an error but does not raise).
    """
    # Best-effort disable before deletion (ignore failure -- delete may still succeed).
    try:
        current = await client.read_controller(controller_guid)
        disable_update = current.to_controller_update()
        disable_update.is_enabled = False
        await client.update_controller(disable_update)
        LOGGER.info(
            "Controller disabled before deletion",
            extra={"event": "controller_disable_ok"},
        )
    except Exception as disable_err:
        LOGGER.warning(
            "Could not disable controller before deletion; proceeding anyway: %s",
            disable_err,
            extra={"event": "controller_disable_failed"},
        )

    for attempt in range(1, DELETE_MAX_RETRIES + 1):
        try:
            await client.delete_controller(controller_guid)
            LOGGER.info(
                "Controller deleted",
                extra={"event": "controller_delete_ok",
                       "controller_guid": controller_guid, "attempt": attempt},
            )
            return True
        except Exception as err:
            if attempt < DELETE_MAX_RETRIES:
                LOGGER.warning(
                    "Delete controller attempt %d/%d failed; retrying in %.0fs: %s",
                    attempt, DELETE_MAX_RETRIES, DELETE_RETRY_DELAY, err,
                    extra={"event": "controller_delete_retry", "attempt": attempt},
                )
                await asyncio.sleep(DELETE_RETRY_DELAY)
            else:
                LOGGER.error(
                    "Failed to delete controller after %d attempts -- manual cleanup required: %s",
                    DELETE_MAX_RETRIES, err,
                    extra={"event": "controller_delete_failed",
                           "controller_guid": controller_guid},
                )
    return False


async def poll_download(
    client: Any,
    controller_guid: str,
    timeout: float,
    checks: dict[str, str],
) -> None:
    """
    Poll get_download_feedback until state is DONE. Raises RuntimeError on
    FAILED/CANCELED or if the timeout expires before completion.
    """
    from ftecho_sdk.interfaces.service.enums import OperationState

    LOGGER.info("Polling download feedback...", extra={"event": "download_poll_start"})
    deadline = time.monotonic() + timeout
    last_progress = -1

    while time.monotonic() < deadline:
        await asyncio.sleep(DOWNLOAD_POLL_INTERVAL)

        fb = await client.get_download_feedback(controller_guid)
        state = fb.state
        progress = fb.progress if fb.progress is not None else 0

        if progress != last_progress:
            LOGGER.info(
                "Download in progress",
                extra={"event": "download_progress",
                       "progress": progress, "state": str(state)},
            )
            last_progress = progress

        if state == OperationState.DONE:
            LOGGER.info(
                "Download completed",
                extra={"event": "download_done", "progress": progress},
            )
            checks["download_completed"] = "passed"
            return

        if state in (OperationState.FAILED, OperationState.CANCELED):
            msgs = "; ".join(fb.messages) if fb.messages else "(no messages)"
            checks["download_completed"] = "failed"
            raise RuntimeError(
                f"Download ended with state {state}: {msgs}"
            )

    checks["download_completed"] = "failed"
    raise RuntimeError(
        f"Download did not complete within {timeout:.0f} seconds"
    )


async def poll_project_loaded(
    client: Any,
    controller_guid: str,
    checks: dict[str, str],
) -> None:
    """
    Poll read_controller until loaded_project_name is non-empty.

    The Echo download feedback API reports DONE before the controller finishes
    loading the project internally.  Attempting to read or update the controller
    in this transitional window raises an SDK error.  This function bridges that
    gap by waiting until the controller reports a non-empty project name before
    the caller attempts any further API calls.

    Raises RuntimeError if the project is not loaded within PROJECT_LOAD_TIMEOUT.
    """
    LOGGER.info(
        "Waiting for controller to finish loading project after download...",
        extra={"event": "project_load_poll_start"},
    )
    deadline = time.monotonic() + PROJECT_LOAD_TIMEOUT
    attempt = 0
    while time.monotonic() < deadline:
        await asyncio.sleep(PROJECT_LOAD_POLL)
        attempt += 1
        try:
            state = await client.read_controller(controller_guid)
            if state.loaded_project_name:
                LOGGER.info(
                    "Project loaded in controller",
                    extra={"event": "project_load_done",
                           "project": state.loaded_project_name, "attempt": attempt},
                )
                checks["project_loaded"] = "passed"
                return
            LOGGER.info(
                "Project not yet loaded; polling... (attempt %d)",
                attempt,
                extra={"event": "project_load_poll", "attempt": attempt},
            )
        except Exception as err:
            LOGGER.warning(
                "read_controller failed during project load poll (attempt %d); retrying: %s",
                attempt, err,
                extra={"event": "project_load_poll_error", "attempt": attempt},
            )

    checks["project_loaded"] = "failed"
    raise RuntimeError(
        f"Controller project did not finish loading within {PROJECT_LOAD_TIMEOUT:.0f} seconds"
    )


async def run_lifecycle(
    service_api_client_type: Any,
    acd_path: Path,
    timeout_seconds: float,
    result: dict[str, Any],
) -> int:
    from ftecho_sdk import FTEchoSdkError
    from ftecho_sdk.interfaces.service.chassis import ChassisUpdate
    from ftecho_sdk.interfaces.service.controller import ControllerUpdate
    from ftecho_sdk.interfaces.service.enums import KeySwitchPosition
    from ftecho_sdk.interfaces.service.ip_address import IP4ConfigurationData

    checks: dict[str, str] = result["checks"]
    controller_guid: str | None = None
    test_chassis_guid: str | None = None
    cleanup_ok: bool = True

    client = service_api_client_type.create()
    LOGGER.info("Echo service client created", extra={"event": "service_client_create"})

    try:
        # ------------------------------------------------------------------ #
        # Pre-flight: clean up any leftovers from prior runs                  #
        # ------------------------------------------------------------------ #
        LOGGER.info(
            "Pre-flight: checking for leftover CICD_TEST_01 / CICD_TEST_CHASSIS",
            extra={"event": "preflight_check_start"},
        )
        existing_ctrl = await find_cicd_test_controller(client)
        if existing_ctrl:
            LOGGER.warning(
                "Found leftover CICD_TEST_01 -- deleting before starting",
                extra={"event": "preflight_leftover_ctrl",
                       "controller_guid": existing_ctrl.controller_guid},
            )
            await safe_delete_controller(client, existing_ctrl.controller_guid)

        existing_ch = await find_cicd_test_chassis(client)
        if existing_ch:
            LOGGER.warning(
                "Found leftover CICD_TEST_CHASSIS -- deleting before starting",
                extra={"event": "preflight_leftover_chassis",
                       "chassis_guid": existing_ch.chassis_guid},
            )
            await safe_delete_chassis(client, existing_ch.chassis_guid)

        checks["preflight_cleanup"] = (
            "performed" if (existing_ctrl or existing_ch) else "not_needed"
        )

        # ------------------------------------------------------------------ #
        # Send ACD to Echo service                                             #
        # ------------------------------------------------------------------ #
        LOGGER.info(
            "Sending ACD file to Echo service",
            extra={"event": "send_file_start", "acd": str(acd_path)},
        )
        file_handle = await client.send_file(str(acd_path))
        if not file_handle.is_valid:
            raise RuntimeError("send_file returned an invalid file handle")
        checks["file_sent"] = "passed"
        LOGGER.info(
            "ACD file sent",
            extra={"event": "send_file_done", "local_path": file_handle.local_path},
        )

        # ------------------------------------------------------------------ #
        # Read controller metadata from the ACD                               #
        # ------------------------------------------------------------------ #
        LOGGER.info(
            "Reading controller info from ACD",
            extra={"event": "acd_info_read_start"},
        )
        ctrl_info = await client.get_controller_info_from_acd(file_handle)
        firmware_guid = ctrl_info.firmware_package_guid
        acd_slot = ctrl_info.slot           # slot number embedded in the ACD
        project_path = ctrl_info.project_path
        needs_chassis = (
            ctrl_info.chassis_guid is not None
            and ctrl_info.chassis_guid != EMPTY_GUID
        )
        # The CI/CD test controller always uses 127.0.0.2 (loopback alias).
        # All 127.x.x.x addresses are loopback on Windows -- no adapter
        # configuration required.  This avoids conflicts with:
        #   127.0.0.1 -- used by TEMP_SCP in the default Echo instance
        #   10.243.x.x -- used by all chassis controllers in the default Echo instance
        ip_config = IP4ConfigurationData(address="127.0.0.2", netmask="255.0.0.0")
        result["acd_firmware_package_guid"] = firmware_guid
        result["acd_slot"] = acd_slot
        result["acd_project_path"] = project_path
        result["acd_needs_chassis"] = needs_chassis
        result["cicd_test_ip_address"] = ip_config.address
        checks["acd_info_read"] = "passed"
        LOGGER.info(
            "ACD info read",
            extra={"event": "acd_info_read_done",
                   "firmware_guid": firmware_guid,
                   "acd_slot": acd_slot,
                   "needs_chassis": needs_chassis},
        )

        # ------------------------------------------------------------------ #
        # Create a dedicated test chassis (if the ACD requires one)           #
        # The ACD embeds a slot number; we create a temp chassis so the       #
        # controller can occupy that exact slot without conflicting with       #
        # existing chassis controllers.                                        #
        # ------------------------------------------------------------------ #
        if needs_chassis:
            LOGGER.info(
                "Creating dedicated test chassis",
                extra={"event": "chassis_create_start", "chassis_name": CHASSIS_NAME},
            )
            test_chassis = await client.create_chassis(
                ChassisUpdate(
                    name=CHASSIS_NAME,
                    description=CHASSIS_DESCRIPTION,
                )
            )
            test_chassis_guid = test_chassis.chassis_guid
            result["test_chassis_guid"] = test_chassis_guid
            checks["chassis_created"] = "passed"
            LOGGER.info(
                "Test chassis created",
                extra={"event": "chassis_create_done",
                       "chassis_guid": test_chassis_guid},
            )
            create_chassis_guid = test_chassis_guid
            create_slot = acd_slot
        else:
            create_chassis_guid = None
            create_slot = None
            LOGGER.info(
                "Controller will be chassis-less", extra={"event": "chassis_less"}
            )

        # ------------------------------------------------------------------ #
        # Create controller                                                    #
        # ------------------------------------------------------------------ #
        LOGGER.info(
            "Creating controller",
            extra={"event": "controller_create_start",
                   "ctrl_name": CONTROLLER_NAME, "slot": create_slot},
        )
        created = await client.create_controller(
            ControllerUpdate(
                name=CONTROLLER_NAME,
                description=CONTROLLER_DESCRIPTION,
                firmware_package_guid=firmware_guid,
                chassis_guid=create_chassis_guid,
                slot=create_slot,
                is_enabled=True,              # must be started before download() is accepted
                project_path=project_path,
                ip_configuration_data=ip_config,  # required for download
            )
        )
        controller_guid = created.controller_guid
        checks["controller_created"] = "passed"
        result["created_controller"] = controller_data_summary(created)
        LOGGER.info(
            "Controller created",
            extra={"event": "controller_create_done",
                   "controller_guid": controller_guid, "slot": created.slot},
        )

        # ------------------------------------------------------------------ #
        # Download project                                                     #
        # ------------------------------------------------------------------ #
        LOGGER.info(
            "Downloading project to controller",
            extra={"event": "download_start", "controller_guid": controller_guid},
        )
        await client.download(controller_guid, file_handle)
        checks["download_started"] = "passed"

        await poll_download(client, controller_guid, DOWNLOAD_MAX_WAIT, checks)

        # ------------------------------------------------------------------ #
        # Wait for the project to finish loading inside the controller.       #
        # Echo's download feedback reports DONE before the controller finishes#
        # its internal load; any API call in that window fails immediately.   #
        # ------------------------------------------------------------------ #
        await poll_project_loaded(client, controller_guid, checks)

        # ------------------------------------------------------------------ #
        # Set RUN mode (key switch to RUN position)                           #
        # update_controller requires Name to be non-null; read current state  #
        # first and use to_controller_update() to get all required fields.    #
        # ------------------------------------------------------------------ #
        LOGGER.info(
            "Setting controller to RUN mode",
            extra={"event": "controller_run_start"},
        )
        current_state = await client.read_controller(controller_guid)
        run_update = current_state.to_controller_update()
        run_update.key_switch_position = KeySwitchPosition.RUN
        await client.update_controller(run_update)
        checks["controller_run_requested"] = "passed"

        # Brief settle time -- Echo updates run mode asynchronously
        await asyncio.sleep(MODE_SETTLE_WAIT)

        # ------------------------------------------------------------------ #
        # Verify controller is in RUN mode                                    #
        # ------------------------------------------------------------------ #
        LOGGER.info(
            "Verifying controller state",
            extra={"event": "controller_verify_start"},
        )
        final = await client.read_controller(controller_guid)
        result["final_controller"] = controller_data_summary(final)

        if not final.is_in_run_mode:
            checks["controller_in_run_mode"] = "failed"
            mode_str = str(final.controller_mode) if final.controller_mode else "unknown"
            raise RuntimeError(
                f"Controller is not in RUN mode after setting key switch to RUN. "
                f"controller_mode={mode_str}, is_faulted={final.is_faulted}"
            )

        checks["controller_in_run_mode"] = "passed"
        LOGGER.info(
            "Controller verified in RUN mode",
            extra={"event": "controller_verify_done",
                   "is_in_run_mode": final.is_in_run_mode,
                   "controller_mode": str(final.controller_mode)},
        )

        return exit_codes.SUCCESS

    finally:
        # ------------------------------------------------------------------ #
        # Always delete controller then chassis                                #
        # ------------------------------------------------------------------ #
        ctrl_deleted = True
        if controller_guid:
            LOGGER.info(
                "Deleting test controller (finally block)",
                extra={"event": "controller_cleanup_start",
                       "controller_guid": controller_guid},
            )
            ctrl_deleted = await safe_delete_controller(client, controller_guid)
            checks["controller_deleted"] = "passed" if ctrl_deleted else "failed"
        else:
            checks["controller_deleted"] = "not_needed"

        ch_deleted = True
        if test_chassis_guid:
            LOGGER.info(
                "Deleting test chassis (finally block)",
                extra={"event": "chassis_cleanup_start",
                       "chassis_guid": test_chassis_guid},
            )
            ch_deleted = await safe_delete_chassis(client, test_chassis_guid)
            checks["chassis_deleted"] = "passed" if ch_deleted else "failed"
        else:
            checks["chassis_deleted"] = "not_needed"

        cleanup_ok = ctrl_deleted and ch_deleted

        if not cleanup_ok:
            result["cleanup_error"] = (
                f"Controller {controller_guid} or chassis {test_chassis_guid} "
                "was NOT deleted. Investigate manually before the next pipeline run."
            )
            raise CleanupError(
                f"Failed to delete controller {controller_guid}"
            )


class CleanupError(RuntimeError):
    """Raised when the finally-block controller deletion fails."""


def main() -> int:
    args = parse_args()
    configure_logging(args.verbose)
    started = time.monotonic()
    result = base_result(args.acd)

    if not ((3, 12) <= sys.version_info[:2] < (3, 14)):
        result["error"] = {
            "type": "UnsupportedPythonVersion",
            "message": "ftecho_sdk 4.0.0 requires Python 3.12 or 3.13.",
        }
        return finish(result, args.output, exit_codes.CONFIGURATION_ERROR, started)

    if not args.acd.is_file():
        result["error"] = {
            "type": "FileNotFoundError",
            "message": f"ACD file not found: {args.acd}",
        }
        return finish(result, args.output, exit_codes.CONFIGURATION_ERROR, started)

    try:
        from ftecho_sdk import FTEchoSdkError, ServiceApiClientV2

        result["sdk_version"] = metadata.version("ftecho_sdk")
        result["checks"]["sdk_import"] = "passed"
    except Exception as err:
        LOGGER.exception("Unable to import the Echo SDK",
                         extra={"event": "sdk_import_failed"})
        result["checks"]["sdk_import"] = "failed"
        result["error"] = {"type": type(err).__name__, "message": str(err)}
        return finish(result, args.output, exit_codes.DEPENDENCY_ERROR, started)

    try:
        exit_code = asyncio.run(
            asyncio.wait_for(
                run_lifecycle(ServiceApiClientV2, args.acd, args.timeout_seconds, result),
                timeout=args.timeout_seconds,
            )
        )
        return finish(result, args.output, exit_code, started)

    except CleanupError as err:
        LOGGER.error("Controller cleanup failed",
                     extra={"event": "cleanup_error", "error": str(err)})
        result["error"] = {"type": "CleanupError", "message": str(err)}
        return finish(result, args.output, exit_codes.CLEANUP_FAILURE, started)

    except TimeoutError:
        LOGGER.exception("Lifecycle timed out", extra={"event": "lifecycle_timeout"})
        result["checks"].setdefault("lifecycle_timeout", "triggered")
        result["error"] = {
            "type": "TimeoutError",
            "message": f"Lifecycle exceeded {args.timeout_seconds:.0f} seconds",
        }
        return finish(result, args.output, exit_codes.TEST_FAILURE, started)

    except Exception as err:
        from ftecho_sdk import FTEchoSdkError

        LOGGER.exception("Deploy test failed", extra={"event": "unexpected_error"})
        result["error"] = {"type": type(err).__name__, "message": str(err)}
        if isinstance(err, FTEchoSdkError):
            return finish(result, args.output, exit_codes.SERVICE_UNAVAILABLE, started)
        return finish(result, args.output, exit_codes.TEST_FAILURE, started)


if __name__ == "__main__":
    sys.exit(main())
