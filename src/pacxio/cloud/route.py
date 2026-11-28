"""哪些命令转给云端，怎么转（–2 节）。

`maybe_forward(argv)` 在命令行入口和 MCP 交给 Typer 之前调用：不该转发就返回 None，
该转发就同步 job、在服务器上跑同一条命令、把允许回传的文件拉回来，返回（退出码, 输出文本）。
"""

from __future__ import annotations

import contextvars
import inspect
import json
import os
import random
import sys
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from pacxio.cloud import auth, config
from pacxio.cloud.client import RETRY_STATUSES, Client, CloudError
from pacxio.cloud.sync import (
    SKIP_TOP,
    SYNC_WORKERS,
    pull,
    push_with_retry,
    sha256_bytes,
    sha256_file,
)
from pacxio.messages import t
from pacxio.model.common import Issue, Result

# 和服务端 pacxio_server.policy.CLOUD_COMMANDS 保持一致（服务端测试会对照）
CLOUD_COMMANDS: frozenset[tuple[str, ...]] = frozenset({
    ("render",),
    ("preflight",),
    ("export",),
    ("mockup",),
    ("plan",),
    ("flat",),
    ("approve",),
    ("art", "*"),
    ("restore", "*"),
    ("visual", "trace"),
    ("visual", "prepare"),
    ("concept", "choose"),  # 取主色要换算 CMYK
    ("color", "*"),
    ("finish", "*"),
    ("surface", "*"),
    ("attach", "*"),  # 配件外形要转矢量
})  # fmt: skip

POLL_S = 0.5
RUN_TIMEOUT_S = 30 * 60
PROGRESS_INTERVAL_S = 8.0
PROGRESS_CALLBACK: contextvars.ContextVar[Callable[[int, str], None] | None] = (
    contextvars.ContextVar("pacxio_cloud_progress", default=None)
)
ACTIVE_RUN_FILE = "active_run.json"


def command_of(argv: list[str]) -> tuple[str, ...] | None:
    words = [a for a in argv[:2] if not a.startswith("-")]
    if len(words) >= 2 and (
        (words[0], words[1]) in CLOUD_COMMANDS or (words[0], "*") in CLOUD_COMMANDS
    ):
        return (words[0], words[1])
    if words and (words[0],) in CLOUD_COMMANDS:
        return (words[0],)
    return None


def should_forward(argv: list[str]) -> bool:
    if any(a in ("--help", "-h") for a in argv):
        return False
    return command_of(argv) is not None and config.use_cloud()


