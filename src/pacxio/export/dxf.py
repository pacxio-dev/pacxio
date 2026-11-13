"""DXF R12 导出（）：只用 LINE 和 ARC，每种线型一个图层，名称和颜色取自配置档。

R12 不支持 $INSUNITS 头变量（ezdxf 明确不导出），所以单位 mm 只能靠约定，
交付时须在规格书里写明"DXF 单位：mm"。
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ezdxf._options import options as ezdxf_options
from ezdxf.filemanagement import new as new_dxf
from ezdxf.layouts.layout import Modelspace
from shapely.geometry.base import BaseGeometry

from pacxio.attachments import CROSS_SHARE, AttachmentLayout, is_round
from pacxio.geometry import Segment, polygons_of
from pacxio.messages import t as translate
from pacxio.model.dieline import Dieline
from pacxio.model.profile import DxfLayer, Profile

COORD_DECIMALS = 4  # 输出前取整，避免 89.99999999999999 之类的浮点毛刺


def _r(v: float) -> float:
    return round(v, COORD_DECIMALS) + 0.0  # +0.0 把 -0.0 规整为 0.0


@contextmanager
def _deterministic_ezdxf() -> Iterator[None]:
    """固定 ezdxf 写入的时间戳与版本标记（D8：同一 job.json 两次导出结果一致），用后恢复。"""
    previous = ezdxf_options.write_fixed_meta_data_for_testing
    ezdxf_options.write_fixed_meta_data_for_testing = True
    try:
        yield
    finally:
        ezdxf_options.write_fixed_meta_data_for_testing = previous


def dieline_to_dxf(
    dl: Dieline, profile: Profile, path: Path, attachments: list[AttachmentLayout] | None = None
) -> None:
    with _deterministic_ezdxf():
        _write_dxf(dl, profile, path, attachments or [])


def _write_dxf(
    dl: Dieline, profile: Profile, path: Path, attachments: list[AttachmentLayout]
) -> None:
    # ezdxf 创建 R12 文档时会为默认的 $INSUNITS 记一条告警；该变量本来就不会写入 R12，这里静音
    ezdxf_log = logging.getLogger("ezdxf")
    previous = ezdxf_log.level
    ezdxf_log.setLevel(logging.ERROR)
    try:
        doc = new_dxf("R12")
    finally:
        ezdxf_log.setLevel(previous)
    del doc.header["$INSUNITS"]
    msp = doc.modelspace()
    layers = profile.dxf.layers
    used = {e.type.value for e in dl.edges}
    missing = used - set(layers)
    if missing:
        raise KeyError(translate("Profile dxf.layers is missing line types: {types}", types=sorted(missing)))
    for t in sorted(used):
        doc.layers.add(layers[t].name, color=layers[t].aci)

    for e in dl.edges:
        attribs = {"layer": layers[e.type.value].name}
        for s in e.segments:
            _add_segment(msp, s, attribs)
    if dl.glue_areas:  # 糊胶区标注图层（只描边，不是刀线）
        if "glue_area" not in layers:
            raise KeyError(translate("Profile dxf.layers is missing glue_area for the auto-lock bottom glue annotation"))
        glue = layers["glue_area"]
        doc.layers.add(glue.name, color=glue.aci)
        for area in dl.glue_areas:
            for s in area.segments:
                _add_segment(msp, s, {"layer": glue.name})
    if dl.keep_clear:  # 封口 / 拉链 / 撕口 / 搭接区标注图层（软袋、标签；不是刀线，C5）
        lay = layers.get("keep_clear") or DxfLayer(name="SEAL", aci=6)
        doc.layers.add(lay.name, color=lay.aci)
        for k in dl.keep_clear:
            for s in k.path.segments:
                _add_segment(msp, s, {"layer": lay.name})
    if attachments:  # 配件贴标位置图层（外形线 + 中心十字；不是刀线，）
        if "attachment" not in layers:
            raise KeyError(translate("Profile dxf.layers is missing attachment for attachment-position marks"))
        lay = layers["attachment"]
        doc.layers.add(lay.name, color=lay.aci)
        for a in attachments:
            _add_outline(msp, a.world, {"layer": lay.name})
    doc.saveas(path)


def _add_outline(msp: Modelspace, g: BaseGeometry, attribs: dict[str, str]) -> None:
    """配件外形：圆形写成两段半圆 ARC（D4：只用直线和圆弧），其它按折线写 LINE；再加中心十字。"""
    x0, y0, x1, y1 = g.bounds
    cx, cy, w, h = (x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0
    if is_round(g):
        for a0, a1 in ((0.0, 180.0), (180.0, 360.0)):
            msp.add_arc((_r(cx), _r(cy)), _r(w / 2), a0, a1 % 360.0, dxfattribs=attribs)
    else:
        for poly in polygons_of(g):
            pts = list(poly.exterior.coords)
            for (ax, ay), (bx, by) in zip(pts, pts[1:], strict=False):
                msp.add_line((_r(ax), _r(ay)), (_r(bx), _r(by)), dxfattribs=attribs)
    arm = min(w, h) * CROSS_SHARE
    msp.add_line((_r(cx - arm), _r(cy)), (_r(cx + arm), _r(cy)), dxfattribs=attribs)
    msp.add_line((_r(cx), _r(cy - arm)), (_r(cx), _r(cy + arm)), dxfattribs=attribs)


def _add_segment(msp: Modelspace, s: Segment, attribs: dict[str, str]) -> None:
    if s.is_line:
        msp.add_line((_r(s.start.x), _r(s.start.y)), (_r(s.end.x), _r(s.end.y)), dxfattribs=attribs)
        return
    center, radius, start_ang, theta, ccw = s.arc_info()
    # DXF 的 ARC 一律逆时针：顺时针弧改为从终点逆时针扫到起点
    a0 = start_ang if ccw else (s.end - center).angle()
    msp.add_arc(
        (_r(center.x), _r(center.y)),
        _r(radius),
        math.degrees(a0) % 360.0,
        math.degrees(a0 + theta) % 360.0,
        dxfattribs=attribs,
    )
