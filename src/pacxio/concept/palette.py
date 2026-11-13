"""从选定的概念图里提取主色，作为正式设计的品牌色 / 底色建议。

概念图通常带场景（墙面、台面、植物），整图取色会被场景颜色主导，所以支持只取一个区域
（用 0–1 的比例给出，例如盒子正面所在的矩形）。

确定性 k-means（固定初始化），同一张图、同一区域每次结果一致。CMYK 是**近似值**，只能作为起点：
有 ICC 时按它换算（和照片、插画同一个换算，印出来接近图上的颜色）；没有时用简单公式（印出来偏灰、
偏暗，Brackenfold 实测）。品牌色应以品牌规范或色卡为准。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image

if TYPE_CHECKING:  # 色彩换算闭源（）：`concept choose` 转给云端跑
    from pacxio_pro.color import ColorConverter

MAX_SIDE = 160  # 取色前缩小到这个尺寸以内，足够且快
K = 6
ITERATIONS = 20
MERGE_DISTANCE = 20.0  # RGB 欧氏距离小于它的两个主色合并
MIN_SHARE = 0.02  # 占比低于 2% 的颜色不报告

Region = tuple[float, float, float, float]  # (x0, y0, x1, y1)，0–1 比例，原点在图片左上角


@dataclass(frozen=True)
class PaletteColor:
    hex: str
    rgb: tuple[int, int, int]
    share: float
    cmyk_pct: tuple[int, int, int, int]  # 近似值


def _crop(img: Image.Image, region: Region | None) -> Image.Image:
    if region is None:
        return img
    x0, y0, x1, y1 = region
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise ValueError("region 必须是 0–1 之间的 x0,y0,x1,y1，且 x0<x1、y0<y1")
    w, h = img.size
    return img.crop((round(x0 * w), round(y0 * h), round(x1 * w), round(y1 * h)))


def extract_palette(
    img: Image.Image, region: Region | None = None, k: int = K, conv: ColorConverter | None = None
) -> list[PaletteColor]:
    """conv：job 的色彩换算（有 ICC 时 CMYK 按它换算并压总墨量）；不给用简单公式。"""
    from pacxio_pro.color import rgb_to_cmyk_naive
    from pacxio_pro.color.ink import limit_tac

    small = _crop(img.convert("RGB"), region)
    small.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.BOX)
    px = np.asarray(small, dtype=np.float32).reshape(-1, 3)
    if len(px) == 0:
        return []
    # 确定性初始化：按亮度排序后等间隔取 k 个像素
    order = np.argsort(px.sum(axis=1), kind="stable")
    centres = px[order[np.linspace(0, len(px) - 1, k).astype(int)]].copy()
    labels = np.zeros(len(px), dtype=int)
    for _ in range(ITERATIONS):
        d = ((px[:, None, :] - centres[None, :, :]) ** 2).sum(axis=2)
        labels = d.argmin(axis=1)
        for j in range(k):
            members = px[labels == j]
            if len(members):
                centres[j] = members.mean(axis=0)
    counts = np.bincount(labels, minlength=k).astype(np.float64)
    # 合并太接近的颜色
    groups: list[tuple[np.ndarray, float]] = []
    for idx in np.argsort(-counts, kind="stable"):
        c, n = centres[idx], counts[idx]
        if n == 0:
            continue
        for gi, (gc, gn) in enumerate(groups):
            if float(np.sqrt(((gc - c) ** 2).sum())) < MERGE_DISTANCE:
                groups[gi] = ((gc * gn + c * n) / (gn + n), gn + n)
                break
        else:
            groups.append((c, n))
    total = float(counts.sum())
    out: list[PaletteColor] = []
    for c, n in sorted(groups, key=lambda g: -g[1]):
        share = n / total
        if share < MIN_SHARE:
            continue
        rgb = tuple(int(round(v)) for v in c)
        px1 = np.array([[rgb]], dtype=np.uint8)
        if conv is None or conv.degraded:
            cmyk = rgb_to_cmyk_naive(px1)[0, 0]
        else:
            ink = conv.rgb_to_cmyk(px1)
            if conv.tac_limit_pct is not None:
                ink = limit_tac(ink, conv.tac_limit_pct)[0]
            cmyk = ink[0, 0]
        out.append(
            PaletteColor(
                hex="#{:02X}{:02X}{:02X}".format(*rgb),
                rgb=rgb,  # type: ignore[arg-type]
                share=round(share, 4),
                # 先转成 Python int：uint8 直接乘 100 会溢出
                cmyk_pct=tuple(round(int(v) * 100 / 255) for v in cmyk),  # type: ignore[arg-type]
            )
        )
    return out
