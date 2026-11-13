"""Inner-tuck mailer: shared rolled tray, independent lid/closure (C3, L0).

All clearances are initial engineering values; see C3_EDITABLE_MAILER_ENGINE.md.
No standard number or physical-fold approval is claimed.
"""

from __future__ import annotations

import math
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from pacxio.geometry import Path, Segment, Vec
from pacxio.messages import t as translate
from pacxio.model.common import Issue
from pacxio.model.dieline import Dieline, Edge, PanelRole
from pacxio.model.profile import Material, MaterialType, Profile, load_profile
from pacxio.styles.corrugated import Up, assemble, corrugated_only, crease, flute_advisory
from pacxio.styles.fefco_0427 import (
    EAR_FREE_MIN_MM,
    EAR_LID_GAP_MM,
    LUG_SPACING_MM,
    MAX_H_MM,
    MAX_LW_MM,
    MIN_H_MM,
    MIN_H_T_RATIO,
    MIN_LW_MM,
    MailDims,
    MailerParams,
    MailerStyle,
    _chain,
    _mirror,
    _rounded,
)
from pacxio.styles.rte import _ordered, _rect

# 卷边宽 3t + 0.5：夹层 = 卷边宽 − 2t（内外侧板各一层），要放下前后板的耳朵（一层 t）再留 0.5。
# 0.1.0 是 2t + 0.5，夹层只有 0.5 mm，S06 折叠检查报耳朵和内侧板叠 t − 0.5（2026-11-04 改）。
ROLL_GAP_T, ROLL_EXTRA_MM = 3.0, 0.5
MIN_CLEARANCE_MM, MAX_CLEARANCE_MM = 0.2, 5.0
BOTTOM_MARGIN_MM = 1.0
MIN_TUCK_WIDTH_MM, MIN_TUCK_LEN_MM, MIN_DUST_MM = 20.0, 8.0, 5.0
DEFAULT_TUCK_INSET_MM, DEFAULT_RADIUS_MM = 2.0, 4.0
DEFAULT_FLAP_MAX_MM = 25.0
COORD_DECIMALS = 8  # 0.00000001 mm: shared mirror endpoints must compare identically.
BODY_IDS = frozenset(MailerStyle.panel_order[:13])


def _canonical_coordinates(dl: Dieline) -> Dieline:
    """Remove arithmetic-only mirror drift after normalization, only for this style."""

    def point(v: Vec) -> Vec:
        return Vec(round(v.x, COORD_DECIMALS), round(v.y, COORD_DECIMALS))

    def segment(s: Segment) -> Segment:
        return Segment(point(s.start), point(s.end), s.bulge)

    return dl.model_copy(
        update={
            "panels": [
                p.model_copy(
                    update={"polygon": Path([segment(s) for s in p.polygon.segments], closed=True)}
                )
                for p in dl.panels
            ],
            "edges": [
                e.model_copy(update={"segments": [segment(s) for s in e.segments]})
                for e in dl.edges
            ],
        }
    )


