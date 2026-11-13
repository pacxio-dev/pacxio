"""服务地址、登录状态、什么时候走云端。"""

from __future__ import annotations

import importlib.util
import json
import os
from dataclasses import dataclass
from pathlib import Path

from pacxio.cloud import boundary

DEFAULT_URL = "https://api.pacxio.dev"
URL_ENV = "PACXIO_CLOUD_URL"
TOKEN_ENV = "PACXIO_CLOUD_TOKEN"
MODE_ENV = "PACXIO_CLOUD"  # "1" 强制走云端；"0" 强制本机（服务器上的引擎就是这样跑的）


@dataclass(frozen=True)
class Login:
    url: str
    token: str | None


def home() -> Path:
    return Path(os.environ.get("PACXIO_HOME", str(Path.home() / ".pacxio")))


def creds_path() -> Path:
    return home() / "cloud.json"


def _saved() -> dict[str, str]:
    try:
        data = json.loads(creds_path().read_text())
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str)} if isinstance(data, dict) else {}


def login() -> Login:
    saved = _saved()
    url = os.environ.get(URL_ENV) or saved.get("url") or DEFAULT_URL
    token = os.environ.get(TOKEN_ENV) or saved.get("token") or None
    return Login(url=url.rstrip("/"), token=token)


def save(url: str, token: str) -> Path:
    p = creds_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"url": url.rstrip("/"), "token": token}))
    tmp.chmod(0o600)
    tmp.replace(p)
    return p


def clear() -> bool:
    try:
        creds_path().unlink()
    except FileNotFoundError:
        return False
    return True


def closed_available() -> bool:
    """本机装着闭源部分（开发机、服务器）就不用转发。"""
    try:
        return importlib.util.find_spec(boundary.PROBE) is not None
    except ImportError:
        return False


def use_cloud() -> bool:
    mode = os.environ.get(MODE_ENV, "").strip()
    if mode == "1":
        return True
    if mode == "0":
        return False
    return not closed_available()
