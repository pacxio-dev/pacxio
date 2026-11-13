"""合成服务的 HTTP 接口（ 节）。错误一律变成带修复建议的 CloudError。"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from pacxio.messages import lang as lang_now
from pacxio.messages import t

TIMEOUT_S = 60


class CloudError(Exception):
    def __init__(self, id_: str, message: str, fix: str) -> None:
        super().__init__(message)
        self.id = id_
        self.message = message
        self.fix = fix


class Client:
    def __init__(self, url: str, token: str | None) -> None:
        self.url = url.rstrip("/")
        self.token = token

    def _req(self, method: str, path: str, *, body: Any = None, data: bytes | None = None,
             params: dict[str, str] | None = None, auth: bool = True) -> bytes:  # fmt: skip
        if auth and not self.token:
            raise CloudError(
                "CLOUD_AUTH",
                t("Not signed in to the pacxio cloud service yet"),
                t("Sign in with `pacxio login`, then try again"),
            )
        url = self.url + path + ("?" + urllib.parse.urlencode(params) if params else "")
        headers = {"User-Agent": "pacxio-cli"}
        if auth and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        elif data is not None:
            headers["Content-Type"] = "application/octet-stream"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                return bytes(resp.read())
        except urllib.error.HTTPError as e:
            raise _http_error(e) from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise CloudError("CLOUD_NET", t("Cannot reach the cloud service {url}: {reason}", url=self.url, reason=getattr(e, "reason", e)),
                             t("Check the network; the service address can be changed with PACXIO_CLOUD_URL; try again later")) from e  # fmt: skip

    def _json(self, method: str, path: str, **kw: Any) -> Any:
        return json.loads(self._req(method, path, **kw) or b"null")

    # ---- 接口 ----
    def health(self) -> bool:
        return bool(self._json("GET", "/v1/health", auth=False).get("ok"))

    def create_job(self, name: str) -> str:
        return str(self._json("POST", "/v1/jobs", body={"name": name})["job"])

    def missing(self, job: str, files: dict[str, str]) -> list[str]:
        return list(
            self._json("POST", f"/v1/jobs/{job}/manifest", body={"files": files})["missing"]
        )

    def put_file(self, job: str, rel: str, data: bytes, sha: str) -> None:
        self._req(
            "PUT",
            f"/v1/jobs/{job}/files/{urllib.parse.quote(rel)}",
            data=data,
            params={"sha256": sha},
        )

    def put_inbox(self, job: str, sha: str, name: str, data: bytes) -> None:
        self._req(
            "PUT", f"/v1/jobs/{job}/inbox/{sha}/{urllib.parse.quote(name, safe='')}", data=data
        )

    def start_run(self, job: str, argv: list[Any]) -> str:
        return str(
            self._json("POST", f"/v1/jobs/{job}/runs", body={"argv": argv, "lang": lang_now()})[
                "run"
            ]
        )

    def get_run(self, run: str) -> dict[str, Any]:
        return dict(self._json("GET", f"/v1/runs/{run}"))

    def get_file(self, job: str, rel: str) -> bytes:
        return self._req("GET", f"/v1/jobs/{job}/files/{urllib.parse.quote(rel)}")

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
        return dict(self._json("GET", "/v1/me"))

    def logout(self) -> None:
        self._req("POST", "/v1/logout", body={})

    def crop(self, job: str, rel: str, box: tuple[float, float, float, float]) -> bytes:
        """确认图原分辨率的一块局部（PNG，带水印）；box 是 0–1 相对位置，原点在左上角。"""
        return self._req("POST", f"/v1/jobs/{job}/crop", body={"path": rel, "box": list(box)})


def _http_error(e: urllib.error.HTTPError) -> CloudError:
    try:
        body = json.loads(e.read() or b"{}")
    except ValueError:
        body = {}
    body = body if isinstance(body, dict) else {}
    detail = str(body.get("detail") or "")
    code = e.code
    if code == 400 and isinstance(body.get("error"), str):  # 设备码轮询的回答
        return CloudError("CLOUD_DEVICE", str(body["error"]), "")
    if code == 401:
        return CloudError(
            "CLOUD_AUTH",
            t("The cloud service does not accept this sign-in (token invalid, revoked or expired)"),
            t("Sign in again with `pacxio login`"),
        )
    if code == 404:
        return CloudError(
            "CLOUD_404",
            t("Not found in the cloud: {what}", what=detail or e.url),
            t("Try once more; if it still fails, delete the job's .cloud/ folder to sync again"),
        )
    if code == 409:
        return CloudError(
            "CLOUD_BUSY",
            t("This job still has a command running in the cloud"),
            t("Wait for it to finish and try again"),
        )
    if code == 429:
        return CloudError(
            "CLOUD_LIMIT",
            t("Refused by the cloud: {detail}", detail=detail),
            t(
                "Render again before zooming in; or zoom into a slightly larger region to see it all at once"
            ),
        )
    if code == 400 and detail:
        return CloudError(
            "CLOUD_ARG",
            t("Not accepted by the cloud: {detail}", detail=detail),
            t(
                "Change the parameters as the message says and try again (a crop is at most 1600 × 1600 pixels)"
            ),
        )
    if code == 413:
        return CloudError(
            "CLOUD_SIZE",
            t("File too large for the cloud"),
            t("Make the asset smaller (300 ppi is enough for print) and try again"),
        )
    return CloudError(
        "CLOUD_HTTP",
        t("Cloud service error (HTTP {code}): {detail}", code=code, detail=detail),
        t("Try again later; if it keeps failing, send us this message"),
    )
