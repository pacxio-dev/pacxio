"""测试用的合成展开平面图（不是测试文件）：比例和真实一样、量得出边界。"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from pacxio.concept.rectify import face_rects
from pacxio.dielines import build_dieline_for
from pacxio.model.job import Job

PX_PER_MM = 3
MARGIN = 40


def true_flat(job: Path, faces: list[str], color: tuple[int, int, int]) -> Image.Image:
    """这几个面并排的平面图：白底上一块纯色画面（按真实比例），四周等宽白边。"""
    dl, _ = build_dieline_for(Job.load(job))
    assert dl is not None
    rects = face_rects(dl)
    w = round(sum(rects[f][2] - rects[f][0] for f in faces) * PX_PER_MM)
    h = round(max(rects[f][3] - rects[f][1] for f in faces) * PX_PER_MM)
    img = Image.new("RGB", (w + 2 * MARGIN, h + 2 * MARGIN), (255, 255, 255))
    ImageDraw.Draw(img).rectangle([MARGIN, MARGIN, MARGIN + w - 1, MARGIN + h - 1], fill=color)
    return img
