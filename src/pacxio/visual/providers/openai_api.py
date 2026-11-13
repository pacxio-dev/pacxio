"""OpenAI 图像接口（：用户 API key）。

- key 只从环境变量 OPENAI_API_KEY 读取，不写入 job、不打印、不进元数据；
- 基地址可用 OPENAI_BASE_URL 覆盖（测试用本地假服务），模型可用 PACXIO_OPENAI_IMAGE_MODEL 覆盖；
- 只用标准库发请求，不增加依赖。

【未经真实 API 验证】本实现按公开的 images/generations 接口约定编写，只用本地假服务测过
请求格式与响应解码；没有用真实 key 调用过。上线前须用真实 key 跑通一次。
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request

from pacxio.model.common import Issue
from pacxio.visual.providers.base import GenRequest, GenResult, ProviderError

API_KEY_ENV = "OPENAI_API_KEY"
BASE_URL_ENV = "OPENAI_BASE_URL"
MODEL_ENV = "PACXIO_OPENAI_IMAGE_MODEL"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-image-1"
TIMEOUT_S = 300
# 接口支持的尺寸（宽, 高）：按目标宽高比取最接近的一个
SUPPORTED_SIZES = [(1024, 1024), (1536, 1024), (1024, 1536)]


def pick_size(want: tuple[int, int] | None) -> tuple[int, int]:
    if want is None:
        return SUPPORTED_SIZES[0]
    ratio = want[0] / want[1]
    return min(SUPPORTED_SIZES, key=lambda s: abs(s[0] / s[1] - ratio))


class OpenAIProvider:
    name = "openai"

    def generate(self, request: GenRequest) -> GenResult:
        key = os.environ.get(API_KEY_ENV)
        if not key:
            raise ProviderError(
                Issue(
                    id="ENV",
                    message=f"没有设置环境变量 {API_KEY_ENV}",
                    fix=f"export {API_KEY_ENV}=...（用户自己的 key），或改用 `visual import` 导入图片",
                ),
                exit_code=3,
            )
        base = os.environ.get(BASE_URL_ENV, DEFAULT_BASE_URL).rstrip("/")
        model = os.environ.get(MODEL_ENV, DEFAULT_MODEL)
        w, h = pick_size(request.size)
        body = json.dumps({"model": model, "prompt": request.prompt, "size": f"{w}x{h}", "n": 1})
        req = urllib.request.Request(
            f"{base}/images/generations",
            data=body.encode("utf-8"),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise self._http_error(e) from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ProviderError(
                Issue(
                    id="NET",
                    message=f"连不上出图服务：{e}",
                    fix="检查网络；Codex 沙箱默认不联网，需开启 sandbox_workspace_write.network_access，"
                    "或改用 `visual import`",
                ),
                exit_code=3,
            ) from e
        try:
            item = payload["data"][0]
            image = base64.b64decode(item["b64_json"])
        except (KeyError, IndexError, TypeError, ValueError) as e:
            raise ProviderError(
                Issue(
                    id="PROVIDER",
                    message="出图服务返回了意料之外的响应（缺少 data[0].b64_json）",
                    fix="确认所用模型支持 b64_json 返回；可用 PACXIO_OPENAI_IMAGE_MODEL 指定模型",
                )
            ) from e
        return GenResult(
            image=image,
            model=model,
            revised_prompt=item.get("revised_prompt"),
            extra={"size": f"{w}x{h}"},
        )

    @staticmethod
    def _http_error(e: urllib.error.HTTPError) -> ProviderError:
        try:
            msg = json.loads(e.read().decode("utf-8"))["error"]["message"]
        except (ValueError, KeyError, TypeError):
            msg = e.reason
        fixes = {
            401: f"检查 {API_KEY_ENV} 是否正确、是否过期",
            403: "该 key 没有图像生成权限，或所在地区 / 组织未开通",
            429: "请求太频繁或额度用完，稍后重试或检查账单",
            400: "提示词可能被内容策略拒绝，或参数不被该模型支持；改写提示词后重试",
        }
        return ProviderError(
            Issue(
                id="PROVIDER",
                message=f"出图服务返回 HTTP {e.code}：{msg}",
                fix=fixes.get(e.code, "稍后重试；持续失败请改用 `visual import`"),
            )
        )
