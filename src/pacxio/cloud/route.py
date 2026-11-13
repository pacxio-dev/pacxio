"""哪些命令转给云端，怎么转（–2 节）。

`maybe_forward(argv)` 在命令行入口和 MCP 交给 Typer 之前调用：不该转发就返回 None，
该转发就同步 job、在服务器上跑同一条命令、把允许回传的文件拉回来，返回（退出码, 输出文本）。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from pacxio.cloud import auth, config
from pacxio.cloud.client import Client, CloudError
from pacxio.cloud.sync import SKIP_TOP, pull, push_with_retry, sha256_bytes, sha256_file
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
    result = forward(argv, cwd or Path.cwd())
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


# ---------------- 转发 ----------------


def forward(argv: list[str], cwd: Path) -> Result:
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
    try:
        login = auth.ensure()
    except CloudError as e:
        return res.fail(Issue(id=e.id, message=e.message, fix=e.fix), 3)
    client = Client(login.url, login.token)
    try:
        jid = push_with_retry(client, job)
        for key, path in prep.inbox.items():
            sha, _, name = key.partition("/")
            client.put_inbox(jid, sha, name, path.read_bytes())
        before = _sha(job / "job.json")
        rid = client.start_run(jid, prep.argv)
        run = _wait(client, rid)
        if run["status"] != "done":
            return res.fail(Issue(id="CLOUD_RUN", message=t("The cloud run did not finish: {why}", why=run.get("error") or run["status"]),
                                  fix=t("Run it again; if it keeps failing, send us this message")))  # fmt: skip
        changed_locally = _sha(job / "job.json") != before
        keep = {"job.json"} if changed_locally else set()
        pull(client, jid, job, run.get("files") or {}, keep=keep)
    except CloudError as e:
        if e.id == "CLOUD_AUTH":
            e = auth.relogin_after_401(login)
        return res.fail(
            Issue(id=e.id, message=e.message, fix=e.fix),
            3 if e.id in ("CLOUD_NET", "CLOUD_AUTH", "CLOUD_LOGIN") else 1,
        )
    data = run.get("result")
    if not isinstance(data, dict):
        return res.fail(Issue(id="CLOUD_RUN", message=t("The cloud command returned no result: {tail}", tail=(run.get("error") or "")[-800:]),
                              fix=t("Run it again; if it keeps failing, send us this message")))  # fmt: skip
    data = _local_paths(data, job, prep.inbox)
    out = Result.model_validate(data)
    out.exit_code = int(run.get("exit_code") or 0)
    if changed_locally:
        out.warnings.append(Issue(id="CLOUD_CONFLICT", message=t("job.json changed on this machine while the cloud run was going; it was not overwritten with the cloud version"),
                                  fix=t("Rerun this command so both sides match")))  # fmt: skip
    return out


def _sha(p: Path) -> str | None:
    return sha256_bytes(p.read_bytes()) if p.is_file() else None


def _wait(client: Client, rid: str) -> dict[str, Any]:
    deadline = time.monotonic() + RUN_TIMEOUT_S
    delay = POLL_S
    while True:
        run = client.get_run(rid)
        if run.get("status") in ("done", "failed"):
            return run
        if time.monotonic() > deadline:
            raise CloudError(
                "CLOUD_TIMEOUT", t("The cloud run timed out"), t("Try the same command again later")
            )
        time.sleep(delay)
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
