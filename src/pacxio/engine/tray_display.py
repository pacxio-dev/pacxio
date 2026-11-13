"""托盘连盖的展示开盒选项（2026-11-06，用户的实物盒：卡纸托盒 + 开窗顶板 + 撕开式展示盖）。

都是托盘连盖（hinged_lid，盖挂后墙）上的选项，可以和别的托盘组合：
- lid_front = none：盖远边不带插舌（`add_lid` 先照常做，这里去掉插舌）；
- dust_lock：盖两侧的翼改成矩形、折下去贴在侧墙**外面**（露在外面），下沿两头各一个锁舌（根部压线），往盒里
  折 90°、横着穿过底板 / 侧墙折线上的槽，颈卡在槽里、里头那段带倒刺扣住——这时**侧面不糊胶**；要糊侧面就不用
  dust_lock（用户：“有胶水就不要扣了”）；
- tear_window：盖中间一圈齿线围成撕开窗（四角圆角）；
- display_lid：前墙上沿再挂一层展示盖，往后翻、压在盖外面。有撕开窗时，窗口靠展示盖铰链那一边外面一条横向
  胶条（糊胶区，盖的印刷面上），展示盖背面粘在胶条上——掀起展示盖时胶条把撕开窗沿齿线带下来，展示盖立起来
  当头卡（2026-11-06 用户对着实物纠正：胶在开口靠底那条边上，不在窗块上）。tab：远边两角圆角 + 中间一个
  半圆易开拉手，拉手是能折的小片，合上时翻过盒边贴在后墙外面（真圆弧）。

立体约定同 `tray.Frame`：X = Lb − x，前墙 Y = 0、后墙 Y = Wb，Z 向上。数值都是【初值】，按用户实物照片和
常见开窗展示盒推的比例，未折样。
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from pacxio.geometry import Path, Segment, Vec, arc_segment
from pacxio.model.dieline import Edge, EdgeType, PanelRole

if TYPE_CHECKING:
    from pacxio.engine.tray import Tray, TrayDims, TrayParams

# 【初值】
LOCK_TAB_W_RATIO, LOCK_TAB_W_MAX = 0.18, 18.0  # 锁舌宽 min(0.18·翼长, 18)
LOCK_TAB_END_RATIO = 0.12  # 锁舌离翼两头 0.12·翼长
LOCK_NECK_EXTRA_MM = 0.5  # 锁舌的颈 = 两层纸厚（侧翼 + 侧墙）+ 0.5，卡在槽里
LOCK_TIP_LEN_MM = 6.0  # 颈以里那段（平躺在底板上）长 6
LOCK_BARB_MM = 1.0  # 倒刺比槽每边宽 1：穿过去以后卡住侧墙，拔不出来
LOCK_SLOT_CLEAR_MM = 0.5  # 槽比颈每边宽 0.5
LOCK_SLOT_UP_T, LOCK_SLOT_DOWN_MM = (
    2.5,
    1.0,
)  # 槽骑在底板 / 侧墙折线上：往侧墙开 2.5·t + 1，往底板开 1
SIDE_LAYER = -1.0  # 侧翼贴在侧墙外面
TEAR_MARGIN_RATIO = 0.17  # 撕开窗离盖四边 0.17·短边
TEAR_R_RATIO, TEAR_R_MAX = (
    0.1,
    8.0,
)  # 撕开窗四角圆角 min(0.1·窗口短边, 8)（撕的时候不裂进角里，用户 2026-11-07）【初值】
GLUE_GAP_MM = 2.0  # 胶条离窗口边 2
GLUE_BAND_RATIO, GLUE_BAND_MAX = 0.5, 12.0  # 胶条宽 min(0.5·窗口边距, 12)
DISPLAY_LAYER = -1.0  # 展示盖压在盖外面
CORNER_RATIO = 0.08  # 展示盖远边两角圆角半径 0.08·盒长
PULL_R_RATIO, PULL_R_RANGE = 0.1, (10.0, 20.0)  # 半圆拉手半径 0.1·盒长，10–20
PULL_R_MAX_L, PULL_R_MAX_W, PULL_R_MIN_MM = (
    0.3,
    0.25,
    5.0,
)  # 但不超过 0.3·盒长、0.25·盒宽（拉手收在展示盖长度里）；小于 5 就不做


def extend_lid(p: TrayParams, d: TrayDims, tray: Tray) -> Tray:
    """在已加好盖的托盘上加：去插舌 / 锁舌 + 槽 / 撕开窗 / 展示盖 + 糊胶区。"""
    from pacxio.engine.tray_kit import Kit

    if p.lid_front != "none" and not (p.dust_lock or p.tear_window or p.display_lid != "none"):
        return tray
    kit = Kit.of(tray)
    Lb, Wb, h, t = d.Lb, d.Wb, d.h, d.t
    yh, yl = Wb + h, 2 * Wb + h  # 盖的铰链（后墙上沿）和远边
    if p.lid_front == "none" and "tuck" in kit.panels:
        kit.drop("tuck")
    if p.dust_lock:
        _dust_locks(kit, Lb, h, t, yh, yl)
    glue: list[Path] = []
    if p.tear_window:
        m = TEAR_MARGIN_RATIO * min(Lb, Wb)
        x0, x1, y0, y1 = m, Lb - m, yh + m, yl - m
        r = min(TEAR_R_RATIO * min(x1 - x0, y1 - y0), TEAR_R_MAX)
        win = Path.rounded_rect(x0, y0, x1 - x0, y1 - y0, r)
        kit.cuts.append(Edge(id="perf_tear_window", type=EdgeType.PERF, segments=win.segments,
                             panels=["lid"]))  # fmt: skip
        if p.display_lid != "none":  # 胶条：窗口靠展示盖铰链（盖远边 yl，合上时在前墙）那一边外面
            bw = min(GLUE_BAND_RATIO * m, GLUE_BAND_MAX)
            glue.append(Path.rect(m / 2, y1 + GLUE_GAP_MM, Lb - m, bw))
    far: list[Segment] = []
    true: dict[str, tuple[PanelRole, Path]] = {}
    if p.display_lid != "none":
        far, true = _display(kit, p.display_lid, Lb, Wb, h)
    out = kit.tray()  # 挂面工具按折线近似的面并外轮廓、给自检目标
    if far:
        out = _restore_arcs(out, far)
        out.panels.update(true)  # 再把展示盖、拉手换回真圆弧
    out.glue = glue
    return out


def _dust_locks(kit: object, Lb: float, h: float, t: float, yh: float, yl: float) -> None:
    """盖两侧的翼改成矩形、折下去贴在侧墙**外面**（露在外面的面）；翼下沿两头各一个锁舌（根部压线），
    往盒里折 90°，横着穿过底板和侧墙折线上的槽、平躺在底板上；颈卡在槽里，里头那段比槽宽（倒刺朝翼两头）。
    用户实物（2026-11-06）：侧面不糊胶，靠这个扣住。"""
    from pacxio.engine.tray_kit import Kit

    assert isinstance(kit, Kit)
    c = t + 1.0  # 两头离开后墙角翼一点（不和它们共边）
    step = kit.last_step() + 1
    for side, xe, s in (("a", Lb, 1.0), ("b", 0.0, -1.0)):
        pid = f"dust_{side}"
        kit.drop(pid)
        ya, yb = yh + c, yl - c
        xo = xe + s * h  # 下沿：盖在墙顶，往下一个墙高到盒底
        kit.add(pid, PanelRole.DUST_FLAP, [(xe, ya), (xo, ya), (xo, yb), (xe, yb)], "lid", ((xe, ya), (xe, yb)),
                (0.0, 0.0, -1.0), step, layer=SIDE_LAYER)  # fmt: skip
        span = yb - ya
        tw = min(LOCK_TAB_W_RATIO * span, LOCK_TAB_W_MAX)
        neck = 2 * t + LOCK_NECK_EXTRA_MM
        tl = neck + LOCK_TIP_LEN_MM
        barb = LOCK_SLOT_CLEAR_MM + LOCK_BARB_MM
        inward = (1.0, 0.0, 0.0) if side == "a" else (-1.0, 0.0, 0.0)  # 端墙 a 在 X = 0，往盒里 +X
        centres = [ya + LOCK_TAB_END_RATIO * span + tw / 2, yb - LOCK_TAB_END_RATIO * span - tw / 2]
        for n, yc in enumerate(centres):
            a, b = yc - tw / 2, yc + tw / 2
            xn, xt = xo + s * neck, xo + s * tl
            r = min(1.0, 0.3 * LOCK_TIP_LEN_MM)
            if n == 0:  # 靠 ya 那头：倒刺朝 −y
                pts = [(xo, a), (xn, a), (xn, a - barb), (xt - s * r, a - barb), (xt, a - barb + r),
                       (xt, b - r), (xt - s * r, b), (xo, b)]  # fmt: skip
            else:
                pts = [(xo, a), (xt - s * r, a), (xt, a + r), (xt, b + barb - r), (xt - s * r, b + barb),
                       (xn, b + barb), (xn, b), (xo, b)]  # fmt: skip
            tab = kit.add(f"lock_{side}_{n + 1}", PanelRole.TUCK, pts, pid, ((xo, a), (xo, b)), inward, step + 1,
                          layer=SIDE_LAYER)  # fmt: skip
            # 槽骑在底板 / 侧墙折线上，对着锁舌：底板坐标 x = Lb − X，y = Y
            _, Y, _ = kit.at(tab, xo, yc)
            hl = tw / 2 + LOCK_SLOT_CLEAR_MM
            xc = Lb if side == "a" else 0.0  # 折线
            up, down = LOCK_SLOT_UP_T * t + 1.0, LOCK_SLOT_DOWN_MM
            x0, x1 = (xc - down, xc + up) if side == "a" else (xc - up, xc + down)
            kit.open_slot(f"cut_lock_slot_{side}_{n + 1}", [(x0, Y - hl), (x1, Y - hl), (x1, Y + hl), (x0, Y + hl)],
                          "base")  # fmt: skip


def pull_tab_r(Lb: float, Wb: float | None = None) -> float:
    """半圆拉手半径：0.1·盒长（10–20），但不超过 0.3·盒长（两角圆角之间放得下）和 0.25·盒宽
    （拉手收在展示盖长度里：展示盖连拉手一共 = 盒宽，用户 2026-11-07）。"""
    r = min(max(PULL_R_RATIO * Lb, PULL_R_RANGE[0]), PULL_R_RANGE[1])
    caps = [PULL_R_MAX_L * Lb] + ([PULL_R_MAX_W * Wb] if Wb is not None else [])
    return min(r, *caps)


def display_problems(Lb: float, Wb: float) -> list[str]:
    """展示盖拉手放不下的尺寸。"""
    if pull_tab_r(Lb, Wb) < PULL_R_MIN_MM:
        return [
            f"盒子太小（长 {Lb:.0f}、宽 {Wb:.0f}），展示盖的半圆拉手放不下（要半径 ≥ {PULL_R_MIN_MM:g}）"
        ]
    return []


def far_edge(Lb: float, yf: float, rc: float, r: float) -> list[Segment]:
    """展示盖远边（逆时针里从左到右那一段，外轮廓用）：左角圆角 → 直边 → 半圆拉手 → 直边 → 右角圆角。"""
    xm = Lb / 2
    return [
        arc_segment(Vec(rc, yf + rc), rc, math.pi, 1.5 * math.pi),
        Segment(Vec(rc, yf), Vec(xm - r, yf)),
        arc_segment(Vec(xm, yf), r, math.pi, 2 * math.pi),  # 半圆拉手（往 −y 凸出）
        Segment(Vec(xm + r, yf), Vec(Lb - rc, yf)),
        arc_segment(Vec(Lb - rc, yf + rc), rc, 1.5 * math.pi, 2 * math.pi),
    ]


def _display(
    kit: object, shape: str, Lb: float, Wb: float, h: float
) -> tuple[list[Segment], dict[str, tuple[PanelRole, Path]]]:
    """前墙上沿挂展示盖，往后翻压在盖外面。rect：远边到后墙上沿；tab：两角圆角 + 远边中间半圆拉手，
    **连拉手一共 = 盒宽**（拉手不伸出盒外，合上时平贴在盖上，用户 2026-11-07），拉手是展示盖轮廓的一部分、没有折线。
    返回外轮廓里要换回真圆弧的远边段，和换回真圆弧的面。"""
    from pacxio.engine.tray_kit import Kit

    assert isinstance(kit, Kit)
    ytop = -h  # 铰链（前墙上沿）
    step = kit.last_step() + 1
    if shape != "tab":
        yf = ytop - Wb  # 远边（合上时在后墙上沿）
        pts = [(0.0, ytop), (0.0, yf), (Lb, yf), (Lb, ytop)]
        kit.add("display", PanelRole.LID, pts, "front", ((0.0, ytop), (Lb, ytop)), (0.0, 1.0, 0.0), step,
                layer=DISPLAY_LAYER)  # fmt: skip
        return [], {}
    rc, r = CORNER_RATIO * Lb, pull_tab_r(Lb, Wb)
    yf = ytop - (Wb - r)  # 展示盖本体的远边；拉手再往外 r，尖正好到后墙上沿
    far = far_edge(Lb, yf, rc, r)
    body = Path([Segment(Vec(Lb, ytop), Vec(0.0, ytop)), Segment(Vec(0.0, ytop), Vec(0.0, yf + rc)), *far,
                 Segment(Vec(Lb, yf + rc), Vec(Lb, ytop))], closed=True)  # fmt: skip
    pts = [(q.x, q.y) for q in body.to_points(max_sagitta=0.02)]
    kit.add("display", PanelRole.LID, pts, "front", ((0.0, ytop), (Lb, ytop)), (0.0, 1.0, 0.0), step,
            layer=DISPLAY_LAYER, size=(Lb, Wb))  # fmt: skip
    return far, {"display": (PanelRole.LID, body)}


def _restore_arcs(tray: Tray, far: list[Segment]) -> Tray:
    """挂面工具按多边形并出外轮廓（圆弧变成折线）：把展示盖远边那一段换回真圆弧。"""
    from dataclasses import replace

    start, end = far[0].start, far[-1].end
    segs = tray.outline.segments
    pts = [q.start for q in segs]
    i = next(k for k, q in enumerate(pts) if q.is_close(start, 1e-6))
    j = next(k for k, q in enumerate(pts) if q.is_close(end, 1e-6))
    n = len(pts)
    ring = pts[i:] + pts[:i]  # 从左肩起
    jj = (j - i) % n
    rest = ring[jj:] + [ring[0]]
    out = list(far) + [Segment(a, b) for a, b in zip(rest, rest[1:], strict=False)]
    return replace(tray, outline=Path(out, closed=True))


__all__ = ["extend_lid", "far_edge", "pull_tab_r"]
