"""开窗（）：各盒型通用的选项——卡纸盒身面上的内部切口。

窗口用面局部坐标（同 `art add --box`）定义；盒型生成刀版之后由 `apply_windows` 加上去，
盒型代码不用改。窗口切线作为 CUT 线放进 edges（各种导出自动带上）。贴窗（窗膜的材质、大小、
位置）是工厂的后续工序，引擎不管（2026-10-20 用户决定）；`film` 只记录贴不贴膜，影响效果图
和规格书。约束数值全部来自配置档的 window 规则（【初值】）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from shapely.geometry import Polygon, box
from shapely.geometry.base import BaseGeometry

from pacxio.geometry import Path, Vec, affine_geom, path_to_polygon
from pacxio.messages import t
from pacxio.model.common import Issue
from pacxio.model.dieline import Dieline, DielineWindow, Edge, EdgeType, Panel, PanelRole
from pacxio.model.job import Job, Window
from pacxio.model.profile import Material, Profile, WindowRules
from pacxio.model.report import Finding

WINDOW_EDGE_PREFIX = "cut_window_"
ALLOWED_ROLES = {PanelRole.BODY}  # 第一版只开在盒身四个面（ 节）
GEOM_TOL_MM = 1e-6
PLACEMENT_IN_WINDOW_RATIO = 0.5  # W05：一个放置超过这个比例落在窗口里就提醒


def no_rules_issue(profile: Profile) -> Issue:
    return Issue(
        id="W01",
        message=t("Profile {profile} has no window rules. Windows are unavailable.", profile=profile.id),
        fix=t("Add a window section like the one in profiles/default.yaml and confirm the values with the factory"),
    )


def corner_radius(w: Window, rules: WindowRules) -> float:
    """圆 = 半径为宽一半的圆角矩形；跑道形 = 圆角为短边一半；圆角矩形没给时取配置档默认。"""
    if w.shape == "circle":
        return w.box.w / 2
    if w.shape == "obround":
        return min(w.box.w, w.box.h) / 2
    return w.corner_r if w.corner_r is not None else rules.default_corner_r_mm


def local_cut(w: Window, rules: WindowRules) -> Path:
    """窗口切线（面局部坐标，逆时针，只有直线和圆弧）。"""
    b = w.box
    return Path.rounded_rect(b.x, b.y, b.w, b.h, corner_radius(w, rules))


@dataclass(frozen=True)
class GlueLanding:
    """糊口粘到的位置：盒身另一端那个面里面、靠自由边的一条（宽 = 糊口宽）。"""

    panel: str
    area: Polygon  # 展开图坐标


def glue_landing(dl: Dieline) -> GlueLanding | None:
    """糊口粘到的位置：盒身面横向相接成一条时，糊口在左端就粘到最右那个面的右侧（反之亦然）。"""
    flaps = [p for p in dl.panels if p.role == PanelRole.GLUE_FLAP]
    walls = sorted(
        (p for p in dl.panels if p.role == PanelRole.BODY), key=lambda p: p.polygon.bbox()[0]
    )
    if not flaps or len(walls) < 2:
        return None
    boxes = [w.polygon.bbox() for w in walls]
    if any(abs(a[2] - b[0]) > 1e-6 for a, b in zip(boxes, boxes[1:], strict=False)):
        return None  # 盒身面不是横向相接的一条（飞机盒、托盘）：没有侧缝糊口
    gx0, _, gx1, _ = flaps[0].polygon.bbox()
    width = gx1 - gx0
    if gx1 <= boxes[0][0] + GEOM_TOL_MM:  # 糊口在左端 → 粘到最右边那个面的右侧
        target, x0, x1 = walls[-1], boxes[-1][2] - width, boxes[-1][2]
    else:
        target, x0, x1 = walls[0], boxes[0][0], boxes[0][0] + width
    _, py0, _, py1 = target.polygon.bbox()
    return GlueLanding(target.id, box(x0, py0, x1, py1))


def _local(panel: Panel, geom: BaseGeometry) -> BaseGeometry:
    return affine_geom(geom, panel.frame.inverse())


def _shift_fix(
    wid: str, bounds: tuple[float, float, float, float], allowed: tuple[float, float, float, float]
) -> str:
    """把窗口（面局部包围盒）挪进允许的范围要怎么改：先看放不放得下，再看往哪挪。"""
    x0, y0, x1, y1 = bounds
    ax0, ay0, ax1, ay1 = allowed
    parts: list[str] = []
    if x1 - x0 > ax1 - ax0 + GEOM_TOL_MM:
        parts.append(t("reduce the width to at most {width:.1f} mm", width=max(ax1 - ax0, 0)))
    elif x0 < ax0 - GEOM_TOL_MM:
        parts.append(t("move right by {distance:.1f} mm", distance=ax0 - x0))
    elif x1 > ax1 + GEOM_TOL_MM:
        parts.append(t("move left by {distance:.1f} mm", distance=x1 - ax1))
    if y1 - y0 > ay1 - ay0 + GEOM_TOL_MM:
        parts.append(t("reduce the height to at most {height:.1f} mm", height=max(ay1 - ay0, 0)))
    elif y0 < ay0 - GEOM_TOL_MM:
        parts.append(t("move up by {distance:.1f} mm", distance=ay0 - y0))
    elif y1 > ay1 + GEOM_TOL_MM:
        parts.append(t("move down by {distance:.1f} mm", distance=y1 - ay1))
    return t("For {window}: ", window=wid) + t(", and ").join(parts) if parts else t("Move or resize {window}", window=wid)


def _away_fix(wid: str, mine: BaseGeometry, other: BaseGeometry, need: float) -> str:
    """离 other 不够远（或已经压上）：沿两者中心连线的主方向挪到隔开 need（面局部坐标）。"""
    mx0, my0, mx1, my1 = mine.bounds
    ox0, oy0, ox1, oy1 = other.bounds
    dx = mine.centroid.x - other.centroid.x
    dy = mine.centroid.y - other.centroid.y
    if abs(dx) >= abs(dy):
        way, d = (t("right"), ox1 + need - mx0) if dx > 0 else (t("left"), mx1 - (ox0 - need))
    else:
        way, d = (t("up"), oy1 + need - my0) if dy > 0 else (t("down"), my1 - (oy0 - need))
    return t("Move {window} {direction} by {distance:.1f} mm or make it smaller", window=wid, direction=way, distance=d)


def check_windows(
    dl: Dieline,
    windows: list[Window],
    profile: Profile,
    material: Material | None = None,
    allowed: set[str] | None = None,
    *,
    allowed_on_other_parts: tuple[str, ...] | None = None,
) -> list[Issue]:
    """W01、W02（error）：窗口本身、窗口离边与其它窗口。dl 是还没加窗口的刀版。"""
    if not windows:
        return []
    if material is not None and material.type == "corrugated":
        return [Issue(id="W02", message=t("Windows are not supported on corrugated boxes"),
                      fix=t("Remove the window with `window remove` or use a folding carton style"))]  # fmt: skip
    rules = profile.window
    if rules is None:
        return [no_rules_issue(profile)]
    out: list[Issue] = []
    ids = [w.id for w in windows]
    for wid in sorted({i for i in ids if ids.count(i) > 1}):
        out.append(Issue(id="W01", message=t("Window ID {window} is duplicated", window=wid), fix=t("Use a different --id for each window")))
    landing = glue_landing(dl)
    placed: list[tuple[Window, Panel, Polygon]] = []  # 通过 W01/W02 的窗口
    body = (
        sorted(allowed)
        if allowed is not None
        else sorted(p.id for p in dl.panels if p.role in ALLOWED_ROLES)
    )
    for w in windows:
        try:
            panel = dl.panel(w.panel)
        except KeyError:
            out.append(Issue(id="W02", message=t("Window {window}: panel {panel} does not exist", window=w.id, panel=w.panel),
                             fix=t("Panels that can have windows: ") + t(", ").join(body)))  # fmt: skip
            continue
        if allowed is not None and panel.id not in allowed:
            fix = (
                t("Panels that can have windows: ")
                + (t(", ").join(body) or t("None on this part"))
                if allowed_on_other_parts is None
                else t("Panels that can have windows on this box style: {panels}",
                       panels=t(", ").join(allowed_on_other_parts) or t("None"))
            )
            out.append(Issue(id="W02", message=t("Window {window}: windows are unavailable on {panel}", window=w.id, panel=w.panel),
                             fix=fix))  # fmt: skip
            continue
        if allowed is None and panel.role not in ALLOWED_ROLES:
            out.append(Issue(id="W02", message=t("Window {window} is on {panel} ({role}). Windows are available on body panels only.", window=w.id, panel=w.panel, role=panel.role.value),
                             fix=t("Move it to: ") + t(", ").join(body)))  # fmt: skip
            continue
        bad = _check_shape(w, rules)
        out += bad
        if bad:
            continue
        cut = path_to_polygon(local_cut(w, rules))
        edge_issues = _check_edges(w, panel, cut, rules, landing)
        out += edge_issues
        if not edge_issues:
            placed.append((w, panel, cut))
    out += _check_pairs(placed, rules)
    for w, panel, cut in placed:
        for existing in dl.windows:
            if w.id == existing.id:
                out.append(
                    Issue(
                        id="W01",
                        message=t("Window ID {window} duplicates a structural locating hole", window=w.id),
                        fix=t("Choose a unique window ID"),
                    )
                )
            if (
                existing.panel == panel.id
                and affine_geom(cut, panel.frame).distance(path_to_polygon(existing.cut))
                < rules.min_web_mm - GEOM_TOL_MM
            ):
                out.append(
                    Issue(
                        id="W02",
                        message=t("Window {window} has insufficient board between it and structural hole {hole}", window=w.id, hole=existing.id),
                        fix=t("Move the window or adjust the structural hole array"),
                    )
                )
    return out


def _check_shape(w: Window, rules: WindowRules) -> list[Issue]:
    out: list[Issue] = []
    b = w.box
    if min(b.w, b.h) < rules.min_size_mm - GEOM_TOL_MM:
        out.append(Issue(id="W01", message=t("Window {window} is too small: {width:g} × {height:g} mm (provisional minimum {minimum:g} mm per side)", window=w.id, width=b.w, height=b.h, minimum=rules.min_size_mm),
                         fix=t("Set both window dimensions to at least {minimum:g} mm", minimum=rules.min_size_mm)))  # fmt: skip
    if w.shape == "circle" and abs(b.w - b.h) > GEOM_TOL_MM:
        out.append(Issue(id="W01", message=t("Round window {window} has unequal width and height ({width:g} × {height:g})", window=w.id, width=b.w, height=b.h),
                         fix=t("Use equal width and height in --box for a round window, or use --shape obround for an elongated opening")))  # fmt: skip
    if w.shape != "rect" and w.corner_r is not None:
        out.append(Issue(id="W01", message=t("Window {window} is {shape}. Its shape sets the corner radius, so --r does not apply.", window=w.id, shape=w.shape),
                         fix=t("Omit --r or use --shape rect")))  # fmt: skip
    if w.shape == "rect":
        r = corner_radius(w, rules)
        if r < rules.min_corner_r_mm - GEOM_TOL_MM:
            out.append(Issue(id="W01", message=t("Window {window}: corner radius {radius:g} mm is below the provisional minimum {minimum:g} mm. Sharp corners can tear.", window=w.id, radius=r, minimum=rules.min_corner_r_mm),
                             fix=t("Set --r to at least {minimum:g}", minimum=rules.min_corner_r_mm)))  # fmt: skip
        elif r > min(b.w, b.h) / 2 + GEOM_TOL_MM:
            out.append(Issue(id="W01", message=t("Window {window}: corner radius {radius:g} mm exceeds half the shorter side", window=w.id, radius=r),
                             fix=t("Set --r to at most {maximum:g} or use --shape obround", maximum=min(b.w, b.h) / 2)))  # fmt: skip
    return out


def _check_edges(
    w: Window,
    panel: Panel,
    cut: Polygon,
    rules: WindowRules,
    landing: GlueLanding | None,
) -> list[Issue]:
    """W02：窗口离压线 / 外轮廓 / 糊口落胶位置至少 edge_clearance_mm。"""
    out: list[Issue] = []
    face = _local(panel, path_to_polygon(panel.polygon))
    fx0, fy0, fx1, fy1 = face.bounds
    need = rules.edge_clearance_mm
    gap = face.boundary.distance(cut) if face.contains(cut) else 0.0
    if gap < need - GEOM_TOL_MM:
        allowed = (fx0 + need, fy0 + need, fx1 - need, fy1 - need)
        out.append(Issue(id="W02", message=t("Window {window} is only {clearance:.1f} mm from a crease or cut edge (provisional minimum {minimum:g} mm)", window=w.id, clearance=gap, minimum=need),
                         fix=_shift_fix(w.id, cut.bounds, allowed)))  # fmt: skip
        return out
    land = (
        _local(panel, landing.area) if landing is not None and landing.panel == panel.id else None
    )
    if land is not None and cut.distance(land) < need - GEOM_TOL_MM:
        out.append(Issue(id="W02", message=t("Window {window} is only {clearance:.1f} mm from the glue landing along the inside edge of {panel} (provisional minimum {minimum:g} mm)", window=w.id, panel=panel.id, clearance=cut.distance(land), minimum=need),
                         fix=_away_fix(w.id, cut, land, need)))  # fmt: skip
    return out


def _check_pairs(placed: list[tuple[Window, Panel, Polygon]], rules: WindowRules) -> list[Issue]:
    """同一个面上的两个窗口之间至少隔 min_web。"""
    out: list[Issue] = []
    for i, (a, pa, ca) in enumerate(placed):
        for b, pb, cb in placed[i + 1 :]:
            if pa.id != pb.id:
                continue
            gap = ca.distance(cb)
            if gap < rules.min_web_mm - GEOM_TOL_MM:
                out.append(Issue(id="W02", message=t("Windows {first} and {second} have only {gap:.1f} mm between them (provisional minimum {minimum:g} mm). The narrow bridge may tear.", first=a.id, second=b.id, gap=gap, minimum=rules.min_web_mm),
                                 fix=_away_fix(b.id, cb, ca, rules.min_web_mm) + t(", or merge the two windows")))  # fmt: skip
    return out


def apply_windows(
    dl: Dieline,
    windows: list[Window] | None,
    profile: Profile,
    material: Material | None = None,
    allowed: set[str] | None = None,
    *,
    allowed_on_other_parts: tuple[str, ...] | None = None,
) -> tuple[Dieline | None, list[Issue]]:
    """把窗口加到刀版上：CUT 线进 edges，几何进 Dieline.windows。违反 W01、W02 时返回 (None, 问题)。"""
    if not windows:
        return dl, []
    issues = check_windows(dl, windows, profile, material, allowed,
                           allowed_on_other_parts=allowed_on_other_parts)
    if issues:
        return None, issues
    rules = profile.window
    assert rules is not None  # check_windows 已经查过
    edges = list(dl.edges)
    placed: list[DielineWindow] = list(dl.windows)
    for w in windows:
        frame = dl.panel(w.panel).frame
        cut = local_cut(w, rules).transformed(frame)
        edges.append(
            Edge(
                id=WINDOW_EDGE_PREFIX + w.id,
                type=EdgeType.CUT,
                segments=cut.segments,
                panels=[w.panel],
            )
        )
        placed.append(
            DielineWindow(
                id=w.id,
                panel=w.panel,
                cut=cut,
                film=w.film == "pet",
            )
        )
    return dl.model_copy(update={"edges": edges, "windows": placed}), []


def area_findings(dl: Dieline, profile: Profile) -> list[Finding]:
    """W04（warning）：一个面上窗口占的面积太大，盒子会变软。"""
    rules = profile.window
    if not dl.windows or rules is None:
        return []
    out: list[Finding] = []
    for pid in dict.fromkeys(w.panel for w in dl.windows):
        face = path_to_polygon(dl.panel(pid).polygon)
        holes = [path_to_polygon(w.cut) for w in dl.windows if w.panel == pid]
        ratio = sum(h.area for h in holes) / face.area
        if ratio > rules.max_area_ratio:
            out.append(Finding(id="W04", level="warning",
                               message=t("Windows cover {ratio:.0%} of panel {panel}, above the provisional limit {maximum:.0%}. The box may lose rigidity.", panel=pid, ratio=ratio, maximum=rules.max_area_ratio),
                               fix=t("Reduce the window area or fold a sample to check rigidity")))  # fmt: skip
    return out


def placement_findings(
    job: Job, dl: Dieline, texts: Sequence[tuple[str, BaseGeometry]]
) -> list[Finding]:
    """W05（warning）：文字 / 图片 / 图形大部分落在窗口里（会被切掉）。texts 为 (id, 刀版坐标包围盒)。"""
    if not dl.windows:
        return []
    holes = {w.id: path_to_polygon(w.cut) for w in dl.windows}
    items: list[tuple[str, BaseGeometry]] = list(texts)
    for p in job.placements:
        if (
            p.kind in ("image", "vector", "placeholder")
            and p.panel
            and p.box
            and p.coord_space == "panel"
        ):
            frame = dl.panel(p.panel).frame
            b = p.box
            corners = [
                frame.apply(Vec(x, y))
                for x, y in ((b.x, b.y), (b.x + b.w, b.y), (b.x + b.w, b.y + b.h), (b.x, b.y + b.h))
            ]
            items.append((p.id, Polygon([c.as_tuple() for c in corners])))
    out: list[Finding] = []
    for pid, geom in items:
        if geom.is_empty or geom.area <= 0:
            continue
        for wid, hole in holes.items():
            share = geom.intersection(hole).area / geom.area
            if share > PLACEMENT_IN_WINDOW_RATIO:
                out.append(Finding(id="W05", level="warning",
                                   message=t("{ratio:.0%} of {item} lies inside window {window} and will be cut away", item=pid, ratio=share, window=wid),
                                   fix=t("Move {item} away from window {window}. Run `pacxio panels` to locate the window.", item=pid, window=wid)))  # fmt: skip
    return out


def _local_box(panel: Panel, path: Path) -> dict[str, float]:
    inv = panel.frame.inverse()
    pts = [inv.apply(v) for v in path.to_points(0.05)]
    x0, y0 = min(q.x for q in pts), min(q.y for q in pts)
    x1, y1 = max(q.x for q in pts), max(q.y for q in pts)
    return {"x": round(x0, 3), "y": round(y0, 3), "w": round(x1 - x0, 3), "h": round(y1 - y0, 3)}


def windows_by_panel(
    dl: Dieline, job: Job | None, profile: Profile
) -> dict[str, list[dict[str, object]]]:
    """每个面上的窗口（面局部坐标）：窗口、排字要避开的区域（窗口外扩安全边距）。"""
    spec = {w.id: w for w in (job.windows or [])} if job is not None else {}
    out: dict[str, list[dict[str, object]]] = {}
    m = profile.safe_margin_mm
    for dw in dl.windows:
        panel = dl.panel(dw.panel)
        b = _local_box(panel, dw.cut)
        info: dict[str, object] = {
            "id": dw.id,
            "box_local_mm": b,
            "keep_out_local_mm": {"x": round(b["x"] - m, 3), "y": round(b["y"] - m, 3),
                                  "w": round(b["w"] + 2 * m, 3), "h": round(b["h"] + 2 * m, 3)},
        }  # fmt: skip
        w = spec.get(dw.id)
        if w is not None and profile.window is not None:
            info.update(shape=w.shape, corner_r=corner_radius(w, profile.window), film=w.film)
        out.setdefault(dw.panel, []).append(info)
    return out
