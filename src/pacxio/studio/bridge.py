"""Bounded, local waits for Workbench decisions; never makes a decision for a user."""

from __future__ import annotations

import contextvars
import hashlib
import json
import math
import os
import re
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from pacxio.messages import t
from pacxio.model.common import Issue, Result

POLL_S = 0.25
LEASE_S = 5.0
RECEIPT_S = 45.0
MAX_WAIT_S = 45.0  # Below the clients' default per-tool timeout.
WAIT_CANCEL: contextvars.ContextVar[threading.Event | None] = contextvars.ContextVar(
    "studio_wait_cancel", default=None
)
REVIEW_FILES = (
    "job.json",
    "content_confirmed.json",
    "plan_confirmed.json",
    "concepts/chosen.json",
    "concepts/in_use.json",
    "concepts/flats_confirmed.json",
    "mockup/approval.json",
    "review/notes.json",
)


def revision_of(directory: Path) -> str:
    """Content cursor, independent of display language, mtimes and bridge heartbeats."""
    digest = hashlib.sha256()
    files = set(REVIEW_FILES)
    files.update(
        p.relative_to(directory).as_posix() for p in (directory / "concepts").glob("*.meta.json")
    )
    for name in sorted(files):
        digest.update(name.encode())
        path = directory / name
        for attempt in range(4):
            try:
                raw = path.read_bytes()
                value = json.loads(raw)
                digest.update(json.dumps(value, sort_keys=True, ensure_ascii=False).encode())
                break
            except FileNotFoundError:
                digest.update(b"<absent>")
                break
            except json.JSONDecodeError:
                # Non-atomic concurrent writers briefly expose an empty/partial file; retry shortly.
                if attempt == 3:
                    raise
                time.sleep(0.015)
        digest.update(b"\0")
    return digest.hexdigest()


def _folder(directory: Path) -> Path:
    return directory / "studio"


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _read(directory: Path) -> dict[str, Any]:
    return _read_json_file(_folder(directory) / "agent.json")


def _write_json_atomic(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.stem}-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(record, output, ensure_ascii=False)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def _write(directory: Path, status: str, stage: str | None, revision: str, ttl: float) -> None:
    now = time.time()
    record = {
        "status": status,
        "stage": stage,
        "revision": revision,
        "pid": os.getpid(),
        "updated_at": now,
        "expires_at": now + ttl,
    }
    _write_json_atomic(_folder(directory) / "agent.json", record)


def write_task(
    directory: Path,
    command: str,
    *,
    run_id: str | None = None,
    started_at: float | None = None,
    detail: str | None = None,
    ttl: float = 180.0,
) -> None:
    """Record an active long-running cloud or local task so the Workbench shows live progress."""
    now = time.time()
    record = {
        "status": "running",
        "command": command,
        "run_id": run_id,
        "started_at": started_at if started_at is not None else now,
        "updated_at": now,
        "expires_at": now + ttl,
        "detail": detail,
    }
    _write_json_atomic(_folder(directory) / "task.json", record)


def clear_task(directory: Path) -> None:
    """Clear the active task marker when the command finishes or fails."""
    (_folder(directory) / "task.json").unlink(missing_ok=True)


def active_task(directory: Path) -> dict[str, Any] | None:
    """Return the currently running local or cloud task if still fresh, otherwise None."""
    now = time.time()
    for path in (_folder(directory) / "task.json", directory / ".cloud" / "active_run.json"):
        record = _read_json_file(path)
        if record.get("status") != "running":
            continue
        if now >= float(record.get("expires_at") or 0):
            continue
        started = float(record.get("started_at") or record.get("updated_at") or now)
        return {
            "status": "running",
            "command": str(record.get("command") or record.get("cmd") or ""),
            "run_id": record.get("run_id") or record.get("rid"),
            "started_at": started,
            "elapsed_s": max(0, int(now - started)),
            "detail": record.get("detail"),
        }
    return None


