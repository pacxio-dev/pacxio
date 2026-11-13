"""结构预览（几何引擎第 5 步 ③）：不建 job，按编号 + 尺寸出刀版图和折好的 3D 图，给 agent 推荐方案时给用户看。

面按角色上浅色（盒身、盖 / 底、插舌、防尘翼、糊口……），只为看清结构，不是印刷效果。多件套各件折好并排。
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from pacxio.messages import lang, t
from pacxio.model.dieline import Dieline, EdgeType

TEX_MAX_PX = 2000
VIEW_PX = 640
ROLE_RGB = {
    "body": (238, 234, 226),
    "lid": (206, 222, 238),
    "bottom": (216, 232, 204),
    "tuck": (242, 214, 190),
    "dust_flap": (244, 234, 190),
    "glue_flap": (226, 208, 236),
    "glue_tab": (226, 208, 236),
    "inner_wall": (208, 230, 224),
    "rim": (208, 230, 224),
    "insert": (236, 218, 218),
    "label": (242, 214, 190),
}


class RoleMapping:
    """刀版毫米 → 贴图像素（和 export.preview 的映射同样的接口，不需要排版结果）。"""

    def __init__(self, dl: Dieline) -> None:
        _, _, w, h = dl.bbox
        self.s = min(8.0, TEX_MAX_PX / max(w, h))
        self.x0, self.y1 = 0.0, h
        self.size = (max(1, math.ceil(w * self.s)), max(1, math.ceil(h * self.s)))

    def pt(self, x: float, y: float) -> tuple[float, float]:
        return ((x - self.x0) * self.s, (self.y1 - y) * self.s)


def role_art(dl: Dieline) -> tuple[Image.Image, RoleMapping]:
    from pacxio.geometry import path_to_polygon, polygons_of

    m = RoleMapping(dl)
    img = Image.new("RGB", m.size, (255, 255, 255))
    draw = ImageDraw.Draw(img)
    for panel in dl.panels:
        rgb = ROLE_RGB.get(panel.role.value, ROLE_RGB["body"])
        for poly in polygons_of(path_to_polygon(panel.polygon)):
            draw.polygon([m.pt(x, y) for x, y in poly.exterior.coords], fill=rgb)
    return img, m


def dieline_png(dl: Dieline, px: int = VIEW_PX, width: int = 1, fit: bool = False) -> Image.Image:
    """刀版线图：刀线黑、压线红、齿线蓝。width：线宽（像素，大图放大看时加粗）。
    fit：图按刀版的长宽比（长边 px），不再是正方形（多件套并排的扁长刀版不会缩在一角）。"""
    _, _, w, h = dl.bbox
    s = (px - 24) / max(w, h)
    ph = max(24, round(h * s) + 24) if fit else px
    pw = max(24, round(w * s) + 24) if fit else px
    img = Image.new("RGB", (pw, ph), "white")
    d = ImageDraw.Draw(img)
    color = {EdgeType.CUT: (0, 0, 0), EdgeType.PERF: (30, 90, 200)}
    from pacxio.geometry import Path as GPath

    for e in dl.edges:
        rgb = color.get(e.type, (210, 0, 0))
        pts = GPath(e.segments, closed=False).to_points(max_sagitta=0.5)
        d.line([(12 + q.x * s, ph - 12 - q.y * s) for q in pts], fill=rgb, width=width)
    return img


def folded_png(
    dls: list[Dieline], view_name: str = "front_right", px: int = VIEW_PX
) -> Image.Image | None:
    """按刀版自带的折法折好、按角色上色的 3D 图；多件并排。没有折法的返回 None。"""
    from dataclasses import replace

    from pacxio.mockup.fold import BoxModel
    from pacxio.mockup.folded_view import open_paper_view
    from pacxio.mockup.folding import plan_for
    from pacxio.mockup.raster import render_folded
    from pacxio.mockup.render import VIEWS

    faces, textures = [], {}
    cursor, ymax, zmax = 0.0, 0.0, 0.0
    for i, dl in enumerate(dls):
        plan = plan_for(dl)
        if plan is None:
            return None
        art, m = role_art(dl)
        box, tex = open_paper_view(dl, art, m, step=plan.steps)  # type: ignore[arg-type]
        pts = [c for f in box.faces for c in f.corners]
        lo = [min(p[k] for p in pts) for k in range(3)]
        hi = [max(p[k] for p in pts) for k in range(3)]
        for f in box.faces:
            key = f"{i}.{f.key}"
            corners = tuple((c[0] - lo[0] + cursor, c[1] - lo[1], c[2] - lo[2]) for c in f.corners)
            faces.append(replace(f, key=key, corners=corners))  # type: ignore[arg-type]
            if f.key in tex:
                textures[key] = tex[f.key]
        cursor += hi[0] - lo[0] + 40.0
        ymax, zmax = max(ymax, hi[1] - lo[1]), max(zmax, hi[2] - lo[2])
    view = next(v for v in VIEWS if v.name == view_name)
    box = BoxModel(x=max(cursor - 40.0, 1.0), y=max(ymax, 1.0), z=max(zmax, 1.0), faces=faces)
    return render_folded(box, textures, view, (px, px))


def build(
    code: str, L: float, W: float, H: float, material: str | None = None
) -> tuple[list[Dieline], str]:
    """编号 + 内尺寸 → 刀版（多件套每件一张）和用的材料。"""
    from pacxio.api import structure_params
    from pacxio.model.common import Issue
    from pacxio.model.profile import load_profile, resolve_material
    from pacxio.styles import get_style

    found = structure_params(code if not code.upper().startswith("A") else None,
                             code if code.upper().startswith("A") else None, None, L, W, H)  # fmt: skip
    if isinstance(found, Issue):
        raise ValueError(found.message)
    style_id, params = found
    from pacxio.engine.catalog import default_material

    mat_id = material or default_material(code)
    mat, profile = resolve_material(mat_id), load_profile("default")
    style = get_style(style_id)
    p = style.param_schema()(**params)
    issues = style.validate(p, mat, profile)
    if issues:
        raise ValueError(t("; ").join(i.message for i in issues))
    if style_id == "structure_set":
        return list(style.generate_parts(p, mat, profile).values()), mat_id  # type: ignore[attr-defined]
    return [style.generate(p, mat, profile)], mat_id


def write_preview(
    code: str, L: float, W: float, H: float, out: Path, material: str | None = None
) -> dict[str, Any]:
    """出 <编号>_dieline.png（多件套每件一张）和 <编号>_3d.png；返回文件和信息。"""
    from pacxio.engine import catalog, i18n

    dls, mat_id = build(code, L, W, H, material)
    out.mkdir(parents=True, exist_ok=True)
    safe = code.replace(".", "_")
    files = []
    for dl in dls:
        suffix = f"_{dl.style_meta.get('part')}" if len(dls) > 1 else ""
        path = out / f"{safe}{suffix}_dieline.png"
        dieline_png(dl).save(path)
        files.append(str(path))
    view = folded_png(dls)
    if view is not None:
        path = out / f"{safe}_3d.png"
        view.save(path)
        files.append(str(path))
    entry = catalog.get(code)
    return {"code": code, "material": mat_id, "files": files,
            "entry": i18n.entry_dict(entry, lang()) if entry else None}  # fmt: skip


__all__ = ["build", "dieline_png", "folded_png", "role_art", "write_preview"]


PREVIEW_TEX_PX = 1024  # 改结构页 3D 预览的贴图边长（按角色上色，不需要很清楚）


def _data_url(img: Image.Image) -> str:
    import base64
    import io

    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _preview_part(
    dl: Dieline, part: Any, name: str, poses: list[dict[str, Any]], cfg: dict[str, Any]
) -> dict[str, Any]:
    """一个部件在预览场景里的数据：网格、按角色上色的贴图（data URL）、动作关键帧。"""
    from pacxio.mockup.foldscene import _part_data

    art, m = role_art(dl)
    (px0, py0), (px1, py1) = m.pt(0, 0), m.pt(1, 1)
    w, h = art.size
    sx, sy = px1 - px0, py1 - py0
    k = min(1.0, PREVIEW_TEX_PX / max(w, h))
    small = (
        art.resize((max(1, round(w * k)), max(1, round(h * k))), Image.Resampling.LANCZOS)
        if k < 1
        else art
    )
    # 材质贴图三通道 = 粗糙度 / 金属性 / 涂层（纸的那组值）；高度图 R 高度（128 平）、G 镭射区（0 没有）、B 128
    # ——都同 mockup.materials.atlases，否则查看器会把整张当成镭射
    material = Image.new("RGB", (4, 4), tuple(cfg["paper"][:3]))
    flat = Image.merge("RGB", tuple(Image.new("L", (4, 4), v) for v in (128, 0, 128)))
    return _part_data(part, cfg["fold"]["crease_radius_caliper"] * dl.caliper_mm) | {
        "id": name,
        "tex_mm": [round(w * k / abs(sx), 3), round(h * k / abs(sy), 3)],
        "uv": [sx / w, px0 / w, sy / h, py0 / h],
        "textures": {
            "base": _data_url(small),
            "material": _data_url(material),
            "height": _data_url(flat),
        },
        "poses": poses,
    }


def _scene(
    steps: int, ground: int, open_step: float, items: list[dict[str, Any]], cfg: dict[str, Any]
) -> dict[str, Any]:
    from pacxio.mockup.foldscene import FOLD_SCENE_VERSION
    from pacxio.mockup.scene3d import RENDERER_VERSION

    return {
        "version": FOLD_SCENE_VERSION, "renderer_version": RENDERER_VERSION, "steps": steps, "ground_step": ground,
        "states": {"flat": {"t": 0.0}, "open": {"t": open_step / steps}, "closed": {"t": 1.0}},
        "regions": [], "material_version": cfg["version"], "relief_height_mm": cfg["relief_height_mm"],
        "relief_edge_mm": cfg["relief_edge_mm"], "relief_cavity": cfg["relief_cavity"], "parts": items,
    }  # fmt: skip


def preview_scene(dl: Dieline) -> dict[str, Any] | None:
    """改结构页用的可转动 3D 场景（和工作台 ⑥ 同一种场景格式，按角色上色、没有设计），贴图直接内嵌成 data URL，
    不写文件。没有折法的返回 None。"""
    from pacxio.mockup.folding import build_part, plan_for
    from pacxio.mockup.foldscene import _single_poses
    from pacxio.mockup.materials import presets

    plan = plan_for(dl)
    if plan is None:
        return None
    cfg = presets()
    part = build_part(dl, plan)
    item = _preview_part(dl, part, "main", _single_poses(part), cfg)
    return _scene(plan.steps, plan.ground_step, plan.open_step, [item], cfg)


LANE_GAP_MM = 60.0  # 预览里平铺时每件各占一条道，道与道之间留这么宽
EXPLODE_RATIO, EXPLODE_MIN_MM = 0.6, 30.0  # 套之前，外面那件离开被套件 max(0.6·它的高, 30)


def _set_poses(
    folded: dict[str, Any], set_info: dict[str, Any], steps: int
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
    """引擎多件套的预览动作：平铺时每件一条道 → 各自在自己的道上折好（谁也扫不到谁）→ 全都折好以后挪到被套件
    旁边、拆开着 → 最后套在一起。steps = 折法最多的那件的步数 + 2。
    套合按配方的 fits：套盖（caps）中间件立着、盖帽下面一个、倒扣在上面一个（count 2）；天地盖类（telescope）
    盖倒扣在底上；其余（抽拉套、组件）各自摆在自己的道上。返回 (每个实例的关键帧, 实例 → 部件)。"""
    import numpy as np

    from pacxio.mockup.folding import closed_pose
    from pacxio.mockup.foldscene import _pose, _translate
    from pacxio.mockup.scene import part_box

    kind, fits = set_info.get("set"), set_info.get("fits", {})
    boxes = {n: part_box(f.dl) for n, f in folded.items()}
    fit_of = {n: closed_pose(f, boxes[n]) for n, f in folded.items()}
    flip = np.diag([1.0, -1.0, -1.0, 1.0])  # 绕 x 轴翻 180°（开口朝下）
    # 实例：套盖的盖帽要两个
    instances: dict[str, str] = {}
    for n in folded:
        count = int(fits.get(n, {}).get("count", 1)) if kind == "caps" else 1
        for i in range(count):
            instances[n if count == 1 else f"{n}_{i + 1}"] = n
    lanes, y = {}, 0.0
    for inst, n in instances.items():
        x0, y0, x1, y1 = folded[n].dl.bbox
        lanes[inst] = y - y0
        y += max(y1 - y0, boxes[n].y) + LANE_GAP_MM
    assembled: dict[str, Any] = {}
    exploded: dict[str, Any] = {}
    host = next((n for n in folded if n not in fits), next(iter(folded)))
    hb = boxes[host]
    t = folded[host].dl.caliper_mm
    for inst, n in instances.items():
        b = boxes[n]
        dx, dy = (hb.x - b.x) / 2, (hb.y - b.y) / 2
        if n == host or kind not in ("caps", "telescope") or fits.get(n, {}).get("over") != host:
            assembled[inst] = exploded[inst] = (
                _translate((0.0, lanes[inst] if n != host else 0.0, 0.0)) @ fit_of[n]
            )
            continue
        lift = max(EXPLODE_RATIO * b.z, EXPLODE_MIN_MM)
        if kind == "caps" and inst.endswith("_1"):  # 下面那个盖帽：开口朝上，中间件坐在它的底上
            assembled[inst] = _translate((dx, dy, -t)) @ fit_of[n]
            exploded[inst] = _translate((dx, dy, -t - lift)) @ fit_of[n]
            continue
        top = hb.z + t  # 倒扣：盖顶的内表面压在被套件上沿
        m = _translate((dx, dy + b.y, top)) @ flip @ fit_of[n]
        assembled[inst] = m
        exploded[inst] = _translate((0.0, 0.0, lift)) @ m
    out: dict[str, list[dict[str, Any]]] = {}
    for inst, n in instances.items():
        f = folded[n]
        x0, y0, _, _ = f.dl.bbox
        flat = _translate((-x0, lanes[inst], 0.0))
        in_lane = _translate((0.0, lanes[inst], 0.0)) @ fit_of[n]  # 在自己的道上折好
        out[inst] = [_pose(0, flat), _pose(f.plan.pose_step, in_lane), _pose(steps - 2, in_lane),
                     _pose(steps - 1, exploded[inst]), _pose(steps, assembled[inst])]  # fmt: skip
    return out, instances


def preview_scene_parts(
    style_id: str, dls: dict[str, Dieline], set_info: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """多件套的预览场景：各件折好、摆成打开的样子、最后合到一起（天地盖盖上、抽屉推进去；隔板组插好；
    引擎多件套各件并排）——和 ⑥ 3D 效果图同一套摆法（mockup.foldscene），只是按角色上色。"""
    from pacxio.mockup.folding import build_part, plan_for
    from pacxio.mockup.foldscene import _extra_steps, _pose
    from pacxio.mockup.foldscene import _set_poses as _set_poses_mockup
    from pacxio.mockup.materials import presets

    plans = {name: plan_for(dl) for name, dl in dls.items()}
    if any(p is None for p in plans.values()):
        return None
    cfg = presets()
    folded = {name: build_part(dl, plans[name]) for name, dl in dls.items()}  # type: ignore[arg-type]
    fold_steps = max(f.plan.steps for f in folded.values())
    if style_id == "divider":
        from pacxio.mockup.regular import divider_poses

        steps = 3
        poses = divider_poses(dls, _pose)
        items = []
        for name, f in folded.items():
            qty = int(f.dl.style_meta.get("quantity", 1))
            for i in range(1, qty + 1):
                ident = f"{name}_{i}"
                items.append(_preview_part(f.dl, f, ident, poses[ident], cfg))
    elif style_id == "structure_set" and set_info is not None:  # 引擎多件套：按套合关系装起来
        steps = fold_steps + 2
        poses, instances = _set_poses(folded, set_info, steps)
        items = [
            _preview_part(folded[n].dl, folded[n], inst, poses[inst], cfg)
            for inst, n in instances.items()
        ]
    else:
        steps = fold_steps + _extra_steps(style_id)
        poses = _set_poses_mockup(style_id, folded, steps)
        items = [_preview_part(f.dl, f, name, poses[name], cfg) for name, f in folded.items()]
    first = next(iter(folded.values())).plan
    return _scene(steps, first.ground_step, steps - 1, items, cfg)
