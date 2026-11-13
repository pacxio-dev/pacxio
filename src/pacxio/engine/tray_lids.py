"""连盖托盘的另几种盖（FEFCO 0418、0440；按示意图推读，数值【初值】）。

- lid_front = return（0418）：盖前沿的前翼 v 垂下去，底下隔一条卷边（t）回折 180° 往上，
  回折那片顶边的三个锁舌插进盖和前翼之间压线上的槽，前沿成双层、自锁。
- lid_on = end（0440）：盖挂在端墙 a 的上沿，合上盖住整个托盘；远端前翼盖在端墙 b 外面，
  端墙 b 顶上一条窄边往里折平（盖压在上面）；盖两侧防尘翼折进长边墙里面。
刀版坐标同 tray.build（底板 [0, Lb] × [0, Wb]，side_a 在 x > Lb）；立体 X = Lb − x。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pacxio.engine.tray_kit import Kit, rect
from pacxio.model.dieline import PanelRole

if TYPE_CHECKING:
    from pacxio.engine.tray import Tray, TrayParams

RETURN_TABS = (0.25, 0.5, 0.75)  # 回折锁舌在前翼宽的位置
RETURN_TAB_W_RATIO, RETURN_TAB_W_MAX = 0.12, 30.0  # 锁舌宽 min(0.12·盖宽, 30)
SLOT_CLEAR_MM = 1.0


def lid_return(p: TrayParams, tray: Tray, t: float) -> Tray:
    """把 add_lid 的插舌当前翼 v，接卷边 + 回折内衬 + 锁舌，盖沿压线上开槽。"""
    kit = Kit.of(tray)
    tuck = kit.panels["tuck"][1]
    x0, y0, x1, y1 = tuck.bbox()  # 前翼：y0 是和盖的压线，y1 是远边
    kit.reshape("tuck", rect(x0, y0, x1, y1))
    step = kit.last_step()
    kit.add(
        "tuck_roll",
        PanelRole.RIM,
        rect(x0, y1, x1, y1 + t),
        "tuck",
        ((x0, y1), (x1, y1)),
        (0, 1, 0),
        step,
    )
    depth = y1 - y0
    tw = min(RETURN_TAB_W_RATIO * (x1 - x0), RETURN_TAB_W_MAX)
    tl = 2 * t + 3.0
    ye = y1 + t + depth - t  # 回折内衬顶边（合上后比盖低一层纸厚），锁舌从这里往上穿过盖沿的槽
    pts = [(x0, y1 + t)]
    pts.append((x0, ye))
    for f in RETURN_TABS:
        xc = x0 + f * (x1 - x0)
        c = min(tl / 2, tw / 4)
        pts += [(xc - tw / 2, ye), (xc - tw / 2, ye + tl - c), (xc - tw / 2 + c, ye + tl), (xc + tw / 2 - c, ye + tl),
                (xc + tw / 2, ye + tl - c), (xc + tw / 2, ye)]  # fmt: skip
        sw = tw / 2 + SLOT_CLEAR_MM
        # 槽宽（横过盖沿）4t + 3：回折内衬贴在前翼后面，锁舌从这里穿出【初值】
        kit.open_slot(f"cut_slot_lock_{int(f * 4)}", rect(xc - sw, y0 - (4 * t + 3.0), xc + sw, y0 + SLOT_CLEAR_MM),
                      "lid")  # fmt: skip
    pts += [(x1, ye), (x1, y1 + t)]
    kit.add(
        "tuck_return",
        PanelRole.INNER_WALL,
        pts,
        "tuck_roll",
        ((x0, y1 + t), (x1, y1 + t)),
        (0, 0, 1),
        step,
        layer=-2.0,
    )  # fmt: skip  效果图：往盒里错开两层
    return kit.tray()


def lid_on_end(p: TrayParams, d: object, tray: Tray) -> Tray:
    """盖挂端墙 a；远端前翼盖在端墙 b 外面；端墙 b 顶窄边；盖两侧防尘翼折进长边墙里面。"""
    from pacxio.engine.tray import LID_DUST_RATIO, LID_TUCK_CAP_MM, LID_TUCK_RATIO

    Lb, Wb, h, t = d.Lb, d.Wb, d.h, d.t  # type: ignore[attr-defined]
    kit = Kit.of(tray)
    step = kit.last_step() + 1
    xa = Lb + h
    kit.add(
        "lid",
        PanelRole.LID,
        rect(xa, 0, xa + Lb, Wb),
        "side_a",
        ((xa, 0), (xa, Wb)),
        (1, 0, 0),
        step + 1,
    )
    v = p.tuck if p.tuck is not None else min(LID_TUCK_RATIO * h, LID_TUCK_CAP_MM)
    xf = xa + Lb
    kit.add("tuck", PanelRole.TUCK, rect(xf, 0, xf + v, Wb), "lid", ((xf, 0), (xf, Wb)), (0, 0, -1), step + 1,
            layer=-2.0)  # fmt: skip
    if p.rim is not None:  # 端墙 b 顶上的窄边（盖压在上面）
        kit.add("rim_side_b", PanelRole.DUST_FLAP, rect(-h - p.rim, t, -h, Wb - t), "side_b",
                ((-h, t), (-h, Wb - t)), (-1, 0, 0), step, layer=-1.0)  # fmt: skip
    if p.lid_dust:
        dd = p.dust_depth if p.dust_depth is not None else LID_DUST_RATIO * h
        c = t + 1.0
        for fb, yw, sy in (("a", 0.0, -1.0), ("b", Wb, 1.0)):
            kit.add(
                f"dust_{fb}",
                PanelRole.DUST_FLAP,
                rect(xa + c, yw, xf - c, yw + sy * dd),
                "lid",
                ((xa + c, yw), (xf - c, yw)),
                (0, 0, -1),
                step,
                layer=3.0,
            )  # fmt: skip  在角翼里面
    kit.face("top", "lid")
    return kit.tray()


def lock_slot(p: TrayParams, tray: Tray, d: object) -> Tray:
    """盖前沿锁舌插进的槽：开在前墙顶的压线上，跨过卷边 / 窄边（0465 / 0466）。"""
    h, t = d.h, d.t  # type: ignore[attr-defined]
    kit = Kit.of(tray)
    x0, _, x1, _ = kit.panels["tuck"][1].bbox()
    strip = t if p.front_double else (p.rim or t)
    # 前墙双层：槽跨过整条卷边、伸进内墙顶 1 mm（锁舌插进内外墙中间）；窄边：伸进窄边、留 1 mm 不切断
    depth = t + 1.0 if p.front_double else min(strip - 1.0, 2 * t + 2.0)
    kit.open_slot(
        "cut_slot_lock",
        rect(x0 - SLOT_CLEAR_MM, -h - depth, x1 + SLOT_CLEAR_MM, -h + t + 1.0),
        "front",
    )
    return kit.tray()
