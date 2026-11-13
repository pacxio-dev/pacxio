"""高身包裹式连盖盒（FEFCO 0469–0472；按示意图推读，数值【初值】）。

前墙 – 底板 – 后墙 – 盖连成一条（筒沿 L），两面端墙不连底板，挂在后墙两头：
- 端墙（W × H）从后墙的端边往前伸，顶边隔一条卷边（t）回折 180° 成双层；
- 端墙下沿接一块底翼，往里折平铺在底板上；
- 前墙两头各接一块端翼，折 90° 夹进端墙的两层中间；
- 盖挂在后墙上沿（同 tray.add_lid），前沿按 lid_front：插舌 / 外翻前翼 / 回折锁舌；
- front_return：前墙顶边再接一块回折内衬（0472 前墙的 D 形翼）。
刀版坐标同 tray.build（底板 [0, Lb] × [0, Wb]，前墙 y < 0，后墙 y > Wb）；立体 X = Lb − x。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pacxio.engine.tray_kit import Kit, Rigid, rect
from pacxio.model.dieline import PanelRole

if TYPE_CHECKING:
    from pacxio.engine.tray import Tray, TrayDims, TrayParams

import numpy as np

BOTTOM_FLAP_RATIO = 0.5  # 端墙底翼深 min(0.5·墙高, 0.4·长)【初值】
CLEAR_MM = 2.0


def build_wrap(p: TrayParams, d: TrayDims) -> Tray:
    Lb, Wb, h, t = d.Lb, d.Wb, d.h, d.t
    kit = Kit.root("base", PanelRole.BOTTOM, rect(0, 0, Lb, Wb),
                   Rigid(np.array([Lb, 0.0, 0.0]), np.array([-1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0])))  # fmt: skip
    kit.add("front", PanelRole.BODY, rect(0, -h, Lb, 0), "base", ((0, 0), (Lb, 0)), (0, 0, 1), 1,
            crease_id="crease_base_front")  # fmt: skip
    kit.add("back", PanelRole.BODY, rect(0, Wb, Lb, Wb + h), "base", ((0, Wb), (Lb, Wb)), (0, 0, 1), 1,
            crease_id="crease_base_back")  # fmt: skip
    rg, c = t, max(CLEAR_MM, t + 1.0)
    bf = min(BOTTOM_FLAP_RATIO * h, 0.4 * Lb)
    wf = Wb - c  # 前墙端翼伸进端墙的长
    for ab, xc, sx in (("a", Lb, 1.0), ("b", 0.0, -1.0)):
        X = sx  # 立体：往盒里
        side = f"side_{ab}"
        kit.add(side, PanelRole.BODY, rect(xc, Wb, xc + sx * Wb, Wb + h), "back", ((xc, Wb), (xc, Wb + h)),
                (0, -1, 0), 2)  # fmt: skip
        x0, x1 = xc + sx * c, xc + sx * (Wb - c)
        kit.add(f"rim_{ab}", PanelRole.RIM, rect(x0, Wb + h, x1, Wb + h + rg), side, ((x0, Wb + h), (x1, Wb + h)),
                (X, 0, 0), 4)  # fmt: skip
        hin = h - rg - t
        kit.add(f"{side}_in", PanelRole.INNER_WALL, rect(x0, Wb + h + rg, x1, Wb + h + rg + hin), f"rim_{ab}",
                ((x0, Wb + h + rg), (x1, Wb + h + rg)), (0, 0, -1), 4, layer=-2.0)  # fmt: skip
        kit.add(f"floor_{ab}", PanelRole.DUST_FLAP, rect(x0, Wb, x1, Wb - bf), side, ((x0, Wb), (x1, Wb)),
                (X, 0, 0), 3, layer=-1.0)  # fmt: skip
        kit.add(f"wing_{ab}", PanelRole.DUST_FLAP, rect(xc, -h + c, xc + sx * wf, -c), "front", ((xc, -h + c), (xc, -c)),
                (0, 1, 0), 2, layer=1.0)  # fmt: skip
    if p.front_return:  # 前墙内衬让开两头的端墙内层
        e = 4 * t + 1.0
        kit.add(
            "front_roll",
            PanelRole.RIM,
            rect(e, -h, Lb - e, -h - rg),
            "front",
            ((e, -h), (Lb - e, -h)),
            (0, 1, 0),
            3,
        )
        kit.add("front_in", PanelRole.INNER_WALL, rect(e, -h - rg, Lb - e, -h - rg - (h - rg - t)), "front_roll",
                ((e, -h - rg), (Lb - e, -h - rg)), (0, 0, -1), 3, layer=-2.0)  # fmt: skip
    kit.face("bottom", "base")
    return kit.tray()
