"""job 目录和云端那份的同步：按内容哈希只传变了的；只增不删（ 节）。"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from pacxio.cloud.client import Client, CloudError
from pacxio.fontfiles import JOB_FONT_DIR
from pacxio.fontsubset import EPHEMERAL_FONT_DIR, FONT_BINARY_SUFFIXES
from pacxio.messages import t

STATE_DIR = ".cloud"
SYNC_WORKERS = 4
# 生成物不上传（服务器自己会生成）；.cloud 是本机记账；.ephemeral_fonts 仅单次运行时瞬时挂载
SKIP_TOP = {STATE_DIR, "out", "mockup", "studio", EPHEMERAL_FONT_DIR}
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
        # R2-5：完整非开源字体二进制不进持久化 job 同步（避免在服务器留存 30 天）；
        # 只保留轻量 .meta.json 留痕，实际排字由单次运行的瞬时字形子集提供并在转曲后立即删除。
        if rel.parts[0] == JOB_FONT_DIR and p.suffix.lower() in FONT_BINARY_SUFFIXES:
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
    tmp = p.with_name(f".{p.name}.{secrets.token_hex(4)}.tmp")
    try:
        tmp.write_text(json.dumps(state, indent=2))
        os.replace(tmp, p)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def remote_job(client: Client, job: Path) -> str:
    """这个 job 在云端的 id；没有（或换了服务）就新建一个。"""
    state = read_state(job)
    if state.get("url") == client.url and isinstance(state.get("job"), str):
        return str(state["job"])
    jid = client.create_job(job.name)
    write_state(job, {"url": client.url, "job": jid})
    return jid


def push(client: Client, jid: str, job: Path) -> int:
    """把本机有、云端没有或不一样的文件传上去（多文件并发复用连接）。返回传了几个。"""
    files = local_manifest(job)
    missing = client.missing(jid, files)

    def upload_one(rel: str) -> None:
        data = (job / rel).read_bytes()
        client.put_file(jid, rel, data, sha256_bytes(data))

    if len(missing) <= 1:
        for rel in missing:
            upload_one(rel)
    else:
        with ThreadPoolExecutor(max_workers=min(SYNC_WORKERS, len(missing))) as pool:
            for _ in pool.map(upload_one, missing):
                pass
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
    """下载服务器回传的文件（校验哈希、原子写、多文件并发）；keep 里的不覆盖。返回实际写了哪些。"""
    job_root = job.resolve()
    needed: list[tuple[str, Path, str]] = []
    for rel, info in sorted(files.items()):
        if keep and rel in keep:
            continue
        target = (job / rel).resolve()
        if not target.is_relative_to(job_root):
            continue  # 不信任服务器给的路径
        sha = str(info.get("sha256", ""))
        if target.is_file() and sha256_file(target) == sha:
            continue
        needed.append((rel, target, sha))

    def download_one(item: tuple[str, Path, str]) -> str:
        rel, target, sha = item
        data = client.get_file(jid, rel)
        if sha256_bytes(data) != sha:
            raise CloudError(
                "CLOUD_SYNC",
                t("Checksum mismatch for downloaded {path}", path=rel),
                t("Run this command again"),
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.{secrets.token_hex(4)}.part")
        try:
            tmp.write_bytes(data)
            os.replace(tmp, target)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return rel

    if len(needed) <= 1:
        return [download_one(item) for item in needed]
    with ThreadPoolExecutor(max_workers=min(SYNC_WORKERS, len(needed))) as pool:
        written = list(pool.map(download_one, needed))
    return sorted(written)