def maybe_forward(argv: list[str], cwd: Path | None = None) -> tuple[int, str] | None:
    if not should_forward(argv):
        return None
    as_json = "--json" in argv
    started = time.monotonic()
    result = forward(argv, cwd or Path.cwd())
    from pacxio.cloud import telemetry

    if not telemetry.in_scoped_call():
        telemetry.record_result(
            result,
            surface="cli",
            mode="cloud",
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    return result.exit_code, render(result, as_json)


# ---------------- 参数改写 ----------------


class _Prepared:
    def __init__(self) -> None:
        self.argv: list[Any] = []
        self.job: Path | None = None
        self.inbox: dict[str, Path] = {}  # "<sha>/<name>" → 本机路径


def _is_job(p: Path) -> bool:
    return p.is_dir() and (p / "job.json").is_file()


def _inside_job(s: str, cwd: Path, job: Path | None) -> str | None:
    """本机不存在、但指向 job 里面的路径（比如只在服务器上的矢量稿）→ job 内相对路径。"""
    if job is None or not s or s.startswith("-") or "/" not in s:
        return None
    q = Path(s).expanduser()
    q = (q if q.is_absolute() else cwd / q).resolve()
    if q != job and q.is_relative_to(job):
        return q.relative_to(job).as_posix()
    return None


def prepare(argv: list[str], cwd: Path) -> _Prepared:
    """把 argv 里的 job 路径、文件路径换成占位；外部文件记下来待上传。"""
    out = _Prepared()
    items: list[str] = []
    for a in argv:
        if a == "--json":
            continue
        if a.startswith("--") and "=" in a:  # --key=value → --key value，值单独改写
            k, _, v = a.partition("=")
            items += [k, v]
        else:
            items.append(a)

    def resolve(s: str) -> Path | None:
        if not s or s.startswith("-"):
            return None
        p = Path(s).expanduser()
        p = p if p.is_absolute() else cwd / p
        return p if p.exists() else None

    for s in items:  # 先找 job
        p = resolve(s)
        if p is not None and _is_job(p):
            out.job = p.resolve()
            break
    for s in items:
        p = resolve(s)
        if p is None:
            inside = _inside_job(s, cwd, out.job)
            out.argv.append({"jobfile": inside} if inside else s)
        elif out.job is not None and p.resolve() == out.job:
            out.argv.append({"job": True})
        elif p.is_file():
            rp = p.resolve()
            if out.job is not None and rp.is_relative_to(out.job):
                rel = rp.relative_to(out.job)
                if rel.parts[0] not in SKIP_TOP:
                    out.argv.append({"jobfile": rel.as_posix()})
                    continue
            key = f"{sha256_file(rp)}/{rp.name}"
            out.inbox[key] = rp
            out.argv.append({"file": key})
        else:
            out.argv.append(s)  # 存在的普通目录：服务器上没有对应，按原样交给服务器（会被拒）
    return out


# ---------------- 瞬时字体子集与单字体授权门禁（R2-5）----------------


def prepare_ephemeral_fonts(
    job_dir: Path,
) -> tuple[list[tuple[str, str, bytes]], list[dict[str, Any]], list[Issue]]:
    """检查 job 用到的非开源字体授权并切出最小字形子集（只用于单次云端运行，用后即删）。

    返回 `(inbox_blobs, ephemeral_specs, errors)`：
    - `inbox_blobs`: `[(sha256, filename, bytes), ...]`，发往本次请求的 `inbox/`；
    - `ephemeral_specs`: 附在 `POST /v1/jobs/{id}/runs` 请求体里的 `ephemeral_fonts` 契约；
    - `errors`: 未授权或被 `no_subsetting` 拦截时的错误清单（非空则本地直接拦截，不发任何请求）。
    """
    from pydantic import ValidationError

    from pacxio.fonts import get_font_consent, referenced_fonts, resolve_non_open_font
    from pacxio.fontsubset import (
        SUBSET_SUFFIX,
        FontSubsetError,
        SubsetMeta,
        job_font_chars,
        subset_font_bytes,
    )
    from pacxio.model.job import Job

    try:
        job = Job.load(job_dir)
    except (OSError, ValueError, ValidationError):
        return [], [], []

    char_map = job_font_chars(job)
    blobs: list[tuple[str, str, bytes]] = []
    specs: list[dict[str, Any]] = []
    errors: list[Issue] = []

    for fname in sorted(referenced_fonts(job)):
        info = resolve_non_open_font(fname, job_dir)
        if info is None:
            continue
        consent = get_font_consent(info.source_sha256)
        if consent is None:
            errors.append(
                Issue(
                    id="FONT_LICENSE",
                    message=t(
                        "Font {font} ({origin}) requires your one-time upload authorization before cloud typesetting",
                        font=info.name,
                        origin=info.origin,
                    ),
                    fix=t(
                        "Confirm that your license permits uploading glyph subsets for cloud outlining with `pacxio font authorize <job> {font} --by <your-name>`",
                        font=info.name,
                    ),
                )
            )
            continue
        chars = char_map.get(fname, {" "})
        try:
            font_bytes, ext, is_subset, glyph_count = subset_font_bytes(
                info.path,
                chars,
                font_name=info.name,
                face_index=info.face_index,
                allow_full_upload=consent.allow_full_upload,
            )
        except FontSubsetError as e:
            errors.append(e.issue)
            continue
        sub_sha = sha256_bytes(font_bytes)
        sub_filename = f"{info.name}{ext}"
        meta = SubsetMeta(
            name=info.name,
            subset=is_subset,
            subset_sha256=sub_sha,
            source_sha256=info.source_sha256,
            source_version=info.version,
            origin=info.origin,
            glyph_count=glyph_count,
            char_count=len(chars),
            ephemeral=True,
            delete_after_run=True,
            consent_confirmed_at=consent.confirmed_at,
            consent_confirmed_by=consent.confirmed_by,
        )
        meta_bytes = (meta.model_dump_json(indent=2) + "\n").encode("utf-8")
        meta_sha = sha256_bytes(meta_bytes)
        meta_filename = f"{info.name}{SUBSET_SUFFIX}"
        blobs.append((sub_sha, sub_filename, font_bytes))
        blobs.append((meta_sha, meta_filename, meta_bytes))
        specs.append(
            {
                "name": info.name,
                "filename": sub_filename,
                "inbox": f"{sub_sha}/{sub_filename}",
                "sha256": sub_sha,
                "meta_filename": meta_filename,
                "meta_inbox": f"{meta_sha}/{meta_filename}",
                "source_sha256": info.source_sha256,
                "source_version": info.version,
                "origin": info.origin,
                "subset": is_subset,
                "glyph_count": glyph_count,
                "char_count": len(chars),
                "ephemeral": True,
                "delete_after_run": True,
                "consent": {
                    "confirmed_by": consent.confirmed_by,
                    "confirmed_at": consent.confirmed_at,
                },
            }
        )
    return blobs, specs, errors


# ---------------- 转发 ----------------


def _active_run_path(job: Path) -> Path:
    return job / ".cloud" / ACTIVE_RUN_FILE


def _read_active_run(job: Path) -> dict[str, Any]:
    try:
        data = json.loads(_active_run_path(job).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_active_run(job: Path, data: dict[str, Any]) -> None:
    p = _active_run_path(job)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".active-run-", dir=p.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, p)
    finally:
        Path(tmp).unlink(missing_ok=True)


def _clear_active_run(job: Path) -> None:
    _active_run_path(job).unlink(missing_ok=True)


def forward(
    argv: list[str],
    cwd: Path,
    *,
    wait_budget_s: float | None = None,
    resume_run_id: str | None = None,
) -> Result:
    cmd = " ".join(command_of(argv) or ())
    res = Result(command=cmd)
    if cmd == "export" and any(a == "--out" or a.startswith("--out=") for a in argv):
        return res.fail(Issue(id="CLOUD_ARG", message=t("When exporting in the cloud the delivery pack always goes to <job>/out; --out is not supported"),
                              fix=t("Remove --out; after export, take the delivery pack from <job>/out")), 2)  # fmt: skip
    prep = prepare(argv, cwd)
    if prep.job is None:
        return res.fail(Issue(id="CLOUD_JOB", message=t("No job folder (a folder with job.json) found in the arguments"),
                              fix=t("Pass the job folder as an argument, e.g. `pacxio render jobs/<name>`")), 2)  # fmt: skip
    job = prep.job
    res.job = str(job)
    if resume_run_id is None:
        font_blobs, ephemeral_fonts, font_errors = prepare_ephemeral_fonts(job)
        if font_errors:
            res.errors += font_errors
            res.ok, res.exit_code = False, 2
            return res
    else:
        font_blobs, ephemeral_fonts = [], []
    try:
        login = auth.ensure()
    except CloudError as e:
        return res.fail(Issue(id=e.id, message=e.message, fix=e.fix), 3)
    client = Client(login.url, login.token)
    still_running = False
    try:
        if resume_run_id is not None:
            saved = _read_active_run(job)
            jid = str(saved.get("jid") or "")
            if not jid:
                from pacxio.cloud.sync import read_state

                jid = str(read_state(job).get("job") or "")
            before = saved.get("before_sha")
            started_at = float(saved.get("started_at") or time.time())
            inbox_map = {k: Path(v) for k, v in (saved.get("inbox") or {}).items()}
            rid = resume_run_id
        else:
            jid = push_with_retry(client, job)
            inbox_items: list[tuple[str, str, bytes]] = []
            for key, path in prep.inbox.items():
                sha, _, name = key.partition("/")
                inbox_items.append((sha, name, path.read_bytes()))
            inbox_items.extend(font_blobs)
            if len(inbox_items) <= 1:
                for sha, name, payload in inbox_items:
                    client.put_inbox(jid, sha, name, payload)
            else:
                with ThreadPoolExecutor(max_workers=min(SYNC_WORKERS, len(inbox_items))) as pool:
                    for _ in pool.map(lambda it: client.put_inbox(jid, it[0], it[1], it[2]), inbox_items):
                        pass
            before = _sha(job / "job.json")
            started_at = time.time()
            inbox_map = prep.inbox
            rid = (
                client.start_run(jid, prep.argv, ephemeral_fonts=ephemeral_fonts)
                if ephemeral_fonts
                else client.start_run(jid, prep.argv)
            )
        now_ts = time.time()
        _write_active_run(
            job,
            {
                "status": "running",
                "rid": rid,
                "run_id": rid,
                "jid": jid,
                "command": cmd,
                "before_sha": before,
                "started_at": started_at,
                "updated_at": now_ts,
                "expires_at": now_ts + 180.0,
                "detail": None,
                "inbox": {k: str(v) for k, v in inbox_map.items()},
            },
        )
        wait_params = inspect.signature(_wait).parameters
        if "budget_s" in wait_params or any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in wait_params.values()
        ):
            run = _wait(
                client,
                rid,
                budget_s=wait_budget_s,
                job=job,
                cmd=cmd,
                started_at=started_at,
            )
        else:
            run = _wait(client, rid)
        if run.get("status") == "running":
            still_running = True
            elapsed = max(1, int(time.time() - started_at))
            stage = run.get("stage") or run.get("message")
            now_ts = time.time()
            _write_active_run(
                job,
                {
                    "status": "running",
                    "rid": rid,
                    "run_id": rid,
                    "jid": jid,
                    "command": cmd,
                    "before_sha": before,
                    "started_at": started_at,
                    "updated_at": now_ts,
                    "expires_at": now_ts + 180.0,
                    "detail": str(stage) if stage else None,
                    "inbox": {k: str(v) for k, v in inbox_map.items()},
                },
            )
            res.ok = True
            res.data = {
                "status": "running",
                "run_id": rid,
                "elapsed_s": elapsed,
                "stage": stage,
                "message": t(
                    "Cloud task is still running ({elapsed}s elapsed). Tell the user it is working, then call restore_run again with the same job and run_id={rid} to continue waiting.",
                    elapsed=elapsed,
                    rid=rid,
                ),
            }
            return res
        if run["status"] != "done":
            return res.fail(Issue(id="CLOUD_RUN", message=t("The cloud run did not finish: {why}", why=run.get("error") or run["status"]),
                                  fix=t("Run it again; if it keeps failing, send us this message")))  # fmt: skip
        changed_locally = before is not None and _sha(job / "job.json") != before
        keep = {"job.json"} if changed_locally else set()
        if jid:
            pull(client, jid, job, run.get("files") or {}, keep=keep)
    except CloudError as e:
        if e.id == "CLOUD_AUTH":
            e = auth.relogin_after_401(login)
        return res.fail(
            Issue(id=e.id, message=e.message, fix=e.fix),
            3 if e.id in ("CLOUD_NET", "CLOUD_AUTH", "CLOUD_LOGIN") else 1,
        )
    finally:
        client.close()
        if not still_running:
            _clear_active_run(job)
    data = run.get("result")
    if not isinstance(data, dict):
        return res.fail(Issue(id="CLOUD_RUN", message=t("The cloud command returned no result: {tail}", tail=(run.get("error") or "")[-800:]),
                              fix=t("Run it again; if it keeps failing, send us this message")))  # fmt: skip
    data = _local_paths(data, job, inbox_map)
    out = Result.model_validate(data)
    out.exit_code = int(run.get("exit_code") or 0)
    if cmd == "mockup" and out.ok and not changed_locally:
        # Build only from the preview bytes actually returned by the server.
        # No renderer bundle, private artwork, or server update is required.
        from pacxio.mockup.display.service import prepare_job_display

        status, assets = prepare_job_display(job)
        out.data["display_renderer"] = status
        out.artifacts.extend(str(p) for p in assets)
    if changed_locally:
        out.warnings.append(Issue(id="CLOUD_CONFLICT", message=t("job.json changed on this machine while the cloud run was going; it was not overwritten with the cloud version"),
                                  fix=t("Rerun this command so both sides match")))  # fmt: skip
    return out


