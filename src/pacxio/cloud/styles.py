"""官网视觉风格目录读取与本地缓存（）。

风格包内容全部维护在官网独立页面（`https://pacxio.dev/styles`），由静态只读 JSON 暴露：
- 默认地址：`https://pacxio.dev/styles/catalog.json`（可通过 `PACXIO_STYLES_URL` 指向自定义 URL 或本机 JSON 文件）
- 本地缓存：`$PACXIO_HOME/cache/visual-styles.json`（默认 24 小时 TTL，支持 ETag 条件刷新与 `--refresh` 强刷）
- 断网 / 远端不可用时：若有旧缓存自动降级使用并附柔和提醒；无缓存时明确报错但不阻断用户直接用 `--design`
- 联网严格收敛在 `pacxio.cloud` 模块内（符合  开源联网边界）
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from pacxio.cloud import config
from pacxio.messages import t

DEFAULT_STYLES_URL = "https://pacxio.dev/styles/catalog.json"
STYLES_URL_ENV = "PACXIO_STYLES_URL"
STYLES_TTL_ENV = "PACXIO_STYLES_TTL"
DEFAULT_TTL_S = 24 * 3600.0
FETCH_TIMEOUT_S = 8.0


class StylesFetchError(RuntimeError):
    def __init__(self, code: str, message: str, fix: str) -> None:
        super().__init__(message)
        self.id = code
        self.message = message
        self.fix = fix


def styles_source() -> str:
    return (os.environ.get(STYLES_URL_ENV) or DEFAULT_STYLES_URL).strip()


def cache_path() -> Path:
    return config.home() / "cache" / "visual-styles.json"


def _ttl_seconds() -> float:
    raw = os.environ.get(STYLES_TTL_ENV, "").strip()
    if not raw:
        return DEFAULT_TTL_S
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_TTL_S


def _local_path(source: str) -> Path | None:
    if source.startswith(("http://", "https://")):
        return None
    if source.startswith("file://"):
        parsed = urllib.parse.urlparse(source)
        return Path(urllib.request.url2pathname(parsed.path))
    return Path(source).expanduser()


def _read_cache(source: str) -> tuple[dict[str, Any] | None, float, str | None]:
    p = cache_path()
    if not p.is_file():
        return None, 0.0, None
    try:
        saved = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, 0.0, None
    if not isinstance(saved, dict) or saved.get("url") != source:
        return None, 0.0, None
    catalog = saved.get("catalog")
    if not isinstance(catalog, dict):
        return None, 0.0, None
    fetched_at = float(saved.get("fetched_at") or 0.0)
    etag = saved.get("etag") if isinstance(saved.get("etag"), str) else None
    return catalog, fetched_at, etag


def _write_cache(source: str, catalog: dict[str, Any], etag: str | None) -> None:
    p = cache_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        payload = {
            "url": source,
            "fetched_at": time.time(),
            "etag": etag,
            "catalog": catalog,
        }
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    except OSError:
        pass


def _parse_json_bytes(raw: bytes, source: str) -> dict[str, Any]:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise StylesFetchError(
            "STYLES_BAD",
            t("Visual style catalog at {source} is not valid JSON: {error}", source=source, error=e),
            t("Check the catalog JSON format or pass --design directly without --style"),
        ) from e
    if not isinstance(data, dict) or "styles" not in data:
        raise StylesFetchError(
            "STYLES_BAD",
            t("Visual style catalog at {source} is missing the styles list", source=source),
            t("Check the catalog JSON schema or pass --design directly without --style"),
        )
    return data


def cached_catalog() -> dict[str, Any] | None:
    """只读本地（本地文件源或已有缓存），绝不发起网络请求；用于 concept choose 附带软建议。"""
    source = styles_source()
    local = _local_path(source)
    if local is not None:
        if not local.is_file():
            return None
        try:
            return _parse_json_bytes(local.read_bytes(), str(local))
        except (OSError, StylesFetchError):
            return None
    cached, _, _ = _read_cache(source)
    return cached


def fetch_catalog(refresh: bool = False) -> tuple[dict[str, Any], str, str | None]:
    """读取视觉风格目录：支持本地文件与远端 URL + TTL/ETag 本地缓存 + 断网降级旧缓存。

    返回 `(raw_catalog_dict, source, stale_warning)`。
    """
    source = styles_source()
    local = _local_path(source)
    if local is not None:
        if not local.is_file():
            raise StylesFetchError(
                "STYLES_NET",
                t("Visual style catalog file not found: {source}", source=str(local)),
                t("Check PACXIO_STYLES_URL, or pass --design directly without --style"),
            )
        try:
            data = _parse_json_bytes(local.read_bytes(), str(local))
        except OSError as e:
            raise StylesFetchError(
                "STYLES_NET",
                t("Could not read visual style catalog {source}: {error}", source=str(local), error=e),
                t("Check file permissions, or pass --design directly without --style"),
            ) from e
        return data, str(local), None

    cached, fetched_at, etag = _read_cache(source)
    now = time.time()
    if not refresh and cached is not None and (now - fetched_at) < _ttl_seconds():
        return cached, source, None

    headers = {"Accept": "application/json", "User-Agent": "pacxio"}
    if etag and cached is not None:
        headers["If-None-Match"] = etag
    req = urllib.request.Request(source, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_S) as resp:
            raw = resp.read()
            new_etag = resp.headers.get("ETag")
        data = _parse_json_bytes(raw, source)
        _write_cache(source, data, new_etag or etag)
        return data, source, None
    except urllib.error.HTTPError as e:
        if e.code == 304 and cached is not None:
            _write_cache(source, cached, etag)
            return cached, source, None
        reason = f"HTTP {e.code}"
        if cached is not None:
            return (
                cached,
                source,
                t(
                    "Could not refresh visual style catalog from {source} ({reason}); using cached copy",
                    source=source,
                    reason=reason,
                ),
            )
        raise StylesFetchError(
            "STYLES_NET",
            t(
                "Could not load visual style catalog from {source} ({reason})",
                source=source,
                reason=reason,
            ),
            t(
                "Check network access or pacxio.dev/styles availability, set PACXIO_STYLES_URL to a local catalog.json, or pass --design directly without --style"
            ),
        ) from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        reason = str(getattr(e, "reason", e))
        if cached is not None:
            return (
                cached,
                source,
                t(
                    "Could not refresh visual style catalog from {source} ({reason}); using cached copy",
                    source=source,
                    reason=reason,
                ),
            )
        raise StylesFetchError(
            "STYLES_NET",
            t(
                "Could not load visual style catalog from {source} ({reason})",
                source=source,
                reason=reason,
            ),
            t(
                "Check network access or pacxio.dev/styles availability, set PACXIO_STYLES_URL to a local catalog.json, or pass --design directly without --style"
            ),
        ) from e


__all__ = [
    "DEFAULT_STYLES_URL",
    "STYLES_TTL_ENV",
    "STYLES_URL_ENV",
    "StylesFetchError",
    "cache_path",
    "cached_catalog",
    "fetch_catalog",
    "styles_source",
]
