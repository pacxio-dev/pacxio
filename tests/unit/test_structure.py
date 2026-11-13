"""盒型结构对照公开标准（2026-10-22 起）：哪片翼挂在哪个面、该有的缺口 / 锁舌 / 糊胶三角有没有。

不查尺寸（尺寸都是【初值】，要靠折样），只查**结构**——以后改盒型代码，不会悄悄把结构改错。
参照：FEFCO 盒型代码（fefco.org 公开的 PDF）0201、0215、0427、0713；卡纸插舌盒按常规做法。
扣底盒、自动锁底都按  改好了（v2，2026-10-22）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from pacxio import dielines
from pacxio.cli import app
from pacxio.model.dieline import Dieline, EdgeType
from pacxio.model.job import Job

runner = CliRunner()
CARTON = ["--L", "80", "--W", "50", "--H", "120"]
FLAT_TOL = 1.5  # mm：判断"竖直的边""在压线上"的容差


def blank(tmp: Path, style: str, *args: str) -> dict[str | None, Dieline]:
    d = tmp / style
    res = runner.invoke(app, ["job", "init", str(d), "--style", style, *args])
    assert res.exit_code == 0, res.output
    dset, errs = dielines.build_parts_for(Job.load(d))
    assert dset is not None, errs
    return dset.parts


def parent(dl: Dieline, pid: str) -> str | None:
    hinge = dl.panel(pid).hinge
    return hinge.parent_panel if hinge else None


def box(dl: Dieline, pid: str) -> tuple[float, float, float, float]:
    return dl.panel(pid).polygon.bbox()


def points(dl: Dieline, pid: str, step: float = 0.5) -> list[tuple[float, float]]:
    """面的轮廓上每隔 step mm 一个点（直线段也加密：量"中间有多深"要用）。"""
    pts = [(p.x, p.y) for p in dl.panel(pid).polygon.to_points(0.05)]
    out = []
    for (ax, ay), (bx, by) in zip(pts, [*pts[1:], pts[0]], strict=True):
        n = max(1, int(((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5 / step))
        out += [(ax + (bx - ax) * k / n, ay + (by - ay) * k / n) for k in range(n)]
    return out


def flaps_of(dl: Dieline, side: str) -> dict[str, str]:
    """盒身上方（top）或下方（bottom）直接挂在盒身面上的翼 → 它挂在哪个面。"""
    body = {p.id for p in dl.panels if p.role.value == "body"}
    y0 = min(box(dl, b)[1] for b in body)
    y1 = max(box(dl, b)[3] for b in body)
    out = {}
    for p in dl.panels:
        par = parent(dl, p.id)
        if par in body and p.role.value not in ("body", "glue_flap"):
            _, py0, _, py1 = box(dl, p.id)
            if (side == "top" and py0 >= y1 - FLAT_TOL) or (
                side == "bottom" and py1 <= y0 + FLAT_TOL
            ):
                out[p.id] = par
    return out


def internal_cuts(dl: Dieline) -> list[str]:
    return [e.id for e in dl.edges if e.type == EdgeType.CUT and e.id != "cut_outline"]


# ---------------- 卡纸插舌盒：RTE / STE ----------------


@pytest.mark.parametrize(("style", "bottom_lid_on"), [("rte", "front"), ("ste", "back")])
def test_tuck_end_lids_tucks_and_dust_flaps_sit_where_the_standard_puts_them(
    tmp_path: Path, style: str, bottom_lid_on: str
) -> None:
    (dl,) = blank(tmp_path, style, *CARTON).values()
    xs = {pid: box(dl, pid)[0] for pid in ("glue", "back", "side_a", "front", "side_b")}
    assert sorted(xs, key=xs.__getitem__) == ["glue", "back", "side_a", "front", "side_b"]
    assert flaps_of(dl, "top") == {"top_lid": "back", "dust_ta": "side_a", "dust_tb": "side_b"}
    assert flaps_of(dl, "bottom") == {
        "btm_lid": bottom_lid_on,
        "dust_ba": "side_a",
        "dust_bb": "side_b",
    }
    assert parent(dl, "top_tuck") == "top_lid" and parent(dl, "btm_tuck") == "btm_lid"
    lid_h = box(dl, "top_lid")[3] - box(dl, "top_lid")[1]
    side_w = box(dl, "side_a")[2] - box(dl, "side_a")[0]
    assert abs(lid_h - side_w) < 2  # 盖盖住整个 L × W 的顶面


def lock_side(dl: Dieline, flap: str) -> str:
    """防尘翼的卡口（斜边）在哪一侧："left" / "right"。靠盖那侧几乎直，到顶时离分界近得多。"""
    body = parent(dl, flap) or ""
    bx0, _, bx1, _ = box(dl, body)
    fy0, fy1 = box(dl, flap)[1], box(dl, flap)[3]
    up = fy0 >= box(dl, body)[3] - FLAT_TOL
    tip = [x for x, y in points(dl, flap) if abs(y - (fy1 if up else fy0)) < 0.5]
    return "left" if min(tip) - bx0 > bx1 - max(tip) else "right"


@pytest.mark.parametrize(("style", "bottom_lid_on"), [("rte", "front"), ("ste", "back"),
                                                      ("snaplock", None), ("autolock", None)])  # fmt: skip
def test_dust_flap_locks_face_the_panel_the_tuck_goes_into(
    tmp_path: Path, style: str, bottom_lid_on: str | None
) -> None:
    """标准插舌头（reference 刀版）：防尘翼靠盖那侧直，靠插舌插进去那一面斜、根部带卡口。

    顶盖挂 back，插舌从 front 插：side_a 的卡口在右（front 那边）、side_b 的在左。
    插舌底镜像：RTE 底盖挂 front、插舌从 back 插；STE 底盖挂 back、插舌从 front 插。
    """
    (dl,) = blank(tmp_path, style, *CARTON).values()
    assert (lock_side(dl, "dust_ta"), lock_side(dl, "dust_tb")) == ("right", "left")
    if bottom_lid_on == "front":
        assert (lock_side(dl, "dust_ba"), lock_side(dl, "dust_bb")) == ("left", "right")
    elif bottom_lid_on == "back":
        assert (lock_side(dl, "dust_ba"), lock_side(dl, "dust_bb")) == ("right", "left")
    tucks = {e.panels[-1] for e in dl.edges if "slit" in e.id}  # 插舌压线两端的切口
    assert tucks == ({"top_tuck", "btm_tuck"} if bottom_lid_on else {"top_tuck"})


# ---------------- 瓦楞：FEFCO 0201 开槽箱、0427 飞机盒 ----------------


def test_rsc_0201_has_eight_flaps_that_meet_in_the_middle(tmp_path: Path) -> None:
    (dl,) = blank(tmp_path, "fefco_0201", "--L", "300", "--W", "200", "--H", "150",
                  "--material", "flute_b").values()  # fmt: skip
    top, bottom = flaps_of(dl, "top"), flaps_of(dl, "bottom")
    body = {"back", "side_a", "front", "side_b"}
    assert set(top.values()) == set(bottom.values()) == body and len(top) == len(bottom) == 4
    depths = [box(dl, f)[3] - box(dl, f)[1] for f in [*top, *bottom]]
    side_w = box(dl, "side_a")[2] - box(dl, "side_a")[0]
    assert max(depths) - min(depths) < 1 and abs(depths[0] - side_w / 2) < 3  # 翼深 = 宽的一半
    spans = sorted((box(dl, f)[0], box(dl, f)[2]) for f in top)
    assert all(a1 < b0 for (_, a1), (b0, _) in zip(spans, spans[1:], strict=False))  # 翼之间开槽


def test_mailer_0427_has_double_side_walls_ears_and_a_tucked_lid(tmp_path: Path) -> None:
    (dl,) = blank(tmp_path, "fefco_0427", "--L", "250", "--W", "180", "--H", "80",
                  "--material", "flute_e").values()  # fmt: skip
    assert {parent(dl, w) for w in ("front", "back", "side_a", "side_b")} == {"base"}
    for s in ("a", "b"):  # 侧墙两层：外墙 → 卷边 → 内墙，内墙的舌插进底板的槽
        assert parent(dl, f"rim_{s}") == f"side_{s}" and parent(dl, f"side_{s}_in") == f"rim_{s}"
    ears = {p.id: parent(dl, p.id) for p in dl.panels if p.id.startswith("ear_")}
    assert sorted(ears.values()) == ["back", "back", "front", "front"]
    assert parent(dl, "top_lid") == "back"
    assert {parent(dl, f) for f in ("dust_la", "dust_lb", "top_tuck")} == {"top_lid"}
    assert {parent(dl, f"tuck_ear_{s}") for s in ("a", "b")} == {"top_tuck"}  # 插耳挂在插舌两端
    assert internal_cuts(dl)  # 底板上的锁槽


# ---------------- 多部件：天地盖、抽屉盒 ----------------


def test_tray_and_lid_both_have_double_side_walls_and_ears(tmp_path: Path) -> None:
    parts = blank(tmp_path, "tray_lid", "--L", "150", "--W", "100", "--H", "50")
    assert set(parts) == {"base", "lid"}
    for part, centre in (("base", "bottom"), ("lid", "top")):
        dl = parts[part]
        assert {parent(dl, w) for w in ("front", "back", "side_a", "side_b")} == {centre}
        for s in ("a", "b"):
            assert (
                parent(dl, f"rim_{s}") == f"side_{s}" and parent(dl, f"side_{s}_in") == f"rim_{s}"
            )
        ears = sorted(parent(dl, p.id) or "" for p in dl.panels if p.id.startswith("ear_"))
        assert ears == ["back", "back", "front", "front"]
        assert internal_cuts(dl)


def test_drawer_tray_has_double_end_walls_and_the_sleeve_a_thumb_notch(tmp_path: Path) -> None:
    parts = blank(tmp_path, "drawer", "--L", "150", "--W", "100", "--H", "40")
    tray, sleeve = parts["tray"], parts["sleeve"]
    assert {parent(tray, w) for w in ("front", "back", "side_a", "side_b")} == {"bottom"}
    assert parent(tray, "rim_b") == "back" and parent(tray, "back_in") == "rim_b"
    assert parent(tray, "rim_f") == "front" and parent(tray, "front_in") == "rim_f"
    assert sorted(parent(tray, p.id) or "" for p in tray.panels if p.id.startswith("ear_")) == [
        "side_a", "side_a", "side_b", "side_b"]  # fmt: skip
    xs = {pid: box(sleeve, pid)[0] for pid in ("glue", "bottom", "side_a", "top", "side_b")}
    assert sorted(xs, key=xs.__getitem__) == ["glue", "bottom", "side_a", "top", "side_b"]
    outline = next(e for e in sleeve.edges if e.id == "cut_outline")
    x0, _, x1, _ = box(sleeve, "top")
    arcs = [s for s in outline.segments if s.is_arc and x0 < (s.start.x + s.end.x) / 2 < x1]
    assert arcs  # 套筒开口边上的拇指缺口


# ---------------- 扣底盒（1-2-3 底）：FEFCO 0215 ----------------


def under(dl: Dieline, flap: str) -> list[tuple[float, float]]:
    """底翼轮廓上的点：(x, 离底部压线多深)。"""
    crease = box(dl, parent(dl, flap) or "")[1]
    return [(x, crease - y) for x, y in points(dl, flap)]


def _bottom_depth(dl: Dieline, flap: str) -> float:
    return max(d for _, d in under(dl, flap))


def test_snaplock_flap_on_the_glue_side_panel_is_full_width_with_a_centre_notch(
    tmp_path: Path,
) -> None:
    """挂在糊口那一侧主面（back）上的主翼：整宽，自由边中间开一个缺口（接另一片主翼的锁舌）。"""
    (dl,) = blank(tmp_path, "snaplock", *CARTON).values()
    x0, _, x1, _ = box(dl, "back")
    fx0, _, fx1, _ = box(dl, "btm_back")
    assert fx1 - fx0 > 0.9 * (x1 - x0)
    w = x1 - x0
    centre = [d for x, d in under(dl, "btm_back") if abs(x - (x0 + x1) / 2) < 0.1 * w]
    ends = [d for x, d in under(dl, "btm_back") if min(x - fx0, fx1 - x) < 0.1 * w]
    assert max(ends) - max(centre) > 2  # 两端比中间深：中间有缺口


def test_snaplock_other_main_flap_narrows_to_a_tongue(tmp_path: Path) -> None:
    """另一片主翼（front）：两边斜着收窄，尖上一块锁舌。"""
    (dl,) = blank(tmp_path, "snaplock", *CARTON).values()
    x0, _, x1, _ = box(dl, "front")
    depth = _bottom_depth(dl, "btm_front")
    deepest = [x for x, d in under(dl, "btm_front") if d > depth - FLAT_TOL]
    assert max(deepest) - min(deepest) < 0.6 * (x1 - x0)  # 最深处只有锁舌那么宽
    assert depth > 0.5 * (box(dl, "side_a")[2] - box(dl, "side_a")[0])  # 主翼要伸过盒底一半


def test_snaplock_side_flaps_are_straight_toward_the_tongue_and_slanted_toward_the_notch(
    tmp_path: Path,
) -> None:
    """两片侧翼：靠锁舌翼（front）的一边是直的，靠缺口翼（back）的一边是斜的。"""
    (dl,) = blank(tmp_path, "snaplock", *CARTON).values()
    for flap, side, front_on_right in (("btm_sa", "side_a", True), ("btm_sb", "side_b", False)):
        x0, _, x1, _ = box(dl, side)
        depth = _bottom_depth(dl, flap)
        near_front = x1 if front_on_right else x0
        near_back = x0 if front_on_right else x1
        pts = under(dl, flap)
        assert any(abs(x - near_front) < FLAT_TOL and d > 0.7 * depth for x, d in pts)
        assert not any(abs(x - near_back) < FLAT_TOL and d > 0.3 * depth for x, d in pts)


def test_snaplock_has_four_bottom_flaps_and_a_tucked_top(tmp_path: Path) -> None:
    (dl,) = blank(tmp_path, "snaplock", *CARTON).values()
    assert sorted(flaps_of(dl, "bottom").values()) == ["back", "front", "side_a", "side_b"]
    assert flaps_of(dl, "top") == {"top_lid": "back", "dust_ta": "side_a", "dust_tb": "side_b"}
    assert parent(dl, "top_tuck") == "top_lid"


# ---------------- 自动锁底（crash lock）：FEFCO 0713 ----------------


def test_autolock_has_four_bottom_flaps_and_two_glue_triangles_on_45_degree_creases(
    tmp_path: Path,
) -> None:
    (dl,) = blank(tmp_path, "autolock", *CARTON).values()
    bottom = flaps_of(dl, "bottom")
    assert sorted(bottom.values()) == ["back", "front", "side_a", "side_b"]
    glue = [p.id for p in dl.panels if p.role.value == "glue_tab"]
    assert len(glue) == 2
    for g in glue:
        crease = next(e for e in dl.edges if e.type == EdgeType.CREASE and g in e.panels
                      and parent(dl, g) in e.panels)  # fmt: skip
        (seg,) = crease.segments
        dx, dy = abs(seg.end.x - seg.start.x), abs(seg.end.y - seg.start.y)
        assert abs(dx - dy) < 0.05 * max(dx, dy)  # 45° 斜压线


def test_autolock_glue_triangles_belong_to_the_main_flaps(tmp_path: Path) -> None:
    """FEFCO 0713：糊胶三角挂在两片主翼上（靠下一个侧面的角），侧翼上没有糊胶三角。"""
    (dl,) = blank(tmp_path, "autolock", *CARTON).values()
    glue = [p.id for p in dl.panels if p.role.value == "glue_tab"]
    assert {parent(dl, g) for g in glue} == {"btm_back", "btm_front"}
    for g in glue:  # 三角在主翼的右半边（靠下一个侧面）
        mx0, _, mx1, _ = box(dl, parent(dl, g) or "")
        assert box(dl, g)[0] > 0.5 * (mx0 + mx1)
    assert not any(parent(dl, p.id) in ("btm_sa", "btm_sb") for p in dl.panels)


def test_autolock_main_flaps_step_at_the_middle(tmp_path: Path) -> None:
    """主翼自由边：左段全深，正中一个台阶（两片主翼转过来以后互相扣住）。"""
    (dl,) = blank(tmp_path, "autolock", *CARTON).values()
    for flap in ("btm_back", "btm_front"):
        x0, y0, x1, y1 = box(dl, flap)
        xm, ps = 0.5 * (x0 + x1), points(dl, flap)
        left = min(y for x, y in ps if x < xm - 2)  # 左段最深处
        right = min(y for x, y in ps if xm + 2 < x < x1 - 2)  # 右段（台阶底）最深处
        assert left == pytest.approx(y0, abs=FLAT_TOL) and right - left > 0.2 * (y1 - y0)
