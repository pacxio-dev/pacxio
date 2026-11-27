"""客户端匿名漏斗统计与可选错误上报（ A，）。

设计边界（轻盈 + 隐私优先 + 开源联网边界 ）：
- 联网严格收敛在 `pacxio.cloud` 模块，只用 Python 标准库，零新增第三方依赖。
- **两层分离**：
  1. 匿名漏斗事件（默认开启，可随时 `pacxio telemetry off` / `PACXIO_TELEMETRY=0` / `DO_NOT_TRACK=1` 关闭）：
     只记结构化阶段与结果（`setup` → `mcp_connect` → `job_init` → `content` → `concept` → `studio` → `compose` → `preview` → `preflight` → `approve` → `export`），
     绝不包含 job 路径、文件名、品牌文案、提示词、错误信息原文、邮箱或令牌；job 关联仅用不可逆哈希 `sha256(install_id + resolved_path)[:16]`。
  2. 脱敏错误详情上报（**默认关闭，严格 Opt-in**，`pacxio telemetry errors on` 或 `PACXIO_ERROR_REPORTS=1` 开启）：
     仅在命令失败或未捕获异常时附带严格脱敏的错误消息摘要与包内相对栈帧（去除绝对路径、邮箱、令牌、密钥与局部变量）。
- **静默保护**：服务端引擎进程（`PACXIO_CLOUD=0`）、开发机闭源环境、CI、pytest 均自动静默（除非显式设置 `PACXIO_TELEMETRY=1`）。
- **零阻塞**：命令执行期只追加写本地有界 JSONL 队列（< 1 ms），达阈值或关键节点时由独立后台子进程 / 守护线程异步上报（3 s 超时、404 退避 6 h、永不影响主流程）。
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pacxio import __version__
from pacxio.cloud import config
from pacxio.messages import lang as lang_now

TELEMETRY_ENV = "PACXIO_TELEMETRY"
ERROR_REPORTS_ENV = "PACXIO_ERROR_REPORTS"
TELEMETRY_URL_ENV = "PACXIO_TELEMETRY_URL"
DO_NOT_TRACK_ENV = "DO_NOT_TRACK"

SCHEMA_VERSION = 1
MAX_SPOOL_EVENTS = 200
MAX_SPOOL_BYTES = 256 * 1024
FLUSH_BATCH_EVENTS = 10
FLUSH_INTERVAL_S = 300.0
FLUSH_TIMEOUT_S = 3.0
NOT_FOUND_BACKOFF_S = 6 * 3600.0
MIN_ERROR_BACKOFF_S = 60.0
MAX_ERROR_BACKOFF_S = 3600.0
MAX_ERROR_IDS = 5
MAX_ISSUE_DETAILS = 5
MAX_TRACEBACK_FRAMES = 12

# 内部自检探针名称，不计入真实用户 MCP 连接漏斗
PROBE_CLIENTS: frozenset[str] = frozenset({"doctor", "pacxio-runtime-check"})

# 关键漏斗命令 → 阶段（其余只读查询命令成功时不记，失败时记 stage="other" 以捕捉真实卡点）
COMMAND_STAGES: dict[str, str] = {
    "setup": "setup",
    "mcp install": "mcp_install",
    "mcp doctor": "env_check",
    "mcp initialize": "mcp_connect",
    "env check": "env_check",
    "login": "auth",
    "logout": "auth",
    "cloud login": "auth",
    "structures suggest": "structure_explore",
    "structures preview": "structure_explore",
    "structures files": "structure_explore",
    "structures check": "structure_explore",
    "job init": "job_init",
    "structures update": "structure_edit",
    "content add": "content",
    "content confirm": "content",
    "concept styles": "concept",
    "concept prompt": "concept",
    "concept gen": "concept",
    "concept import": "concept",
    "concept choose": "concept",
    "concept use": "concept",
    "concept confirm": "concept",
    "studio start": "studio",
    "plan": "compose",
    "job paper": "compose",
    "visual import": "compose",
    "visual prepare": "compose",
    "visual trace": "compose",
    "art add": "compose",
    "restore run": "compose",
    "restore iterate": "compose",
    "finish add": "compose",
    "surface set": "compose",
    "color add": "compose",
    "attach add": "compose",
    "window add": "compose",
    "render": "preview",
    "mockup": "preview",
    "flat": "preview",
    "zoom": "preview",
    "preflight": "preflight",
    "approve": "approve",
    "export": "export",
}

# 触发立即后台上报的里程碑命令（其余写入本地缓冲，攒批或到间隔再刷）
MILESTONE_COMMANDS: frozenset[str] = frozenset(
    {
        "setup",
        "mcp install",
        "mcp initialize",
        "login",
        "job init",
        "content confirm",
        "concept confirm",
        "studio start",
        "render",
        "mockup",
        "preflight",
        "approve",
        "export",
    }
)

# 上下文：避免 MCP 调 CliRunner / cloud.route 时重复记账，并精确记录单次耗时
_IN_SCOPE: ContextVar[bool] = ContextVar("pacxio_telemetry_in_scope", default=False)
_CLI_STARTED_AT: ContextVar[float | None] = ContextVar("pacxio_telemetry_cli_started", default=None)
_MCP_CLIENT_LOCK = threading.Lock()
_MCP_CLIENT_NAME: str | None = None
_MCP_FLUSH_LOCK = threading.Lock()
_MCP_FLUSH_RUNNING = False

# 严格脱敏正则（仅用于 opt-in 错误详情）
_RE_BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}")
_RE_API_KEY = re.compile(r"\b(?:sk|pk|rk|ghp|gho|xox[baprs]|pat|pacxio)[-_][A-Za-z0-9_-]{10,}\b")
_RE_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_RE_URL_QUERY = re.compile(r"(https?://[^\s?#]+)\?[^\s#\"'<>]+")
_RE_WIN_PATH = re.compile(r"(?i)\b[A-Z]:\\(?:[^\s\"'<>|:*?]+\\)*[^\s\"'<>|:*?]+")
_RE_UNIX_PATH = re.compile(
    r"(?:/(?:Users|home|var|tmp|private|opt|srv|mnt|Volumes|root|etc|Applications)(?:/[^\s\"'<>:,;)]+)+)"
)
_RE_SAFE_TOKEN = re.compile(r"[^A-Za-z0-9._-]+")


def telemetry_dir() -> Path:
    return config.home() / "telemetry"


def config_path() -> Path:
    return telemetry_dir() / "config.json"


def spool_path() -> Path:
    return telemetry_dir() / "events.jsonl"


def state_path() -> Path:
    return telemetry_dir() / "state.json"


def _lock_path() -> Path:
    return telemetry_dir() / "flush.lock"


def _env_bool(name: str) -> bool | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    v = raw.strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return None


def _atomic_write_json(path: Path, data: dict[str, Any], *, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        if private and os.name != "nt":
            with suppress(OSError):
                tmp.chmod(0o600)
        tmp.replace(path)
    finally:
        with suppress(OSError):
            tmp.unlink(missing_ok=True)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _load_config(*, create_id: bool = False) -> dict[str, Any]:
    raw = _read_json(config_path())
    install_id = raw.get("install_id")
    if not isinstance(install_id, str) or len(install_id) < 8:
        install_id = str(uuid.uuid4()) if create_id else ""
    cfg: dict[str, Any] = {
        "install_id": install_id,
        "enabled": raw.get("enabled") if isinstance(raw.get("enabled"), bool) else True,
        "error_reports": raw.get("error_reports")
        if isinstance(raw.get("error_reports"), bool)
        else False,
    }
    if create_id and raw.get("install_id") != install_id:
        with suppress(OSError):
            _atomic_write_json(config_path(), cfg, private=True)
    return cfg


def install_id(*, create: bool = True) -> str:
    return str(_load_config(create_id=create).get("install_id") or "")


def endpoint_url() -> str:
    custom = os.environ.get(TELEMETRY_URL_ENV, "").strip()
    if custom:
        return custom
    return f"{config.login().url}/v1/telemetry/events"


def effective_status() -> dict[str, Any]:
    """返回当前生效的统计与错误上报开关及原因（纯本地，不联网）。"""
    cfg = _load_config(create_id=False)
    env_flag = _env_bool(TELEMETRY_ENV)
    dnt = _env_bool(DO_NOT_TRACK_ENV)
    err_env = _env_bool(ERROR_REPORTS_ENV)

    enabled = bool(cfg["enabled"])
    reason = "enabled" if enabled else "disabled_by_config"

    if env_flag is False:
        enabled, reason = False, "disabled_by_env"
    elif env_flag is True:
        enabled, reason = True, "enabled_by_env"
    else:
        if dnt is True:
            enabled, reason = False, "do_not_track"
        elif os.environ.get("PYTEST_CURRENT_TEST"):
            enabled, reason = False, "pytest_environment"
        elif os.environ.get("CI"):
            enabled, reason = False, "ci_environment"
        elif os.environ.get(config.MODE_ENV, "").strip() == "0":
            enabled, reason = False, "local_offline_mode"
        elif config.closed_available() and os.environ.get(config.MODE_ENV, "").strip() != "1":
            enabled, reason = False, "closed_dev_or_server_env"

    if not enabled:
        error_reports = False
        error_reason = reason
    elif err_env is True:
        error_reports, error_reason = True, "enabled_by_env"
    elif err_env is False:
        error_reports, error_reason = False, "disabled_by_env"
    else:
        error_reports = bool(cfg["error_reports"])
        error_reason = "enabled_by_config" if error_reports else "opt_in_off"

    pending = len(read_spool())
    st = _read_json(state_path())
    return {
        "enabled": enabled,
        "reason": reason,
        "config_enabled": bool(cfg["enabled"]),
        "error_reports": error_reports,
        "error_reports_reason": error_reason,
        "config_error_reports": bool(cfg["error_reports"]),
        "install_id": cfg["install_id"] or None,
        "endpoint": endpoint_url(),
        "pending_events": pending,
        "last_flush_at": st.get("last_flush_at"),
        "last_status": st.get("last_status"),
    }


def is_enabled() -> bool:
    return bool(effective_status()["enabled"])


def error_reports_enabled() -> bool:
    return bool(effective_status()["error_reports"])


def set_enabled(enabled: bool) -> dict[str, Any]:
    cfg = _load_config(create_id=enabled)
    cfg["enabled"] = bool(enabled)
    _atomic_write_json(config_path(), cfg, private=True)
    if not enabled:
        clear_spool()
    return effective_status()


def set_error_reports(enabled: bool) -> dict[str, Any]:
    cfg = _load_config(create_id=enabled)
    cfg["error_reports"] = bool(enabled)
    _atomic_write_json(config_path(), cfg, private=True)
    return effective_status()


def clear_spool() -> int:
    count = len(read_spool())
    with suppress(OSError):
        spool_path().unlink(missing_ok=True)
    return count


def install_id_header() -> dict[str, str]:
    """仅供 `cloud/client.py` 附带匿名安装 ID；关闭统计时不发送。"""
    if not is_enabled():
        return {}
    iid = install_id(create=True)
    return {"X-Pacxio-Install-Id": iid} if iid else {}


# ---------------- 隐私哈希与脱敏 ----------------


def _sanitize_token(value: str | None, *, max_len: int = 40) -> str | None:
    if not value or not isinstance(value, str):
        return None
    cleaned = _RE_SAFE_TOKEN.sub("-", value.strip()).strip("-")[:max_len]
    return cleaned or None


def job_hash(job: str | Path | None, *, iid: str | None = None) -> str | None:
    """用匿名 install_id 做加盐截断 SHA-256，用于串联同一盒子的漏斗，绝不暴露路径或品牌名。"""
    if not job:
        return None
    try:
        resolved = str(Path(job).expanduser().resolve())
    except OSError:
        resolved = str(job)
    salt = iid or install_id(create=True)
    return hashlib.sha256(f"{salt}:{resolved}".encode()).hexdigest()[:16]


def scrub_text(text: str, *, max_len: int = 400) -> str:
    """从可选错误详情中剥离路径、邮箱、Bearer 令牌、API 密钥和 URL 查询串。"""
    s = str(text or "")
    s = _RE_BEARER.sub(r"\1<redacted>", s)
    s = _RE_API_KEY.sub("<redacted-key>", s)
    s = _RE_EMAIL.sub("<redacted-email>", s)
    s = _RE_URL_QUERY.sub(r"\1?<redacted>", s)
    s = _RE_WIN_PATH.sub("<path>", s)
    s = _RE_UNIX_PATH.sub("<path>", s)
    s = " ".join(s.split())
    return s[:max_len]


def _relative_frame_path(filename: str) -> str:
    norm = filename.replace("\\", "/")
    for marker in ("/pacxio/", "/pacxio_pro/"):
        idx = norm.rfind(marker)
        if idx >= 0:
            return norm[idx + 1 :]
    return Path(norm).name


def scrub_traceback(exc: BaseException) -> list[str]:
    """仅保留脱敏后的包内相对文件名、行号与函数名，不包含任何源码上下文或局部变量值。"""
    tb = exc.__traceback__
    if tb is None:
        return []
    frames = traceback.extract_tb(tb)
    out: list[str] = []
    for frame in frames[-MAX_TRACEBACK_FRAMES:]:
        rel = _relative_frame_path(frame.filename)
        fn = _sanitize_token(frame.name, max_len=64) or "<fn>"
        out.append(f"{rel}:{frame.lineno}:{fn}")
    return out


# ---------------- 上下文与事件构建 ----------------


def begin_cli() -> None:
    _CLI_STARTED_AT.set(time.monotonic())


def _elapsed_cli_ms() -> int | None:
    started = _CLI_STARTED_AT.get()
    if started is None:
        return None
    return max(0, int((time.monotonic() - started) * 1000))


def in_scoped_call() -> bool:
    return bool(_IN_SCOPE.get())


@contextmanager
def scoped_call() -> Iterator[None]:
    """在 MCP 外层调用内屏蔽内层 CLI / cloud.route 重复记账。"""
    token = _IN_SCOPE.set(True)
    try:
        yield
    finally:
        _IN_SCOPE.reset(token)


def set_mcp_client(name: str | None) -> str | None:
    global _MCP_CLIENT_NAME
    clean = _sanitize_token(name, max_len=40)
    with _MCP_CLIENT_LOCK:
        _MCP_CLIENT_NAME = clean
    return clean


def mcp_client() -> str | None:
    with _MCP_CLIENT_LOCK:
        return _MCP_CLIENT_NAME


def stage_of(command: str) -> str | None:
    cmd = " ".join(command.strip().lower().split())
    if cmd in COMMAND_STAGES:
        return COMMAND_STAGES[cmd]
    # 子命令前缀归类（如 restore text / art update / finish remove 等）
    head = cmd.split(" ", 1)[0] if cmd else ""
    if head in ("art", "restore", "finish", "surface", "color", "attach", "window"):
        return "compose"
    if head == "concept":
        return "concept"
    if head == "content":
        return "content"
    return None


def _client_info() -> dict[str, str]:
    py = f"{sys.version_info.major}.{sys.version_info.minor}"
    return {
        "version": __version__,
        "os": platform.system().lower() or sys.platform,
        "arch": platform.machine().lower() or "unknown",
        "python": py,
        "lang": lang_now(),
    }


def _extract_safe_meta(
    command: str,
    data: dict[str, Any] | None,
    *,
    mode: str | None = None,
    agent: str | None = None,
) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    if mode in ("local", "cloud"):
        meta["mode"] = mode
    clean_agent = _sanitize_token(agent)
    if clean_agent:
        meta["agent"] = clean_agent
    if not isinstance(data, dict):
        return meta

    cmd = " ".join(command.strip().lower().split())
    if cmd == "setup":
        if isinstance(data.get("yes"), bool):
            meta["applied"] = data["yes"]
        elif isinstance(data.get("applied"), bool):
            meta["applied"] = data["applied"]
        agents = data.get("agents")
        if isinstance(agents, list):
            clean = [a for x in agents if (a := _sanitize_token(str(x), max_len=24))]
            if clean:
                meta["agents"] = clean[:6]
        elif isinstance(data.get("agent"), str):
            a = _sanitize_token(data["agent"], max_len=24)
            if a:
                meta["agents"] = [a]
    elif cmd == "mcp install":
        if isinstance(data.get("client"), str):
            a = _sanitize_token(data["client"], max_len=24)
            if a:
                meta["agent"] = a
        if isinstance(data.get("written"), bool):
            meta["applied"] = data["written"]
    elif cmd in ("job init", "job paper"):
        for key in ("style", "material", "paper", "code", "fefco", "ecma"):
            val = data.get(key)
            if isinstance(val, str):
                clean_val = _sanitize_token(val, max_len=32)
                if clean_val:
                    meta[key] = clean_val
    elif cmd.startswith("concept "):
        vs = data.get("visual_style") or data.get("style_id")
        if isinstance(vs, str):
            clean_vs = _sanitize_token(vs, max_len=40)
            if clean_vs:
                meta["visual_style"] = clean_vs
    return meta


def build_event(
    *,
    command: str,
    ok: bool,
    exit_code: int = 0,
    surface: str = "cli",
    job: str | Path | None = None,
    duration_ms: int | None = None,
    error_ids: list[str] | None = None,
    issues: list[dict[str, str]] | None = None,
    exception: BaseException | None = None,
    data: dict[str, Any] | None = None,
    mode: str | None = None,
    agent: str | None = None,
    include_error_detail: bool | None = None,
) -> dict[str, Any] | None:
    """构建单条漏斗 / 错误事件；非关键只读成功命令返回 None。"""
    cmd = " ".join(command.strip().lower().split())
    if not cmd or cmd.startswith("telemetry"):
        return None
    stage = stage_of(cmd)
    if stage is None and ok and exception is None:
        return None

    iid = install_id(create=True)
    clean_ids: list[str] = []
    for eid in error_ids or []:
        tok = _sanitize_token(str(eid), max_len=32)
        if tok and tok not in clean_ids:
            clean_ids.append(tok)
        if len(clean_ids) >= MAX_ERROR_IDS:
            break

    event: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "event_id": str(uuid.uuid4()),
        "ts": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "install_id": iid,
        "stage": stage or "other",
        "command": cmd[:48],
        "surface": "mcp" if surface == "mcp" else "cli",
        "ok": bool(ok),
        "exit_code": int(exit_code),
        "client": _client_info(),
    }
    jh = job_hash(job, iid=iid)
    if jh:
        event["job_hash"] = jh
    if duration_ms is not None and duration_ms >= 0:
        event["duration_ms"] = int(duration_ms)
    if clean_ids:
        event["error_ids"] = clean_ids
    meta = _extract_safe_meta(cmd, data, mode=mode, agent=agent)
    if meta:
        event["meta"] = meta

    want_err = error_reports_enabled() if include_error_detail is None else bool(include_error_detail)
    if want_err and (not ok or exception is not None):
        detail: dict[str, Any] = {}
        if issues:
            scrubbed_issues = []
            for item in issues[:MAX_ISSUE_DETAILS]:
                eid = _sanitize_token(str(item.get("id") or "ERR"), max_len=32) or "ERR"
                msg = scrub_text(str(item.get("message") or ""))
                scrubbed_issues.append({"id": eid, "message": msg})
            if scrubbed_issues:
                detail["issues"] = scrubbed_issues
        if exception is not None:
            detail["exception_type"] = _sanitize_token(type(exception).__name__, max_len=64) or "Error"
            detail["exception_message"] = scrub_text(str(exception))
            tb = scrub_traceback(exception)
            if tb:
                detail["traceback"] = tb
        if detail:
            event["error_detail"] = detail

    return event


# ---------------- 本地有界队列与后台上报 ----------------


@contextmanager
def _flush_lock() -> Iterator[bool]:
    telemetry_dir().mkdir(parents=True, exist_ok=True)
    with _lock_path().open("a+b") as handle:
        if sys.platform == "win32":
            import msvcrt

            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                yield False
                return
            try:
                yield True
            finally:
                handle.seek(0)
                with suppress(OSError):
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
            try:
                yield True
            finally:
                with suppress(OSError):
                    fcntl.flock(handle, fcntl.LOCK_UN)


def read_spool(*, limit: int | None = None) -> list[dict[str, Any]]:
    p = spool_path()
    if not p.is_file():
        return []
    events: list[dict[str, Any]] = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict):
                events.append(obj)
    except OSError:
        return []
    if limit is not None and limit >= 0:
        return events[-limit:] if limit > 0 else []
    return events


def _write_spool(events: list[dict[str, Any]]) -> None:
    p = spool_path()
    if not events:
        with suppress(OSError):
            p.unlink(missing_ok=True)
        return
    trimmed = events[-MAX_SPOOL_EVENTS:]
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".events.", dir=p.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for ev in trimmed:
                fh.write(json.dumps(ev, ensure_ascii=False, separators=(",", ":")) + "\n")
        if os.name != "nt":
            with suppress(OSError):
                tmp.chmod(0o600)
        tmp.replace(p)
    finally:
        with suppress(OSError):
            tmp.unlink(missing_ok=True)


def append_event(event: dict[str, Any]) -> int:
    """追加 1 条事件到本地有界队列，返回当前排队条数。"""
    p = spool_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
    try:
        with p.open("a", encoding="utf-8") as fh:
            fh.write(line)
        size = p.stat().st_size
        if size > MAX_SPOOL_BYTES:
            events = read_spool()
            _write_spool(events[-MAX_SPOOL_EVENTS:])
            return min(len(events), MAX_SPOOL_EVENTS)
        # 快速计数
        return len(read_spool())
    except OSError:
        return 0


def _should_trigger_flush(command: str, pending: int, *, has_error_detail: bool = False) -> bool:
    if pending <= 0:
        return False
    now = time.time()
    st = _read_json(state_path())
    next_retry = float(st.get("next_retry_at") or 0.0)
    if next_retry > now:
        return False
    if has_error_detail or pending >= FLUSH_BATCH_EVENTS:
        return True
    if command in MILESTONE_COMMANDS:
        return True
    last_flush = float(st.get("last_flush_at") or 0.0)
    return (now - last_flush) >= FLUSH_INTERVAL_S


def flush_now(*, force: bool = False, timeout_s: float = FLUSH_TIMEOUT_S) -> dict[str, Any]:
    """同步上报队列里的事件（由后台子进程 / 守护线程或 `pacxio telemetry flush` 调用）。"""
    if not force and not is_enabled():
        return {"sent": 0, "remaining": len(read_spool()), "status": "disabled"}

    with _flush_lock() as acquired:
        if not acquired:
            return {"sent": 0, "remaining": len(read_spool()), "status": "busy"}

        now = time.time()
        st = _read_json(state_path())
        if not force and float(st.get("next_retry_at") or 0.0) > now:
            return {"sent": 0, "remaining": len(read_spool()), "status": "backoff"}

        events = read_spool()
        if not events:
            return {"sent": 0, "remaining": 0, "status": "empty"}

        batch = events[:50]
        sent_ids = {str(e.get("event_id") or "") for e in batch}
        url = endpoint_url()
        payload = json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "install_id": install_id(create=True),
                "events": batch,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "User-Agent": f"pacxio-cli/{__version__}",
        }
        iid = install_id(create=False)
        if iid:
            headers["X-Pacxio-Install-Id"] = iid
        login = config.login()
        if login.token and url.startswith(login.url):
            headers["Authorization"] = f"Bearer {login.token}"

        req = urllib.request.Request(url, data=payload, method="POST", headers=headers)
        status_code: int | None = None
        err_kind: str | None = None
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                status_code = int(resp.status)
        except urllib.error.HTTPError as e:
            status_code = int(e.code)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            err_kind = type(e).__name__

        # 重新读一次队列，防止上报期间并发追加的新事件丢失
        latest = read_spool()
        if status_code is not None and 200 <= status_code < 300:
            remaining = [e for e in latest if str(e.get("event_id") or "") not in sent_ids]
            _write_spool(remaining)
            _atomic_write_json(
                state_path(),
                {
                    "last_flush_at": now,
                    "next_retry_at": 0.0,
                    "failures": 0,
                    "last_status": f"ok:{status_code}",
                },
            )
            return {"sent": len(batch), "remaining": len(remaining), "status": "sent"}

        if status_code in (400, 413, 422):
            # 坏包丢弃，避免死循环重试
            remaining = [e for e in latest if str(e.get("event_id") or "") not in sent_ids]
            _write_spool(remaining)
            _atomic_write_json(
                state_path(),
                {
                    "last_flush_at": now,
                    "next_retry_at": now + MIN_ERROR_BACKOFF_S,
                    "failures": 0,
                    "last_status": f"dropped:{status_code}",
                },
            )
            return {"sent": 0, "remaining": len(remaining), "status": f"dropped:{status_code}"}

        if status_code in (404, 405, 501):
            # 云端尚未上线采集接口：静默退避 6 小时，零报错、不刷屏
            _atomic_write_json(
                state_path(),
                {
                    "last_flush_at": now,
                    "next_retry_at": now + NOT_FOUND_BACKOFF_S,
                    "failures": 0,
                    "last_status": f"unsupported:{status_code}",
                },
            )
            return {"sent": 0, "remaining": len(latest), "status": f"unsupported:{status_code}"}

        failures = int(st.get("failures") or 0) + 1
        backoff = min(MIN_ERROR_BACKOFF_S * (2 ** min(failures - 1, 6)), MAX_ERROR_BACKOFF_S)
        tag = f"http:{status_code}" if status_code is not None else f"net:{err_kind or 'error'}"
        _atomic_write_json(
            state_path(),
            {
                "last_flush_at": now,
                "next_retry_at": now + backoff,
                "failures": failures,
                "last_status": tag,
            },
        )
        return {"sent": 0, "remaining": len(latest), "status": tag}


def _spawn_flush_subprocess() -> None:
    """独立后台短进程异步上报，绝不阻塞 CLI 退出、绝不污染 stdout/stderr。"""
    try:
        options: dict[str, Any] = {"start_new_session": True}
        if sys.platform == "win32":
            options = {
                "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            }
        env = os.environ.copy()
        env["PACXIO_NO_UPDATE"] = "1"
        subprocess.Popen(
            [sys.executable, "-I", "-m", "pacxio.cloud.telemetry", "--flush"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            env=env,
            **options,
        )
    except OSError:
        pass


def _spawn_flush_thread() -> None:
    """长驻 MCP 服务内用守护线程异步上报，省去频繁派生子进程。"""
    global _MCP_FLUSH_RUNNING
    with _MCP_FLUSH_LOCK:
        if _MCP_FLUSH_RUNNING:
            return
        _MCP_FLUSH_RUNNING = True

    def _run() -> None:
        global _MCP_FLUSH_RUNNING
        try:
            flush_now(force=False)
        except Exception:  # noqa: BLE001
            pass
        finally:
            with _MCP_FLUSH_LOCK:
                _MCP_FLUSH_RUNNING = False

    threading.Thread(target=_run, name="pacxio-telemetry-flush", daemon=True).start()


def _record_and_maybe_flush(event: dict[str, Any] | None, *, surface: str) -> None:
    if event is None:
        return
    pending = append_event(event)
    if _should_trigger_flush(
        str(event.get("command") or ""),
        pending,
        has_error_detail="error_detail" in event,
    ):
        if surface == "mcp":
            _spawn_flush_thread()
        else:
            _spawn_flush_subprocess()


# ---------------- 对外记录入口（永不抛异常）----------------


def record_result(
    result: Any,
    *,
    surface: str = "cli",
    duration_ms: int | None = None,
    mode: str | None = None,
    agent: str | None = None,
    command_override: str | None = None,
) -> None:
    """记录一条 `Result` 或 MCP 结果字典；任何内部异常均静默吞掉，绝不影响主流程。"""
    try:
        if not is_enabled():
            return
        if isinstance(result, dict):
            command = command_override or str(result.get("command") or "")
            ok = bool(result.get("ok", True))
            exit_code = int(result.get("exit_code", 0 if ok else 1))
            job = result.get("job")
            data = result.get("data") if isinstance(result.get("data"), dict) else None
            raw_errors = result.get("errors") or []
            error_ids = [
                str(e.get("id") or "") for e in raw_errors if isinstance(e, dict) and e.get("id")
            ]
            issues = [
                {"id": str(e.get("id") or "ERR"), "message": str(e.get("message") or "")}
                for e in raw_errors
                if isinstance(e, dict)
            ]
        else:
            command = command_override or str(getattr(result, "command", "") or "")
            ok = bool(getattr(result, "ok", True))
            exit_code = int(getattr(result, "exit_code", 0 if ok else 1))
            job = getattr(result, "job", None)
            data = getattr(result, "data", None)
            raw_errors = getattr(result, "errors", None) or []
            error_ids = [str(getattr(e, "id", "")) for e in raw_errors if getattr(e, "id", None)]
            issues = [
                {
                    "id": str(getattr(e, "id", "ERR")),
                    "message": str(getattr(e, "message", "")),
                }
                for e in raw_errors
            ]

        if duration_ms is None and surface == "cli":
            duration_ms = _elapsed_cli_ms()
        if mode is None:
            mode = "cloud" if config.use_cloud() else "local"
        if agent is None and surface == "mcp":
            agent = mcp_client()

        event = build_event(
            command=command,
            ok=ok,
            exit_code=exit_code,
            surface=surface,
            job=job,
            duration_ms=duration_ms,
            error_ids=error_ids,
            issues=issues,
            data=data,
            mode=mode,
            agent=agent,
        )
        _record_and_maybe_flush(event, surface=surface)
    except Exception:  # noqa: BLE001
        pass


def record_mcp_initialize(client_name: str | None) -> None:
    """记录一次真实 Agent 客户端的 MCP 握手连接（跳过内部自检探针）。"""
    try:
        clean = set_mcp_client(client_name)
        if clean and clean.lower() in PROBE_CLIENTS:
            return
        if not is_enabled():
            return
        event = build_event(
            command="mcp initialize",
            ok=True,
            exit_code=0,
            surface="mcp",
            mode="cloud" if config.use_cloud() else "local",
            agent=clean,
        )
        _record_and_maybe_flush(event, surface="mcp")
    except Exception:  # noqa: BLE001
        pass


def record_exception(
    command: str,
    exc: BaseException,
    *,
    surface: str = "cli",
    job: str | Path | None = None,
    duration_ms: int | None = None,
) -> None:
    """记录未捕获异常；仅在 opt-in 错误上报开启时附带脱敏异常与包内栈帧。"""
    try:
        if not is_enabled():
            return
        if duration_ms is None and surface == "cli":
            duration_ms = _elapsed_cli_ms()
        event = build_event(
            command=command or "cli",
            ok=False,
            exit_code=1,
            surface=surface,
            job=job,
            duration_ms=duration_ms,
            error_ids=[type(exc).__name__],
            exception=exc,
            mode="cloud" if config.use_cloud() else "local",
            agent=mcp_client() if surface == "mcp" else None,
        )
        _record_and_maybe_flush(event, surface=surface)
    except Exception:  # noqa: BLE001
        pass


def _main() -> None:
    if len(sys.argv) == 2 and sys.argv[1] == "--flush":
        flush_now(force=False)


if __name__ == "__main__":
    _main()


__all__ = [
    "COMMAND_STAGES",
    "DO_NOT_TRACK_ENV",
    "ERROR_REPORTS_ENV",
    "MILESTONE_COMMANDS",
    "PROBE_CLIENTS",
    "SCHEMA_VERSION",
    "TELEMETRY_ENV",
    "TELEMETRY_URL_ENV",
    "append_event",
    "begin_cli",
    "build_event",
    "clear_spool",
    "config_path",
    "effective_status",
    "endpoint_url",
    "error_reports_enabled",
    "flush_now",
    "in_scoped_call",
    "install_id",
    "install_id_header",
    "is_enabled",
    "job_hash",
    "mcp_client",
    "read_spool",
    "record_exception",
    "record_mcp_initialize",
    "record_result",
    "scoped_call",
    "scrub_text",
    "scrub_traceback",
    "set_enabled",
    "set_error_reports",
    "set_mcp_client",
    "spool_path",
    "stage_of",
    "state_path",
    "telemetry_dir",
]
