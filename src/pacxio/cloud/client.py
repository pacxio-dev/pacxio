"""合成服务的 HTTP 接口（ 节）。错误一律变成带修复建议的 CloudError。

只用标准库：无代理时通过线程局部 `http.client` 复用 Keep-Alive 连接（省去重复 TCP/TLS 握手），
配了 HTTP/HTTPS 代理时自动回退到 `urllib.request`。幂等请求对瞬态网关/限流错误自动退避重试。
"""

from __future__ import annotations

import http.client
import json
import random
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import suppress
from typing import Any

from pacxio.messages import lang as lang_now
from pacxio.messages import t

TIMEOUT_S = 60
RETRY_STATUSES = frozenset({429, 502, 503, 504, 520, 522, 524})
MAX_RETRIES = 3
RETRY_BASE_S = 0.5
RETRY_MAX_S = 10.0


class CloudError(Exception):
    def __init__(
        self,
        id_: str,
        message: str,
        fix: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.id = id_
        self.message = message
        self.fix = fix
        self.status_code = status_code
        self.retry_after = retry_after


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        secs = float(value.strip())
    except ValueError:
        return None
    return secs if secs >= 0 else None


def _is_transient_os_error(exc: BaseException) -> bool:
    if isinstance(exc, (ConnectionRefusedError, socket.gaierror)):
        return False
    if isinstance(
        exc,
        (
            ConnectionResetError,
            BrokenPipeError,
            TimeoutError,
            http.client.RemoteDisconnected,
            http.client.BadStatusLine,
            http.client.IncompleteRead,
        ),
    ):
        return True
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, BaseException):
            return _is_transient_os_error(reason)
    return False


