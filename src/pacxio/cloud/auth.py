"""登录云端服务（S4）：设备码（RFC 8628 的做法），不存密码。

1. 客户端向服务器要一个设备码，得到网址和一个 8 位代码（XXXX-XXXX）；
2. 用户本人在浏览器里打开网址、用邮箱验证码登录（第一次就是注册），核对代码后点 Approve；
3. 客户端轮询拿到令牌，存进 ~/.pacxio/cloud.json（只有自己能读）。

第一次调用云端命令时如果还没登录，不卡在流程中间：自动开始登录、把网址和代码放进错误里返回；
用户在浏览器确认后重跑同一条命令，就接着登录完成并继续（）。
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pacxio.cloud import config
from pacxio.cloud.client import Client, CloudError
from pacxio.messages import t
from pacxio.model.common import Issue, Result

PENDING_FILE = "login_pending.json"
DEFAULT_WAIT_S = 15 * 60


def _pending_path() -> Path:
    return config.home() / PENDING_FILE


def label() -> str:
    """服务器账户页上显示的设备名。"""
    host = socket.gethostname().split(".")[0][:60]
    return f"pacxio CLI on {host}" if host else "pacxio CLI"


def read_pending(url: str) -> dict[str, Any] | None:
    try:
        data = json.loads(_pending_path().read_text())
    except (OSError, ValueError):
        return None
    ok = (
        isinstance(data, dict)
        and data.get("url") == url
        and float(data.get("expires_at", 0)) > time.time() + 5
    )
    return data if ok else None


def _save_pending(data: dict[str, Any]) -> None:
    p = _pending_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.chmod(0o600)
    tmp.replace(p)


def clear_pending() -> None:
    _pending_path().unlink(missing_ok=True)


def begin(url: str) -> tuple[dict[str, Any], bool]:
    """(进行中的登录, 是不是刚开始的)。同一个服务已有没过期的就接着用，不重新要代码。"""
    pending = read_pending(url)
    if pending is not None:
        return pending, False
    d = Client(url, None).device_start(label())
    pending = {
        "url": url,
        "device_code": d["device_code"],
        "user_code": d["user_code"],
        "verification_uri": d["verification_uri"],
        "verification_uri_complete": d.get("verification_uri_complete") or d["verification_uri"],
        "interval": int(d.get("interval") or 5),
        "expires_at": time.time() + float(d.get("expires_in") or 900),
    }
    _save_pending(pending)
    return pending, True


def poll(pending: dict[str, Any], *, retry_slow: bool = True) -> str | None:
    """用户确认了就返回令牌；还没确认返回 None；拒绝或过期抛 CloudError（并清掉这次登录）。"""
    client = Client(str(pending["url"]), None)
    try:
        return client.device_poll(str(pending["device_code"]))
    except CloudError as e:
        if e.id != "CLOUD_DEVICE":
            raise
        if e.message == "slow_down" and retry_slow:
            time.sleep(int(pending.get("interval") or 5))
            return poll(pending, retry_slow=False)
        if e.message in ("authorization_pending", "slow_down"):
            return None
        clear_pending()
        if e.message == "access_denied":
            raise CloudError("CLOUD_LOGIN_DENIED", t("The sign-in was declined in the browser"),
                             t("To sign in, rerun this command or run `pacxio login`")) from None  # fmt: skip
        raise CloudError("CLOUD_LOGIN_EXPIRED", t("The sign-in code expired (it must be approved within 15 minutes)"),
                         t("Rerun this command for a new code, or run `pacxio login`")) from None  # fmt: skip


def _finish(pending: dict[str, Any], token: str) -> None:
    config.save(str(pending["url"]), token)
    clear_pending()


def login_needed(pending: dict[str, Any]) -> CloudError:
    return CloudError(
        "CLOUD_LOGIN",
        t(
            "Sign in to the pacxio cloud service first: open {url} in a browser, sign in with an email code (the first time creates the account), check the code {code} and click Approve (valid for 15 minutes)",
            url=pending["verification_uri_complete"],
            code=pending["user_code"],
        ),
        t(
            "The user signs in and approves in the browser themselves (the agent cannot approve for them), then rerun this command; or run `pacxio login` and wait for it to finish"
        ),
    )


def ensure() -> config.Login:
    """云端命令用：有令牌就用；没有就开始（或接着）设备码登录，还没确认时抛 CLOUD_LOGIN。"""
    login = config.login()
    if login.token:
        return login
    pending, fresh = begin(login.url)
    token = None if fresh else poll(pending)
    if token is None:
        raise login_needed(pending)
    _finish(pending, token)
    return config.login()


def relogin_after_401(login: config.Login) -> CloudError:
    """服务器不认保存的令牌（被撤销、账户删除）：清掉它、开始新的登录。环境变量给的令牌不动。"""
    if os.environ.get(config.TOKEN_ENV):
        return CloudError("CLOUD_AUTH", t("The cloud service does not accept the token in PACXIO_CLOUD_TOKEN"),
                          t("Use a valid token, or remove this environment variable and run `pacxio login`"))  # fmt: skip
    config.clear()
    try:
        pending, _ = begin(login.url)
    except CloudError as e:
        return e
    return login_needed(pending)


def login(url: str | None = None, *, wait: bool = True, browser: bool = True, timeout_s: float = DEFAULT_WAIT_S,
          say: Callable[[str], None] | None = None) -> Result:  # fmt: skip
    """`pacxio login`。say 打印给人看的提示（JSON 模式下打到 stderr）。"""
    r = Result(command="login")
    current = config.login()
    target = (url or current.url).rstrip("/")
    say = say or (lambda s: print(s, file=sys.stderr))
    try:
        if current.token and current.url == target:
            try:
                me = Client(target, current.token).me()
                r.data = {
                    "url": target,
                    "signed_in": True,
                    "email": me.get("email"),
                    "already": True,
                }
                say(t("Already signed in: {email} ({url})", email=me.get("email"), url=target))
                return r
            except CloudError as e:
                if e.id != "CLOUD_AUTH" or os.environ.get(config.TOKEN_ENV):
                    raise
                config.clear()
        pending, _ = begin(target)
        link = str(pending["verification_uri_complete"])
        say(
            t(
                "Open {url} in a browser\nSign in with an email code (the first time creates the account), check the code {code} and click Approve.",
                url=link,
                code=pending["user_code"],
            )
        )
        if browser and sys.stdout.isatty() and sys.stdin.isatty():
            webbrowser.open(link)
        if not wait:
            r.data = {"url": target, "signed_in": False, "pending": True, "user_code": pending["user_code"],
                      "verification_uri_complete": link,
                      "expires_in": int(float(pending["expires_at"]) - time.time())}  # fmt: skip
            return r
        deadline = time.monotonic() + timeout_s
        while True:
            token = poll(pending)
            if token is not None:
                break
            if time.monotonic() > deadline or float(pending["expires_at"]) < time.time():
                return r.fail(Issue(id="CLOUD_LOGIN", message=t("Waited a long time and the browser approval has not come"),
                                    fix=t("Approve it, then run `pacxio login` again (the code is valid for 15 minutes)")), 3)  # fmt: skip
            time.sleep(int(pending.get("interval") or 5))
        _finish(pending, token)
        me = Client(target, token).me()
    except CloudError as e:
        return r.fail(
            Issue(id=e.id, message=e.message, fix=e.fix),
            3 if e.id in ("CLOUD_NET", "CLOUD_AUTH") else 1,
        )
    r.data = {"url": target, "signed_in": True, "email": me.get("email")}
    say(t("Signed in: {email}", email=me.get("email")))
    return r


def logout() -> Result:
    """退出：服务器上撤销这个令牌（连不上也照样删本机的），删掉本机保存的令牌和进行中的登录。"""
    login = config.login()
    revoked = False
    if login.token and not os.environ.get(config.TOKEN_ENV):
        try:
            Client(login.url, login.token).logout()
            revoked = True
        except CloudError:
            pass
    removed = config.clear()
    clear_pending()
    return Result(command="logout", data={"removed": removed, "revoked": revoked})
