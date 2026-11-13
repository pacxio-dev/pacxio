"""job 目录和云端那份的同步：按内容哈希只传变了的；只增不删（ 节）。"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from pacxio.cloud.client import Client, CloudError
from pacxio.messages import t

STATE_DIR = ".cloud"
# 生成物不上传（服务器自己会生成）；.cloud 是本机记账
SKIP_TOP = {STATE_DIR, "out", "mockup", "studio"}
SKIP_NAMES = {".DS_Store"}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def local_manifest(job: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(job.rglob("*")):
        rel = p.relative_to(job)
        if rel.parts[0] in SKIP_TOP or p.name in SKIP_NAMES or p.is_symlink() or not p.is_file():
            continue
        out[rel.as_posix()] = sha256_file(p)
    return out


def _state_path(job: Path) -> Path:
    return job / STATE_DIR / "state.json"


def read_state(job: Path) -> dict[str, Any]:
    try:
        data = json.loads(_state_path(job).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_state(job: Path, state: dict[str, Any]) -> None:
    p = _state_path(job)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2))


def remote_job(client: Client, job: Path) -> str:
    """这个 job 在云端的 id；没有（或换了服务）就新建一个。"""
    state = read_state(job)
    if state.get("url") == client.url and isinstance(state.get("job"), str):
        return str(state["job"])
    jid = client.create_job(job.name)
    write_state(job, {"url": client.url, "job": jid})
    return jid


def push(client: Client, jid: str, job: Path) -> int:
    """把本机有、云端没有或不一样的文件传上去。返回传了几个。"""
    files = local_manifest(job)
    missing = client.missing(jid, files)
    for rel in missing:
        data = (job / rel).read_bytes()
        client.put_file(jid, rel, data, sha256_bytes(data))
    return len(missing)


def push_with_retry(client: Client, job: Path) -> str:
    """同步；云端那份不在了（过了保留期被清掉）就新建一份再全部上传。"""
    jid = remote_job(client, job)
    try:
        push(client, jid, job)
    except CloudError as e:
        if e.id != "CLOUD_404":
            raise
        write_state(job, {})
        jid = remote_job(client, job)
        push(client, jid, job)
    return jid


def pull(client: Client, jid: str, job: Path, files: dict[str, dict[str, Any]],
         keep: set[str] | None = None) -> list[str]:  # fmt: skip
    """下载服务器回传的文件（校验哈希）；keep 里的不覆盖。返回实际写了哪些。"""
    written: list[str] = []
    for rel, info in sorted(files.items()):
        if keep and rel in keep:
            continue
        target = (job / rel).resolve()
        if not target.is_relative_to(job.resolve()):
            continue  # 不信任服务器给的路径
        sha = str(info.get("sha256", ""))
        if target.is_file() and sha256_file(target) == sha:
            continue
        data = client.get_file(jid, rel)
        if sha256_bytes(data) != sha:
            raise CloudError(
                "CLOUD_SYNC",
                t("Checksum mismatch for downloaded {path}", path=rel),
                t("Run this command again"),
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.part")
        tmp.write_bytes(data)
        os.replace(tmp, target)
        written.append(rel)
    return written
