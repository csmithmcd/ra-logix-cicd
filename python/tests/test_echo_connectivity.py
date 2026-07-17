"""Offline regression tests for the Echo connectivity smoke test."""

from __future__ import annotations

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
from smoke import echo_connectivity  # noqa: E402


class FakeFTEchoSdkError(Exception):
    """Stand-in for the Rockwell SDK exception type."""


class FakeProductInfo:
    product_name = "FactoryTalk Logix Echo"
    version = "4.0"


class FakeLicenseInfo:
    state = "active"
    license_key = "must-not-be-serialized"
    personalized_license = object()


class FakeChassis:
    chassis_guid = "11111111-1111-1111-1111-111111111111"


class FakeServiceClient:
    calls: list[tuple[str, object | None]] = []

    @classmethod
    def create(cls) -> "FakeServiceClient":
        cls.calls.append(("create", None))
        return cls()

    async def get_product_info(self) -> FakeProductInfo:
        self.calls.append(("get_product_info", None))
        return FakeProductInfo()

    async def get_license_info(self) -> FakeLicenseInfo:
        self.calls.append(("get_license_info", None))
        return FakeLicenseInfo()

    async def list_chassis(self) -> list[FakeChassis]:
        self.calls.append(("list_chassis", None))
        return [FakeChassis()]

    async def list_controllers(self, chassis_guid: object) -> list[object]:
        self.calls.append(("list_controllers", chassis_guid))
        if chassis_guid == echo_connectivity.EMPTY_GUID:
            return [object()]
        return [object(), object()]


class EchoConnectivityTests(unittest.TestCase):
    def test_success_path_uses_only_expected_read_methods(self) -> None:
        fake_module = types.ModuleType("ftecho_sdk")
        fake_module.FTEchoSdkError = FakeFTEchoSdkError
        fake_module.ServiceApiClientV2 = FakeServiceClient
        FakeServiceClient.calls = []

        with tempfile.TemporaryDirectory() as temporary_directory:
            output_path = Path(temporary_directory) / "result.json"
            argv = ["echo_connectivity.py", "--output", str(output_path)]

            with (
                patch.dict(sys.modules, {"ftecho_sdk": fake_module}),
                patch.object(echo_connectivity.metadata, "version", return_value="4.0.0"),
                patch.object(sys, "argv", argv),
                redirect_stdout(StringIO()),
            ):
                exit_code = echo_connectivity.main()

            self.assertEqual(exit_codes.SUCCESS, exit_code)
            result = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual("passed", result["status"])
            self.assertTrue(result["read_only"])
            self.assertEqual(1, result["inventory_counts"]["chassis"])
            self.assertEqual(3, result["inventory_counts"]["controllers_total"])
            self.assertNotIn("license_key", result["license_info"])
            self.assertEqual(
                {"type": "object"},
                result["license_info"]["personalized_license"],
            )
            self.assertNotIn(" at 0x", output_path.read_text(encoding="utf-8"))

            called_methods = [name for name, _ in FakeServiceClient.calls]
            self.assertEqual(
                [
                    "create",
                    "get_product_info",
                    "get_license_info",
                    "list_chassis",
                    "list_controllers",
                    "list_controllers",
                ],
                called_methods,
            )


if __name__ == "__main__":
    unittest.main()
