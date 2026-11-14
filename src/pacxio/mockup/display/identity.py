"""Stable identity shared with the browser, independent of JSON number spelling."""

from __future__ import annotations

import hashlib
import json
import struct
from typing import Any


def canonical(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        number = float(value)
        return "n" + struct.pack(">d", number if number else 0.0).hex()
    if isinstance(value, str):
        return "s" + json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, list):
        return "[" + ",".join(canonical(v) for v in value) + "]"
    if isinstance(value, dict):
        return (
            "{"
            + ",".join(
                json.dumps(k, ensure_ascii=False) + ":" + canonical(value[k])
                for k in sorted(value, key=lambda k: k.encode("utf-16-be"))
            )
            + "}"
        )
    raise TypeError("Unsupported scene value")


def digest(scene: dict[str, Any]) -> str:
    return hashlib.sha256(
        canonical({k: v for k, v in scene.items() if k != "display"}).encode()
    ).hexdigest()
