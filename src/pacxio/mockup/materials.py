"""3D 独立底色与材质遮罩；不烘焙高光。所有通道复用图文的裁切、拼底和多部件映射。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import numpy as np
from PIL import Image, ImageDraw
from shapely.geometry.base import BaseGeometry

from pacxio.attachments import PLATING_RGB, split_rim
from pacxio.export.canvas import _Map
from pacxio.geometry.bridge import paint_order
from pacxio.mockup.art import (
    MOCKUP_DPI,
    _geom_mask,
    _mask,
    _paint_window,
    face_textures,
)
from pacxio.mockup.scene import build_scene, scene_textures
from pacxio.mockup.scene3d import State
from pacxio.model.job import Job
from pacxio.model.profile import profile_dirs

if TYPE_CHECKING:  # 闭源（）：只在服务器上有，这里只用作类型
    from pacxio_pro.layout.build import LayoutResult
    from pacxio_pro.pipeline import Prepared

RELIEF_STEPS = 8  # 斜边分几层叠出来：层够多，坡才是平滑的直坡


def presets() -> dict[str, Any]:
    path = next(
        p / "render-materials.json"
        for p in profile_dirs()
        if (p / "render-materials.json").is_file()
    )
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _relief(size: tuple[int, int], geom: BaseGeometry, m: _Map, width_mm: float) -> Image.Image:
    """击凸 / 压凹的高度遮罩：沿轮廓一圈宽 width_mm 的直坡（里外各一半），里面是平顶。

    把轮廓往外、往里偏移几层叠起来，坡宽处处一样，细笔画也是平顶；
    高斯模糊会把比坡窄的笔画糊成圆鼓的一条，看起来不规整（2026-10-24 用户反馈）。"""
    acc = np.zeros((size[1], size[0]), dtype=np.float32)
    for i in range(RELIEF_STEPS):
        layer = Image.new("L", size, 0)
        d = ImageDraw.Draw(layer)
        for poly in paint_order(geom.buffer(width_mm * ((i + 0.5) / RELIEF_STEPS - 0.5))):
            d.polygon([m.pt(x, y) for x, y in poly.exterior.coords], fill=255)
            for hole in poly.interiors:
                d.polygon([m.pt(x, y) for x, y in hole.coords], fill=0)
        acc += np.asarray(layer, dtype=np.float32)
    return Image.fromarray(np.round(acc / RELIEF_STEPS).astype(np.uint8), "L")


def roughness_map(layout: LayoutResult, size: tuple[int, int], m: _Map) -> Image.Image:
    """静态效果图反光用的粗糙度（0–255，L 图，和整张成品外观图同尺寸）：纸、覆膜 / 光油、局部 UV，
    值和 `atlases` 材质图的粗糙度通道一样。烫金不算（静态图里烫金的光泽已经画在颜色上）。"""
    cfg = presets()
    rough = Image.new("L", size, cfg["paper"][0])
    for s in layout.surfaces:
        rough.paste(cfg[s.treatment.type][0], (0, 0, *size), _mask(size, s, m))
    for f in layout.finishes:
        if f.type == "spot_uv":
            rough.paste(cfg["spot_uv"][0], (0, 0, *size), _mask(size, f, m))
    return rough


def atlases(q: Prepared) -> tuple[dict[str, Image.Image], _Map, list[dict[str, Any]]]:
    from pacxio_pro.export.preview import _base_image  # 闭源：只在服务器上跑（mockup 转发）
    from pacxio_pro.finishes.regions import records

    cfg = presets()
    layout, dl = q.layout, q.dl
    base = _base_image(layout, MOCKUP_DPI)
    m = _Map(layout, MOCKUP_DPI)
    mat = Image.new("RGB", base.size, tuple(cfg["paper"][:3]))
    height = Image.new("L", base.size, 128)
    holo_mask = Image.new("L", base.size, 0)
    masks = {}
    for s in layout.surfaces:
        mask = _mask(base.size, s, m)
        mat.paste(tuple(cfg[s.treatment.type][:3]), (0, 0, *base.size), mask)
        masks["region:" + s.treatment.id] = mask
    priority = {
        "white": 0,
        "foil_gold": 1,
        "foil_silver": 1,
        "foil_copper": 1,
        "foil_holo": 1,
        "spot_uv": 2,
        "emboss": 3,
        "deboss": 3,
    }
    for f in sorted(layout.finishes, key=lambda f: priority.get(f.type, 0)):
        mask = _mask(base.size, f, m)
        masks["region:" + f.id] = mask
        if f.type.startswith("foil_"):
            base.paste(tuple(cfg[f.type]["base"]), (0, 0, *base.size), mask)
            mat.paste(tuple(cfg[f.type]["material"][:3]), (0, 0, *base.size), mask)
            if f.type == "foil_holo":
                holo_mask.paste(255, (0, 0, *base.size), mask)
        elif f.type == "white":
            base.paste(tuple(cfg["white"]), (0, 0, *base.size), mask)
        elif f.type == "spot_uv":
            # 只改变透明涂层与粗糙度，保留下面的金属性/底色。
            r, metal, coat = mat.split()
            r.paste(cfg["spot_uv"][0], (0, 0, *base.size), mask)
            coat.paste(cfg["spot_uv"][2], (0, 0, *base.size), mask)
            mat = Image.merge("RGB", (r, metal, coat))
        elif f.type in ("emboss", "deboss"):
            ramp = _relief(base.size, f.world, m, cfg["relief_edge_mm"])
            height.paste(255 if f.type == "emboss" else 0, (0, 0, *base.size), ramp)
    for a in layout.attachments:  # 金属标：电镀色、金属材质、凸起（示意，）
        mask = _geom_mask(base.size, a.world, m)
        masks["region:" + a.id] = mask
        mid, light, deep = PLATING_RGB[a.spec.plating]
        base.paste(
            tuple((x + y) // 2 for x, y in zip(mid, light, strict=True)), (0, 0, *base.size), mask
        )
        mat.paste(tuple(cfg["attachment"]["field"][:3]), (0, 0, *base.size), mask)
        ramp = _relief(base.size, a.world, m, cfg["attachment"]["edge_mm"])
        height.paste(cfg["attachment"]["height"], (0, 0, *base.size), ramp)
        if not a.relief.is_empty:
            rmask = _geom_mask(base.size, a.relief, m)
            _, pattern = split_rim(a)  # 字、纹样深古铜色；宽边按底面颜色
            base.paste(deep, (0, 0, *base.size), _geom_mask(base.size, pattern, m))
            mat.paste(tuple(cfg["attachment"]["relief"][:3]), (0, 0, *base.size), rmask)
            height.paste(
                255, (0, 0, *base.size), _relief(base.size, a.relief, m, cfg["relief_edge_mm"])
            )
    for w in dl.windows:
        _paint_window(base, w, m)
    return (
        {
            "base": base,
            "material": mat,
            "height": Image.merge("RGB", (height, holo_mask, Image.new("L", base.size, 128))),
            **masks,
        },
        m,
        records(layout),
    )


def merged_regions(
    parts: list[Prepared], records_of: dict[str, list[dict[str, Any]]]
) -> dict[str, dict[str, Any]]:
    """各部件的工艺区域合成一张表（同一 id 算一项，面名带部件前缀）。"""
    out: dict[str, dict[str, Any]] = {}
    for q in parts:
        for r in records_of[str(q.part)]:
            key = r["id"]
            if key not in out:
                out[key] = {
                    "id": key,
                    "label": r["label"],
                    "type": r["type"],
                    "detail": r["detail"],
                    "panels": [],
                }
            out[key]["panels"] += [f"{q.part}.{p}" if q.part else p for p in r["panels"]]
    return out


def material_states(
    job: Job, parts: list[Prepared]
) -> tuple[dict[str, State], dict[str, Any], list[dict[str, Any]]]:
    atl = {str(q.part): atlases(q) for q in parts}
    all_regions = merged_regions(parts, {k: v[2] for k, v in atl.items()})
    states, materials = {}, {}
    cfg = presets()
    for state in ["closed", "open"] if parts[0].part else ["closed"]:
        if parts[0].part:
            dls = {str(q.part): q.dl for q in parts}
            scene = build_scene(job.style, dls, opened=state == "open")
            box = scene.box
        else:
            from pacxio.mockup.fold import build_box

            box = build_box(parts[0].dl)
        channels = ["base", "material", "height", *("region:" + key for key in all_regions)]
        mapped = {}
        for channel in channels:
            arts = {}
            for part, (maps, m, _) in atl.items():
                fallback = Image.new("L", maps["base"].size, 0)
                arts[part] = (maps.get(channel, fallback), m)
            if parts[0].part:
                textures = scene_textures(scene, arts, dls if channel == "base" else None)
                for face in box.faces:
                    src = scene.sources[face.key]
                    if not isinstance(src[1], tuple) and channel != "base":
                        color = (
                            tuple(cfg["paper"][:3])
                            if channel == "material"
                            else (128, 0, 128)
                            if channel == "height"
                            else 0
                        )
                        textures[face.key] = Image.new(
                            "RGB" if channel in ("material", "height") else "L", (8, 8), color
                        )
            else:
                image, m = arts[str(None)]
                fill = (
                    tuple(cfg["paper"][:3])
                    if channel == "material"
                    else (128, 0, 128)
                    if channel == "height"
                    else 0
                )
                textures = face_textures(image, m, box, fill=fill, cutout=channel == "base")
            mapped[channel] = textures
        states[state] = (box, mapped.pop("base"))
        materials[state] = {
            face.key: {channel: textures[face.key] for channel, textures in mapped.items()}
            for face in box.faces
        }
    return states, materials, list(all_regions.values())
