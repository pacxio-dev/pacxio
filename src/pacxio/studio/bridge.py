"""Bounded, local waits for Workbench decisions; never makes a decision for a user."""

from __future__ import annotations

import contextvars
import hashlib
import json
import math
import os
import re
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
        try:
            raw = (directory / name).read_bytes()
            value = json.loads(raw)
            digest.update(json.dumps(value, sort_keys=True, ensure_ascii=False).encode())
        except FileNotFoundError:
            digest.update(b"<absent>")
        digest.update(b"\0")
    return digest.hexdigest()


def _folder(directory: Path) -> Path:
    return directory / "studio"


def _read(directory: Path) -> dict[str, Any]:
    try:
        value = json.loads((_folder(directory) / "agent.json").read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(directory: Path, status: str, stage: str | None, revision: str, ttl: float) -> None:
    folder = _folder(directory)
    folder.mkdir(parents=True, exist_ok=True)
    now = time.time()
    record = {
        "status": status,
        "stage": stage,
        "revision": revision,
        "pid": os.getpid(),
        "updated_at": now,
        "expires_at": now + ttl,
    }
    fd, name = tempfile.mkstemp(prefix=".agent-", dir=folder)
    try:
        with os.fdopen(fd, "w") as output:
            json.dump(record, output)
        os.replace(name, folder / "agent.json")
    finally:
        Path(name).unlink(missing_ok=True)


def agent_status(directory: Path) -> dict[str, Any]:
    record = _read(directory)
    status = record.get("status", "idle")
    if status in ("waiting", "notified", "received"):
        expired = time.time() >= record.get("expires_at", 0)
        if not expired and status == "waiting":
            try:
                os.kill(int(record["pid"]), 0)
            except (OSError, KeyError, ValueError):
                expired = True
        if expired:
            status = "idle" if status == "received" else "disconnected"
    return {
        "status": status,
        "stage": record.get("stage"),
        "revision": record.get("revision"),
        "updated_at": record.get("updated_at"),
        "expires_at": record.get("expires_at"),
    }


@contextmanager
def _lock(directory: Path) -> Iterator[bool]:
    """One waiter per job, including across MCP processes; crashes release the lock."""
    folder = _folder(directory)
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "agent.lock").open("a+b") as handle:
        if os.name == "nt":
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
    if _read(directory).get("status") != "notified":
        return
    with _lock(directory) as locked:
        record = _read(directory)
        if locked and record.get("status") == "notified" and revision_of(directory) == revision:
            _write(directory, "received", record.get("stage"), revision, RECEIPT_S)


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
