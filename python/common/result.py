"""Helpers for writing stable JSON smoke-test artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_SENSITIVE_NAME_PARTS = (
    "key",
    "password",
    "secret",
    "serial",
    "token",
)


def summarize_public_attributes(value: Any) -> dict[str, object]:
    """Return simple public attributes while excluding likely secrets."""

    summary: dict[str, object] = {}
    for name in sorted(dir(value)):
        lowered_name = name.lower()
        if name.startswith("_") or any(part in lowered_name for part in _SENSITIVE_NAME_PARTS):
            continue

        try:
            attribute = getattr(value, name)
        except Exception:
            continue

        if callable(attribute):
            continue

        if attribute is None or isinstance(attribute, (bool, float, int, str)):
            summary[name] = attribute
        elif isinstance(attribute, (list, tuple)):
            summary[name] = {"count": len(attribute)}
        else:
            rendered_attribute = str(attribute)
            if (
                rendered_attribute.startswith("<")
                and " at 0x" in rendered_attribute
                and rendered_attribute.endswith(">")
            ):
                summary[name] = {"type": type(attribute).__name__}
            else:
                summary[name] = rendered_attribute

        if len(summary) >= 50:
            break

    return summary


def write_json_result(path: Path, payload: dict[str, object]) -> None:
    """Write a JSON artifact atomically."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(path)