def agent_status(directory: Path) -> dict[str, Any]:
    record = _read(directory)
    status = record.get("status", "idle")
    if status in ("waiting", "notified", "received"):
        expired = time.time() >= record.get("expires_at", 0)
        if not expired and status == "waiting":
            try:
                os.kill(int(record["pid"]), 0)
            except (OSError, SystemError, KeyError, ValueError):
                expired = True
        if expired:
            status = "idle" if status == "received" else "disconnected"
    return {
        "status": status,
        "stage": record.get("stage"),
        "revision": record.get("revision"),
        "updated_at": record.get("updated_at"),
        "expires_at": record.get("expires_at"),
        "task": active_task(directory),
    }


@contextmanager
def _lock(directory: Path) -> Iterator[bool]:
    """One waiter per job, including across MCP processes; crashes release the lock."""
    folder = _folder(directory)
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "agent.lock").open("a+b") as handle:
        if sys.platform == "win32":
            import msvcrt

            if os.fstat(handle.fileno()).st_size == 0:
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
                fcntl.flock(handle, fcntl.LOCK_UN)


def acknowledge(directory: Path, revision: str) -> None:
    """A real next/job_next call acknowledges a delivered result, not a UI refresh."""
    cur_status = _read(directory).get("status")
    if cur_status not in ("notified", "disconnected"):
        return
    with _lock(directory) as locked:
        if not locked:
            return
        record = _read(directory)
        if record.get("status") == "notified" and revision_of(directory) == revision:
            _write(directory, "received", record.get("stage"), revision, RECEIPT_S)
        elif record.get("status") == "disconnected":
            _write(directory, "idle", record.get("stage"), revision, 0)


def wait_for_user(
    directory: Path,
    after_revision: str,
    timeout_s: float = 40,
    cancel: threading.Event | None = None,
) -> Result:
    """Wait for review changes, returning the latest next step without approving it."""
    from pacxio.studio.next import next_step

    r = Result(command="studio wait", job=str(directory))
    if (
        not isinstance(after_revision, str)
        or not re.fullmatch(r"[0-9a-f]{64}", after_revision)
        or isinstance(timeout_s, bool)
        or not isinstance(timeout_s, int | float)
        or not math.isfinite(timeout_s)
        or not 0 < timeout_s <= MAX_WAIT_S
    ):
        return r.fail(
            Issue(
                id="ARG",
                message=t(
                    "Use the revision from job_next and a timeout greater than zero and no more than 45 seconds."
                ),
            ),
            2,
        )
    initial = next_step(directory, strict=True)
    if not initial.ok:
        initial.command = r.command
        return initial
    cancel = cancel or WAIT_CANCEL.get() or threading.Event()
    stage = (initial.data.get("stage") or {}).get("id")
    with _lock(directory) as locked:
        if not locked:
            r.data = {"status": "busy", "revision": initial.data["revision"], "next": initial.data}
            return r
        deadline = time.monotonic() + timeout_s
        last = initial
        final_status = "disconnected"
        try:
            while True:
                if cancel.is_set():
                    status = "cancelled"
                    break
                current = revision_of(directory)
                if current != after_revision:
                    last = next_step(directory, strict=True)
                    if not last.ok:
                        last.command = r.command
                        return last
                    status, final_status = "changed", "notified"
                    break
                if initial.data.get("who") != "user":
                    status, final_status = "not_waiting", "idle"
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    status = "timeout"
                    break
                _write(directory, "waiting", stage, after_revision, LEASE_S)
                cancel.wait(min(POLL_S, remaining))
            r.data = {"status": status, "revision": last.data["revision"], "next": last.data}
            return r
        except (OSError, ValueError) as exc:
            return r.fail(
                Issue(
                    id="STUDIO",
                    message=t("Could not read the Workbench review state: {error}", error=exc),
                ),
                2,
            )
        finally:
            _write(
                directory,
                final_status,
                stage,
                last.data.get("revision", after_revision),
                RECEIPT_S if final_status == "notified" else 0,
            )
