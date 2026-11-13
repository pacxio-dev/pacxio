"""Two distinct L0 mailer closures, sharing only the rolled tray / dust shell.

See . Defaults are engineering starting values.
"""

from __future__ import annotations

import math
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from pacxio.geometry import Affine, Path, Segment, Vec, arc_segment
from pacxio.messages import t
from pacxio.model.common import Issue
from pacxio.model.dieline import Dieline, Edge, EdgeType, PanelRole
from pacxio.model.profile import Material, MaterialType, Profile, load_profile
from pacxio.styles.corrugated import Up, assemble, crease
from pacxio.styles.fefco_0427 import EAR_LID_GAP_MM, MailerStyle, _chain, _mirror
from pacxio.styles.mailer_tuck import (
    BODY_IDS,
    InnerTuckMailerStyle,
    InnerTuckParams,
    TuckDims,
    _canonical_coordinates,
    _dust,
)
from pacxio.styles.rte import _rect

EDGE_MARGIN_MM = 1.0
MIN_HEAD_LENGTH_MM = 3.0
MIN_HOOK_ENGAGEMENT_MM = 1.0
MIN_TAB_GAP_MM = 6.0
MIN_PERF_MM = 0.5
ShellGeometry = tuple[TuckDims, dict[str, tuple[PanelRole, Path]], list[Edge], list[Edge]]
ClosureGeometry = tuple[
    TuckDims,
    dict[str, tuple[PanelRole, Path]],
    list[Edge],
    list[Edge],
    list[Segment],
    dict[str, float],
]


