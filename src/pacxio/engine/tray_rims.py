"""托盘墙顶窄边的收头和墙角加强（FEFCO 0431–0434、0451.1；按示意图推读，数值【初值】）。

用挂面工具（tray_kit）在 build() / ear_tray() 的托盘上加面。刀版坐标同 tray.build：底板 [0, Lb] × [0, Wb]，
正面墙在 y < 0，side_a 在 x > Lb；立体 X = Lb − x，Y = y，Z 向上（side_a 在 X = 0）。

- rim_ends（长边墙窄边在墙角怎么收）：
  - posts  窄边两头各接一段立柱，折下来贴在端墙里面（0433：插进三角角柱）
  - lugs   立柱下端再接插脚，往外穿过端墙脚的槽锁住（0431）
  - mitre  窄边接着绕过墙角、斜接到端墙顶上（角翼在端墙外面，各盖半个端墙，0434）
  - tabs   窄边两头的圆角插舌往下插进双层端墙卷边上的槽（0432，ear 托盘）
- triangle_posts：端墙卷边成双层，内层两头斜 45° 折到长边墙、再贴长边墙一段，墙角成三角角柱（0433）
- inner_wings：ear 托盘的内墙两侧各带一块翼，折 90° 贴长边墙里面（0432）
- edge_slots：底板四条边上各两个底边孔（0451.1）
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from pacxio.engine.tray_kit import Kit, rect
from pacxio.messages import t as translate
from pacxio.model.common import Issue
from pacxio.model.dieline import PanelRole

if TYPE_CHECKING:
    from pacxio.engine.tray import Tray, TrayDims, TrayParams

# 【初值】
POST_SLOT_H_T, POST_SLOT_H_MIN = 2.0, 5.0  # 端墙脚的槽高 max(2t, 5)，插脚从槽中间穿出
POST_LUG_W_RATIO, POST_LUG_W_MAX, POST_LUG_W_MIN = (
    0.6,
    30.0,
    6.0,
)  # 插脚宽 min(0.6·立柱宽, 30)，至少 6
POST_LUG_LEN_T, POST_LUG_LEN_EXTRA = 1.0, 5.0  # 插脚长 t + 5
POST_LUG_DROP_T = 2.5  # 立柱插脚比按刀版墙高算的位置抬高几层纸厚，对准端墙脚的槽【初值】
SLOT_CLEAR_MM = 2.0  # 槽比插脚 / 插舌每边宽这么多
WRAP_LEN_RATIO = 0.8  # 端墙窄边头包角那段长 0.8·墙高
DROP_LEN_RATIO, DROP_LEN_MAX = 0.4, 30.0  # 窄边头短舌长 min(0.4·墙高, 30)
TRI_EXTRA_T = 2.0  # 三角角柱的直角边 a = 窄边宽 + 2t（立柱放得进）
TAB_W_RATIO, TAB_LEN_RATIO, TAB_LEN_MAX = 0.5, 0.5, 30.0  # 插舌宽 0.5·窄边，长 min(0.5·墙高, 30)
TAB_CHAMFER_RATIO = 0.3
EDGE_SLOT_RATIO, EDGE_SLOT_MIN, EDGE_SLOT_MAX = 0.1, 15.0, 40.0  # 底边孔长 clamp(0.1·边长, 15, 40)
EDGE_SLOT_D_T, EDGE_SLOT_D_MIN = 2.0, 4.0  # 底边孔深 max(2t, 4)
EDGE_SLOT_AT = (0.25, 0.75)


def _sx(ab: str) -> float:
    """端墙 a 在 X = 0（往盒里 +X），b 在 X = Lb（往盒里 −X）。"""
    return 1.0 if ab == "a" else -1.0


def post_slot_h(t: float) -> float:
    return max(POST_SLOT_H_T * t, POST_SLOT_H_MIN)


def tri_leg(p: TrayParams, t: float) -> float:
    assert p.rim is not None
    return p.rim + TRI_EXTRA_T * t


def tab_width(v: float, ii: float) -> float:
    """插舌宽 0.5·窄边，但卷边上的槽要落在卷边里（离卷边头至少 1 mm）。"""
    return min(TAB_W_RATIO * v, v - SLOT_CLEAR_MM - ii - 1.0)


def validate(p: TrayParams, d: TrayDims | None) -> list[Issue]:
    """d 为 None：ear 托盘（校准部件，尺寸另查）。"""
    out: list[Issue] = []
    if p.rim_ends == "tabs" and (p.corner != "ear" or p.rim is None or p.rim_on != "long"):
        return [Issue(id="S03", message=translate("Rim tucks are supported on long-side rims of double-end-wall trays (ear) only"),
                      fix="corner=ear，rim_on=long")]  # fmt: skip
    if p.inner_wings and p.corner != "ear":
        return [
            Issue(
                id="S03",
                message=translate("Inner-wall wings require double-end-wall trays (ear)"),
                fix="corner=ear",
            )
        ]
    if d is None:
        if p.rim_ends not in ("none", "tabs") or p.triangle_posts or p.edge_slots:
            return [Issue(id="S03", message=translate("Double-end-wall trays (ear) cannot have posts, mitres, triangular corner posts, or base-edge holes"),
                          fix=translate("Remove these options or use a single-wall tray"))]  # fmt: skip
        return out
    t, v = d.t, p.rim or 0.0
    if p.rim_ends == "wrap" and (p.rim is None or p.rim_on != "end" or p.corner != "flap" or p.corner_on != "end"
                                 or p.corner_side != "out"):  # fmt: skip
        return [Issue(id="S03", message=translate("An end-rim corner wrap requires an end-wall rim and end-wall corner flaps on the outside of the long walls"),
                      fix="rim_on=end，corner=flap，corner_on=end，corner_side=out")]  # fmt: skip
    if p.end_height is not None and (p.corner_on != "long" and p.corner != "open"):
        return [
            Issue(
                id="S03",
                message=translate(
                    "A lowered end wall requires long-side corner flaps or open corners"
                ),
                fix="corner_on=long",
            )
        ]
    if p.end_height is not None and p.end_height > d.h:
        out.append(
            Issue(
                id="S03",
                message=translate("end_height exceeds the long-side wall height"),
                fix=translate("Set end_height at or below the wall height"),
            )
        )
    if p.end_height is not None and p.rim_ends == "posts" and d.h - p.end_height - d.slot < 10:
        out.append(
            Issue(
                id="S03",
                message=translate(
                    "The end and long-side walls are too close in height to fit the post"
                ),
                fix=translate("Reduce end_height"),
            )
        )
    if p.end_double and (p.end_height or d.h) - 2 * t < 15:
        out.append(
            Issue(
                id="S03",
                message=translate("The end wall is too low to fit the double-wall rollover"),
                fix=translate("Increase end_height or H"),
            )
        )
    if p.flap_drops and p.cross_flaps != "long":
        return [
            Issue(
                id="S03",
                message=translate("Fold-down ends require half-width long-side lid flaps"),
                fix=translate("Set top to flaps with flap=long"),
            )
        ]
    if p.cup_holes and (p.rim is None or p.rim_on != "long"):
        return [
            Issue(
                id="S03",
                message=translate("Cup holes go in the rim at the top of the long-side wall"),
                fix=translate("Specify rim and use rim_on=long"),
            )
        ]
    if p.cup_holes and cup_layout(d.Lb, p.rim or 0.0)[0] < 25:
        out.append(
            Issue(
                id="S03",
                message=translate("The rim is too narrow to fit a usable cup hole"),
                fix=translate("Make W larger"),
            )
        )
    if p.front_low and (
        p.corner not in ("open", "flap", "gusset")
        or p.corner_diagonal
        or (p.corner == "gusset" and p.corner_on == "end")
    ):
        return [Issue(id="S03", message=translate("A display carton with a lowered front wall and sloped end walls supports open corners, corner flaps, or gussets on the long-side walls"),
                      fix=translate("Use corner=open, flap, or gusset with corner_on=long, and remove corner_diagonal"))]  # fmt: skip
    if p.front_low and (p.front_double or p.end_double or p.rim is not None or p.hand_holes):
        return [Issue(id="S03", message=translate("A lowered-front display carton cannot add double-wall rollovers, rims, or hand holes to the front or end walls"),
                      fix=translate("Remove front_double, end_double, rim, or hand_holes"))]  # fmt: skip
    if p.front_low and LOW_FRONT_RATIO * d.h < d.slot + 10:
        out.append(
            Issue(
                id="S03",
                message=translate("The front wall is too low to fit the corner flaps"),
                fix=translate("Increase the height H"),
            )
        )
    if p.corner_hooks and (p.corner != "flap" or p.corner_on != "long" or p.corner_side != "out"):
        return [
            Issue(
                id="S03",
                message=translate(
                    "Corner hooks require long-side corner flaps on the outside of the end walls"
                ),
                fix="corner_on=long，corner_side=out",
            )
        ]
    if p.corner_hooks and d.depth > 0.5 * d.Wb - 10:
        out.append(
            Issue(
                id="S03",
                message=translate(
                    "The corner flaps are too deep. The hook slots from both ends meet in the centre of the end wall."
                ),
                fix=translate("Reduce corner_depth"),
            )
        )
    if p.corner_posts and (p.corner != "flap" or p.corner_on != "end"):
        return [
            Issue(
                id="S03",
                message=translate("Triangular corner posts are folded from end-wall corner flaps"),
                fix="corner=flap，corner_on=end",
            )
        ]
    if p.end_window and (p.rim_on in ("all", "end") and p.rim is not None or p.stack != "none"):
        return [
            Issue(
                id="S03",
                message=translate(
                    "An end-wall opening cannot share an end-wall rim or stacking tabs"
                ),
                fix=translate("Remove the end-wall rim or stacking tabs"),
            )
        ]
    if p.corner_posts:
        a = corner_post_leg(d.Wb, d.h)
        if d.h - post_tab_len(t) - 1 < 15 or 2 * a > d.Wb - 20:
            out.append(
                Issue(
                    id="S03",
                    message=translate(
                        "The wall is too low or the end wall is too short for triangular corner posts"
                    ),
                    fix=translate("Increase H or W"),
                )
            )
    if p.flap_drops and d.Wb / 2 - 2 * SLOT_CLEAR_MM < 2 * 6.0:
        out.append(
            Issue(
                id="S03",
                message=translate("The lid flap is too narrow to fit locking tabs at both ends"),
                fix=translate("Make W larger"),
            )
        )
    if p.rim_ends == "drop" and (p.rim is None or p.rim_on != "long" or p.corner == "ear"):
        return [Issue(id="S03", message=translate("Short fold-down rim tabs require a long-side rim on a single-wall tray"),
                      fix=translate("Specify rim and use rim_on=long"))]  # fmt: skip
    if p.rim_ends in ("posts", "lugs", "mitre"):
        if p.rim is None or p.corner != "flap" or p.corner_on != "long":
            return [Issue(id="S03", message=translate("Rim end finishes (posts, tabs, or mitres) require long-side corner flaps and rims"),
                          fix=translate("Set corner=flap and corner_on=long, then specify rim"))]  # fmt: skip
        if p.rim_ends == "mitre" and (p.corner_side != "out" or p.rim_on != "long"):
            return [Issue(id="S03", message=translate("Mitred rims require corner flaps outside the end walls and rims only on the long-side walls"),
                          fix="corner_side=out，rim_on=long")]  # fmt: skip
        if p.rim_ends == "mitre" and d.depth < v + d.slot:
            out.append(
                Issue(
                    id="S03",
                    message=translate("The corner flap is too narrow to fit the mitre"),
                    fix=translate("Increase corner flap depth or reduce rim"),
                )
            )
        if p.rim_ends == "lugs":
            lw = min(POST_LUG_W_RATIO * (v - d.slot), POST_LUG_W_MAX)
            if lw < POST_LUG_W_MIN:
                out.append(Issue(id="S03", message=translate("Rim {width:g} is too narrow to fit the post tab", width=v),
                                 fix=f"rim ≥ {2 * POST_LUG_W_MIN + d.slot:.0f}"))  # fmt: skip
            if post_slot_h(t) >= d.h - d.slot:
                out.append(
                    Issue(
                        id="S03",
                        message=translate("The wall is too low to fit the tab slot"),
                        fix=translate("Increase the height H"),
                    )
                )
    if p.triangle_posts:
        if p.rim_ends != "posts" or p.rim_on != "long" or p.corner_side != "in":
            return [Issue(id="S03", message=translate("Triangular corner posts require long-side rim posts and corner flaps inside the end walls"),
                          fix="rim_ends=posts，rim_on=long，corner_side=in")]  # fmt: skip
        a = tri_leg(p, t)
        if 2 * (a + t) >= d.Wb - 10:
            out.append(
                Issue(
                    id="S03",
                    message=translate(
                        "The end wall is too short. The two triangular corner posts collide."
                    ),
                    fix=translate("Increase W or reduce rim"),
                )
            )
        if 0.5 * d.Lb - t - a - 2 * t - 5.0 < 10.0:
            out.append(
                Issue(
                    id="S03",
                    message=translate(
                        "The long-side wall is too short for the glue flange of the triangular corner post"
                    ),
                    fix=translate("Increase L or reduce rim"),
                )
            )
    return out


def extend(p: TrayParams, d: TrayDims, tray: Tray) -> Tray:
    """build() 出来的托盘：长边窄边的收头、三角角柱、底边孔。"""
    if (p.rim_ends == "none" and not p.edge_slots and p.end_height is None and not p.end_double
            and not p.front_double and not p.back_double and not p.hand_holes and not p.end_slits):  # fmt: skip
        return tray
    kit = Kit.of(tray)
    Lb, Wb, h, t, g = d.Lb, d.Wb, d.h, d.t, d.slot
    he = p.end_height if p.end_height is not None else h
    if p.end_height is not None:
        kit.reshape("side_a", rect(Lb, 0, Lb + he, Wb))
        kit.reshape("side_b", rect(-he, 0, 0, Wb))
    if p.end_double:
        _end_double(kit, Lb, Wb, he, t, p.end_roll, p.end_foot)
        for rid in ("rim_front", "rim_back"):  # 效果图：长边盖翼压在端墙卷边平顶上
            if p.cross_flaps != "none" and rid in kit.folds:
                kit.folds[rid]["layer"] = -1.0
    if p.end_slits:
        _end_slits(kit, Lb, Wb, h, t)
    if p.hand_holes:
        _hand_holes(kit, Lb, Wb, he, t, p.end_double)
    if p.front_double:
        _front_double(kit, Lb, h, t, ends_double=p.end_double)
    if p.back_double:
        _front_double(kit, Lb, h, t, "back", Wb, ends_double=p.end_double)
    if p.rim_ends == "wrap":
        assert p.rim is not None
        _end_wraps(kit, Lb, Wb, h, g, p.rim)
    if p.rim_ends in ("posts", "lugs", "mitre", "drop"):
        assert p.rim is not None
        _long_rims(p, kit, Lb, Wb, h, t, g, p.rim, d.depth)
    if p.triangle_posts:
        _triangle_posts(p, kit, Lb, Wb, h, t)
    if p.edge_slots:
        _edge_slots(kit, Lb, Wb, t)
    return kit.tray()


def _long_rims(p: TrayParams, kit: Kit, Lb: float, Wb: float, h: float, t: float, g: float, v: float,
               depth: float) -> None:  # fmt: skip
    step = kit.last_step() + 1
    for fb, y0, sy in (("front", -h, -1.0), ("back", Wb + h, 1.0)):
        rid = f"rim_{fb}"
        lift = -1.0 if p.triangle_posts else 0.0  # 压在三角角柱顶上
        kit.add(rid, PanelRole.DUST_FLAP, rect(0, y0, Lb, y0 + sy * v), fb, ((0, y0), (Lb, y0)),
                (0, -sy, 0), step, layer=lift)  # fmt: skip
        for ab, xc, sx in (("a", Lb, 1.0), ("b", 0.0, -1.0)):
            inward = (_sx(ab), 0.0, 0.0)
            if p.rim_ends == "mitre":  # 墙角三角：沿墙角线翻 180° 叠到窄边下面；其余盖在角翼顶上
                tri = f"mitre_{fb}_{ab}"
                pts = [(xc, y0), (xc, y0 + sy * v), (xc + sx * v, y0 + sy * v)]
                kit.add(tri, PanelRole.DUST_FLAP, pts, rid, ((xc, y0), (xc, y0 + sy * v)), inward, step + 1,
                        layer=1.0)  # fmt: skip
                top = f"rim_{fb}_{ab}"
                corner = f"corner_{fb}_{ab}"
                pts = [(xc, y0), (xc + sx * v, y0 + sy * v), (xc + sx * depth, y0 + sy * v),
                       (xc + sx * depth, y0)]  # fmt: skip
                kit.add(
                    top,
                    PanelRole.DUST_FLAP,
                    pts,
                    corner,
                    ((xc, y0), (xc + sx * depth, y0)),
                    inward,
                    step + 1,
                    layer=-3.0,
                )  # fmt: skip  效果图：压在窄边和三角上面
                kit.link(f"crease_{tri}_{top}", (xc, y0), (xc + sx * v, y0 + sy * v), [tri, top])
                continue
            post = f"post_{fb}_{ab}"
            # 插脚：立柱底要落在槽中线上——折好后底板、墙顶窄边各占纸厚，按刀版墙高算会低 2.5·t（S07 量出来的）
            hp = h - (post_slot_h(t) / 2 + POST_LUG_DROP_T * t if p.rim_ends == "lugs" else g)
            if p.end_height is not None:  # 矮端墙：立柱只垂到端墙顶（0481）
                hp = h - p.end_height - g
            ya, yb = y0 + sy * g, y0 + sy * v
            pts = rect(xc, ya, xc + sx * hp, yb)
            if p.rim_ends == "drop":  # 短舌：折下去贴端墙里面，远端两角斜切
                hp = min(DROP_LEN_RATIO * h, DROP_LEN_MAX)
                c = min(0.3 * hp, 0.3 * abs(yb - ya))
                xe = xc + sx * hp
                pts = [
                    (xc, ya),
                    (xe - sx * c, ya),
                    (xe, ya + sy * c),
                    (xe, yb - sy * c),
                    (xe - sx * c, yb),
                    (xc, yb),
                ]
            kit.add(post, PanelRole.DUST_FLAP, pts, rid, ((xc, ya), (xc, yb)), (0, 0, -1), step + 1,
                    layer=2.0)  # fmt: skip
            if p.rim_ends == "lugs":
                lw = min(POST_LUG_W_RATIO * (v - g), POST_LUG_W_MAX)
                ll = POST_LUG_LEN_T * t + POST_LUG_LEN_EXTRA
                c = min(ll / 2, lw / 4)
                ym, xe = (ya + yb) / 2, xc + sx * hp
                pts = [(xe, ym - lw / 2), (xe + sx * (ll - c), ym - lw / 2), (xe + sx * ll, ym - lw / 2 + c),
                       (xe + sx * ll, ym + lw / 2 - c), (xe + sx * (ll - c), ym + lw / 2), (xe, ym + lw / 2)]  # fmt: skip
                kit.add(f"lug_{fb}_{ab}", PanelRole.DUST_FLAP, pts, post, ((xe, ym - lw / 2), (xe, ym + lw / 2)),
                        (-_sx(ab), 0, 0), step + 1, layer=2.0)  # fmt: skip
                sh, sw = post_slot_h(t), lw + 2 * SLOT_CLEAR_MM
                yc = abs(ym - y0)  # 立体：离长边墙多远 = 刀版上离墙顶线多远
                yy = yc if fb == "front" else Wb - yc
                _slot_on_crease(kit, f"cut_slot_post_{fb}_{ab}", f"side_{ab}", xc, sx, sh, yy, sw)
    if p.rim_on == "all":  # 端墙窄边：整幅（两头让一层纸厚），压在立柱 / 长边窄边的头上
        for ab, x0, sx in (("a", Lb + h, 1.0), ("b", -h, -1.0)):
            kit.add(f"rim_side_{ab}", PanelRole.DUST_FLAP, rect(x0, t, x0 + sx * v, Wb - t), f"side_{ab}",
                    ((x0, t), (x0, Wb - t)), (_sx(ab), 0, 0), step + 2, layer=-1.0)  # fmt: skip
    if p.rim_ends == "lugs":  # 角翼让出插脚：从槽顶往上
        sh = post_slot_h(t)
        for fb, sy in (("front", -1.0), ("back", 1.0)):
            for ab, xc, sx in (("a", Lb, 1.0), ("b", 0.0, -1.0)):
                yw = 0.0 if fb == "front" else Wb
                kit.reshape(
                    f"corner_{fb}_{ab}", rect(xc, yw + sy * sh, xc + sx * depth, yw + sy * h)
                )
                kit.move_crease(f"crease_corner_{fb}_{ab}", (xc, yw + sy * sh), (xc, yw + sy * h))


def _end_double(kit: Kit, Lb: float, Wb: float, he: float, t: float, roll: float | None = None,
                foot: bool = False) -> None:  # fmt: skip
    """端墙卷边成双层：卷边 rg（默认 t；给了 roll 就是宽卷边、墙顶成平顶）、内墙下沿两个插脚插进底板端线上的孔；
    foot：内墙下沿一条平脚往盒里折平压在底板上，不开孔（0403）。数值【初值】。"""
    rg, ii = (roll if roll is not None else t), t + 1.0
    hin = he - t
    lw = min(30.0, max(10.0, 0.2 * Wb))
    ll, sw = t + 3.0, lw + 2.0
    c = min(1.5, ll / 2, lw / 4)
    sd = rg + 1.5 * t + 0.5
    step = kit.last_step() + 1
    for ab, x0, xb, sx in (("a", Lb + he, Lb, 1.0), ("b", -he, 0.0, -1.0)):
        roll_pid = kit.add(f"rim_side_{ab}", PanelRole.RIM, rect(x0, ii, x0 + sx * rg, Wb - ii), f"side_{ab}",
                           ((x0, ii), (x0, Wb - ii)), (_sx(ab), 0, 0), step)  # fmt: skip
        xi = x0 + sx * rg
        xe = xi + sx * hin
        if foot:
            kit.add(f"side_{ab}_in", PanelRole.INNER_WALL, rect(xi, ii, xe, Wb - ii), roll_id(ab),
                    ((xi, ii), (xi, Wb - ii)), (0, 0, -1), step, layer=-2.0)  # fmt: skip
            fd = min(0.2 * he, 25.0)
            kit.add(f"foot_{ab}", PanelRole.INNER_WALL, rect(xe, ii + fd / 2, xe + sx * fd, Wb - ii - fd / 2),
                    f"side_{ab}_in", ((xe, ii + fd / 2), (xe, Wb - ii - fd / 2)), (_sx(ab), 0, 0), step, layer=1.0)  # fmt: skip
            continue
        pts = [(xi, ii), (xe, ii)]
        for f in (0.25, 0.75):
            yc = ii + f * (Wb - 2 * ii)
            pts += [(xe, yc - lw / 2), (xe + sx * (ll - c), yc - lw / 2), (xe + sx * ll, yc - lw / 2 + c),
                    (xe + sx * ll, yc + lw / 2 - c), (xe + sx * (ll - c), yc + lw / 2), (xe, yc + lw / 2)]  # fmt: skip
            kit.open_slot(
                f"cut_slot_{ab}{int(f * 4)}",
                rect(xb - sx * sd, yc - sw / 2, xb, yc + sw / 2),
                "base",
            )
        pts += [(xe, Wb - ii), (xi, Wb - ii)]
        kit.add(f"side_{ab}_in", PanelRole.INNER_WALL, pts, roll_pid, ((xi, ii), (xi, Wb - ii)), (0, 0, -1), step,
                layer=-2.0)  # fmt: skip


def roll_id(ab: str) -> str:
    return f"rim_side_{ab}"


def _front_double(kit: Kit, Lb: float, h: float, t: float, wall: str = "front", Wb: float = 0.0,
                  ends_double: bool = False) -> None:  # fmt: skip
    """长边墙卷边成双层：卷边 rg = t、内墙下沿三个插脚插进底板边线上的孔（0426 / 0475 前墙，0461 两面）【初值】。
    端墙也双层时内墙两头让开端墙的内层（0465）。"""
    rg, ii = t, (4 * t + 1.0 if ends_double else t + 1.0)
    hin = h - rg - t
    lw = min(30.0, max(10.0, 0.12 * Lb))
    ll, sw = t + 3.0, lw + 2.0
    c = min(1.5, ll / 2, lw / 4)
    sd = rg + 1.5 * t + 0.5
    step = kit.last_step() + 1
    sy = -1.0 if wall == "front" else 1.0  # 刀版上往墙顶的方向
    yb = 0.0 if wall == "front" else Wb  # 和底板的压线
    y0 = yb + sy * h
    kit.add(f"rim_{wall}", PanelRole.RIM, rect(ii, y0, Lb - ii, y0 + sy * rg), wall, ((ii, y0), (Lb - ii, y0)),
            (0, -sy, 0), step)  # fmt: skip
    yi, ye = y0 + sy * rg, y0 + sy * (rg + hin)
    pts = [(Lb - ii, yi), (Lb - ii, ye)]
    for f in (0.75, 0.5, 0.25):
        xc = ii + f * (Lb - 2 * ii)
        pts += [(xc + lw / 2, ye), (xc + lw / 2, ye + sy * (ll - c)), (xc + lw / 2 - c, ye + sy * ll),
                (xc - lw / 2 + c, ye + sy * ll), (xc - lw / 2, ye + sy * (ll - c)), (xc - lw / 2, ye)]  # fmt: skip
        kit.open_slot(
            f"cut_slot_{wall}_{int(f * 4)}",
            rect(xc - sw / 2, yb, xc + sw / 2, yb - sy * sd),
            "base",
        )
    pts += [(ii, ye), (ii, yi)]
    kit.add(f"{wall}_in", PanelRole.INNER_WALL, pts, f"rim_{wall}", ((ii, yi), (Lb - ii, yi)), (0, 0, -1), step,
            layer=-2.0)  # fmt: skip


HAND_W_RATIO, HAND_W_MAX, HAND_H_RATIO, HAND_H_MAX = 0.4, 90.0, 0.3, 28.0  # 手孔宽 / 高【初值】
HAND_TOP_RATIO = 0.25  # 手孔顶离墙顶 0.25·墙高


def hand_dims(Wb: float, he: float) -> tuple[float, float, float]:
    return (
        min(HAND_W_RATIO * Wb, HAND_W_MAX),
        min(HAND_H_RATIO * he, HAND_H_MAX),
        HAND_TOP_RATIO * he,
    )


def _hand_holes(kit: Kit, Lb: float, Wb: float, he: float, t: float, double: bool) -> None:
    """端墙手孔（八边形，四角斜切）；端墙双层时内墙同一位置也开孔（0465）。"""
    w, hh, top = hand_dims(Wb, he)
    c = min(hh, w) / 3
    y0, y1 = (Wb - w) / 2, (Wb + w) / 2
    for ab, xc, sx in (("a", Lb, 1.0), ("b", 0.0, -1.0)):
        za, zb = he - top - hh, he - top  # 立体高度
        xa, xb = xc + sx * za, xc + sx * zb
        lo, hi = min(xa, xb), max(xa, xb)
        pts = [(lo + c, y0), (hi - c, y0), (hi, y0 + c), (hi, y1 - c), (hi - c, y1), (lo + c, y1), (lo, y1 - c),
               (lo, y0 + c)]  # fmt: skip
        kit.slot(f"cut_hand_{ab}", pts, f"side_{ab}")
        if double:  # 内墙：从卷边往下 he − rg − z
            rg = t
            xi = xc + sx * (he + rg)  # 内墙顶线（刀版）
            ia, ib = xi + sx * (he - zb), xi + sx * (he - za)
            lo, hi = min(ia, ib), max(ia, ib)
            pts = [(lo + c, y0), (hi - c, y0), (hi, y0 + c), (hi, y1 - c), (hi - c, y1), (lo + c, y1), (lo, y1 - c),
                   (lo, y0 + c)]  # fmt: skip
            kit.slot(f"cut_hand_{ab}_in", pts, f"side_{ab}_in")


def _end_slits(kit: Kit, Lb: float, Wb: float, h: float, t: float) -> None:
    """端墙两道竖缝（离长边墙一个墙高处），从底板压线上 1 mm 到墙顶下 1 mm，把端墙分成三段（0445）。"""
    from pacxio.geometry import Segment, Vec
    from pacxio.model.dieline import Edge, EdgeType

    off = min(h, 0.3 * Wb)
    for ab, xc, sx in (("a", Lb, 1.0), ("b", 0.0, -1.0)):
        for i, y in enumerate((off, Wb - off), 1):
            a, b = Vec(xc + sx * 1.0, y), Vec(xc + sx * (h - 1.0), y)
            kit.cuts.append(Edge(id=f"cut_slit_{ab}{i}", type=EdgeType.CUT, segments=[Segment(a, b)],
                                 panels=[f"side_{ab}"]))  # fmt: skip


def _end_wraps(kit: Kit, Lb: float, Wb: float, h: float, g: float, v: float) -> None:
    """端墙窄边整幅，两头再接一段折下去压在长边墙外面的角翼上（0480）。"""
    step = kit.last_step() + 1
    e = WRAP_LEN_RATIO * h
    for ab, x0, sx in (("a", Lb + h, 1.0), ("b", -h, -1.0)):
        rid = f"rim_side_{ab}"
        kit.add(rid, PanelRole.DUST_FLAP, rect(x0, 0, x0 + sx * v, Wb), f"side_{ab}", ((x0, 0), (x0, Wb)),
                (_sx(ab), 0, 0), step, layer=-1.0)  # fmt: skip
        for fb, yw, sy in (("front", 0.0, -1.0), ("back", Wb, 1.0)):
            xa, xb = x0 + sx * g, x0 + sx * v  # 和角翼之间留一道缝
            kit.add(f"wrap_{fb}_{ab}", PanelRole.DUST_FLAP, rect(xa, yw, xb, yw + sy * e), rid,
                    ((xa, yw), (xb, yw)), (0, 0, -1), step + 1, layer=-3.0)  # fmt: skip


def _slot_on_crease(kit: Kit, eid: str, wall: str, xc: float, sx: float, depth: float, yc: float,
                    width: float) -> None:  # fmt: skip
    """墙脚的槽：从底板压线往墙里开（压线在槽口断开）。"""
    y0, y1 = yc - width / 2, yc + width / 2
    kit.slot(eid, [(xc, y0), (xc + sx * depth, y0), (xc + sx * depth, y1), (xc, y1)], wall)
    _split_crease(kit, f"crease_base_{wall}", [(y0, y1)], axis="y")


def _split_crease(kit: Kit, eid: str, gaps: list[tuple[float, float]], axis: str) -> None:
    """把一条直压线在 gaps（沿压线的区间）处断开；第一段保留原 id（铰链按它找）。"""
    from pacxio.geometry import Segment, Vec

    k = next(i for i, e in enumerate(kit.creases) if e.id == eid)
    edge = kit.creases[k]
    a, b = edge.segments[0].start, edge.segments[-1].end
    i = 1 if axis == "y" else 0
    lo, hi = sorted(((a.x, a.y)[i], (b.x, b.y)[i]))
    fixed = (a.x, a.y)[1 - i]
    cuts = sorted(gaps)
    spans, cur = [], lo
    for g0, g1 in cuts:
        if g0 > cur:
            spans.append((cur, g0))
        cur = max(cur, g1)
    if cur < hi:
        spans.append((cur, hi))
    out = []
    for n, (s0, s1) in enumerate(spans):
        p0 = Vec(fixed, s0) if axis == "y" else Vec(s0, fixed)
        p1 = Vec(fixed, s1) if axis == "y" else Vec(s1, fixed)
        out.append(edge.model_copy(update={"id": eid if n == 0 else f"{eid}_{n + 1}",
                                           "segments": [Segment(p0, p1)]}))  # fmt: skip
    kit.creases[k : k + 1] = out


def _triangle_posts(p: TrayParams, kit: Kit, Lb: float, Wb: float, h: float, t: float) -> None:
    rg = t  # 卷边宽【初值】，同瓦楞 0422
    a = tri_leg(p, t)
    s = a * math.sqrt(2)
    hin = h - rg
    e = min(h, 0.5 * Lb - rg - a - 2 * t - 5.0)  # 贴长边墙那段长 ≤ 墙高【初值】
    step = kit.last_step() + 1
    for ab, x0, sx in (("a", Lb + h, 1.0), ("b", -h, -1.0)):
        X = _sx(ab)
        roll, inner = f"rim_side_{ab}", f"side_{ab}_in"
        kit.add(roll, PanelRole.RIM, rect(x0, a, x0 + sx * rg, Wb - a), f"side_{ab}", ((x0, a), (x0, Wb - a)),
                (X, 0, 0), step)  # fmt: skip
        xi = x0 + sx * rg
        xo = xi + sx * hin
        kit.add(inner, PanelRole.INNER_WALL, rect(xi, a, xo, Wb - a), roll, ((xi, a), (xi, Wb - a)),
                (0, 0, -1), step, layer=-2.0)  # fmt: skip
        for fb, yh, sy in (("front", a, -1.0), ("back", Wb - a, 1.0)):
            diag, flat = f"post_{fb}_{ab}_diag", f"post_{fb}_{ab}_flat"
            ys = yh + sy * s
            kit.add(diag, PanelRole.INNER_WALL, rect(xi, yh, xo, ys), inner, ((xi, yh), (xo, yh)),
                    (X / math.sqrt(2), sy / math.sqrt(2), 0), step + 1, layer=-1.0)  # fmt: skip
            kit.add(flat, PanelRole.INNER_WALL, rect(xi, ys, xo, ys + sy * e), diag, ((xi, ys), (xo, ys)),
                    (X, 0, 0), step + 1, layer=-1.0)  # fmt: skip


def _edge_slots(kit: Kit, Lb: float, Wb: float, t: float) -> None:
    depth = max(EDGE_SLOT_D_T * t, EDGE_SLOT_D_MIN)
    for wall, length, along in (("front", Lb, "x"), ("back", Lb, "x"), ("side_a", Wb, "y"),
                                ("side_b", Wb, "y")):  # fmt: skip
        sl = min(EDGE_SLOT_MAX, max(EDGE_SLOT_MIN, EDGE_SLOT_RATIO * length))
        gaps = [(f * length - sl / 2, f * length + sl / 2) for f in EDGE_SLOT_AT]
        for i, (c0, c1) in enumerate(gaps, 1):
            if wall == "front":
                pts = rect(c0, 0, c1, depth)
            elif wall == "back":
                pts = rect(c0, Wb - depth, c1, Wb)
            elif wall == "side_a":
                pts = rect(Lb - depth, c0, Lb, c1)
            else:
                pts = rect(0, c0, depth, c1)
            kit.slot(f"cut_edgehole_{wall}_{i}", pts, "base")  # 底边孔（不是插舌的槽）
        _split_crease(kit, f"crease_base_{wall}", gaps, axis=along)


# ---- 双层端墙（ear，校准过的托盘部件）上的窄边插舌和内墙翼（0432） ----------------------------


def extend_ear(p: TrayParams, d: Any, tray: Tray) -> Tray:
    """ear_tray 的刀版坐标：底 x 0..bl、y 0..bw；端墙外墙 → 卷边（rim_a）→ 内墙（side_a_in）。"""
    if p.rim_ends != "tabs" and not p.inner_wings and p.cross_flaps != "long":
        return tray
    kit = Kit.of(tray)
    bl, bw, hf, t = d.bl, d.bw, d.hf, d.t
    if p.cross_flaps == "long":  # 长边墙（单层）顶上各一片半宽盖翼（0414）
        for fb, y0, sy in (("front", -hf, -1.0), ("back", bw + hf, 1.0)):
            kit.add(f"rim_{fb}", PanelRole.DUST_FLAP, rect(d.X0, y0, d.X1, y0 + sy * bw / 2), fb,
                    ((d.X0, y0), (d.X1, y0)), (0, -sy, 0), kit.last_step() + 1, layer=-1.0)  # fmt: skip
    xr, xi0 = bl + d.ho, bl + d.ho + d.rg
    step = kit.last_step() + 1
    if p.rim_ends == "tabs":
        assert p.rim is not None
        v = p.rim
        tw, tl = tab_width(v, d.ii), min(TAB_LEN_RATIO * hf, TAB_LEN_MAX)
        c = TAB_CHAMFER_RATIO * min(tw, tl)
        xa, xb = d.rg / 2, bl - d.rg / 2  # 插舌落在卷边正中（立体 X = rg / 2）
        for fb, y0, sy in (("front", -hf, -1.0), ("back", bw + hf, 1.0)):
            rid = f"rim_{fb}"
            kit.add(rid, PanelRole.DUST_FLAP, rect(xa, y0, xb, y0 + sy * v), fb, ((d.X0, y0), (d.X1, y0)),
                    (0, -sy, 0), step)  # fmt: skip
            yi, yo = y0 + sy * (v - tw), y0 + sy * v  # 插舌在窄边靠盒里那半
            for ab, xc, sx in (("a", xb, 1.0), ("b", xa, -1.0)):
                pts = [(xc, yi), (xc + sx * (tl - c), yi), (xc + sx * tl, yi + sy * c),
                       (xc + sx * tl, yo - sy * c), (xc + sx * (tl - c), yo), (xc, yo)]  # fmt: skip
                kit.add(f"tab_{fb}_{ab}", PanelRole.TUCK, pts, rid, ((xc, yi), (xc, yo)), (0, 0, -1), step + 1,
                        layer=1.0)  # fmt: skip
                # 卷边上开槽：立体里插舌离长边墙 v − tw .. v
                ys = (v - tw - SLOT_CLEAR_MM, v + SLOT_CLEAR_MM)
                y_lo, y_hi = ys if fb == "front" else (bw - ys[1], bw - ys[0])
                x_lo, x_hi = (xr - t, xi0 + t) if ab == "a" else (bl - xi0 - t, bl - xr + t)
                kit.slot(f"cut_slot_tab_{fb}_{ab}", rect(x_lo, y_lo, x_hi, y_hi), f"rim_{ab}")
        for ab in ("a", "b"):
            gaps = []
            for fb in ("front", "back"):
                ys = (v - tw - SLOT_CLEAR_MM, v + SLOT_CLEAR_MM)
                gaps.append(ys if fb == "front" else (bw - ys[1], bw - ys[0]))
            _split_crease(kit, f"crease_side_{ab}_rim_{ab}", gaps, axis="y")
            _split_crease(kit, f"crease_rim_{ab}_side_{ab}_in", gaps, axis="y")
        # 耳朵上半截让出插舌（目录图上耳朵是台阶形）
        yn, zn = v - tw - SLOT_CLEAR_MM, hf - tl - SLOT_CLEAR_MM
        far = d.ed + hf + 10.0
        for ear, (xa_, xb_), (ya_, yb_) in (
            ("ear_fa", (d.X1 + yn, d.X1 + far), (-far, -zn)),
            ("ear_fb", (bl - d.X1 - far, bl - d.X1 - yn), (-far, -zn)),
            ("ear_ba", (d.X1 + yn, d.X1 + far), (bw + zn, bw + far)),
            ("ear_bb", (bl - d.X1 - far, bl - d.X1 - yn), (bw + zn, bw + far)),
        ):
            if yn < d.ed:
                kit.clip(ear, rect(xa_, ya_, xb_, yb_))
    if p.inner_wings:
        wd = min(d.hin, 0.5 * bl - d.rg - 5.0)  # 翼宽【初值】
        xe = xi0 + d.hin
        for ab, x_cut in (("a", xi0 - d.t), ("b", bl - xi0 + d.t)):  # 耳朵不伸到内墙翼那边
            for fb in ("f", "b"):
                ear = f"ear_{fb}{ab}"
                x0_, x1_ = (
                    (x_cut, x_cut + 2 * d.hf + d.ed)
                    if ab == "a"
                    else (x_cut - 2 * d.hf - d.ed, x_cut)
                )
                y0_, y1_ = (-2 * hf, 0.0) if fb == "f" else (bw, bw + 2 * hf)
                if (ab == "a" and d.X1 + d.ed > x_cut) or (ab == "b" and bl - d.X1 - d.ed < x_cut):
                    kit.clip(ear, rect(x0_, y0_, x1_, y1_))
        for ab, sx in (("a", 1.0), ("b", -1.0)):
            inner = f"side_{ab}_in"
            x_lo, x_hi = (xi0, xe) if ab == "a" else (bl - xe, bl - xi0)
            for fb, yh, sy in (("front", d.ii, -1.0), ("back", bw - d.ii, 1.0)):
                kit.add(f"wing_{fb}_{ab}", PanelRole.INNER_WALL, rect(x_lo, yh, x_hi, yh + sy * wd), inner,
                        ((x_lo, yh), (x_hi, yh)), (sx, 0, 0), step + 2, layer=-1.0)  # fmt: skip
    return kit.tray()


# ---- 端墙开口、三角角柱插斜缝（0483）、半宽盖翼两头折下插底（0437） ------------------------------

WINDOW_W_RATIO, WINDOW_D_RATIO, WINDOW_GAP_MM = (
    0.6,
    0.45,
    1.0,
)  # 端墙开口宽 0.6·端墙长、深 0.45·墙高【初值】
POST_TAB_T, POST_TAB_EXTRA, POST_TAB_W_RATIO = 1.0, 3.0, 0.6  # 角柱斜面插舌长 t + 3、宽 0.6·斜面
CORNER_POST_RATIO = 0.25  # 三角角柱直角边 a = min(0.25·端墙长, 0.4·墙高)
FLAP_LUG_W_RATIO, FLAP_LUG_W_MAX = 0.4, 30.0  # 盖翼头插脚宽 min(0.4·翼宽, 30)
DUST_EAR_RATIO = 0.8  # 盖翼头防尘耳深 0.8·墙高


def corner_post_leg(Wb: float, h: float) -> float:
    return min(CORNER_POST_RATIO * Wb, 0.4 * h)


def post_tab_len(t: float) -> float:
    return POST_TAB_T * t + POST_TAB_EXTRA


def extend_more(p: TrayParams, d: TrayDims, tray: Tray) -> Tray:
    """build() 托盘的另几种机构：端墙开口（end_window）、三角角柱（corner_posts）、半宽盖翼两头折下（flap_drops）。"""
    if not (
        p.end_window
        or p.corner_posts
        or p.flap_drops
        or p.flap_slots
        or p.flap_ears
        or p.corner_hooks
    ):
        return tray
    kit = Kit.of(tray)
    Lb, Wb, h, t = d.Lb, d.Wb, d.h, d.t
    if p.end_window:
        ww, wd, gap = WINDOW_W_RATIO * Wb, WINDOW_D_RATIO * h, WINDOW_GAP_MM
        y0, y1 = (Wb - ww) / 2, (Wb + ww) / 2
        step = kit.last_step() + 1
        for ab, xt, sx in (("a", Lb + h, 1.0), ("b", -h, -1.0)):
            xc = xt - sx * wd  # 开口底（压线）
            kit.clip(f"side_{ab}", rect(xc, y0, xt + sx, y1))
            kit.add(f"window_{ab}", PanelRole.INNER_WALL, rect(xc, y0 + gap, xt - sx * gap, y1 - gap), f"side_{ab}",
                    ((xc, y0 + gap), (xc, y1 - gap)), (0, 0, -1), step, layer=-2.0)  # fmt: skip
    if p.corner_posts:
        _corner_posts(kit, Lb, Wb, h, t)
        for rid in ("rim_front", "rim_back"):  # 效果图：长边窄边压在角柱顶上
            if rid in kit.folds:
                kit.folds[rid]["layer"] = -1.0
    if p.flap_drops:
        _flap_drops(kit, Lb, Wb, h, t)
    if p.flap_slots:
        _flap_slots(p, kit, Lb, Wb, h, t)
    if p.flap_ears:
        _flap_ears(kit, Lb, Wb, h, t)
    if p.corner_hooks:
        _corner_hooks(kit, Lb, Wb, h, t, d.slot, d.depth)
    return kit.tray()


HOOK_W_RATIO, HOOK_LEN_T, HOOK_LEN_EXTRA = 0.35, 1.0, 4.0  # 扣角钩舌宽 0.35·墙高、长 t + 4【初值】


def _corner_hooks(kit: Kit, Lb: float, Wb: float, h: float, t: float, g: float, D: float) -> None:
    """扣角（0457）：长边墙的角翼贴在端墙外面，角翼头上一块钩舌折 90° 穿进端墙上的竖缝（目录图是圆钩，画成直角舌）。"""
    tw = HOOK_W_RATIO * (h - g)
    tl = HOOK_LEN_T * t + HOOK_LEN_EXTRA
    vm = (g + h) / 2
    step = kit.last_step() + 1
    for fb, y0, sy in (("front", 0.0, -1.0), ("back", Wb, 1.0)):
        for ab, x0, sx in (("a", Lb, 1.0), ("b", 0.0, -1.0)):

            def xy(
                u: float, v: float, x0: float = x0, sx: float = sx, y0: float = y0, sy: float = sy
            ) -> tuple[float, float]:
                return (x0 + sx * u, y0 + sy * v)

            pts = [
                xy(D, vm - tw / 2),
                xy(D + tl, vm - tw / 2),
                xy(D + tl, vm + tw / 2),
                xy(D, vm + tw / 2),
            ]
            kit.add(f"hook_{fb}_{ab}", PanelRole.GLUE_FLAP, pts, f"corner_{fb}_{ab}",
                    (xy(D, vm - tw / 2), xy(D, vm + tw / 2)), (_sx(ab), 0, 0), step)  # fmt: skip
            # 端墙上的竖缝：离长边墙 D，高度对着钩舌
            yy = D if fb == "front" else Wb - D
            ya, yb = (yy - 0.5, yy + t + 1.0) if fb == "front" else (yy - t - 1.0, yy + 0.5)
            za, zb = vm - tw / 2 - SLOT_CLEAR_MM, vm + tw / 2 + SLOT_CLEAR_MM
            kit.slot(
                f"cut_slot_hook_{fb}_{ab}", rect(x0 + sx * za, ya, x0 + sx * zb, yb), f"side_{ab}"
            )


def _flap_ears(kit: Kit, Lb: float, Wb: float, h: float, t: float) -> None:
    """长边两片半宽盖翼两头接梯形防尘耳，合上后折 90° 进端墙里面（0416）【初值】。"""
    dd = DUST_EAR_RATIO * h
    clr = SLOT_CLEAR_MM + t
    step = kit.last_step() + 1
    for fb, y0, sy in (("front", -h, -1.0), ("back", Wb + h, 1.0)):
        rid = f"rim_{fb}"
        ya, yb = y0 + sy * clr, y0 + sy * (Wb / 2 - clr)
        k = dd * math.tan(math.radians(15.0))
        for ab, xc, sx in (("a", Lb, 1.0), ("b", 0.0, -1.0)):
            pts = [(xc, ya), (xc + sx * dd, ya + sy * k), (xc + sx * dd, yb - sy * k), (xc, yb)]
            kit.add(f"ear_{fb}_{ab}", PanelRole.DUST_FLAP, pts, rid, ((xc, ya), (xc, yb)), (0, 0, -1), step,
                    layer=2.0)  # fmt: skip


def _flap_slots(p: TrayParams, kit: Kit, Lb: float, Wb: float, h: float, t: float) -> None:
    """长边半宽盖翼两头各伸出 t + 2，在端墙凸舌的位置开槽：合上时凸舌穿过槽扣住盖翼（0464）。"""
    from pacxio.engine.tray_stack import positions, tab_h, tab_w

    e = t + 2.0
    w = tab_w(Wb)
    for rid in ("rim_front", "rim_back"):  # 效果图：盖翼压在端墙顶上
        kit.folds[rid]["layer"] = -1.0
    for fb, y0, sy in (("front", -h, -1.0), ("back", Wb + h, 1.0)):
        rid = f"rim_{fb}"
        _, ya, _, yb = kit.panels[rid][1].bbox()
        kit.reshape(rid, rect(-e, ya, Lb + e, yb))
        for yc in positions(p.stack, Wb, t):
            dist = yc if fb == "front" else Wb - yc  # 离这面长边墙多远
            if dist > Wb / 2:
                continue  # 这块凸舌在另一片盖翼下面
            y = y0 + sy * dist
            for ab, xc, sx in (("a", Lb, 1.0), ("b", 0.0, -1.0)):
                kit.slot(f"cut_slot_flap_{fb}_{ab}", rect(xc - sx * (t + 1.0), y - w / 2 - SLOT_CLEAR_MM,
                                                          xc + sx * 1.0, y + w / 2 + SLOT_CLEAR_MM), rid)  # fmt: skip
    del tab_h


def _corner_posts(kit: Kit, Lb: float, Wb: float, h: float, t: float) -> None:
    """端墙两头的角翼换成三段：贴长边墙里面 a → 斜回端墙 a√2 → 贴端墙 a，围成三角角柱；
    斜面下沿的插舌插进底板四角的斜缝。角翼离长边墙的缝放大到够插舌伸出来。"""
    a = corner_post_leg(Wb, h)
    s = a * math.sqrt(2)
    g = post_tab_len(t)  # 角柱离长边墙的缝
    tl = g + t + 2.0  # 插舌穿过底板 t + 2
    tw = POST_TAB_W_RATIO * s
    step = kit.last_step() + 1
    for fb, yw, sy in (("front", 0.0, -1.0), ("back", Wb, 1.0)):
        for ab, xc, sx in (("a", Lb, 1.0), ("b", 0.0, -1.0)):
            kit.drop(f"corner_{fb}_{ab}")
            X, Y = _sx(ab), -sy  # 立体：往盒里
            x0, x1 = xc + sx * g, xc + sx * h
            s1, s2, s3 = (f"post_{fb}_{ab}_{n}" for n in (1, 2, 3))
            ya, yb, yc, yd = yw, yw + sy * a, yw + sy * (a + s), yw + sy * (2 * a + s)
            kit.add(s1, PanelRole.DUST_FLAP, rect(x0, ya, x1, yb), f"side_{ab}", ((x0, ya), (x1, ya)), (X, 0, 0),
                    2, layer=1.0)  # fmt: skip
            kit.add(s2, PanelRole.DUST_FLAP, rect(x0, yb, x1, yc), s1, ((x0, yb), (x1, yb)),
                    (-X / math.sqrt(2), Y / math.sqrt(2), 0), step)  # fmt: skip
            kit.add(
                s3,
                PanelRole.DUST_FLAP,
                rect(x0, yc, x1, yd),
                s2,
                ((x0, yc), (x1, yc)),
                (0, -Y, 0),
                step,
                layer=-1.0,
            )  # fmt: skip  效果图：贴回端墙里面
            ym = (yb + yc) / 2
            c = min(tl / 2, tw / 4)
            pts = [(x0, ym - tw / 2), (x0 - sx * (tl - c), ym - tw / 2), (x0 - sx * tl, ym - tw / 2 + c),
                   (x0 - sx * tl, ym + tw / 2 - c), (x0 - sx * (tl - c), ym + tw / 2), (x0, ym + tw / 2)]  # fmt: skip
            kit.add(f"post_{fb}_{ab}_tab", PanelRole.DUST_FLAP, pts, s2, ((x0, ym - tw / 2), (x0, ym + tw / 2)),
                    (0, 0, -1), step)  # fmt: skip
            # 插舌在刀版上伸进长边墙的墙头：长边墙那里让一个小缺口
            kit.clip(fb, rect(xc - sx * (tl - g + SLOT_CLEAR_MM), ym - tw / 2 - SLOT_CLEAR_MM, xc + sx,
                              ym + tw / 2 + SLOT_CLEAR_MM))  # fmt: skip
            # 底板斜缝：沿斜面落地那条线（立体 (a, 0) → (0, a)，离墙角），宽 t + 1
            cx, cy = (xc - sx * a / 2, yw - sy * a / 2)  # 斜线中点（刀版）
            half, wid = tw / 2 + SLOT_CLEAR_MM, (t + 1.0) / 2
            dx, dy = (sx / math.sqrt(2), -sy / math.sqrt(2))  # 沿斜线
            nx, ny = (-dy, dx)
            pts = [(cx - dx * half - nx * wid, cy - dy * half - ny * wid), (cx + dx * half - nx * wid, cy + dy * half - ny * wid),
                   (cx + dx * half + nx * wid, cy + dy * half + ny * wid), (cx - dx * half + nx * wid, cy - dy * half + ny * wid)]  # fmt: skip
            kit.slot(f"cut_slot_post_{fb}_{ab}", pts, "base")


def _flap_drops(kit: Kit, Lb: float, Wb: float, h: float, t: float) -> None:
    """长边墙的两片半宽盖翼（rim_front / rim_back）两头各接一段，合盖后折下贴端墙里面，插脚插进端线上的孔。"""
    v = Wb / 2
    ll = t + 3.0
    clr = SLOT_CLEAR_MM
    step = kit.last_step() + 1
    for fb, y0, sy in (("front", -h, -1.0), ("back", Wb + h, 1.0)):
        rid = f"rim_{fb}"
        for ab, xc, sx in (("a", Lb, 1.0), ("b", 0.0, -1.0)):
            ya, yb = y0 + sy * clr, y0 + sy * (v - clr)
            hp = h - t  # 折下到离底一层纸厚，插脚再往下穿过底板
            lw = min(FLAP_LUG_W_RATIO * (v - 2 * clr), FLAP_LUG_W_MAX)
            ym, xe = (ya + yb) / 2, xc + sx * hp
            c = min(ll / 2, lw / 4)
            lo, hi = min(ya, yb), max(ya, yb)
            pts = [(xc, lo), (xe, lo), (xe, ym - lw / 2), (xe + sx * (ll - c), ym - lw / 2),
                   (xe + sx * ll, ym - lw / 2 + c), (xe + sx * ll, ym + lw / 2 - c), (xe + sx * (ll - c), ym + lw / 2),
                   (xe, ym + lw / 2), (xe, hi), (xc, hi)]  # fmt: skip
            kit.add(f"drop_{fb}_{ab}", PanelRole.DUST_FLAP, pts, rid, ((xc, ya), (xc, yb)), (0, 0, -1), step,
                    layer=2.0)  # fmt: skip
            yc = abs(ym - y0)  # 立体：离这面长边墙多远
            yy = yc if fb == "front" else Wb - yc
            sd, sw = 3 * t + 1.0, lw + 2 * clr
            kit.open_slot(
                f"cut_slot_drop_{fb}_{ab}", rect(xc - sx * sd, yy - sw / 2, xc, yy + sw / 2), "base"
            )


# ---- 杯托（0446）、展示盒（0485） ----------------------------------------------------------------

CUP_D_RATIO, CUP_D_MAX, CUP_GAP_MM = 0.4, 70.0, 8.0  # 杯孔直径 min(0.4·W, 70)、孔间距 8【初值】


def cup_layout(Lb: float, v: float) -> tuple[float, int, int]:
    d = min(CUP_D_RATIO * 2 * v, CUP_D_MAX)
    cols = max(1, int((v - CUP_GAP_MM) // (d + CUP_GAP_MM)))
    rows = max(1, int((Lb - CUP_GAP_MM) // (d + CUP_GAP_MM)))
    return d, cols, rows


def extend_display(p: TrayParams, d: TrayDims, tray: Tray) -> Tray:
    """cup_holes：长边墙顶两块半宽隔板（rim）平铺在顶上，上面开一排排圆杯孔（真圆弧，0446）；
    front_low：前墙矮（0.4·H）、端墙顶边从后墙高斜到前墙高（0485）。"""
    if not (p.cup_holes or p.front_low or p.base_holes or p.open_taper):
        return tray
    from pacxio.geometry import Vec, arc_segment
    from pacxio.model.dieline import Edge, EdgeType

    kit = Kit.of(tray)
    Lb, Wb, h = d.Lb, d.Wb, d.h
    if p.cup_holes:
        assert p.rim is not None
        v = p.rim
        dia, cols, rows = cup_layout(Lb, v)
        r = dia / 2
        for fb, y0, sy in (("front", -h, -1.0), ("back", Wb + h, 1.0)):
            _, ya, _, yb = kit.panels[f"rim_{fb}"][1].bbox()
            pitch_x = (Lb - 2 * CUP_GAP_MM) / rows
            pitch_y = (v - 2 * CUP_GAP_MM) / cols
            for i in range(rows):
                for j in range(cols):
                    cx = CUP_GAP_MM + (i + 0.5) * pitch_x
                    cy = y0 + sy * (CUP_GAP_MM + (j + 0.5) * pitch_y)
                    c = Vec(cx, cy)
                    q = math.pi / 2  # 四段四分之一圆弧（两段半圆的端点相同，查重会当成重复线）
                    segs = [arc_segment(c, r, k * q, (k + 1) * q) for k in range(4)]
                    kit.cuts.append(Edge(id=f"cut_cup_{fb}_{i}_{j}", type=EdgeType.CUT, segments=segs,
                                         panels=[f"rim_{fb}"]))  # fmt: skip
            del ya, yb
    if p.base_holes:  # 底板中线上一排圆孔
        n = p.base_holes
        r = min(BASE_HOLE_W_RATIO * Wb, BASE_HOLE_PITCH_RATIO * Lb / (n + 1), BASE_HOLE_MAX_MM) / 2
        q = math.pi / 2
        for i in range(n):
            c = Vec(Lb * (i + 1) / (n + 1), Wb / 2)
            kit.cuts.append(Edge(id=f"cut_base_hole_{i + 1}", type=EdgeType.CUT, panels=["base"],
                                 segments=[arc_segment(c, r, k * q, (k + 1) * q) for k in range(4)]))  # fmt: skip
    if p.open_taper:  # 四面墙两头斜收，墙角留 V 口
        kl, kw = (
            min(TAPER_H_RATIO * h, TAPER_LEN_RATIO * Lb),
            min(TAPER_H_RATIO * h, TAPER_LEN_RATIO * Wb),
        )
        kit.reshape("front", [(0, 0), (Lb, 0), (Lb - kl, -h), (kl, -h)])
        kit.reshape("back", [(0, Wb), (kl, Wb + h), (Lb - kl, Wb + h), (Lb, Wb)])
        kit.reshape("side_a", [(Lb, 0), (Lb + h, kw), (Lb + h, Wb - kw), (Lb, Wb)])
        kit.reshape("side_b", [(0, 0), (0, Wb), (-h, Wb - kw), (-h, kw)])
    if p.front_low:
        v = LOW_FRONT_RATIO * h
        kit.reshape("front", rect(0, -v, Lb, 0))
        kit.reshape("side_a", [(Lb, 0), (Lb + v, 0), (Lb + h, Wb), (Lb, Wb)])
        kit.reshape("side_b", [(0, 0), (-v, 0), (-h, Wb), (0, Wb)])
        _low_front_corners(p, d, kit, v)
    return kit.tray()


def _low_front_corners(p: TrayParams, d: TrayDims, kit: Kit, v: float) -> None:
    """前墙矮到 v：前面两个墙角的角翼 / 连角跟着缩到 v 高（后面两个不变）。"""
    from pacxio.engine.tray import _ccw, corners

    g = d.slot
    for c in corners(d):
        tag = f"{c.fb}_{c.ab}"
        if c.fb == "back":  # 后面的长边墙角翼贴在斜墙上：远端跟着墙顶斜下来
            if p.corner == "flap" and p.corner_on == "long":
                pid, D = f"corner_{tag}", d.depth
                top = d.h - (d.h - v) * D / d.Wb
                kit.reshape(pid, _ccw([c.xy(0.0, g), c.xy(0.0, d.h), c.xy(D, top), c.xy(D, g)]))
            continue
        if p.corner == "flap":
            pid, D = f"corner_{tag}", d.depth
            if p.corner_on == "end":
                uv, hinge = [(g, 0.0), (v, 0.0), (v, D), (g, D)], ((g, 0.0), (v, 0.0))
            else:
                uv, hinge = [(0.0, g), (0.0, v), (D, v), (D, g)], ((0.0, g), (0.0, v))
            kit.reshape(pid, _ccw([c.xy(*q) for q in uv]))
            kit.move_crease(f"crease_{pid}", c.xy(*hinge[0]), c.xy(*hinge[1]))
        elif p.corner == "gusset":
            tl, te = f"gusset_{tag}_long", f"gusset_{tag}_end"
            kit.reshape(tl, _ccw([c.xy(0, 0), c.xy(0, v), c.xy(v, v)]))
            kit.reshape(te, _ccw([c.xy(0, 0), c.xy(v, 0), c.xy(v, v)]))
            kit.move_crease(f"crease_{tl}", c.xy(0, 0), c.xy(0, v))
            kit.move_crease(f"crease_{te}", c.xy(0, 0), c.xy(v, 0))
            kit.move_crease(f"crease_{tag}_diag", c.xy(0, 0), c.xy(v, v))


LOW_FRONT_RATIO = 0.4  # 展示盒前墙高 0.4·H【初值】
BASE_HOLE_W_RATIO, BASE_HOLE_PITCH_RATIO, BASE_HOLE_MAX_MM = 0.3, 0.6, 50.0  # 底孔径【初值】
TAPER_H_RATIO, TAPER_LEN_RATIO = 0.25, 0.15  # 梯形墙两头各收 min(0.25·墙高, 0.15·墙长)【初值】