def _sha(p: Path) -> str | None:
    return sha256_bytes(p.read_bytes()) if p.is_file() else None


def _wait(
    client: Client,
    rid: str,
    *,
    budget_s: float | None = None,
    job: Path | None = None,
    cmd: str = "",
    started_at: float | None = None,
) -> dict[str, Any]:
    now_mono = time.monotonic()
    deadline = now_mono + RUN_TIMEOUT_S
    budget_deadline = (now_mono + budget_s) if budget_s is not None else None
    t0 = started_at if started_at is not None else time.time()
    last_progress = now_mono
    delay = POLL_S
    transient_errs = 0
    while True:
        try:
            run = client.get_run(rid)
            transient_errs = 0
        except CloudError as e:
            retryable = e.id == "CLOUD_NET" or (
                e.status_code is not None and e.status_code in RETRY_STATUSES
            )
            transient_errs += 1
            if not retryable or transient_errs > 3 or time.monotonic() > deadline:
                raise
            wait_s = e.retry_after if e.retry_after is not None else min(delay * 2.0, 5.0)
            time.sleep(min(max(0.2, wait_s), 10.0))
            continue
        if run.get("status") in ("done", "failed"):
            return run
        now_mono = time.monotonic()
        stage = run.get("stage") or run.get("message")
        if now_mono - last_progress >= PROGRESS_INTERVAL_S:
            last_progress = now_mono
            elapsed = max(1, int(time.time() - t0))
            if job is not None:
                cur = _read_active_run(job)
                now_ts = time.time()
                cur.update(
                    {
                        "status": "running",
                        "rid": rid,
                        "run_id": rid,
                        "command": cmd,
                        "started_at": t0,
                        "updated_at": now_ts,
                        "expires_at": now_ts + 180.0,
                        "detail": str(stage) if stage else None,
                    }
                )
                _write_active_run(job, cur)
            cb = PROGRESS_CALLBACK.get()
            if cb is not None:
                cb(elapsed, str(stage) if stage else f"{cmd or 'cloud'} ({elapsed}s)")
        if budget_deadline is not None and now_mono >= budget_deadline:
            return {"status": "running", "run_id": rid, "stage": stage}
        if now_mono > deadline:
            raise CloudError(
                "CLOUD_TIMEOUT", t("The cloud run timed out"), t("Try the same command again later")
            )
        sleep_s = delay * (0.85 + 0.3 * random.random())
        if budget_deadline is not None:
            sleep_s = min(sleep_s, max(0.05, budget_deadline - now_mono))
        time.sleep(sleep_s)
        delay = min(delay * 1.5, 3.0)


