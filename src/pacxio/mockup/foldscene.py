"""场景 v3（ 节）：每块面一个网格、铰链和折叠步骤，折叠在浏览器里算。

贴图不再按面裁：每个部件一整张刀版图（底色、材质、高度、工艺区域），网格按刀版坐标取。

scene.json：
    {"version": 3, "fingerprint": …, "steps": 总步数, "ground_step": …,
     "states": {"flat": {"t": 0}, "open": {"t": …}, "closed": {"t": 1}},
     "regions": […], "material_version": …, "relief_*": …,
     "parts": [{"id", "caliper", "paper": [r, g, b], "tex_mm": [宽, 高], "uv": [sx, ox, sy, oy],
                "textures": {"base": …, "material": …, "height": …, "region:<id>": …},
                "poses": [{"step", "q": [x, y, z, w], "t": [x, y, z]}, …],
                "panels": [{"id", "parent": 序号或 -1, "origin", "axis", "keys", "layer",
                            "vertices": [x, y, …], "triangles": [i, …], "rings": [[x, y, …], …]}, …],
                "strips": [{"parent", "child", "origin", "along", "to_parent", "half",
                            "crease", "parent_end", "child_end"}, …]}, …]}
坐标单位 mm。uv：u = sx·x + ox，v = sy·y + oy。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from PIL import Image

from pacxio.mockup.fold import build_box
from pacxio.mockup.folding import (
    FoldedPart,
    FoldPlan,
    Mat,
    build_meshes,
    build_part,
    closed_pose,
    plan_for,
)
from pacxio.mockup.materials import atlases, merged_regions, presets
from pacxio.mockup.scene import (
    OPEN_GAP_MM,
    OPEN_LIFT_RATIO,
    paper_rgb,
    part_box,
    part_offsets,
)
from pacxio.mockup.scene3d import RENDERER_VERSION, SCENE_DIR, SCENE_FILE
from pacxio.model.job import Job

if TYPE_CHECKING:  # 闭源（）：只用作类型
    from pacxio_pro.pipeline import Prepared

FOLD_SCENE_VERSION = 3
FOLD_TEX_MAX = 4096  # 一整张刀版：面比以前小，贴图要大一些才和以前一样清楚


def foldable(parts: list[Prepared]) -> bool:
    return all(plan_for(q.dl) is not None for q in parts)


def _quat(m: Mat) -> list[float]:
    """旋转矩阵 → 四元数 [x, y, z, w]。"""
    r = m[:3, :3]
    w = np.sqrt(max(0.0, 1 + r[0, 0] + r[1, 1] + r[2, 2])) / 2
    x = np.sqrt(max(0.0, 1 + r[0, 0] - r[1, 1] - r[2, 2])) / 2
    y = np.sqrt(max(0.0, 1 - r[0, 0] + r[1, 1] - r[2, 2])) / 2
    z = np.sqrt(max(0.0, 1 - r[0, 0] - r[1, 1] + r[2, 2])) / 2
    x = float(np.copysign(x, r[2, 1] - r[1, 2]))
    y = float(np.copysign(y, r[0, 2] - r[2, 0]))
    z = float(np.copysign(z, r[1, 0] - r[0, 1]))
    return [round(v, 6) for v in (x, y, z, float(w))]


def _pose(step: float, m: Mat) -> dict[str, Any]:
    return {"step": step, "q": _quat(m), "t": [round(float(v), 3) for v in m[:3, 3]]}


def _single_poses(part: FoldedPart) -> list[dict[str, Any]]:
    """平铺（印刷面朝上）→ 合上。"""
    pose = closed_pose(part, build_box(part.dl))
    return [_pose(0, np.eye(4)), _pose(part.plan.pose_step, pose)]


def _translate(d: tuple[float, float, float]) -> Mat:
    m = np.eye(4)
    m[:3, 3] = d
    return m


def _extra_steps(style: str) -> int:
    """折好之后还要几步合到一起。天地盖：盖子挪到地盒正上方，再竖直盖下去。"""
    return 2 if style == "tray_lid" else 1


def _set_poses(
    style: str, folded: dict[str, FoldedPart], steps: int
) -> dict[str, list[dict[str, Any]]]:
    """多部件：并排平铺 → 各自折好、摆成打开的样子 → 最后一步合到一起。

    天地盖的打开是上下的（用户 2026-11-01）："打开"时盖子悬在地盒正上方，合上是竖直盖下去；
    盖子先在旁边折好，抬到那个高度再平移过去，路上不穿过地盒。静态效果图仍是盖放旁边。"""
    dls = {name: part.dl for name, part in folded.items()}
    opened, closed = part_offsets(style, dls, True), part_offsets(style, dls, False)
    out: dict[str, list[dict[str, Any]]] = {}
    x = 0.0
    for name, part in folded.items():
        fit = closed_pose(part, part_box(part.dl))
        x0, y0, x1, _ = part.dl.bbox
        at = _translate(opened[name]) @ fit
        keys = [_pose(0, _translate((x - x0, -y0, 0.0))), _pose(part.plan.pose_step, at)]
        if _extra_steps(style) == 2:
            lift = OPEN_LIFT_RATIO * part_box(part.dl).z if name == "lid" else 0.0
            ox, oy, oz = opened[name]
            cx, cy, cz = closed[name]
            keys += [
                _pose(steps - 2, at),
                _pose(steps - 1.5, _translate((ox, oy, cz + lift)) @ fit),
                _pose(steps - 1, _translate((cx, cy, cz + lift)) @ fit),
            ]
        else:
            keys.append(_pose(steps - 1, at))
        keys.append(_pose(steps, _translate(closed[name]) @ fit))
        out[name] = keys
        x += x1 - x0 + OPEN_GAP_MM
    return out


def _small(img: Image.Image) -> Image.Image:
    k = FOLD_TEX_MAX / max(img.size)
    if k >= 1:
        return img
    size = (max(1, round(img.width * k)), max(1, round(img.height * k)))
    return img.resize(size, Image.Resampling.LANCZOS)


def _flat(values: Any) -> list[float]:
    return [round(float(v), 4) for point in values for v in point]


def _part_data(part: FoldedPart, radius: float) -> dict[str, Any]:
    meshes, strips = build_meshes(part, radius)
    by_id = {m.panel: m for m in meshes}
    order = [part.plan.root, *(j.child for j in part.joints)]
    index = {pid: i for i, pid in enumerate(order)}
    panels: list[dict[str, Any]] = []
    joints = {j.child: j for j in part.joints}
    for pid in order:
        mesh = by_id[pid]
        j = joints.get(pid)
        panels.append(
            {
                "id": pid,
                "parent": index[j.parent] if j else -1,
                "origin": list(j.origin) if j else None,
                "axis": list(j.axis) if j else None,
                "keys": [list(k) for k in j.keys] if j else [],
                "layer": j.layer if j else 0.0,
                # 内侧板的插脚穿过底板后比底面长（刀版尺寸如此）：放到地面时不把盒子顶起来
                "below_ground": part.dl.panel(pid).role.value == "inner_wall",
                "vertices": _flat(mesh.vertices),
                "triangles": [i for tri in mesh.triangles for i in tri],
                "rings": [_flat(r) for r in mesh.rings],
            }
        )
    return {
        "caliper": part.dl.caliper_mm,
        "paper": list(paper_rgb(part.dl)),
        "panels": panels,
        "strips": [
            {
                "parent": index[s.parent],
                "child": index[s.child],
                "origin": list(s.origin),
                "along": list(s.along),
                "to_parent": list(s.to_parent),
                "half": round(s.half_width, 4),
                "crease": [round(v, 4) for v in s.crease],
                "parent_end": [round(v, 4) for v in s.parent_end],
                "child_end": [round(v, 4) for v in s.child_end],
            }
            for s in strips
        ],
    }


def _plan(q: Prepared) -> FoldPlan:
    plan = plan_for(q.dl)
    assert plan is not None
    return plan


def write_fold_scene(out: Path, job: Job, parts: list[Prepared], fingerprint: str) -> list[Path]:
    """写场景文件与贴图；返回写出的文件。旧的贴图先清掉。"""
    tex_dir = out / SCENE_DIR
    tex_dir.mkdir(parents=True, exist_ok=True)
    for old in tex_dir.glob("*.png"):
        old.unlink()
    cfg = presets()
    atl = {str(q.part): atlases(q) for q in parts}
    regions = merged_regions(parts, {k: v[2] for k, v in atl.items()})
    region_ids = list(regions)
    files: list[Path] = []
    data_parts = []
    folded = {str(q.part): build_part(q.dl, _plan(q)) for q in parts}
    multipart = parts[0].part is not None
    fold_steps = max(f.plan.steps for f in folded.values())
    divider = job.style == "divider"
    steps = 3 if divider else fold_steps + _extra_steps(job.style) if multipart else fold_steps
    if divider:
        from pacxio.mockup.regular import divider_poses

        set_poses = divider_poses({name: f.dl for name, f in folded.items()}, _pose)
    else:
        set_poses = _set_poses(job.style, folded, steps) if multipart else {}
    for q in parts:
        part = folded[str(q.part)]
        maps, m, _ = atl[str(q.part)]
        name = str(q.part) if q.part else "main"
        textures = {}
        for channel, image in maps.items():
            suffix = channel
            if channel.startswith("region:"):
                suffix = f"region_{region_ids.index(channel[7:])}"
            fn = f"{name}_{suffix}.png"
            _small(image).save(tex_dir / fn)
            files.append(tex_dir / fn)
            textures[channel] = f"{SCENE_DIR}/{fn}"
        w, h = maps["base"].size
        (px0, py0), (px1, py1) = m.pt(0, 0), m.pt(1, 1)
        sx, sy = px1 - px0, py1 - py0
        item = _part_data(part, cfg["fold"]["crease_radius_caliper"] * q.dl.caliper_mm)
        item |= {
            "id": name,
            "tex_mm": [round(w / abs(sx), 3), round(h / abs(sy), 3)],
            "uv": [sx / w, px0 / w, sy / h, py0 / h],
            "textures": textures,
            "poses": [] if divider else set_poses.get(str(q.part)) or _single_poses(part),
        }
        if divider:
            for i in range(1, int(q.dl.style_meta["quantity"]) + 1):
                ident = f"{q.part}_{i}"
                data_parts.append(item | {"id": ident, "poses": set_poses[ident]})
        else:
            data_parts.append(item)
    first = folded[str(parts[0].part)].plan
    open_step = steps - 1 if multipart else first.open_step
    data = {
        "version": FOLD_SCENE_VERSION,
        "renderer_version": RENDERER_VERSION,
        "fingerprint": fingerprint,
        "steps": steps,
        "ground_step": first.ground_step,
        "states": {
            "flat": {"t": 0.0},
            "open": {"t": open_step / steps},
            "closed": {"t": 1.0},
        },
        "regions": list(regions.values()),
        "material_version": cfg["version"],
        "relief_height_mm": cfg["relief_height_mm"],
        "relief_edge_mm": cfg["relief_edge_mm"],
        "relief_cavity": cfg["relief_cavity"],
        "parts": data_parts,
    }
    scene = out / SCENE_FILE
    scene.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
    return [scene, *files]


__all__ = ["FOLD_SCENE_VERSION", "FOLD_TEX_MAX", "foldable", "write_fold_scene"]
