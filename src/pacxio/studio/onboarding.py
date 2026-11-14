"""Machine-local guide preference, shared by Workbench jobs and ports."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def preference_path() -> Path:
    return Path(os.environ.get("PACXIO_HOME", str(Path.home() / ".pacxio"))) / "workbench-guide.json"


def seen() -> bool:
    try:
        data = json.loads(preference_path().read_text(encoding="utf-8"))
        return isinstance(data, dict) and data.get("seen") is True
    except (OSError, ValueError):
        return False


def dismiss() -> None:
    path = preference_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".guide-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"seen": True}, stream)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)