class InnerTuckParams(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    L: float = Field(gt=0, description="Length, front width mm")
    W: float = Field(gt=0, description="Width, front-to-back depth mm")
    H: float = Field(gt=0, description="Height mm")
    dim_basis: Literal["inner", "outer"] = "inner"
    roll_gap: float | None = Field(
        default=None, gt=0, description="Roll-over width; default 3t+0.5; sets lid width / inner walls"
    )
    inner_inset: float | None = Field(default=None, gt=0, description="Inner side wall end clearance; default t")
    ear_offset: float | None = Field(default=None, gt=0, description="Front/back wall end clearance; default t")
    ear_depth: float | None = Field(default=None, gt=0, description="Front/back ear depth; default 0.4Wi")
    ear_gap: float | None = Field(default=None, gt=0, description="Ear distance from the base crease; default 3")
    lug_w: float | None = Field(default=None, gt=0, description="Inner side wall lug root width; receiving hole changes with it")
    lug_len: float | None = Field(default=None, gt=0, description="Inner side wall lug length; default 2.5t")
    slot_depth: float | None = Field(
        default=None, gt=0, description="Base receiving hole depth; sets the interrupted base crease"
    )
    lid_side_clearance: float = Field(
        default=0.5, ge=MIN_CLEARANCE_MM, description="Clearance between lid side and the inner edge of the roll-over"
    )
    front_clearance: float = Field(
        default=0.5, ge=MIN_CLEARANCE_MM, description="Clearance between the tuck and the back of the front wall; sets lid depth"
    )
    tuck_len: float | None = Field(default=None, gt=0, description="Inner tuck depth; default min(25, 0.55Hi)")
    tuck_side_inset: float = Field(
        default=DEFAULT_TUCK_INSET_MM, ge=0.5, description="Tuck inset from the lid sides"
    )
    tuck_radius: float | None = Field(
        default=None, ge=0, description="Tuck free corner radius (a real cut-line parameter)"
    )
    dust_depth: float | None = Field(
        default=None, gt=0, description="Lid side dust flap depth; default min(25, 0.5Hi)"
    )
    dust_taper_deg: float = Field(default=20, ge=0, le=45, description="Dust flap end bevel, degrees")
    dust_radius: float | None = Field(default=None, ge=0, description="Dust flap free corner radius")
    flute_dir: Literal["x", "y"] = "y"
    apply_compensation: bool = True


def _body_params(p: InnerTuckParams, m: Material) -> MailerParams:
    names = (
        "L",
        "W",
        "H",
        "dim_basis",
        "roll_gap",
        "inner_inset",
        "ear_offset",
        "ear_depth",
        "ear_gap",
        "lug_w",
        "lug_len",
        "slot_depth",
        "flute_dir",
        "apply_compensation",
    )
    values = p.model_dump(include=set(names))
    if p.roll_gap is None:
        values["roll_gap"] = ROLL_GAP_T * m.caliper_mm + ROLL_EXTRA_MM
    return MailerParams(**values)


class TuckDims:
    def __init__(self, p: InnerTuckParams, m: Material, profile: Profile):
        self.body = d = MailDims(_body_params(p, m), m, profile)
        # Only this new instance changes; existing 0427 generation is untouched.
        d.C0 += p.lid_side_clearance
        d.C1 -= p.lid_side_clearance
        self.lid_depth = d.bw - d.t - p.front_clearance
        self.root = d.Lb
        self.tip = self.root + self.lid_depth
        # 防尘翼根部止于前壁内侧（让 front_clearance）；外包片款的盖更深，盖要越过前壁，防尘翼不跟着
        self.dust_tip = self.tip
        self.t0, self.t1 = d.C0 + p.tuck_side_inset, d.C1 - p.tuck_side_inset
        self.tuck_len = (
            p.tuck_len if p.tuck_len is not None else min(DEFAULT_FLAP_MAX_MM, 0.55 * d.Hi)
        )
        self.tuck_r = (
            p.tuck_radius
            if p.tuck_radius is not None
            else min(DEFAULT_RADIUS_MM, self.tuck_len / 2)
        )
        self.dust_depth = (
            p.dust_depth if p.dust_depth is not None else min(DEFAULT_FLAP_MAX_MM, 0.5 * d.Hi)
        )
        self.dust_r = (
            p.dust_radius
            if p.dust_radius is not None
            else min(DEFAULT_RADIUS_MM, self.dust_depth / 2)
        )
        self.taper = math.tan(math.radians(p.dust_taper_deg))


def _dust(q: TuckDims) -> list[Segment]:
    x, dx = q.body.C1, q.dust_depth
    return _rounded(
        [
            Vec(x, q.root),
            Vec(x + dx, q.root + dx * q.taper),
            Vec(x + dx, q.dust_tip - dx * q.taper),
            Vec(x, q.dust_tip),
        ],
        {1: q.dust_r, 2: q.dust_r},
    )


def _cap(q: TuckDims) -> list[Segment]:
    y = q.tip + q.tuck_len
    return _rounded(
        [Vec(q.t1, q.tip), Vec(q.t1, y), Vec(q.t0, y), Vec(q.t0, q.tip)], {1: q.tuck_r, 2: q.tuck_r}
    )


class InnerTuckMailerStyle:
    id: ClassVar[str] = "mailer_tuck"
    name_zh: ClassVar[str] = "内插舌飞机盒"
    version: ClassVar[str] = "0.2.0"
    refs: ClassVar[dict[str, str]] = {}
    material_types: ClassVar[frozenset[MaterialType]] = frozenset({"corrugated"})
    panel_order: ClassVar[tuple[str, ...]] = MailerStyle.panel_order[:-2]

    def param_schema(self) -> type[BaseModel]:
        return InnerTuckParams

    def validate(
        self, params: Any, material: Material, profile: Profile | None = None
    ) -> list[Issue]:
        p: InnerTuckParams = params
        issues = corrugated_only(self.id, material)
        if issues:
            return issues

        def rng(name: str, value: float, lo: float, hi: float) -> None:
            if not lo - 1e-9 <= value <= hi + 1e-9:
                issues.append(
                    Issue(
                        id="S03",
                        message=translate(
                            "{parameter}={value:.2f} is outside the provisional range {minimum:.2f}–{maximum:.2f}",
                            parameter=name,
                            value=value,
                            minimum=lo,
                            maximum=hi,
                        ),
                        fix=translate(
                            "Set {parameter} to {minimum:.2f}–{maximum:.2f}. Increase the box dimensions if needed.",
                            parameter=name,
                            minimum=lo,
                            maximum=hi,
                        ),
                    )
                )

        t = material.caliper_mm
        for name, lo, hi in (
            ("L", MIN_LW_MM, MAX_LW_MM),
            ("W", MIN_LW_MM, MAX_LW_MM),
            ("H", max(MIN_H_MM, MIN_H_T_RATIO * t), MAX_H_MM),
        ):
            rng(name, getattr(p, name), lo, hi)
        if issues:
            return issues
        q = TuckDims(p, material, profile or load_profile("default"))
        d = q.body
        if min(d.Li, d.Wi, d.Hi) <= 0:
            return [
                Issue(
                    id="S03",
                    message=translate(
                        "Subtracting board caliper and rollovers leaves no positive inside cavity"
                    ),
                    fix=translate("Increase the dimensions or set dim_basis to inner"),
                )
            ]
        checks = (
            ("roll_gap", d.rg, 2 * t, 5 * t + 2),
            ("inner_inset", d.ii, 0.5 * t, 3 * t + 2),
            ("ear_offset", d.eo, 0.5 * t, d.rg - 1),
            ("ear_depth", d.ed, 15, 0.5 * d.Wi - 2),
            ("ear_gap", d.eg, 0.5 * t, min(3 * t + 2, d.hf - EAR_FREE_MIN_MM)),
            ("lug_w", d.lug_w, max(8, d.sd + 1), 0.5 * d.wall_w),
            ("lug_len", d.lug_len, t + 1, 3 * t + 5),
            ("slot_depth", d.sd, t + 1, 4 * t + 2),
            (
                "lid_side_clearance",
                p.lid_side_clearance,
                MIN_CLEARANCE_MM,
                min(MAX_CLEARANCE_MM, d.hr / 4),
            ),
            (
                "front_clearance",
                p.front_clearance,
                MIN_CLEARANCE_MM,
                min(MAX_CLEARANCE_MM, d.hr / 4),
            ),
            ("tuck_side_inset", p.tuck_side_inset, 0.5, (d.C1 - d.C0 - MIN_TUCK_WIDTH_MM) / 2),
            ("tuck_len", q.tuck_len, MIN_TUCK_LEN_MM, d.hr - t - BOTTOM_MARGIN_MM),
            ("tuck_radius", q.tuck_r, 0, min(q.tuck_len, (q.t1 - q.t0) / 2)),
            ("dust_depth", q.dust_depth, MIN_DUST_MM, d.hr - t - BOTTOM_MARGIN_MM),
            ("dust_radius", q.dust_r, 0, q.dust_depth / 2),
        )
        for check in checks:
            rng(*check)
        need = len(d.lug_y) * d.lug_w + (len(d.lug_y) + 1) * LUG_SPACING_MM
        rng("插脚与间距总宽", need, 0, d.wall_w)
        free = q.lid_depth - 2 * q.dust_depth * q.taper
        tangent = q.dust_r / math.tan(math.radians(90 + p.dust_taper_deg) / 2)
        rng("防尘翼自由直边", free - 2 * tangent, BOTTOM_MARGIN_MM, q.lid_depth)
        return issues

    def advisories(
        self, params: Any, material: Material, profile: Profile | None = None
    ) -> list[Issue]:
        return flute_advisory(
            material,
            {"E", "B"},
            translate("Tuck-in mailer. Verify all closure parameters with a physical sample."),
        )

    def _geometry(
        self, p: InnerTuckParams, m: Material, profile: Profile
    ) -> tuple[TuckDims, Path, dict[str, tuple[PanelRole, Path]], list[Edge]]:
        q = TuckDims(p, m, profile)
        d, body = q.body, MailerStyle()
        # Reuse only the rolled tray panels/slots. Lid and closure are new geometry.
        polys = {pid: value for pid, value in body._panels(d, False).items() if pid in BODY_IDS}
        dust, cap = _dust(q), _cap(q)
        polys |= {
            "top_lid": (PanelRole.LID, _rect(d.C0, q.root, d.C1, q.tip)),
            "dust_la": (
                PanelRole.DUST_FLAP,
                Path([*dust, Segment(dust[-1].end, dust[0].start)], closed=True),
            ),
            "dust_lb": (
                PanelRole.DUST_FLAP,
                Path(_mirror([*dust, Segment(dust[-1].end, dust[0].start)], d.bl), closed=True),
            ),
            "top_tuck": (
                PanelRole.TUCK,
                Path([*cap, Segment(cap[-1].end, cap[0].start)], closed=True),
            ),
        }
        xr = d.bl + d.ho
        xe = xr + d.rg + d.hin
        right = (
            _chain(
                [
                    Vec(d.F1, -d.hf),
                    Vec(d.F1 + d.ed, -d.hf),
                    Vec(d.F1 + d.ed, -d.eg),
                    Vec(d.F1, -d.eg),
                    Vec(d.F1, 0),
                    Vec(d.bl, 0),
                    Vec(xr, 0),
                    Vec(xr + d.rg, d.ii),
                    Vec(xe, d.ii),
                    *body._lugs(d, xe),
                    Vec(xe, d.bw - d.ii),
                    Vec(xr + d.rg, d.bw - d.ii),
                    Vec(xr, d.bw),
                    Vec(d.bl, d.bw),
                    Vec(d.X1, d.bw),
                    Vec(d.X1, d.bw + d.eg),
                    Vec(d.X1 + d.ed, d.bw + d.eg),
                    Vec(d.X1 + d.ed, q.root - EAR_LID_GAP_MM),
                    Vec(d.X1, q.root - EAR_LID_GAP_MM),
                    Vec(d.C1, q.root - EAR_LID_GAP_MM),
                    Vec(d.C1, q.root),
                ]
            )
            + dust
        )
        outline = Path(
            [
                *_chain([Vec(d.F0, -d.hf), Vec(d.F1, -d.hf)]),
                *right,
                *_chain([right[-1].end, cap[0].start]),
                *cap,
                *_chain([cap[-1].end, Vec(d.C0, q.tip)]),
                *_mirror(right, d.bl),
            ],
            closed=True,
        )
        creases = [e for e in body._creases(d) if set(e.panels) <= BODY_IDS]
        creases += [
            crease("crease_back_top_lid", (d.C0, q.root), (d.C1, q.root), ["back", "top_lid"]),
            crease("crease_top_lid_dust_la", (d.C1, q.root), (d.C1, q.tip), ["top_lid", "dust_la"]),
            crease("crease_top_lid_dust_lb", (d.C0, q.root), (d.C0, q.tip), ["top_lid", "dust_lb"]),
            crease("crease_top_lid_tuck", (q.t0, q.tip), (q.t1, q.tip), ["top_lid", "top_tuck"]),
        ]
        return q, outline, polys, creases

    def expected_panel_sizes(
        self, params: Any, material: Material, profile: Profile
    ) -> dict[str, tuple[float, float]]:
        _, _, polys, _ = self._geometry(params, material, profile)
        return {
            pid: (poly.bbox()[2] - poly.bbox()[0], poly.bbox()[3] - poly.bbox()[1])
            for pid, (_, poly) in polys.items()
        }

    def generate(self, params: Any, material: Material, profile: Profile) -> Dieline:
        p: InnerTuckParams = params
        q, outline, polys, creases = self._geometry(p, material, profile)
        d = q.body
        ups: dict[str, Up] = {
            "front": "down",
            "side_a": "right",
            "side_b": "left",
            "top_lid": "down",
            "top_tuck": "down",
            "side_a_in": "left",
            "side_b_in": "right",
        }
        return _canonical_coordinates(
            assemble(
                style_id=self.id,
                params=p,
                material=material,
                profile=profile,
                outline=outline,
                polys=_ordered(polys, self.panel_order),
                creases=creases,
                inner_cuts=MailerStyle()._slots(d),
                ups=ups,
                provisional=set(polys),
                meta={
                    "name_zh": self.name_zh,
                    "refs": self.refs,
                    "version": self.version,
                    "compensation_applied": p.apply_compensation,
                    "layout": "tray",
                    "lid_between_rims": True,
                    "front_is_tuck": False,
                    "closure": "inner_tuck",
                    "open_only": ["top_tuck"],
                    "faces": {
                        "front": "front",
                        "back": "back",
                        "side_a": "side_a",
                        "side_b": "side_b",
                        "top": "top_lid",
                        "bottom": "base",
                    },
                    "flat_stacks": [
                        {"faces": ["top_lid", "top_tuck"], "joined": True},
                        {"faces": ["back", "base"], "joined": True},
                        {"faces": ["front"], "joined": False},
                        {"faces": ["side_b", "side_a"], "joined": False},
                    ],
                    "dims": {
                        "Li": d.Li,
                        "Wi": d.Wi,
                        "Hi": d.Hi,
                        "base_l": d.bl,
                        "base_w": d.bw,
                        "wall_h": d.hr,
                        "lid_w": d.C1 - d.C0,
                        "lid_depth": q.lid_depth,
                        "roll_gap": d.rg,
                        "tuck_len": q.tuck_len,
                        "tuck_w": q.t1 - q.t0,
                        "tuck_radius": q.tuck_r,
                        "front_clearance": p.front_clearance,
                        "dust_depth": q.dust_depth,
                    },
                    "flute_dir": p.flute_dir,
                    "flute_note": f"Flute along the flat's {p.flute_dir} axis (provisional)",
                    "print_note": "Offset print laminated to board; back unprinted; all structure parameters await a folded sample",
                    "lock": "A short round-cornered tuck goes inside the front wall and holds by friction; no outer tuck ears; inner side wall lugs go into base holes",
                    "assumptions": [
                        "L0 工程初值，尚未实物折样；摩擦保持力 / 运输强度未确认",
                        "卷边 3t+0.5，盖侧留让位；插舌印刷面在前壁纸背内侧 front_clearance",
                        "正面为前板；内插舌合上隐藏；三组尺寸仅作参数验证",
                    ],
                },
            )
        )
