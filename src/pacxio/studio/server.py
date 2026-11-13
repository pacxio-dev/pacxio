"""工作台的本机服务（、2.3、6 节）。

- 只监听 127.0.0.1；检查 Host 头（防 DNS 重绑定）与 Sec-Fetch-Site（别的网站发来的请求一律拒绝）；
- 只读 job 目录里的文件，防 `../` 越界；`studio/` 目录（口令、日志、缓存）不对外；
- 每 POLL_S 秒查一次文件修改时间，变化稳定 SETTLE_S 秒后经 SSE 推给页面；
- 页面不引用任何外部资源（CSP 限定本机）；不新增依赖：标准库 http.server + 线程。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import sys
import threading
import time
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from PIL import Image

from pacxio import __version__
from pacxio.concept.service import choose_concept, confirm_flats, use_concept
from pacxio.inventory import content_confirm
from pacxio.messages import LANGS, LOCALES, display_name, lang, t, use_language
from pacxio.model.common import Issue, Result
from pacxio.notes import USER, add_note, delete_note, reopen_note, reply_note, resolve_note
from pacxio.studio.state import find_action, job_state
from pacxio.visual.meta import now_iso
from pacxio.zoom import zoom

HOST = "127.0.0.1"
STUDIO_DIR = "studio"  # job 目录下工作台自己的文件（不对页面开放）
SERVER_FILE = "server.json"
CACHE_DIR = "cache"
LOG_FILE = "server.log"
POLL_S = 0.5
SETTLE_S = 0.5  # 文件修改时间稳定这么久才推：agent 写到一半的文件不读
KEEPALIVE_S = 15.0
THUMB_WIDTHS = (480, 960, 1920)
STATIC_DIR = Path(__file__).parent / "static"
CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".pdf": "application/pdf",
    ".json": "application/json; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".dxf": "application/octet-stream",
    ".cf2": "application/octet-stream",
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
}
THUMBABLE = {".png", ".jpg", ".jpeg", ".webp"}
CSP = (
    "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; connect-src 'self'; frame-src 'self'; object-src 'self'; "
    "base-uri 'none'; form-action 'none'"
)
SAME_SITE = {"same-origin", "none"}
MAX_BODY = 64 * 1024
MAX_NOTE = 500
BY_STUDIO = "用户（工作台）"  # 工作台上点的确认，记录里写这个（和命令行的记录格式一样）
NOTE_ID = re.compile(r"^n\d{1,6}$")


def _local_or_cloud(argv: list[str], local: Callable[[], Result]) -> Result:
    """用户在工作台点的确认，和命令行同一个函数；开源版里这条命令要转给云端（）。"""
    from pacxio.cloud import route

    if route.should_forward(argv):
        return route.forward(argv, Path.cwd())
    return local()


def _make_plan(d: Path, by: str, note: str | None) -> Result:
    from pacxio_pro.plan import make_plan  # 闭源：排版初稿示意图

    res: Result = make_plan(d, confirm=True, by=by, note=note)
    return res


def _approve(d: Path, by: str, note: str | None) -> Result:
    from pacxio.mockup.service import approve_design  # 闭源：刀版确认图

    return approve_design(d, by=by, note=note)


def studio_dir(directory: Path) -> Path:
    return directory / STUDIO_DIR


def _skipped(rel: Path) -> bool:
    return rel.parts[0] == STUDIO_DIR or any(p.startswith(".") for p in rel.parts)


def scan(directory: Path) -> dict[str, tuple[int, int]]:
    """job 目录里每个文件的 (修改时间 ns, 大小)；跳过 studio/ 与隐藏文件。"""
    out: dict[str, tuple[int, int]] = {}
    for root, dirs, files in os.walk(directory):
        rel_root = Path(root).relative_to(directory)
        dirs[:] = [d for d in dirs if not _skipped(rel_root / d)]
        for name in files:
            rel = rel_root / name
            if _skipped(rel):
                continue
            try:
                st = (Path(root) / name).stat()
            except OSError:
                continue
            out[rel.as_posix()] = (st.st_mtime_ns, st.st_size)
    return out


class Watcher:
    """轮询 job 目录；变化稳定之后把版本号加一、记下变了哪些文件，并叫醒等着的 SSE 连接。"""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.snapshot = scan(directory)
        self.version = 0
        self.changed: list[str] = []
        self.cond = threading.Condition()
        self.stop = threading.Event()

    def poll_once(self, pending: set[str], last: float | None) -> tuple[set[str], float | None]:
        snap = scan(self.directory)
        if snap != self.snapshot:
            keys = set(snap) | set(self.snapshot)
            pending |= {k for k in keys if snap.get(k) != self.snapshot.get(k)}
            self.snapshot = snap
            return pending, time.monotonic()
        if pending and last is not None and time.monotonic() - last >= SETTLE_S:
            with self.cond:
                self.version += 1
                self.changed = sorted(pending)
                self.cond.notify_all()
            return set(), None
        return pending, last

    def run(self) -> None:
        pending: set[str] = set()
        last: float | None = None
        while not self.stop.wait(POLL_S):
            pending, last = self.poll_once(pending, last)

    def wait(self, seen: int, timeout: float) -> tuple[int, list[str]]:
        """等到版本号不再是 seen（或超时、或停止）；返回 (版本号, 变了的文件)。"""
        with self.cond:
            self.cond.wait_for(lambda: self.version != seen or self.stop.is_set(), timeout=timeout)
            return self.version, list(self.changed)

    def close(self) -> None:
        self.stop.set()
        with self.cond:
            self.cond.notify_all()


class StudioHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, directory: Path, port: int) -> None:
        super().__init__((HOST, port), Handler)
        self.directory = directory
        self.token = secrets.token_urlsafe(24)
        self.watcher = Watcher(directory)
        self._state_lock = threading.Lock()
        self._state_cache: dict[str, tuple[int, bytes]] = {}
        self._editable_lock = threading.Lock()
        self.write_lock = (
            threading.Lock()
        )  # 改 job.json 的请求一个一个来（页面连点时不会两个同时读改写）
        self._editable_cache: dict[str, tuple[int, bytes]] = {}
        # 结构面板的可编辑项算一次要几秒：启动时和 job.json 变了以后在后台先算好
        threading.Thread(target=self._warm_editable, daemon=True).start()

    @property
    def port(self) -> int:
        return int(self.server_address[1])

    @property
    def url(self) -> str:
        return f"http://{HOST}:{self.port}/"

    def _warm_editable(self) -> None:
        while True:
            try:
                self.editable_json()
            except Exception:  # noqa: BLE001  后台预算失败不影响页面；打开面板时会再算并报错
                pass
            time.sleep(POLL_S * 4)

    def editable_json(self) -> bytes:
        """结构面板的可编辑项：按 job.json 的修改时间缓存（算一次要试一两百次生成）。"""
        from pacxio import api

        job_file = self.directory / "job.json"
        key = job_file.stat().st_mtime_ns if job_file.is_file() else 0
        language = lang()
        with self._editable_lock:
            cached = self._editable_cache.get(language)
            if cached is None or cached[0] != key:
                r = api.structure_editable(directory=self.directory, lang=language)
                body = {"ok": r.ok, "data": r.data,
                        "errors": [e.model_dump(mode="json") for e in r.errors]}  # fmt: skip
                cached = self._editable_cache[language] = (
                    key,
                    json.dumps(body, ensure_ascii=False).encode("utf-8"),
                )
            return cached[1]

    def state_json(self) -> bytes:
        """按文件变化的版本号缓存：同一版只算一次（算指纹要读素材文件）。"""
        with self._state_lock:
            v = self.watcher.version
            language = lang()
            cached = self._state_cache.get(language)
            if cached is None or cached[0] != v:
                data = job_state(self.directory).model_dump(mode="json")
                data["version"] = v
                cached = self._state_cache[language] = (
                    v,
                    json.dumps(data, ensure_ascii=False).encode("utf-8"),
                )
            return cached[1]


class Handler(BaseHTTPRequestHandler):
    server: StudioHTTPServer
    server_version = f"pacxio-studio/{__version__}"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        if os.environ.get("PACXIO_STUDIO_LOG"):
            super().log_message(format, *args)

    # ---------- 守卫 ----------

    def _allowed(self, navigation: bool) -> bool:
        host = self.headers.get("Host", "")
        if host not in {f"{HOST}:{self.server.port}", f"localhost:{self.server.port}"}:
            return False
        site = self.headers.get("Sec-Fetch-Site")
        return navigation or site is None or site in SAME_SITE

    def _send(
        self, status: int, body: bytes, ctype: str, extra: dict[str, str] | None = None
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-cache")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _error(self, status: HTTPStatus, message: str) -> None:
        body = json.dumps({"ok": False, "error": message}, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    # ---------- 路由 ----------

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        language = query.get("lang", [None])[0]
        with use_language(language if language in LANGS else None):
            self._get(unquote(url.path), query)

    def _get(self, path: str, query: dict[str, list[str]]) -> None:
        if not self._allowed(navigation=path in ("/", "/index.html")):
            self._error(
                HTTPStatus.FORBIDDEN, t("Only requests from the local Workbench page are accepted")
            )
            return
        try:
            if path in ("/", "/index.html"):
                self._index()
            elif path.startswith("/static/"):
                self._static(path[len("/static/") :])
            elif path == "/api/ping":
                body = json.dumps({"ok": True, "pid": os.getpid(), "job": str(self.server.directory),
                                   "version": __version__}, ensure_ascii=False)  # fmt: skip
                self._send(HTTPStatus.OK, body.encode("utf-8"), "application/json; charset=utf-8")
            elif path == "/api/state":
                self._send(HTTPStatus.OK, self.server.state_json(), "application/json; charset=utf-8",
                           {"Cache-Control": "no-store"})  # fmt: skip
            elif path == "/api/events":
                self._events()
            elif path == "/api/ui-zh":
                self._send(HTTPStatus.OK, (LOCALES / "zh" / "studio_ui.json").read_bytes(),
                           CONTENT_TYPES[".json"])  # fmt: skip
            elif path == "/api/structure":
                self._send(HTTPStatus.OK, self.server.editable_json(), "application/json; charset=utf-8",
                           {"Cache-Control": "no-store"})  # fmt: skip
            elif path.startswith("/files/"):
                self._file(path[len("/files/") :], query.get("w", [None])[0], "v" in query)
            else:
                self._error(HTTPStatus.NOT_FOUND, t("Unknown route: {path}", path=path))
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self) -> None:  # noqa: N802
        url = urlsplit(self.path)
        language = parse_qs(url.query).get("lang", [None])[0]
        with use_language(language if language in LANGS else None):
            self._post(unquote(url.path))

    def _post(self, path: str) -> None:
        """写操作（批注、确认、结构面板试改 / 保存）：要带页面里的口令、同源、JSON。"""
        if not self._allowed(navigation=False) or not self._writer_ok():
            self._error(
                HTTPStatus.FORBIDDEN, t("Write requests must come from the local Workbench page")
            )
            return
        body = self._json_body()
        if body is None:
            self._error(HTTPStatus.BAD_REQUEST, t("The request body must be a JSON object"))
            return
        if path == "/api/zoom":
            self._zoom(body)
            return
        try:
            result = self._note_action(path, body)
        except (BrokenPipeError, ConnectionResetError):
            return
        if result is None:
            self._error(HTTPStatus.NOT_FOUND, t("Unknown route: {path}", path=path))
            return
        status = HTTPStatus.OK if result.ok else HTTPStatus.BAD_REQUEST
        self._send(status, result.model_dump_json().encode("utf-8"), "application/json; charset=utf-8",
                   {"Cache-Control": "no-store"})  # fmt: skip

    def _zoom(self, body: dict[str, Any]) -> None:
        """确认图的一块原分辨率局部（开源版向云端要，）：成功回 PNG，失败回 Result JSON。"""
        file, box = body.get("file"), body.get("box")
        r = Result(command="zoom", job=str(self.server.directory))
        if not (isinstance(file, str) and isinstance(box, list) and len(box) == 4
                and all(isinstance(v, int | float) for v in box)):  # fmt: skip
            r.fail(
                Issue(id="ARG", message=t("zoom requires a file and a box with four numbers")), 2
            )
        else:
            try:
                r = zoom(self.server.directory, box=[float(v) for v in box], image=file)
            except (OSError, ValueError) as e:  # 图坏了、读不出来：回一条说得清的错，别断开连接
                r = Result(command="zoom", job=str(self.server.directory))
                r.fail(
                    Issue(
                        id="ZOOM",
                        message=t("Could not zoom in: {error}", error=e),
                        fix=t("Run render again and retry"),
                    ),
                    1,
                )
        if not r.ok:
            self._send(HTTPStatus.BAD_REQUEST, r.model_dump_json().encode("utf-8"),
                       "application/json; charset=utf-8", {"Cache-Control": "no-store"})  # fmt: skip
            return
        self._send(
            HTTPStatus.OK,
            Path(r.artifacts[0]).read_bytes(),
            "image/png",
            {"Cache-Control": "no-store"},
        )

    def _writer_ok(self) -> bool:
        token = self.headers.get("X-Studio-Token", "")
        if not secrets.compare_digest(token.encode("utf-8"), self.server.token.encode("utf-8")):
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin not in {
            f"http://{HOST}:{self.server.port}",
            f"http://localhost:{self.server.port}",
        }:
            return False
        return self.headers.get("Content-Type", "").split(";")[0].strip() == "application/json"

    def _json_body(self) -> dict[str, Any] | None:
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None
        if n <= 0 or n > MAX_BODY:
            return None
        try:
            data = json.loads(self.rfile.read(n).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _confirm(self, body: dict[str, Any]) -> Result:
        """用户点的确认：先按现在的状态核对这一项能不能点，再调用和命令行同一个函数。"""
        d = self.server.directory
        r = Result(command="studio confirm", job=str(d))
        action, target, note = body.get("action"), body.get("target"), body.get("note")
        if not isinstance(action, str) or not all(
            v is None or isinstance(v, str) for v in (target, note)
        ):
            return r.fail(
                Issue(
                    id="CONFIRM",
                    message=t("Invalid confirmation parameters"),
                    fix=t("action must be a string"),
                ),
                2,
            )
        found = find_action(job_state(d), action, target)
        if found is None:
            return r.fail(
                Issue(
                    id="CONFIRM",
                    message=t("This confirmation is no longer available"),
                    fix=t("Refresh the page to see the current status"),
                ),
                2,
            )
        if not found.enabled:
            return r.fail(
                Issue(id="CONFIRM", message=found.reason or t("This item cannot be confirmed yet")),
                2,
            )
        text = (note or "").strip()[:MAX_NOTE] or None
        if action == "content":
            return content_confirm(d, by=BY_STUDIO, note=text)
        extra = ["--note", text] if text else []
        if action == "plan":
            return _local_or_cloud(["plan", str(d), "--confirm", "--by", BY_STUDIO, *extra],
                                   lambda: _make_plan(d, BY_STUDIO, text))  # fmt: skip
        if action == "concept":
            return _local_or_cloud(["concept", "choose", str(d), str(target), "--by", BY_STUDIO, *extra],
                                   lambda: choose_concept(d, str(target), by=BY_STUDIO, note=text))  # fmt: skip
        if action == "use":
            return use_concept(d, str(target), by=BY_STUDIO, note=text)
        if action == "flats":
            return confirm_flats(d, by=BY_STUDIO, note=text)
        return _local_or_cloud(["approve", str(d), "--by", BY_STUDIO, *extra],
                               lambda: _approve(d, BY_STUDIO, text))  # fmt: skip

    def _note_action(self, path: str, body: dict[str, Any]) -> Result | None:
        d = self.server.directory
        if path == "/api/confirm":
            return self._confirm(body)
        if path in ("/api/structure/try", "/api/structure/save"):
            with self.server.write_lock:
                return self._structure(path, body)
        text = body.get("text")
        if path == "/api/notes":
            rel = body.get("rel")
            shape = body.get("shape", "none")
            ok_rel = rel is None or (
                isinstance(rel, list)
                and len(rel) == 4
                and all(isinstance(v, int | float) for v in rel)
            )
            if (
                not isinstance(text, str)
                or not ok_rel
                or shape not in ("pin", "box", "item", "none")
            ):
                return Result(command="notes add", job=str(d)).fail(
                    Issue(
                        id="NOTE",
                        message=t("Invalid note parameters"),
                        fix=t(
                            "text must be a string and rel must contain four numbers from 0 to 1"
                        ),
                    ),
                    2,
                )
            card, file, item = body.get("card"), body.get("file"), body.get("item")
            return add_note(d, text, USER, card if isinstance(card, str) else None,
                            file if isinstance(file, str) else None, shape,
                            tuple(float(v) for v in rel) if rel else None,  # type: ignore[arg-type]
                            item if isinstance(item, str) else None)  # fmt: skip
        parts = path.split("/")
        if len(parts) != 5 or parts[:3] != ["", "api", "notes"] or not NOTE_ID.match(parts[3]):
            return None
        nid, action = parts[3], parts[4]
        if action == "reply":
            if not isinstance(text, str):
                return Result(command="notes reply", job=str(d)).fail(
                    Issue(id="NOTE", message=t("A reply must contain text")), 2
                )
            return reply_note(d, nid, text, by=USER)
        actions = {"resolve": resolve_note, "reopen": reopen_note, "delete": delete_note}
        fn = actions.get(action)
        return fn(d, nid, by=USER) if fn is not None else None

    def _structure(self, path: str, body: dict[str, Any]) -> Result:
        """结构面板：试改（不写文件）/ 保存（改 job.json、重出刀版；已有确认会过期，页面上先问过用户）。"""
        from pacxio import api

        d = self.server.directory
        patch = body.get("patch")
        if not isinstance(patch, dict):
            return Result(command="studio structure", job=str(d)).fail(
                Issue(
                    id="ARG",
                    message=t("patch must be an object"),
                    fix=t("Use the paths from structures editable"),
                ),
                2,
            )
        material = body.get("material")
        material = material if isinstance(material, str) and material else None
        corners = body.get("corners") if isinstance(body.get("corners"), dict) else None
        if path.endswith("/try"):
            result = api.structure_try(d, patch, material, corners)
            if result.ok:
                sizes = result.data["sizes"]
                if "master" in sizes:
                    sizes["master"] = display_name(sizes["master"])
                for part in sizes.get("parts", []):
                    part["name"] = display_name(part["name"])
            return result
        return api.structure_update(d, patch, material, corners)

    def _index(self) -> None:
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        html = html.replace("{{TOKEN}}", self.server.token).replace("{{VERSION}}", __version__)
        self._send(HTTPStatus.OK, html.encode("utf-8"), CONTENT_TYPES[".html"],
                   {"Content-Security-Policy": CSP, "Cache-Control": "no-store"})  # fmt: skip

    def _static(self, name: str) -> None:
        target = STATIC_DIR / name
        if "/" in name or "\\" in name or name.startswith(".") or not target.is_file():
            self._error(HTTPStatus.NOT_FOUND, t("File not found: {name}", name=name))
            return
        self._send(
            HTTPStatus.OK,
            target.read_bytes(),
            CONTENT_TYPES.get(target.suffix, "application/octet-stream"),
        )

    def _resolve(self, rel: str) -> Path | None:
        root = self.server.directory.resolve()
        try:
            target = (root / rel).resolve()
            inside = target.relative_to(root)
        except (ValueError, OSError):
            return None
        if not inside.parts or _skipped(inside) or not target.is_file():
            return None
        return target if target.suffix.lower() in CONTENT_TYPES else None

    def _file(self, rel: str, width: str | None, versioned: bool) -> None:
        target = self._resolve(rel)
        if target is None:
            self._error(
                HTTPStatus.NOT_FOUND,
                t("The file is missing from this job or is unavailable: {rel}", rel=rel),
            )
            return
        if width is not None and target.suffix.lower() in THUMBABLE:
            w = int(width) if width.isdigit() else 0
            if w in THUMB_WIDTHS:
                target = thumbnail(self.server.directory, target, w)
        ctype = CONTENT_TYPES[target.suffix.lower()]
        size = target.stat().st_size
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("X-Content-Type-Options", "nosniff")
        # 页面引用的网址带 ?v=修改时间：文件一变网址就变，同一网址可以放心长期缓存
        self.send_header(
            "Cache-Control", "max-age=31536000, immutable" if versioned else "no-cache"
        )
        self.end_headers()
        if self.command != "HEAD":
            with target.open("rb") as f:
                shutil.copyfileobj(f, self.wfile)

    def _events(self) -> None:
        w = self.server.watcher
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        seen = w.version
        self.wfile.write(
            f"retry: 2000\nevent: hello\ndata: {json.dumps({'version': seen})}\n\n".encode()
        )
        self.wfile.flush()
        while not w.stop.is_set():
            version, changed = w.wait(seen, KEEPALIVE_S)
            if w.stop.is_set():
                break
            if version != seen:
                seen = version
                data = json.dumps({"version": version, "changed": changed}, ensure_ascii=False)
                self.wfile.write(f"event: change\ndata: {data}\n\n".encode())
            else:
                self.wfile.write(b": keepalive\n\n")
            self.wfile.flush()


def thumbnail(directory: Path, src: Path, width: int) -> Path:
    """缩略图（缓存在 studio/cache/）；原图不比要的宽就直接用原图。"""
    st = src.stat()
    key = hashlib.sha1(src.relative_to(directory.resolve()).as_posix().encode("utf-8")).hexdigest()[
        :16
    ]
    cache = studio_dir(directory) / CACHE_DIR
    out = cache / f"{key}_{st.st_mtime_ns}_{width}.png"
    if out.is_file():
        return out
    with Image.open(src) as im:
        if im.width <= width:
            return src
        im.load()
        h = max(1, round(im.height * width / im.width))
        small = im.convert("RGBA" if "A" in im.getbands() else "RGB").resize(
            (width, h), Image.Resampling.LANCZOS
        )
    cache.mkdir(parents=True, exist_ok=True)
    for old in cache.glob(f"{key}_*_{width}.png"):  # 同一张图的旧缩略图
        old.unlink(missing_ok=True)
    tmp = out.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    small.save(tmp, format="PNG")
    os.replace(tmp, out)
    return out


class Studio:
    """一个 job 的工作台服务：起服务、轮询线程；serve_forever 阻塞，shutdown 从别的线程调。"""

    def __init__(self, directory: Path, port: int = 0) -> None:
        self.directory = directory.resolve()
        self.httpd = StudioHTTPServer(self.directory, port)
        self._watch = threading.Thread(
            target=self.httpd.watcher.run, name="studio-watch", daemon=True
        )

    @property
    def url(self) -> str:
        return self.httpd.url

    @property
    def port(self) -> int:
        return self.httpd.port

    @property
    def token(self) -> str:
        return self.httpd.token

    def serve_forever(self) -> None:
        self._watch.start()
        self.httpd.serve_forever(poll_interval=0.25)

    def shutdown(self) -> None:
        self.httpd.watcher.close()
        self.httpd.shutdown()
        self.httpd.server_close()


def server_info(studio: Studio) -> dict[str, Any]:
    return {
        "pid": os.getpid(),
        "port": studio.port,
        "url": studio.url,
        "token": studio.token,
        "job": str(studio.directory),
        "started": now_iso(),
        "version": __version__,
    }


def run(directory: Path, port: int = 0) -> None:
    """前台运行（`pacxio studio serve`，或 `studio start` 起的后台进程）：写 studio/server.json，收到 SIGTERM 退出。"""
    studio = Studio(directory, port)
    info_file = studio_dir(studio.directory) / SERVER_FILE
    info_file.parent.mkdir(parents=True, exist_ok=True)
    info = server_info(studio)
    tmp = info_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, info_file)

    def _stop(signum: int, frame: object) -> None:
        raise SystemExit(0)

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _stop)
    print(
        t(
            "pacxio Workbench: {url} (job {job}; press Ctrl+C to quit)",
            url=studio.url,
            job=studio.directory,
        ),
        flush=True,
    )
    try:
        studio.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        studio.httpd.watcher.close()
        studio.httpd.server_close()
        try:
            current = json.loads(info_file.read_text(encoding="utf-8"))
            if current.get("pid") == os.getpid():
                info_file.unlink()
        except (OSError, ValueError):
            pass


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m pacxio.studio.server")
    ap.add_argument("directory", type=Path)
    ap.add_argument("--port", type=int, default=0)
    a = ap.parse_args(argv)
    run(a.directory, a.port)


if __name__ == "__main__":
    main(sys.argv[1:])