def _local_paths(data: dict[str, Any], job: Path, inbox: dict[str, Path]) -> dict[str, Any]:
    """服务器结果里的 @job / @inbox 占位换回本机路径。"""
    text = json.dumps(data, ensure_ascii=False)
    for key, path in inbox.items():
        text = text.replace(json.dumps(f"@inbox/{key}")[1:-1], json.dumps(str(path))[1:-1])
    text = text.replace("@job", json.dumps(str(job))[1:-1])
    return dict(json.loads(text))


def render(result: Result, as_json: bool) -> str:
    """和 cli._emit 一样的输出格式。"""
    if as_json:
        return result.model_dump_json(indent=2)
    lines = [f"{'✓' if result.ok else '✗'} {result.command}"]
    lines += [t("  created {path}", path=a) for a in result.artifacts]
    for i in result.errors:
        lines.append(
            t("  error [{id}] {message}", id=i.id, message=i.message)
            + (t("\n    fix: {fix}", fix=i.fix) if i.fix else "")
        )
    for i in result.warnings:
        lines.append(
            t("  warning [{id}] {message}", id=i.id, message=i.message)
            + (t("\n    suggestion: {fix}", fix=i.fix) if i.fix else "")
        )
    return "\n".join(lines)


def main_hook(argv: list[str] | None = None) -> None:
    """命令行入口用：该转发就转发并退出。"""
    out = maybe_forward(sys.argv[1:] if argv is None else argv)
    if out is not None:
        code, text = out
        print(text)
        raise SystemExit(code)