class Client:
    def __init__(self, url: str, token: str | None) -> None:
        self.url = url.rstrip("/")
        self.token = token
        parsed = urllib.parse.urlsplit(self.url)
        self._scheme = parsed.scheme.lower()
        self._host = parsed.hostname or ""
        self._port = parsed.port or (443 if self._scheme == "https" else 80)
        self._base_path = parsed.path.rstrip("/")
        self._ssl_ctx = ssl.create_default_context() if self._scheme == "https" else None
        self._local = threading.local()
        self._conns_lock = threading.Lock()
        self._conns: set[http.client.HTTPConnection] = set()

    def _use_direct_pool(self) -> bool:
        if self._scheme not in ("http", "https") or not self._host:
            return False
        proxies = urllib.request.getproxies()
        if self._scheme in proxies and not urllib.request.proxy_bypass(self._host):
            return False
        return True

    def _get_conn(self) -> tuple[http.client.HTTPConnection, bool]:
        conn: http.client.HTTPConnection | None = getattr(self._local, "conn", None)
        if conn is not None:
            return conn, True
        if self._scheme == "https":
            conn = http.client.HTTPSConnection(
                self._host, self._port, timeout=TIMEOUT_S, context=self._ssl_ctx
            )
        else:
            conn = http.client.HTTPConnection(self._host, self._port, timeout=TIMEOUT_S)
        self._local.conn = conn
        with self._conns_lock:
            self._conns.add(conn)
        return conn, False

    def _drop_conn(self) -> None:
        conn: http.client.HTTPConnection | None = getattr(self._local, "conn", None)
        if conn is not None:
            self._local.conn = None
            with self._conns_lock:
                self._conns.discard(conn)
            with suppress(Exception):
                conn.close()

    def close(self) -> None:
        with self._conns_lock:
            conns = list(self._conns)
            self._conns.clear()
        self._local.conn = None
        for conn in conns:
            with suppress(Exception):
                conn.close()

    def _send_direct(
        self, method: str, target: str, payload: bytes | None, headers: dict[str, str]
    ) -> tuple[int, bytes, float | None]:
        conn, reused = self._get_conn()
        try:
            conn.request(method, target, body=payload, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
            status = resp.status
            retry_after = _parse_retry_after(resp.getheader("Retry-After"))
            if resp.will_close or (resp.getheader("Connection") or "").lower() == "close":
                self._drop_conn()
            return status, raw, retry_after
        except (
            http.client.RemoteDisconnected,
            http.client.CannotSendRequest,
            http.client.BadStatusLine,
            BrokenPipeError,
            ConnectionResetError,
        ):
            self._drop_conn()
            if not reused:
                raise
            # Stale keep-alive socket closed by peer/proxy before handling request: retry once on fresh socket.
            conn, _ = self._get_conn()
            try:
                conn.request(method, target, body=payload, headers=headers)
                resp = conn.getresponse()
                raw = resp.read()
                status = resp.status
                retry_after = _parse_retry_after(resp.getheader("Retry-After"))
                if resp.will_close or (resp.getheader("Connection") or "").lower() == "close":
                    self._drop_conn()
                return status, raw, retry_after
            except Exception:
                self._drop_conn()
                raise
        except Exception:
            self._drop_conn()
            raise

    def _send_urllib(
        self, method: str, url: str, payload: bytes | None, headers: dict[str, str]
    ) -> tuple[int, bytes, float | None]:
        req = urllib.request.Request(url, data=payload, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                return int(resp.status), bytes(resp.read()), _parse_retry_after(
                    resp.headers.get("Retry-After")
                )
        except urllib.error.HTTPError as e:
            raw = b""
            with suppress(Exception):
                raw = e.read() or b""
            retry_after = _parse_retry_after(e.headers.get("Retry-After") if e.headers else None)
            return int(e.code), raw, retry_after

    def _req(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        data: bytes | None = None,
        params: dict[str, str] | None = None,
        auth: bool = True,
        retry: bool = False,
    ) -> bytes:
        if auth and not self.token:
            raise CloudError(
                "CLOUD_AUTH",
                t("Not signed in to the pacxio cloud service yet"),
                t("Sign in with `pacxio login`, then try again"),
            )
        query = ("?" + urllib.parse.urlencode(params)) if params else ""
        url = self.url + path + query
        target = (self._base_path + path + query) or "/"
        headers = {"User-Agent": "pacxio-cli", "Connection": "keep-alive"}
        if auth and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        payload = data
        if body is not None:
            payload = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        elif payload is not None:
            headers["Content-Type"] = "application/octet-stream"

        direct = self._use_direct_pool()
        max_attempts = (MAX_RETRIES + 1) if retry else 1
        for attempt in range(max_attempts):
            try:
                if direct:
                    status, raw, retry_after = self._send_direct(method, target, payload, headers)
                else:
                    status, raw, retry_after = self._send_urllib(method, url, payload, headers)
            except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as e:
                if attempt + 1 < max_attempts and _is_transient_os_error(e):
                    _backoff(attempt, None)
                    continue
                raise CloudError(
                    "CLOUD_NET",
                    t(
                        "Cannot reach the cloud service {url}: {reason}",
                        url=self.url,
                        reason=getattr(e, "reason", e),
                    ),
                    t(
                        "Check the network; the service address can be changed with PACXIO_CLOUD_URL; try again later"
                    ),
                ) from e
            if 200 <= status < 300:
                return raw
            if attempt + 1 < max_attempts and status in RETRY_STATUSES:
                _backoff(attempt, retry_after)
                continue
            raise _http_error_status(status, raw, url, retry_after=retry_after)
        raise AssertionError("unreachable")  # pragma: no cover

    def _json(self, method: str, path: str, **kw: Any) -> Any:
        return json.loads(self._req(method, path, **kw) or b"null")

    # ---- 接口 ----
    def health(self) -> bool:
        return bool(self._json("GET", "/v1/health", auth=False, retry=True).get("ok"))

    def create_job(self, name: str) -> str:
        return str(self._json("POST", "/v1/jobs", body={"name": name})["job"])

    def missing(self, job: str, files: dict[str, str]) -> list[str]:
        return list(
            self._json("POST", f"/v1/jobs/{job}/manifest", body={"files": files}, retry=True)[
                "missing"
            ]
        )

    def put_file(self, job: str, rel: str, data: bytes, sha: str) -> None:
        self._req(
            "PUT",
            f"/v1/jobs/{job}/files/{urllib.parse.quote(rel)}",
            data=data,
            params={"sha256": sha},
            retry=True,
        )

    def put_inbox(self, job: str, sha: str, name: str, data: bytes) -> None:
        self._req(
            "PUT",
            f"/v1/jobs/{job}/inbox/{sha}/{urllib.parse.quote(name, safe='')}",
            data=data,
            retry=True,
        )

    def start_run(
        self,
        job: str,
        argv: list[Any],
        *,
        ephemeral_fonts: list[dict[str, Any]] | None = None,
    ) -> str:
        body: dict[str, Any] = {"argv": argv, "lang": lang_now()}
        if ephemeral_fonts:
            body["ephemeral_fonts"] = ephemeral_fonts
        return str(self._json("POST", f"/v1/jobs/{job}/runs", body=body)["run"])

    def get_run(self, run: str) -> dict[str, Any]:
        return dict(self._json("GET", f"/v1/runs/{run}", retry=True))

    def get_file(self, job: str, rel: str) -> bytes:
        return self._req("GET", f"/v1/jobs/{job}/files/{urllib.parse.quote(rel)}", retry=True)

    # ---- 账户（S4）----
    def device_start(self, client_label: str) -> dict[str, Any]:
        return dict(
            self._json("POST", "/v1/device/code", body={"client": client_label}, auth=False)
        )

    def device_poll(self, device_code: str) -> str:
        """令牌；还没确认等情况抛 CloudError("CLOUD_DEVICE", <RFC 8628 的 error 名>)。"""
        return str(
            self._json("POST", "/v1/device/token", body={"device_code": device_code}, auth=False)[
                "access_token"
            ]
        )

    def me(self) -> dict[str, Any]:
        return dict(self._json("GET", "/v1/me", retry=True))

    def logout(self) -> None:
        self._req("POST", "/v1/logout", body={})

    def crop(self, job: str, rel: str, box: tuple[float, float, float, float]) -> bytes:
        """确认图原分辨率的一块局部（PNG）；box 是 0–1 相对位置，原点在左上角。"""
        return self._req("POST", f"/v1/jobs/{job}/crop", body={"path": rel, "box": list(box)})


def _backoff(attempt: int, retry_after: float | None) -> None:
    if retry_after is not None:
        delay = min(max(0.05, retry_after), RETRY_MAX_S)
    else:
        delay = min(RETRY_BASE_S * (2**attempt) * (0.85 + 0.3 * random.random()), RETRY_MAX_S)
    time.sleep(delay)


def _http_error_status(
    code: int, raw: bytes, url: str, *, retry_after: float | None = None
) -> CloudError:
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        body = {}
    body = body if isinstance(body, dict) else {}
    detail = str(body.get("detail") or "")
    if code == 400 and isinstance(body.get("error"), str):  # 设备码轮询的回答
        return CloudError("CLOUD_DEVICE", str(body["error"]), "", status_code=code)
    if code == 401:
        return CloudError(
            "CLOUD_AUTH",
            t("The cloud service does not accept this sign-in (token invalid, revoked or expired)"),
            t("Sign in again with `pacxio login`"),
            status_code=code,
        )
    if code == 404:
        return CloudError(
            "CLOUD_404",
            t("Not found in the cloud: {what}", what=detail or url),
            t("Try once more; if it still fails, delete the job's .cloud/ folder to sync again"),
            status_code=code,
        )
    if code == 409:
        return CloudError(
            "CLOUD_BUSY",
            t("This job still has a command running in the cloud"),
            t("Wait for it to finish and try again"),
            status_code=code,
        )
    if code == 429:
        low = detail.lower()
        is_zoom = "zoom" in low or "crop" in low
        fix = (
            t("Render again before zooming in; or zoom into a slightly larger region to see it all at once")
            if is_zoom
            else t("Wait a moment and try again; too many concurrent requests or queued jobs")
        )
        return CloudError(
            "CLOUD_LIMIT",
            t("Refused by the cloud: {detail}", detail=detail),
            fix,
            status_code=code,
            retry_after=retry_after,
        )
    if code == 400 and detail:
        return CloudError(
            "CLOUD_ARG",
            t("Not accepted by the cloud: {detail}", detail=detail),
            t(
                "Change the parameters as the message says and try again (a crop is at most 1600 × 1600 pixels)"
            ),
            status_code=code,
        )
    if code == 413:
        return CloudError(
            "CLOUD_SIZE",
            t("File too large for the cloud"),
            t("Make the asset smaller (300 ppi is enough for print) and try again"),
            status_code=code,
        )
    return CloudError(
        "CLOUD_HTTP",
        t("Cloud service error (HTTP {code}): {detail}", code=code, detail=detail),
        t("Try again later; if it keeps failing, send us this message"),
        status_code=code,
        retry_after=retry_after,
    )


def _http_error(e: urllib.error.HTTPError) -> CloudError:
    raw = b""
    with suppress(Exception):
        raw = e.read() or b""
    retry_after = _parse_retry_after(e.headers.get("Retry-After") if e.headers else None)
    return _http_error_status(int(e.code), raw, str(e.url), retry_after=retry_after)
