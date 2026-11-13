"""配件（）：另外做好、组装时贴到盒上的东西（第一版只有金属标）。

不印、不出工艺版：外形和凸起图案换算到面局部 / 刀版坐标，给刀版确认图、生产文件的贴标位置层、
规格书、交付包里给标牌厂的图稿、效果图、印前检查用。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shapely.geometry.base import BaseGeometry

from pacxio.geometry import affine_geom
from pacxio.messages import display_name, t
from pacxio.model.common import Issue, Result
from pacxio.model.dieline import Dieline
from pacxio.model.job import ATTACHMENT_ZH, MATERIAL_ZH, MOUNT_ZH, PLATING_ZH, Attachment

ROUND_TOL = 0.03  # 面积和同直径的圆差这么多以内、宽高差 0.2 mm 以内算圆形（尺寸写直径）
# 电镀色在确认图、效果图里的示意色（屏幕 sRGB；颜色以标牌厂色板实样为准）：底面中间色、亮色、
# 凸起图案的深色（概念图里字和纹样是深古铜色，底面是浅的哑光金属；宽边按底面画）
PLATING_RGB: dict[str, tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]]] = {
    "champagne_rose_gold": ((214, 182, 146), (242, 226, 204), (156, 104, 62)),
    "rose_gold": ((200, 140, 116), (238, 196, 176), (138, 82, 64)),
    "champagne_gold": ((212, 190, 142), (244, 230, 196), (150, 120, 70)),
    "gold": ((204, 164, 76), (246, 220, 144), (140, 100, 36)),
    "silver": ((188, 192, 200), (238, 240, 244), (118, 122, 132)),
}
RIM_TOUCH_MM = 0.3  # 凸起图案里离外形边这么近的块算"宽边"（按底面颜色画，只在效果图、确认图里区分）
CROSS_SHARE = 0.12  # 贴标位置的中心十字：每边伸出外形短边的这么多


class AttachmentError(Exception):
    def __init__(self, issue: Issue) -> None:
        super().__init__(issue.message)
        self.issue = issue


@dataclass
class AttachmentLayout:
    spec: Attachment
    local: BaseGeometry  # 外形，面局部坐标 (mm)
    world: BaseGeometry  # 外形，刀版坐标 (mm)
    relief: BaseGeometry  # 凸起图案，刀版坐标 (mm)；没给 relief 时为空
    relief_local: BaseGeometry

    @property
    def id(self) -> str:
        return self.spec.id

    @property
    def panel(self) -> str:
        return self.spec.panel


def split_rim(a: AttachmentLayout) -> tuple[BaseGeometry, BaseGeometry]:
    """凸起图案分成贴着外缘的宽边和里面的字、纹样（刀版坐标）；只用来画示意图。"""
    from shapely.ops import unary_union

    from pacxio_pro.finishes.vectorize import polygons

    edge = a.world.boundary.buffer(RIM_TOUCH_MM)
    rim = [p for p in polygons(a.relief) if p.intersects(edge)]
    pattern = [p for p in polygons(a.relief) if not p.intersects(edge)]
    return unary_union(rim) if rim else _empty(), unary_union(pattern) if pattern else _empty()


def is_round(local: BaseGeometry) -> bool:
    """外形是不是圆形（DXF 写成圆弧、尺寸写直径）。"""
    x0, y0, x1, y1 = local.bounds
    w, h = x1 - x0, y1 - y0
    return w > 0 and abs(w - h) <= 0.2 and abs(local.area / (math.pi * w * w / 4) - 1) <= ROUND_TOL


def size_text(local: BaseGeometry) -> str:
    """外形尺寸：圆形写直径，其它写宽 × 高。"""
    x0, y0, x1, y1 = local.bounds
    w, h = x1 - x0, y1 - y0
    if is_round(local):
        return t("Diameter {d} mm", d=f"{w:.1f}")
    return f"{w:.1f} × {h:.1f} mm"


def describe(a: AttachmentLayout) -> str:
    s = a.spec
    return t(
        "{kind} · {size} · {thickness} mm thick · {material} · {plating} plating · {mount}",
        kind=display_name(ATTACHMENT_ZH[s.type]), size=size_text(a.local),
        thickness=f"{s.thickness_mm:g}", material=display_name(MATERIAL_ZH[s.material]),
        plating=display_name(PLATING_ZH[s.plating]), mount=display_name(MOUNT_ZH[s.mount]),
    )


ARTWORK_BASE = "#e9d6c4"  # 给标牌厂的图稿：外形 / 底面（浅）
ARTWORK_RELIEF = "#5a3c28"  # 凸起的图案（深）


def artwork_svg(a: AttachmentLayout) -> str:
    """给标牌厂的 1:1 图稿（单位 mm）：浅色是外形 / 底面，深色是凸起的图案；不写字（尺寸、材质在规格书）。"""
    from pacxio_pro.finishes.vectorize import polygons

    x0, y0, x1, y1 = a.local.bounds
    w, h = x1 - x0, y1 - y0

    def d(g: BaseGeometry) -> str:
        out = []
        for poly in polygons(g):
            for ring in (poly.exterior, *poly.interiors):
                pts = list(ring.coords)[:-1]
                out.append("M" + " L".join(f"{x - x0:.3f},{y1 - y:.3f}" for x, y in pts) + " Z")
        return " ".join(out)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:.3f} {h:.3f}" '
        f'width="{w:.3f}mm" height="{h:.3f}mm">',
        f'<path fill="{ARTWORK_BASE}" fill-rule="evenodd" d="{d(a.local)}"/>',
    ]
    if not a.relief_local.is_empty:
        parts.append(f'<path fill="{ARTWORK_RELIEF}" fill-rule="evenodd" d="{d(a.relief_local)}"/>')
    return "\n".join([*parts, "</svg>"]) + "\n"


def _svg(job_dir: Path, a: Attachment, rel: str, what: str) -> tuple[BaseGeometry, Issue | None]:
    if "concepts" in Path(rel).parts:
        return _empty(), Issue(id="CONCEPT", message=t("Attachment {id} uses a concept image for its {kind}", id=a.id, kind=what),
                               fix=t("Use the concept as a reference to draw two SVGs, one for the outline and one for the raised artwork"))  # fmt: skip
    src = job_dir / rel
    if not src.is_file():
        return _empty(), Issue(id="ASSET", message=t("Attachment {id}: {kind} file not found: {file}", id=a.id, kind=what, file=rel),
                               fix="`attach set <job> <id> --source / --relief <SVG>`")  # fmt: skip
    if src.suffix.lower() != ".svg":
        return _empty(), Issue(id="ATTACH", message=t("Attachment {id}: the {kind} must be an SVG: {file}", id=a.id, kind=what, file=rel),
                               fix=t("Send the attachment supplier separate SVGs for the outline and raised artwork using the same artboard"))  # fmt: skip
    try:  # SVG 转外形是工艺矢量化，闭源（）：开源版这里走不到，`attach add / set` 转给云端
        from pacxio_pro.finishes.vectorize import VectorizeError, fit_into_box, trace_svg
    except ImportError:
        return _empty(), Issue(id="CLOUD", message=t("Attachment {id}: the {kind} requires cloud outline processing", id=a.id, kind=what),
                               fix=t("Run `pacxio login` and retry"))  # fmt: skip
    try:
        traced = trace_svg(src)
        return fit_into_box(traced.ink, a.box, traced.board), None
    except VectorizeError as e:
        return _empty(), e.issue


def _empty() -> BaseGeometry:
    from shapely.geometry import Polygon

    return Polygon()


def layout_attachment(a: Attachment, dl: Dieline, job_dir: Path) -> AttachmentLayout:
    try:
        panel = dl.panel(a.panel)
    except KeyError:
        raise AttachmentError(Issue(id="PANEL", message=t("Attachment {id} refers to missing panel {panel}", id=a.id, panel=a.panel),
                                    fix=t("Panels available for attachments: ") + ", ".join(p.id for p in dl.panels))) from None  # fmt: skip
    local, issue = _svg(job_dir, a, a.source, t("outline"))
    if issue is not None:
        raise AttachmentError(issue)
    if local.is_empty:
        raise AttachmentError(Issue(id="ATTACH", message=t("The outline SVG for attachment {id} is empty", id=a.id),
                                    fix=t("Use one solid shape in the outline SVG, such as a filled circle for a round badge")))  # fmt: skip
    relief_local = _empty()
    if a.relief:
        relief_local, issue = _svg(job_dir, a, a.relief, t("raised artwork"))
        if issue is not None:
            raise AttachmentError(issue)
        relief_local = relief_local.intersection(local)
    return AttachmentLayout(
        spec=a,
        local=local,
        world=affine_geom(local, panel.frame),
        relief=affine_geom(relief_local, panel.frame)
        if not relief_local.is_empty
        else relief_local,
        relief_local=relief_local,
    )


def edit_attachments(
    directory: Path, action: str, fields: dict[str, Any] | None = None, item_id: str | None = None
) -> Result:
    """`attach add / set / remove / list`：校验通过（面存在、SVG 读得出、id 不重复）才写 job.json。"""
    from pydantic import ValidationError

    from pacxio.dielines import build_parts_for
    from pacxio.jobfiles import import_asset, is_concept
    from pacxio.model.job import load_job_checked

    r = Result(command=f"attach {action}", job=str(directory))
    job, issues = load_job_checked(directory)
    if job is None:
        r.errors += issues
        r.ok, r.exit_code = False, 2
        return r
    items = list(job.attachments or [])
    if action == "list":
        r.data = {"attachments": [a.model_dump(exclude_none=True) for a in items]}
        return r
    try:
        if action in ("set", "remove") and item_id not in [a.id for a in items]:
            raise ValueError(t("Attachment {id} does not exist. Run attach list to see the IDs.", id=item_id))
        if action == "remove":
            items = [a for a in items if a.id != item_id]
        else:
            data = {k: v for k, v in (fields or {}).items() if v is not None}
            if action == "set":
                old = next(a for a in items if a.id == item_id)
                data = {**old.model_dump(), **data, "id": item_id}
            else:
                n = 1
                while f"badge{n}" in [a.id for a in items]:
                    n += 1
                data.setdefault("id", f"badge{n}")
                for key, opt in (("panel", "--panel"), ("box", "--box"), ("source", "--source")):
                    if key not in data:
                        raise ValueError(t("Missing {option}", option=opt))
            for key in ("note", "relief", "tag"):
                if data.get(key) == "":  # --note "" / --relief "" 去掉
                    data[key] = None
            for key in ("source", "relief"):
                src = (fields or {}).get(key)
                if not src:
                    continue
                if is_concept(directory, Path(src)):
                    raise AttachmentError(Issue(id="CONCEPT", message=t("A concept image cannot be used directly as attachment artwork"),
                                                fix=t("Use the concept as a reference to draw an outline SVG and a raised-artwork SVG")))  # fmt: skip
                rel = import_asset(directory, Path(src))
                if rel is None:
                    raise AttachmentError(
                        Issue(id="ASSET", message=t("Attachment file does not exist: {path}", path=src), fix=t("Check the path"))
                    )
                data[key] = rel
            new = Attachment.model_validate(data)
            items = (
                [new if a.id == item_id else a for a in items] if action == "set" else [*items, new]
            )
            item_id = new.id
        candidate = job.model_copy(update={"attachments": items or None})
        ids = [f.id for f in candidate.finishes] + [
            s.id for s in candidate.surface_treatments or []
        ]
        ids += [a.id for a in candidate.attachments or []]
        if len(ids) != len(set(ids)):
            raise ValueError(t("The attachment ID duplicates a finish, surface treatment, or attachment. Choose another --id."))
        ds, issues = build_parts_for(candidate)
        if ds is None:
            r.errors += issues
        else:
            all_panels = {p.id for p in ds.merged().panels}
            for a in candidate.attachments or []:
                if a.panel not in all_panels:
                    raise AttachmentError(Issue(id="PANEL", message=t("Unknown attachment panel {panel}", panel=a.panel),
                                                fix=t("Panels available for attachments: ") + ", ".join(sorted(all_panels))))  # fmt: skip
            for part, dl in ds.parts.items():
                for a in candidate.for_part(part).attachments or []:
                    layout_attachment(a, dl, directory)
    except AttachmentError as e:
        r.errors.append(e.issue)
    except (ValueError, ValidationError) as e:
        r.errors.append(Issue(id="ARG", message=str(e).splitlines()[0] if isinstance(e, ValidationError) else str(e),
                              fix=t("Run `pacxio attach add --help` to see the parameters")))  # fmt: skip
    if r.errors:
        r.ok, r.exit_code = False, 2
        return r
    r.artifacts = [str(candidate.save(directory))]
    r.data = {"id": item_id, "attachments": [a.model_dump(exclude_none=True) for a in items]}
    return r


__all__ = [
    "CROSS_SHARE",
    "PLATING_RGB",
    "AttachmentError",
    "AttachmentLayout",
    "artwork_svg",
    "split_rim",
    "describe",
    "edit_attachments",
    "layout_attachment",
    "size_text",
]
