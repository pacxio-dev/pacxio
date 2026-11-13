"""`pacxio studio start|stop|status`：在后台起 / 停工作台服务（ 节）。

一个 job 一个服务；端口、进程号、口令记在 `<job>/studio/server.json`。同一个 job 再 start 就复用。
判断服务还活着要两样都对：进程在、`/api/ping` 回的进程号和 job 目录也对（防止进程号被别的程序复用）。
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

from pacxio import dielines
from pacxio.messages import t
from pacxio.model.common import Issue, Result
from pacxio.studio.server import HOST, LOG_FILE, SERVER_FILE, studio_dir

START_TIMEOUT_S = 20.0
STOP_TIMEOUT_S = 5.0
PING_TIMEOUT_S = 1.0
LOG_TAIL_CHARS = 1500


def read_info(directory: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((studio_dir(directory) / SERVER_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


_LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 只连本机：不走代理


def ping(port: int) -> dict[str, Any] | None:
    try:
        with _LOCAL.open(f"http://{HOST}:{port}/api/ping", timeout=PING_TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return None
    return data if isinstance(data, dict) else None


def alive(directory: Path) -> dict[str, Any] | None:
    """服务在跑就返回 server.json 的内容（去掉口令），否则 None。"""
    info = read_info(directory)
    if info is None or not isinstance(info.get("port"), int):
        return None
    pong = ping(info["port"])
    if pong is None or pong.get("pid") != info.get("pid"):
        return None
    if Path(str(pong.get("job"))).resolve() != directory.resolve():
        return None
    return {k: v for k, v in info.items() if k != "token"}


def _public(info: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"running": True, "url": info["url"], "port": info["port"], "pid": info["pid"],
            "started": info.get("started"), **extra}  # fmt: skip


def _log_tail(directory: Path) -> str:
    try:
        return (studio_dir(directory) / LOG_FILE).read_text(encoding="utf-8")[-LOG_TAIL_CHARS:]
    except OSError:
        return ""


def studio_status(directory: Path) -> Result:
    r = Result(command="studio status", job=str(directory))
    info = alive(directory)
    r.data = _public(info) if info else {"running": False}
    return r


def _spawn(directory: Path, port: int) -> subprocess.Popen[bytes]:
    sdir = studio_dir(directory)
    sdir.mkdir(parents=True, exist_ok=True)
    log = (sdir / LOG_FILE).open("wb")
    cmd = [
        sys.executable,
        "-m",
        "pacxio.studio.server",
        str(directory.resolve()),
        "--port",
        str(port),
    ]
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": log,
        "stderr": subprocess.STDOUT,
    }
    if os.name == "nt":  # 不随调用它的命令一起退出
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
    else:
        kwargs["start_new_session"] = True
    try:
        return subprocess.Popen(cmd, **kwargs)
    finally:
        log.close()


def studio_start(directory: Path, port: int = 0, open_browser: bool = False) -> Result:
    """起工作台（已经在跑就复用），返回网址。"""
    r = Result(command="studio start", job=str(directory))
    job, errs = dielines.load_job(directory)
    if job is None:
        r.errors += errs
        r.ok, r.exit_code = False, 2
        return r
    info = alive(directory)
    if info is not None:
        r.data = _public(info, reused=True)
    else:
        proc = _spawn(directory, port)
        deadline = time.monotonic() + START_TIMEOUT_S
        while time.monotonic() < deadline:
            info = alive(directory)
            if info is not None and info.get("pid") == proc.pid:
                break
            if proc.poll() is not None:
                info = None
                break
            time.sleep(0.1)
        else:
            info = None
        if info is None:
            if proc.poll() is None:
                proc.terminate()
            return r.fail(
                Issue(
                    id="STUDIO",
                    message=t("The Workbench service could not start: ") + (_log_tail(directory).strip() or t("no output")),
                    fix=t("If the port is busy, choose another with --port. Check studio/server.log or run `pacxio studio serve <job>` in the foreground for details."),
                ),
                3,
            )
        r.data = _public(info, reused=False)
    if open_browser:
        webbrowser.open(r.data["url"])
    r.warnings.append(
        Issue(
            id="STUDIO",
            message=t("The Workbench lets the user review and annotate the job. Only the user can confirm wording, drafts, concept direction, flats, and the dieline proof."),
            fix=t("Run `pacxio notes list <job> --open` before each round of work, then reply with `notes reply`. Leave confirmations and closing notes to the user. Stop the service with `pacxio studio stop <job>`."),
        )
    )
    return r


def studio_stop(directory: Path) -> Result:
    r = Result(command="studio stop", job=str(directory))
    info = alive(directory)
    info_file = studio_dir(directory) / SERVER_FILE
    if info is None:
        info_file.unlink(missing_ok=True)  # 进程已经不在了：清掉过期的记录
        r.data = {"running": False, "stopped": False}
        return r
    os.kill(int(info["pid"]), signal.SIGTERM)
    deadline = time.monotonic() + STOP_TIMEOUT_S
    while time.monotonic() < deadline and ping(int(info["port"])) is not None:
        time.sleep(0.1)
    if ping(int(info["port"])) is not None:
        return r.fail(
            Issue(id="STUDIO", message=t("Workbench process {pid} did not exit", pid=info['pid']),
                  fix=t("Stop it manually with kill {pid}", pid=info['pid'])),
        )  # fmt: skip
    info_file.unlink(missing_ok=True)
    r.data = {"running": False, "stopped": True, "pid": info["pid"]}
    return r


__all__ = ["alive", "studio_start", "studio_status", "studio_stop"]