class ShellParams(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    L: float = Field(gt=0, description="Length, front width mm")
    W: float = Field(gt=0, description="Width, front-to-back depth mm")
    H: float = Field(gt=0, description="Height mm")
    dim_basis: Literal["inner", "outer"] = "inner"
    roll_gap: float | None = Field(default=None, gt=0, description="Roll-over width; default 3t+0.5")
    inner_inset: float | None = Field(default=None, gt=0, description="Inner side wall end inset; default t")
    ear_offset: float | None = Field(default=None, gt=0, description="Front/back ear end inset; default t")
    ear_depth: float | None = Field(default=None, gt=0, description="Front/back ear depth; default 0.4Wi")
    ear_gap: float | None = Field(default=None, gt=0, description="Ear distance from the bottom; default 3")
    lug_w: float | None = Field(default=None, gt=0, description="Inner side wall lug root width; receiving hole follows")
    lug_len: float | None = Field(default=None, gt=0, description="Inner side wall lug length; default 2.5t")
    slot_depth: float | None = Field(default=None, gt=0, description="Depth of the side wall lug receiving hole (not the front lock slot)")
    lid_side_clearance: float = Field(default=0.5, ge=0.2, description="Gap between lid side and roll-over")
    dust_depth: float | None = Field(default=None, gt=0, description="Lid side dust flap depth")
    dust_taper_deg: float = Field(default=20, ge=0, le=45, description="Dust flap end bevel")
    dust_radius: float | None = Field(default=None, ge=0, description="Dust flap free corner radius")
    flute_dir: Literal["x", "y"] = "y"
    apply_compensation: bool = True


class FrontLockParams(ShellParams):
    apron_depth: float | None = Field(
        default=None, gt=0, description="Outer front apron depth; moves the front panel lock slot height"
    )
    front_clearance: float = Field(default=0.5, ge=0.2, le=5, description="Clearance between the back of the apron and the front panel")
    lock_neck_w: float | None = Field(
        default=None, gt=0, description="Neck width of both lock tabs; sets the front panel receiving slot width"
    )
    lock_tab_len: float | None = Field(default=None, gt=0, description="Total lock tab length")
    lock_neck_len: float | None = Field(
        default=None, gt=0, description="Neck length before the lock shoulder; must pass through all front wall layers"
    )
    lock_hook: float | None = Field(
        default=None, gt=0, description="Lock shoulder flare per side; the head must be wider than the receiving slot"
    )
    lock_spacing: float | None = Field(
        default=None, gt=0, description="Centre distance between the two lock tabs; the receiving slots move with it"
    )
    lock_clearance: float = Field(default=0.8, ge=0.2, le=2, description="Lock tab / front slot assembly allowance")
    lock_bevel: float = Field(default=1, ge=0, description="Lock head free corner chamfer")


class TearSealParams(ShellParams):
    header_depth: float | None = Field(default=None, gt=0, description="Lid front connecting flap depth")
    tear_width: float = Field(default=6, ge=3, le=15, description="Tear-off strip width; the actual perforation lines on both sides follow")
    seal_depth: float | None = Field(
        default=None, gt=0, description="Adhesive flap depth left on the front panel; default glue width + glue margins on both sides"
    )
    adhesive_width: float = Field(default=8, ge=3, le=20, description="Adhesive strip width, on the back of the adhesive flap")
    adhesive_margin: float = Field(
        default=2, ge=1, le=5, description="Adhesive strip distance from the perforation / free edge of the adhesive flap"
    )
    adhesive_end_inset: float = Field(
        default=8, ge=2, description="Adhesive strip end inset; sets the front panel ink-free receiving area"
    )
    pull_tab: float = Field(default=10, ge=3, le=30, description="Pull tab overhang on the left of the tear strip")
    perf_cut: float = Field(
        default=3, ge=MIN_PERF_MM, le=10, description="Length of each perforation cut (explicit production segments)"
    )
    perf_bridge: float = Field(
        default=2, ge=MIN_PERF_MM, le=8, description="Paper bridges kept between perforation cuts, also at both ends"
    )


def _proxy(p: ShellParams) -> InnerTuckParams:
    # Internal shell only: no irrelevant inner-tuck options in either public schema.
    return InnerTuckParams(**p.model_dump(include=set(ShellParams.model_fields)))


class LockDims:
    def __init__(self, p: FrontLockParams, q: TuckDims):
        d = q.body
        self.apron = p.apron_depth if p.apron_depth is not None else min(20, 0.32 * d.Hi)
        self.offset = d.t + p.front_clearance
        self.neck_w = (
            p.lock_neck_w if p.lock_neck_w is not None else max(10, min(24, 0.1 * (d.C1 - d.C0)))
        )
        self.length = p.lock_tab_len if p.lock_tab_len is not None else min(20, 0.2 * d.Wi)
        self.neck_len = (
            p.lock_neck_len
            if p.lock_neck_len is not None
            else self.offset + d.t + p.lock_clearance + EDGE_MARGIN_MM
        )
        self.hook = (
            p.lock_hook
            if p.lock_hook is not None
            else (d.t + 2 * p.lock_clearance + MIN_HOOK_ENGAGEMENT_MM) / 2
        )
        self.spacing = p.lock_spacing if p.lock_spacing is not None else 0.44 * (d.C1 - d.C0)
        self.centres = ((d.C0 + d.C1 - self.spacing) / 2, (d.C0 + d.C1 + self.spacing) / 2)
        self.head_w = self.neck_w + 2 * self.hook
        self.slot_h = d.t + p.lock_clearance
        self.slot_w = self.neck_w + self.slot_h + p.lock_clearance
        self.slot_z = d.hr - self.apron + d.t / 2


def _right(q: TuckDims) -> list[Segment]:
    d, body = q.body, MailerStyle()
    xr = d.bl + d.ho
    xe = xr + d.rg + d.hin
    return (
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
        + _dust(q)
        + _chain([Vec(d.C1, q.dust_tip), Vec(d.C1, q.tip)])
    )


def _shell(p: ShellParams, m: Material, profile: Profile, extra_depth: float) -> ShellGeometry:
    q = TuckDims(_proxy(p), m, profile)
    d, body = q.body, MailerStyle()
    q.lid_depth = d.bw + extra_depth
    q.tip = (
        q.root + q.lid_depth
    )  # 盖越过前壁接外包片；q.dust_tip 留在前壁内侧（S06：0.1.0 防尘翼前端顶进前壁）
    polys = {pid: value for pid, value in body._panels(d, False).items() if pid in BODY_IDS}
    dust = _dust(q)
    dust_poly = Path([*dust, Segment(dust[-1].end, dust[0].start)], closed=True)
    polys |= {
        "top_lid": (PanelRole.LID, _rect(d.C0, q.root, d.C1, q.tip)),
        "dust_la": (PanelRole.DUST_FLAP, dust_poly),
        "dust_lb": (PanelRole.DUST_FLAP, Path(_mirror(dust_poly.segments, d.bl), closed=True)),
    }
    creases = [e for e in body._creases(d) if set(e.panels) <= BODY_IDS]
    creases += [
        crease("crease_back_top_lid", (d.C0, q.root), (d.C1, q.root), ["back", "top_lid"]),
        crease(
            "crease_top_lid_dust_la", (d.C1, q.root), (d.C1, q.dust_tip), ["top_lid", "dust_la"]
        ),
        crease(
            "crease_top_lid_dust_lb", (d.C0, q.root), (d.C0, q.dust_tip), ["top_lid", "dust_lb"]
        ),
    ]
    return q, polys, creases, body._slots(d)


def _tab(cx: float, y: float, k: LockDims, bevel: float) -> list[Segment]:
    n, h = k.neck_w / 2, k.head_w / 2
    return _chain(
        [
            Vec(cx + n, y),
            Vec(cx + n, y + k.neck_len),
            Vec(cx + h, y + k.neck_len),
            Vec(cx + h, y + k.length - bevel),
            Vec(cx + h - bevel, y + k.length),
            Vec(cx - h + bevel, y + k.length),
            Vec(cx - h, y + k.length - bevel),
            Vec(cx - h, y + k.neck_len),
            Vec(cx - n, y + k.neck_len),
            Vec(cx - n, y),
        ]
    )


def _perf(
    eid: str, x0: float, x1: float, y: float, cut: float, bridge: float, panels: list[str]
) -> Edge:
    segments = []
    x = x0 + bridge
    while x + cut <= x1 - bridge + 1e-9:
        segments.append(Segment(Vec(x, y), Vec(x + cut, y)))
        x += cut + bridge
    return Edge(id=eid, type=EdgeType.PERF, segments=segments, panels=panels)


class FrontLockMailerStyle:
    id: ClassVar[str] = "mailer_front_lock"
    name_zh: ClassVar[str] = "前板插槽锁扣飞机盒"
    version: ClassVar[str] = "0.2.0"
    refs: ClassVar[dict[str, str]] = {}
    material_types: ClassVar[frozenset[MaterialType]] = frozenset({"corrugated"})

    def param_schema(self) -> type[BaseModel]:
        return FrontLockParams

    def validate(
        self, params: Any, material: Material, profile: Profile | None = None
    ) -> list[Issue]:
        p: FrontLockParams = params
        profile = profile or load_profile("default")
        issues = InnerTuckMailerStyle().validate(_proxy(p), material, profile)
        if issues:
            return issues
        q = TuckDims(_proxy(p), material, profile)
        d = q.body
        k = LockDims(p, q)

        def require(ok: bool, message: str) -> None:
            if not ok:
                issues.append(
                    Issue(
                        id="S03",
                        message=message + t(" (provisional)"),
                        fix=t(
                            "Adjust the tab and slot fit, wrap depth, spacing, or box dimensions. Keep the receiving slot aligned with the mechanism."
                        ),
                    )
                )

        require(d.Hi >= 30, t("Inside height must be at least 30 mm"))
        require(10 <= k.neck_w <= 40, t("Locking tab neck width must be 10–40 mm"))
        require(
            k.head_w >= k.slot_w + MIN_HOOK_ENGAGEMENT_MM - 1e-9,
            t("The locking tab head must be at least 1 mm wider than the receiving slot"),
        )
        require(
            k.neck_len >= k.offset + d.t + p.lock_clearance + EDGE_MARGIN_MM - 1e-9,
            t("The locking shoulder does not reach past the inside of the front wall"),
        )
        require(
            k.neck_len + MIN_HEAD_LENGTH_MM <= k.length <= d.Wi - EDGE_MARGIN_MM,
            t("The locking tab must fit its neck and head without hitting the back wall"),
        )
        require(
            0 <= p.lock_bevel <= min(k.hook, (k.length - k.neck_len) / 2),
            t("The locking head bevel is too large"),
        )
        require(
            k.head_w + MIN_TAB_GAP_MM <= k.spacing <= d.C1 - d.C0 - k.head_w - MIN_TAB_GAP_MM,
            t("The two locking tab heads do not fit or overlap"),
        )
        require(
            k.slot_z - k.slot_h / 2 >= EDGE_MARGIN_MM
            and k.slot_z + k.slot_h / 2 <= d.hf - EDGE_MARGIN_MM,
            t(
                "The front locking slots must fit inside the front wall with at least 1 mm clearance at the top and bottom"
            ),
        )
        require(
            k.centres[0] - k.slot_w / 2 >= d.F0 + EDGE_MARGIN_MM
            and k.centres[1] + k.slot_w / 2 <= d.F1 - EDGE_MARGIN_MM,
            t("The front locking slots extend beyond the left or right edge of the front wall"),
        )
        return issues

    def advisories(
        self, params: Any, material: Material, profile: Profile | None = None
    ) -> list[Issue]:
        return [
            Issue(
                id="S04",
                message=t(
                    "The front lock uses provisional L0 dimensions. Tab insertion depends on board flexibility. Retention has yet to be checked on a physical sample."
                ),
                fix=t(
                    "Use a 1:1 sample to check how the tab inserts and engages and how the board springs back"
                ),
            )
        ]

    def _geometry(self, params: Any, m: Material, profile: Profile) -> ClosureGeometry:
        p: FrontLockParams = params
        q, polys, creases, cuts = _shell(p, m, profile, m.caliper_mm + p.front_clearance)
        d, k = q.body, LockDims(p, q)
        y = q.tip + k.apron
        polys["top_tuck"] = (PanelRole.TUCK, _rect(d.C0, q.tip, d.C1, y))
        creases.append(
            crease("crease_top_lid_tuck", (d.C0, q.tip), (d.C1, q.tip), ["top_lid", "top_tuck"])
        )
        boundary = _chain([Vec(d.C1, q.tip), Vec(d.C1, y)])
        cursor = Vec(d.C1, y)
        for pid, cx in reversed(list(zip(("lock_tab_b", "lock_tab_a"), k.centres, strict=True))):
            tab = _tab(cx, y, k, p.lock_bevel)
            boundary += _chain([cursor, tab[0].start]) + tab
            cursor = tab[-1].end
            polys[pid] = (
                PanelRole.TUCK,
                Path([*tab, Segment(tab[-1].end, tab[0].start)], closed=True),
            )
            creases.append(
                crease(
                    f"crease_apron_{pid}",
                    (cx - k.neck_w / 2, y),
                    (cx + k.neck_w / 2, y),
                    ["top_tuck", pid],
                )
            )
            hole = Path.rounded_rect(
                cx - k.slot_w / 2, -k.slot_z - k.slot_h / 2, k.slot_w, k.slot_h, k.slot_h / 2
            )
            cuts.append(
                Edge(
                    id=f"cut_front_{pid}",
                    type=EdgeType.CUT,
                    segments=hole.segments,
                    panels=["front"],
                )
            )
        boundary += _chain([cursor, Vec(d.C0, y), Vec(d.C0, q.tip)])
        return (
            q,
            polys,
            creases,
            cuts,
            boundary,
            {
                "apron_depth": k.apron,
                "lock_neck_w": k.neck_w,
                "lock_tab_len": k.length,
                "lock_neck_len": k.neck_len,
                "lock_hook": k.hook,
                "lock_spacing": k.spacing,
                "front_slot_w": k.slot_w,
                "front_slot_h": k.slot_h,
                "front_slot_z": k.slot_z,
                "closure_depth": k.apron,
            },
        )

    def expected_panel_sizes(
        self, params: Any, material: Material, profile: Profile
    ) -> dict[str, tuple[float, float]]:
        _, polys, *_ = self._geometry(params, material, profile)
        return {
            pid: (p.bbox()[2] - p.bbox()[0], p.bbox()[3] - p.bbox()[1])
            for pid, (_, p) in polys.items()
        }

    def generate(self, params: Any, material: Material, profile: Profile) -> Dieline:
        return _generate(self, params, material, profile)


class TearSealMailerStyle(FrontLockMailerStyle):
    id: ClassVar[str] = "mailer_tear_seal"
    name_zh: ClassVar[str] = "自封撕拉邮寄飞机盒"

    def param_schema(self) -> type[BaseModel]:
        return TearSealParams

    def validate(
        self, params: Any, material: Material, profile: Profile | None = None
    ) -> list[Issue]:
        p: TearSealParams = params
        profile = profile or load_profile("default")
        issues = InnerTuckMailerStyle().validate(_proxy(p), material, profile)
        if issues:
            return issues
        q = TuckDims(_proxy(p), material, profile)
        d = q.body
        header = p.header_depth if p.header_depth is not None else min(12, 0.18 * d.Hi)
        seal = (
            p.seal_depth if p.seal_depth is not None else p.adhesive_width + 2 * p.adhesive_margin
        )

        def require(ok: bool, message: str) -> None:
            if not ok:
                issues.append(
                    Issue(
                        id="S03",
                        message=message + t(" (provisional)"),
                        fix=t(
                            "Adjust the connecting flap, tear strip, adhesive strip, or bridge widths, or increase box height or length"
                        ),
                    )
                )

        require(
            d.Hi >= 35,
            t("The peel-and-seal tear-strip style requires at least 35 mm inside height"),
        )
        require(header >= 4, t("The front lid connector must be at least 4 mm deep"))
        require(
            header + p.tear_width + seal <= d.hr - d.t - EDGE_MARGIN_MM,
            t("Total closure flap depth exceeds the usable front-wall height"),
        )
        require(
            seal >= p.adhesive_width + 2 * p.adhesive_margin,
            t("The adhesive flap is too small for the adhesive strip and both margins"),
        )
        require(
            p.adhesive_end_inset * 2 + 20 <= d.C1 - d.C0,
            t(
                "The adhesive strip must retain at least 20 mm of usable length after both end insets"
            ),
        )
        require(
            p.pull_tab >= p.tear_width / 2 + MIN_PERF_MM,
            t("The pull tab is too short to accommodate the rounded tear-strip end"),
        )
        require(
            d.C1 - d.C0 >= 3 * (p.perf_cut + p.perf_bridge),
            t("The perforation must fit at least three cut-and-bridge cycles"),
        )
        return issues

    def advisories(
        self, params: Any, material: Material, profile: Profile | None = None
    ) -> list[Issue]:
        return [
            Issue(
                id="S04",
                message=t(
                    "The peel-and-seal closure uses provisional L0 dimensions. Adhesive, release liner, perforation bridges, bond strength, and tear strength have yet to be tested on a physical sample."
                ),
                fix=t(
                    "Confirm the adhesive and ink-free areas, perforation cuts, and bridges with the factory, then fold a sample. Return shipping would require a separate adhesive strip."
                ),
            )
        ]

    def _geometry(self, params: Any, m: Material, profile: Profile) -> ClosureGeometry:
        p: TearSealParams = params
        q, polys, creases, cuts = _shell(p, m, profile, m.caliper_mm)
        d = q.body
        header = p.header_depth if p.header_depth is not None else min(12, 0.18 * d.Hi)
        seal = (
            p.seal_depth if p.seal_depth is not None else p.adhesive_width + 2 * p.adhesive_margin
        )
        y1, y2 = q.tip + header, q.tip + header + p.tear_width
        y3 = y2 + seal
        r = p.tear_width / 2
        centre = Vec(d.C0 - p.pull_tab + r, (y1 + y2) / 2)
        pull = [
            *_chain([Vec(d.C0, y2), Vec(centre.x, y2)]),
            arc_segment(centre, r, math.pi / 2, 3 * math.pi / 2),
            *_chain([Vec(centre.x, y1), Vec(d.C0, y1)]),
        ]
        band = [
            *_chain([Vec(d.C1, y1), Vec(d.C1, y2), Vec(d.C0, y2)]),
            *pull,
            Segment(Vec(d.C0, y1), Vec(d.C1, y1)),
        ]
        polys |= {
            "top_tuck": (PanelRole.TUCK, _rect(d.C0, q.tip, d.C1, y1)),
            "tear_strip": (PanelRole.TUCK, Path(band, closed=True)),
            "seal_pad": (PanelRole.TUCK, _rect(d.C0, y2, d.C1, y3)),
        }
        creases += [
            crease("crease_top_lid_tuck", (d.C0, q.tip), (d.C1, q.tip), ["top_lid", "top_tuck"]),
            _perf(
                "perf_tear_upper",
                d.C0,
                d.C1,
                y1,
                p.perf_cut,
                p.perf_bridge,
                ["top_tuck", "tear_strip"],
            ),
            _perf(
                "perf_tear_lower",
                d.C0,
                d.C1,
                y2,
                p.perf_cut,
                p.perf_bridge,
                ["tear_strip", "seal_pad"],
            ),
        ]
        boundary = [
            *_chain([Vec(d.C1, q.tip), Vec(d.C1, y3), Vec(d.C0, y3), Vec(d.C0, y2)]),
            *pull,
            *_chain([Vec(d.C0, y1), Vec(d.C0, q.tip)]),
        ]
        return (
            q,
            polys,
            creases,
            cuts,
            boundary,
            {
                "header_depth": header,
                "tear_width": p.tear_width,
                "seal_depth": seal,
                "adhesive_width": p.adhesive_width,
                "adhesive_margin": p.adhesive_margin,
                "adhesive_end_inset": p.adhesive_end_inset,
                "pull_tab": p.pull_tab,
                "perf_cut": p.perf_cut,
                "perf_bridge": p.perf_bridge,
                "closure_depth": header + p.tear_width + seal,
            },
        )


def _generate(style: FrontLockMailerStyle, p: Any, m: Material, profile: Profile) -> Dieline:
    q, polys, creases, cuts, boundary, extra = style._geometry(p, m, profile)
    d = q.body
    right = _right(q)
    outline = Path(
        [*_chain([Vec(d.F0, -d.hf), Vec(d.F1, -d.hf)]), *right, *boundary, *_mirror(right, d.bl)],
        closed=True,
    )
    ups: dict[str, Up] = {
        "front": "down",
        "side_a": "right",
        "side_b": "left",
        "top_lid": "down",
        "top_tuck": "down",
        "side_a_in": "left",
        "side_b_in": "right",
        "tear_strip": "down",
        "seal_pad": "down",
    }
    dims = {
        "Li": d.Li,
        "Wi": d.Wi,
        "Hi": d.Hi,
        "base_l": d.bl,
        "base_w": d.bw,
        "wall_h": d.hr,
        "lid_w": d.C1 - d.C0,
        "lid_depth": q.lid_depth,
        "roll_gap": d.rg,
        **extra,
    }
    tear = isinstance(style, TearSealMailerStyle)
    exposed = ["top_tuck", "tear_strip", "seal_pad"] if tear else ["top_tuck"]
    meta = {
        "name_zh": style.name_zh,
        "refs": style.refs,
        "version": style.version,
        "layout": "tray",
        "lid_between_rims": True,
        "front_is_tuck": False,
        "closure": "tear_seal" if tear else "front_slot_lock",
        "compensation_applied": p.apply_compensation,
        "faces": {
            "front": "front",
            "back": "back",
            "side_a": "side_a",
            "side_b": "side_b",
            "top": "top_lid",
            "bottom": "base",
        },
        "front_overlays": exposed,
        "open_only": [],
        "dims": dims,
        "flute_dir": p.flute_dir,
        "flute_note": f"Flute along the flat's {p.flute_dir} axis (provisional)",
        "print_note": "One-side offset print laminated to board; L0 engineering starting values, confirm structure and process with a folded sample",
        "lock": "The adhesive strip on the back of the seal flap sticks to the printed side of the front wall; removing the tear strip breaks the closure"
        if tear
        else "The two lock tabs on the outer front apron fold into the front panel slots; the lock shoulders sit behind the front wall",
        "assumptions": [
            "独立新机构 L0，尚未实物折样 / 工厂校准，不继承旧款 L2",
            "胶条 / 齿刀工艺和撕裂强度待验证"
            if tear
            else "插锁过程有弹性变形；刚性折叠动画不代表保持力验证",
        ],
    }
    dl = assemble(
        style_id=style.id,
        params=p,
        material=m,
        profile=profile,
        outline=outline,
        polys=polys,
        creases=creases,
        inner_cuts=cuts,
        ups=ups,
        provisional=set(polys),
        meta=meta,
    )
    if tear:
        assert isinstance(p, TearSealParams)
        minx, miny, _, _ = outline.bbox()
        shift = Affine.translate(-minx, -miny)
        y2 = q.tip + extra["header_depth"] + p.tear_width
        glue = _rect(
            d.C0 + p.adhesive_end_inset,
            y2 + p.adhesive_margin,
            d.C1 - p.adhesive_end_inset,
            y2 + p.adhesive_margin + p.adhesive_width,
        )
        target = glue.transformed(Affine.translate(0, -q.tip - d.hr))
        dl.glue_areas = [glue.transformed(shift)]
        dl.zones.no_ink = [target.transformed(shift)]
        dl.style_meta["closure_process"] = {
            "source_panel": "seal_pad",
            "source_side": "reverse",
            "target_panel": "front",
            "target_side": "printed",
            "note": "One strip of double-sided tape / release liner on the back of seal_pad sticks to the ink-free receiving area on the printed side of front; Glue_Area / GLUE shows the adhesive on the back, not auto-lock gluing and not a cut line. PERF is split into cuts / bridges; the perforating rule and adhesive type must be confirmed by the factory (provisional)",
        }
    return _canonical_coordinates(dl)
