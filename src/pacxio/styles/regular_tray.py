"""C2 single-wall corner trays and a connected folding-carton lid (L0)."""

import math
from dataclasses import dataclass
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from pacxio.geometry import Affine, Path, Segment, Vec, arc_segment
from pacxio.messages import t as translate
from pacxio.model.common import Issue
from pacxio.model.dieline import Dieline, Edge, EdgeType, PanelRole
from pacxio.model.profile import Material, MaterialType, Profile, load_profile
from pacxio.styles.compensation import coefficients
from pacxio.styles.components.assembly import stitch
from pacxio.styles.corrugated import assemble, crease

EDGE_MARGIN_MM = 1.0
GLUE_INSET_MM = 0.5
SLOT_CLEARANCE_MM = 0.6


def _run(points: list[Vec]) -> list[Segment]:
    return [Segment(a, b) for a, b in zip(points, points[1:], strict=False) if not a.is_close(b)]


class TrayParams(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    L: float = Field(gt=0)
    W: float = Field(gt=0)
    H: float = Field(gt=0)
    dim_basis: Literal["inner", "outer"] = "inner"
    corner_depth: float | None = Field(default=None, gt=0)
    corner_relief: float = Field(default=1, ge=0.5)
    corner_bevel: float = Field(default=2, ge=0)
    apply_compensation: bool = True


class LockTrayParams(TrayParams):
    tongue_len: float = Field(default=8, gt=0)
    tongue_w: float | None = Field(default=None, gt=0)
    hook: float = Field(default=1.5, gt=0)
    slot_clearance: float = Field(default=SLOT_CLEARANCE_MM, gt=0)


class HingedLidParams(TrayParams):
    tuck_len: float | None = Field(default=None, gt=0)
    tuck_r: float | None = Field(default=None, ge=0)
    dust_depth: float | None = Field(default=None, gt=0)
    dust: bool = True


@dataclass(frozen=True)
class TrayDims:
    li: float
    wi: float
    hi: float
    length: float
    w: float
    h: float
    depth: float
    neck: float
    slot_w: float


def resolve(p: TrayParams, m: Material, profile: Profile) -> TrayDims:
    t = m.caliper_mm
    k = coefficients(profile, m, p.apply_compensation)
    li, wi, hi = (p.L - 2 * t, p.W - 2 * t, p.H - t) if p.dim_basis == "outer" else (p.L, p.W, p.H)
    h = hi + k.k_height * t
    depth = (
        p.corner_depth
        if p.corner_depth is not None
        else min(20 if isinstance(p, LockTrayParams) else 15, 0.3 * wi)
    )
    neck = (
        (p.tongue_w if p.tongue_w is not None else min(10, 0.35 * (h - 2 * p.corner_relief)))
        if isinstance(p, LockTrayParams)
        else 0
    )
    return TrayDims(
        li,
        wi,
        hi,
        li + 2 * k.k_panel * t,
        wi + 2 * k.k_panel * t,
        h,
        depth,
        neck,
        max(1, 2 * t + 0.5),
    )


class GluedTrayStyle:
    id: ClassVar[str] = "tray_glued"
    name_zh: ClassVar[str] = "糊角托盘"
    version: ClassVar[str] = "0.1.0-c2"
    refs: ClassVar[dict[str, str]] = {"design": "单壁四角翼托盘，自定义 L0，待校准 / 折样"}
    material_types: ClassVar[frozenset[MaterialType]] = frozenset({"folding_carton"})

    def param_schema(self) -> type[TrayParams]:
        return TrayParams

    def validate(
        self, params: Any, material: Material, profile: Profile | None = None
    ) -> list[Issue]:
        p = params
        d = resolve(p, material, profile or load_profile("default"))
        errors: list[Issue] = []

        def bad(message: str) -> None:
            errors.append(
                Issue(
                    id="S03",
                    message=message,
                    fix=translate(
                        "Adjust the body, corner flaps, or locking parameters. These values are provisional pending a physical sample."
                    ),
                )
            )

        if material.type not in self.material_types:
            bad(translate("This single-wall tray supports folding carton board"))
        if min(d.li, d.wi) < 40 or d.hi < 15:
            bad(
                translate(
                    "Minimum inside length and width are 40 mm and minimum usable height is 15 mm (provisional)"
                )
            )
        if not 2 * GLUE_INSET_MM < d.depth < d.w / 2 - EDGE_MARGIN_MM:
            bad(
                translate(
                    "The corner flaps are too shallow or the front and rear flaps collide on the side wall"
                )
            )
        if d.h - 2 * p.corner_relief <= 2 * GLUE_INSET_MM or p.corner_bevel > min(
            d.depth / 2, (d.h - 2 * p.corner_relief) / 4
        ):
            bad(translate("The corner flap relief or bevel exceeds the available board area"))
        if isinstance(p, LockTrayParams):
            if p.tongue_len >= d.depth - EDGE_MARGIN_MM:
                bad(
                    translate(
                        "The return locking tab must be shorter than the corner flap depth with room for edge clearance"
                    )
                )
            if d.neck + 2 * p.hook >= d.h - 2 * p.corner_relief - 2 * p.corner_bevel:
                bad(
                    translate("The locking tab head is wider than the available corner flap height")
                )
            if p.slot_clearance >= 2 * p.hook:
                bad(translate("The receiving slot must be shorter than the locking tab shoulder"))
            if (
                d.depth - d.slot_w / 2 < EDGE_MARGIN_MM
                or d.h / 2 - (d.neck + p.slot_clearance) / 2 < EDGE_MARGIN_MM
            ):
                bad(translate("The receiving slot is too close to the side-wall edge"))
        if isinstance(p, HingedLidParams):
            tuck = p.tuck_len if p.tuck_len is not None else min(15, d.h / 2)
            radius = p.tuck_r if p.tuck_r is not None else min(4, tuck / 2)
            dust = p.dust_depth if p.dust_depth is not None else min(15, d.h / 2)
            if tuck >= d.h - EDGE_MARGIN_MM or radius > min(
                tuck / 2, (d.length - 2 * p.corner_relief) / 2
            ):
                bad(translate("The front tuck depth or radius is outside the allowed range"))
            if p.dust and (
                dust >= d.h - EDGE_MARGIN_MM or 2 * dust >= d.length - 2 * EDGE_MARGIN_MM
            ):
                bad(translate("The lid side flaps are too deep and overlap when folded"))
        return errors

    def expected_panel_sizes(
        self, params: Any, material: Material, profile: Profile
    ) -> dict[str, tuple[float, float]]:
        d = resolve(params, material, profile)
        return {
            "base": (d.length, d.w),
            "front": (d.length, d.h),
            "back": (d.length, d.h),
            "side_a": (d.h, d.w),
            "side_b": (d.h, d.w),
        }

    def generate(self, params: Any, material: Material, profile: Profile) -> Dieline:
        p = params
        d = resolve(p, material, profile)
        length, w, h = d.length, d.w, d.h
        r, b, depth = p.corner_relief, p.corner_bevel, d.depth
        polys = {
            "base": (PanelRole.BOTTOM, Path.rect(0, 0, length, w)),
            "front": (PanelRole.BODY, Path.rect(0, -h, length, h)),
            "back": (PanelRole.BODY, Path.rect(0, w, length, h)),
            "side_a": (PanelRole.BODY, Path.rect(length, 0, h, w)),
            "side_b": (PanelRole.BODY, Path.rect(-h, 0, h, w)),
        }
        creases = [
            crease("crease_base_front", (0, 0), (length, 0), ["base", "front"]),
            crease("crease_base_back", (0, w), (length, w), ["base", "back"]),
            crease("crease_base_side_a", (length, 0), (length, w), ["base", "side_a"]),
            crease("crease_base_side_b", (0, 0), (0, w), ["base", "side_b"]),
        ]
        cuts = []
        glue = []
        contours = {}
        lock = isinstance(p, LockTrayParams)
        for wall, sy, ybase in (("front", -1, 0), ("back", 1, w)):
            for side, sx, xbase in (("b", -1, 0), ("a", 1, length)):
                pid = f"corner_{wall}_{side}"

                def point(
                    u: float,
                    v: float,
                    xbase: float = xbase,
                    sx: float = sx,
                    ybase: float = ybase,
                    sy: float = sy,
                ) -> Vec:
                    return Vec(xbase + sx * u, ybase + sy * v)

                local = [
                    (0, r),
                    (depth - b, r),
                    (depth, r + b),
                    (depth, h - r - b),
                    (depth - b, h - r),
                    (0, h - r),
                ]
                main = Path(_run([point(*v) for v in [*local, local[0]]]), closed=True)
                contour = local
                polys[pid] = (PanelRole.DUST_FLAP if lock else PanelRole.GLUE_FLAP, main)
                creases.append(
                    crease(
                        f"crease_{pid}",
                        point(0, r).as_tuple(),
                        point(0, h - r).as_tuple(),
                        [wall, pid],
                    )
                )
                if lock:
                    low, high = h / 2 - d.neck / 2, h / 2 + d.neck / 2
                    tongue = [
                        (depth, low),
                        (depth + p.tongue_len * 0.6, low - p.hook),
                        (depth + p.tongue_len, low),
                        (depth + p.tongue_len, high),
                        (depth + p.tongue_len * 0.6, high + p.hook),
                        (depth, high),
                    ]
                    polys[f"tongue_{wall}_{side}"] = (
                        PanelRole.TUCK,
                        Path.from_points([point(*v) for v in tongue], closed=True),
                    )
                    creases.append(
                        crease(
                            f"crease_tongue_{wall}_{side}",
                            point(depth, low).as_tuple(),
                            point(depth, high).as_tuple(),
                            [pid, f"tongue_{wall}_{side}"],
                        )
                    )
                    contour = [*local[:3], *tongue, *local[3:]]
                    slot = Path.rect(
                        xbase + sx * h / 2 - (d.neck + p.slot_clearance) / 2,
                        (depth if wall == "front" else w - depth) - d.slot_w / 2,
                        d.neck + p.slot_clearance,
                        d.slot_w,
                    )
                    cuts.append(
                        Edge(
                            id=f"cut_slot_{wall}_{side}",
                            type=EdgeType.CUT,
                            segments=slot.segments,
                            panels=[f"side_{side}"],
                        )
                    )
                else:
                    # Mark a conservative rectangular glue strip entirely within the beveled flap.
                    glue.append(
                        Path.from_points(
                            [
                                point(GLUE_INSET_MM, r + b + GLUE_INSET_MM),
                                point(depth - GLUE_INSET_MM, r + b + GLUE_INSET_MM),
                                point(depth - GLUE_INSET_MM, h - r - b - GLUE_INSET_MM),
                                point(GLUE_INSET_MM, h - r - b - GLUE_INSET_MM),
                            ],
                            closed=True,
                        )
                    )
                contours[pid] = [point(*v) for v in contour]
        points = [
            Vec(0, 0),
            *contours["corner_front_b"],
            Vec(0, -h),
            Vec(length, -h),
            *reversed(contours["corner_front_a"]),
            Vec(length, 0),
            Vec(length + h, 0),
            Vec(length + h, w),
            Vec(length, w),
            *contours["corner_back_a"],
            Vec(length, w + h),
        ]
        lid_runs = []
        if isinstance(p, HingedLidParams):
            y = w + h
            tuck = p.tuck_len if p.tuck_len is not None else min(15, h / 2)
            radius = p.tuck_r if p.tuck_r is not None else min(4, tuck / 2)
            polys["lid"] = (PanelRole.LID, Path.rect(0, y, length, w))
            creases.append(crease("crease_back_lid", (0, y), (length, y), ["back", "lid"]))
            ty = y + w
            ts = [Segment(Vec(length - r, ty), Vec(length - r, ty + tuck - radius))]
            if radius:
                ts.append(
                    arc_segment(
                        Vec(length - r - radius, ty + tuck - radius), radius, 0, math.pi / 2
                    )
                )
            ts.append(Segment(Vec(length - r - radius, ty + tuck), Vec(r + radius, ty + tuck)))
            if radius:
                ts.append(
                    arc_segment(Vec(r + radius, ty + tuck - radius), radius, math.pi / 2, math.pi)
                )
            ts += [
                Segment(Vec(r, ty + tuck - radius), Vec(r, ty)),
                Segment(Vec(r, ty), Vec(length - r, ty)),
            ]
            lid_tongue = Path(ts, closed=True)
            polys["lid_tuck"] = (PanelRole.TUCK, lid_tongue)
            creases.append(
                crease("crease_lid_tuck", (r, y + w), (length - r, y + w), ["lid", "lid_tuck"])
            )
            right = [Vec(length, y), Vec(length, y + w)]
            left = [Vec(0, y + w), Vec(0, y)]
            if p.dust:
                dd = p.dust_depth if p.dust_depth is not None else min(15, h / 2)
                for side, sx, x in (("a", 1, length), ("b", -1, 0)):
                    q = [
                        Vec(x, y + r),
                        Vec(x + sx * dd, y + r + b),
                        Vec(x + sx * dd, y + w - r - b),
                        Vec(x, y + w - r),
                    ]
                    polys[f"lid_dust_{side}"] = (
                        PanelRole.DUST_FLAP,
                        Path.from_points(q, closed=True),
                    )
                    creases.append(
                        crease(
                            f"crease_lid_dust_{side}",
                            q[0].as_tuple(),
                            q[-1].as_tuple(),
                            ["lid", f"lid_dust_{side}"],
                        )
                    )
                    if side == "a":
                        right = [Vec(length, y), *q, Vec(length, y + w)]
                    else:
                        left = [Vec(0, y + w), *reversed(q), Vec(0, y)]
            # Free tongue boundary runs from right to left; its last segment is the root.
            lid_runs = [
                _run(right),
                [
                    *_run([Vec(length, y + w), Vec(length - r, y + w)]),
                    *lid_tongue.segments[:-1],
                    *_run([Vec(r, y + w), Vec(0, y + w)]),
                ],
                _run(left),
            ]
        tail = [
            Vec(0, w + h),
            *reversed(contours["corner_back_b"]),
            Vec(0, w),
            Vec(-h, w),
            Vec(-h, 0),
            Vec(0, 0),
        ]
        runs = (
            [
                _run(points),
                *lid_runs,
                _run([points[-1], *tail]),
            ]
            if not lid_runs
            else [_run(points), *lid_runs, _run(tail)]
        )
        outline = stitch(*runs)
        faces = {
            "bottom": "base",
            "front": "front",
            "back": "back",
            "side_a": "side_a",
            "side_b": "side_b",
        }
        if isinstance(p, HingedLidParams):
            faces["top"] = "lid"
        dl = assemble(
            style_id=self.id,
            params=p,
            material=material,
            profile=profile,
            outline=outline,
            polys=polys,
            creases=creases,
            inner_cuts=cuts,
            ups={"front": "down", "side_a": "right", "side_b": "left"},
            provisional=set(polys),
            meta={
                "name_zh": self.name_zh,
                "refs": self.refs,
                "version": self.version,
                "layout": "tray",
                "faces": faces,
                "paper_cutout": True,
                "accuracy_level": "L0",
                "compensation_applied": p.apply_compensation,
                "dims": {"Li": d.li, "Wi": d.wi, "Hi": d.hi, "outer": [length, w, h]},
                "assumptions": ["单层卡纸结构、转角余量、插接及方向均待校准 / 折样【初值】"],
            },
        )
        shift = Affine.translate(-outline.bbox()[0], -outline.bbox()[1])
        dl.glue_areas = [g.transformed(shift) for g in glue]
        return dl


class LockTrayStyle(GluedTrayStyle):
    id: ClassVar[str] = "tray_lock"
    name_zh: ClassVar[str] = "四角锁扣托盘"

    def param_schema(self) -> type[LockTrayParams]:
        return LockTrayParams


class HingedLidStyle(GluedTrayStyle):
    id: ClassVar[str] = "hinged_lid"
    name_zh: ClassVar[str] = "卡纸连体翻盖盒"

    def param_schema(self) -> type[HingedLidParams]:
        return HingedLidParams
