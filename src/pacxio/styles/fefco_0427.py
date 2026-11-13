"""fefco_0427 飞机盒（mailer box / roll end tuck front）：插耳做法，（）。

v1（盒型版本 0.1.0-m4，插舌摩擦锁）见  节。

一片瓦楞纸板，以底板为中心的十字形；印刷面视图是从盒底外面看，所以站在正面看的左侧板 side_a
在右边（和 RTE 的 side_a / side_b 含义一致）::

               [tuck_ear_b][     top_tuck     ][tuck_ear_a]
                 [dust_lb]    [  top_lid   ]    [dust_la]
                 [ear_bb ]  [      back      ]  [ear_ba ]
    [side_b_in][rim_b][side_b]  [   base   ]  [side_a][rim_a][side_a_in]
                 [ear_fb ]  [     front      ]  [ear_fa ]

成型：前、后板两端的耳朵向内折 90°；侧板是双层——外侧板立起，内侧板沿卷边翻进盒内，把耳朵夹在两层之间，
内侧板自由边的插脚插进底板端线上的插孔；盖从后板上沿往前合上，正好卡在两条卷边中间（合上后从上面看得到
两条卷边顶），防尘翼压进盒内；插舌从盖前沿翻下来**包在前板外面**（深度 = 盒高，合上后正面看到的是插舌，
前板被挡在里面、打开盖才看得到），两端的插耳绕过前角折 90° 插进侧板的夹层，把盖锁住。不用胶、不用钉。

按 reference 的 FEFCO 0427（roll end tray with locking cover，4 份刀版，从页面上画刀版的 SVG 读坐标）校准；
它给的尺寸就是底板，这里仍按内腔尺寸算、压线补偿照配置档。全部【初值】，要折样确认。
FEFCO 的图从箱内看，这里按印刷面建模（D5）。
"""

from __future__ import annotations

import math
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field

from pacxio.geometry import Path, Segment, Vec, arc_segment
from pacxio.messages import t as translate
from pacxio.model.common import Issue
from pacxio.model.dieline import Dieline, Edge, EdgeType, PanelRole
from pacxio.model.profile import Material, MaterialType, Profile, load_profile
from pacxio.styles.compensation import coefficients
from pacxio.styles.corrugated import Up, assemble, corrugated_only, crease, flute_advisory
from pacxio.styles.rte import _ordered, _rect, _trapezoid

# ---- 【初值】全部待折样、结构顾问与工厂确认（v2 按 reference 4 份刀版的规律） ----
MIN_LW_MM, MAX_LW_MM = 60.0, 700.0  # 参考一家厂商的 0427 尺寸范围
MIN_H_MM, MIN_H_T_RATIO, MAX_H_MM = 20.0, 6.0, 250.0
ROLL_GAP_T, ROLL_GAP_EXTRA = 3.0, 0.5  # 卷边宽 3t + 0.5：夹层里放耳朵 + 插耳两层（E 楞 5）
FRONT_INSET_EXTRA_MM = 1.0  # 前板两端比后板多缩 1（reference：前板 L − 2t − 2，后板 L − 2t）
EAR_DEPTH_WI_RATIO = 0.4
EAR_DEPTH_MIN_MM = 15.0
EAR_CLEAR_MM = 2.0  # ear_depth ≤ 0.5·Wi − 2（前、后耳朵在侧板里不相撞）
EAR_GAP_MM = 3.0  # 耳朵离底板压线
EAR_LID_GAP_MM = 1.0  # 后板耳朵离盖压线
EAR_FREE_MIN_MM = 10.0  # 耳朵至少这么高
LUG_TWO_MIN_BW_MM = 120.0  # 底板进深够这么宽才放两个插脚（reference：W 100 一个，170、202 两个）
LUG_FRACTIONS = {1: (0.5,), 2: (0.3, 0.7)}  # 插脚中心在底板进深上的位置
LUG_W_RATIO = {1: 0.4, 2: 0.2}  # 插脚根宽 = min(40, 比例·进深)
LUG_W_MAX_MM, LUG_W_MIN_MM = 40.0, 8.0
LUG_LEN_T = 2.5  # 插脚长 2.5t
LUG_TIP_EXTRA_MM = 0.5  # 插脚尖两边各收 t + 0.5（梯形）
LUG_SPACING_MM = 5.0  # 插孔之间、插孔到内侧板两端至少留这么多
SLOT_DEPTH_MIN_MM, SLOT_DEPTH_T_EXTRA = 5.0, 2.0  # 插孔深 max(5, t + 2)，两头半圆
LID_FLAP_TAPER_DEG = 20.0
LID_FLAP_DEPTH_WI_RATIO = 0.8  # 防尘翼深 min(H − t, 0.8·Wi)：太深时两头斜边收不拢
LID_FLAP_R_MAX_MM, LID_FLAP_R_H_RATIO = 15.0, 0.25  # 防尘翼外角圆角 min(15, H/4)
LID_FLAP_DEPTH_MIN_MM = 10.0
TUCK_SHOULDER_MM = 1.0  # 插舌比盖宽出来的两截离盖压线 1
TUCK_CHAMFER_MM = 2.5  # 插舌自由边两角倒角
TUCK_LEN_MIN_MM = 10.0
TUCK_EAR_LEN_RATIO = 0.89  # 插耳长 ≈ 0.89 × 插耳深（沿压线）
TUCK_EAR_MIN_MM = 10.0
THUMB_NOTCH_R_MM = 12.0
USUAL_FLUTES = {"E", "B"}


class MailerParams(BaseModel):
    """飞机盒参数。可选项为 None 时按草案公式取默认值。

    lid_flap_inset、tuck_radius 只有 v1 用（盖在防尘翼那段内缩、插舌圆角），留着只为旧 job.json 读得进来。
    """

    L: float = Field(gt=0, description="Length (front width, the lid hinge side)")
    W: float = Field(gt=0, description="Width (front-to-back depth)")
    H: float = Field(gt=0, description="Height")
    dim_basis: Literal["inner", "outer"] = "inner"
    roll_gap: float | None = None
    inner_inset: float | None = None
    ear_offset: float | None = None
    ear_depth: float | None = None
    ear_gap: float | None = None
    lug_w: float | None = None
    lug_len: float | None = None
    slot_depth: float | None = None
    lid_flap_inset: float | None = Field(
        default=None, description="Legacy v1 jobs only; has no effect on the v2 dieline and is not an editable item"
    )
    lid_flap_depth: float | None = None
    lid_flap_taper_deg: float = LID_FLAP_TAPER_DEG
    tuck_inset: float | None = None
    tuck_len: float | None = None
    tuck_radius: float | None = Field(
        default=None, description="Legacy v1 jobs only; has no effect on the v2 dieline and is not an editable item"
    )
    tuck_ear_len: float | None = None
    thumb_notch: bool = False
    flute_dir: Literal["x", "y"] = "y"
    apply_compensation: bool = True


class MailDims:
    """解析后的尺寸（mm）。展开图记号：底板 x 0..bl、y 0..bw（归一化之前）。"""

    def __init__(self, p: MailerParams, material: Material, profile: Profile) -> None:
        t = self.t = material.caliper_mm
        self.rg = p.roll_gap if p.roll_gap is not None else ROLL_GAP_T * t + ROLL_GAP_EXTRA
        end = 2 * self.rg + t  # 底板两端各放下卷边和半层纸：内腔长 = 底板 − (2·rg + t)
        if p.dim_basis == "outer":  # 外长 = 底板 + 2t（外侧板在底板端线外面）
            self.Li, self.Wi, self.Hi = p.L - end - 2 * t, p.W - 2 * t, p.H - 2 * t
        else:
            self.Li, self.Wi, self.Hi = p.L, p.W, p.H
        k = coefficients(profile, material, p.apply_compensation)
        Wi, Hi = self.Wi, self.Hi
        self.bl = self.Li + k.k_panel * t + end
        self.bw = Wi + k.k_panel * t
        self.hr = Hi + k.k_height * t  # 后板、外侧板（底板压线到盖压线 / 卷边压线）
        self.ho = self.hr
        self.hf = self.hr - t  # 前板比后板低 t：盖压在前板上沿，插舌包在前板外面
        self.hin = self.hr - 1.5 * t  # 内侧板（卷边压线到插脚根）
        self.lw = self.bw + k.k_lid * t
        self.ii = p.inner_inset if p.inner_inset is not None else t
        self.eo = p.ear_offset if p.ear_offset is not None else t  # 后板两端离底板端线
        self.ed = p.ear_depth if p.ear_depth is not None else EAR_DEPTH_WI_RATIO * Wi
        self.eg = p.ear_gap if p.ear_gap is not None else EAR_GAP_MM
        n = 2 if self.bw >= LUG_TWO_MIN_BW_MM else 1
        self.lug_w = p.lug_w if p.lug_w is not None else min(LUG_W_MAX_MM, LUG_W_RATIO[n] * self.bw)
        self.lug_len = p.lug_len if p.lug_len is not None else LUG_LEN_T * t
        self.lug_c = min(t + LUG_TIP_EXTRA_MM, self.lug_w / 4)  # 插脚尖两边各收这么多
        self.lug_y = [f * self.bw for f in LUG_FRACTIONS[n]]
        self.sd = (
            p.slot_depth
            if p.slot_depth is not None
            else max(SLOT_DEPTH_MIN_MM, t + SLOT_DEPTH_T_EXTRA)
        )
        self.sw = self.lug_w  # 插孔和插脚根一样宽（插脚尖窄，插得进）
        self.lfd = (
            p.lid_flap_depth
            if p.lid_flap_depth is not None
            else min(self.hr - t, LID_FLAP_DEPTH_WI_RATIO * Wi)
        )
        self.lft = math.tan(math.radians(p.lid_flap_taper_deg))
        self.lfr = min(LID_FLAP_R_MAX_MM, LID_FLAP_R_H_RATIO * Hi)
        self.tki = p.tuck_inset if p.tuck_inset is not None else self.eo
        self.tkl = p.tuck_len if p.tuck_len is not None else self.hr
        self.ted = self.tkl - TUCK_SHOULDER_MM - TUCK_CHAMFER_MM  # 插耳深（沿压线）
        self.tel = p.tuck_ear_len if p.tuck_ear_len is not None else TUCK_EAR_LEN_RATIO * self.ted
        self.wall_w = self.bw - 2 * self.ii  # 内侧板宽（沿 y）
        # 常用坐标
        self.X0, self.X1 = self.eo, self.bl - self.eo  # 后板
        self.F0, self.F1 = self.eo + FRONT_INSET_EXTRA_MM, self.bl - self.eo - FRONT_INSET_EXTRA_MM
        self.T0, self.T1 = self.tki, self.bl - self.tki  # 插舌
        self.C0, self.C1 = self.rg, self.bl - self.rg  # 盖：卡在两条卷边中间
        self.Lb = self.bw + self.hr  # 盖压线
        self.Lt = self.Lb + self.lw  # 盖前沿（插舌压线）
        self.ear_free = self.hf - self.eg  # 前板耳朵高


def _dedupe(points: list[Vec]) -> list[Vec]:
    out: list[Vec] = []
    for v in points:
        if not out or not out[-1].is_close(v):
            out.append(v)
    return out


def _chain(points: list[Vec]) -> list[Segment]:
    pts = _dedupe(points)
    return [Segment(a, b) for a, b in zip(pts, pts[1:], strict=False)]


def _fillet(a: Vec, b: Vec, c: Vec, r: float) -> tuple[Vec, Segment, Vec]:
    """把 a→b→c 在 b 处的折角倒成半径 r 的相切圆弧：返回 (切点 1, 圆弧, 切点 2)。"""
    u, v = (b - a).normalized(), (c - b).normalized()
    left = u.cross(v) > 0  # 左转：圆弧逆时针
    phi = math.acos(max(-1.0, min(1.0, -u.dot(v))))  # 内角
    dist = r / math.tan(phi / 2)
    t1, t2 = b - u * dist, b + v * dist
    center = t1 + (Vec(-u.y, u.x) if left else Vec(u.y, -u.x)) * r
    a0 = math.atan2(t1.y - center.y, t1.x - center.x)
    a1 = math.atan2(t2.y - center.y, t2.x - center.x)
    if left and a1 < a0:
        a1 += 2 * math.pi
    if not left and a1 > a0:
        a1 -= 2 * math.pi
    return t1, arc_segment(center, r, a0, a1), t2


def _rounded(points: list[Vec], radii: dict[int, float]) -> list[Segment]:
    """折线；radii 里列出的顶点倒成圆角（radii 的键是 points 的下标）。"""
    out: list[Segment] = []
    cur = points[0]
    for i in range(1, len(points)):
        r = radii.get(i, 0.0)
        if r > 0 and i < len(points) - 1:
            t1, arc, t2 = _fillet(points[i - 1], points[i], points[i + 1], r)
            out += _chain([cur, t1])
            out.append(arc)
            cur = t2
        else:
            out += _chain([cur, points[i]])
            cur = points[i]
    return out


def _mirror(segs: list[Segment], bl: float) -> list[Segment]:
    """关于底板竖中线镜像并反向（镜像和反向各翻一次圆弧方向，bulge 不变）。"""

    def m(v: Vec) -> Vec:
        return Vec(bl - v.x, v.y)

    return [Segment(m(s.end), m(s.start), s.bulge) for s in reversed(segs)]


class MailerStyle:
    """飞机盒（插耳做法：插舌两端插耳插进侧板夹层 + 内侧板插脚）。"""

    id: ClassVar[str] = "fefco_0427"
    name_zh: ClassVar[str] = "飞机盒"
    version: ClassVar[str] = "0.2.0"
    refs: ClassVar[dict[str, str]] = {"fefco": "0427"}
    material_types: ClassVar[frozenset[MaterialType]] = frozenset({"corrugated"})
    panel_order: ClassVar[tuple[str, ...]] = (
        "base", "front", "back", "side_a", "side_b", "rim_a", "rim_b", "side_a_in", "side_b_in",
        "ear_fa", "ear_fb", "ear_ba", "ear_bb", "top_lid", "dust_la", "dust_lb", "top_tuck",
        "tuck_ear_a", "tuck_ear_b",
    )  # fmt: skip

    def param_schema(self) -> type[BaseModel]:
        return MailerParams

    # ---- 约束 ----

    def validate(
        self, params: Any, material: Material, profile: Profile | None = None
    ) -> list[Issue]:
        p: MailerParams = params
        issues = corrugated_only(self.id, material)

        def bad(msg: str, fix: str) -> None:
            issues.append(Issue(id="S03", message=msg, fix=fix))

        if issues:
            return issues
        t = material.caliper_mm
        min_h = max(MIN_H_MM, MIN_H_T_RATIO * t)
        for name, lo, hi in (
            ("L", MIN_LW_MM, MAX_LW_MM),
            ("W", MIN_LW_MM, MAX_LW_MM),
            ("H", min_h, MAX_H_MM),
        ):
            v = getattr(p, name)
            if not lo <= v <= hi:
                bad(
                    translate(
                        "{parameter}={value:g} is outside the provisional range {minimum:g}–{maximum:g}",
                        parameter=name,
                        value=v,
                        minimum=lo,
                        maximum=hi,
                    ),
                    translate(
                        "Set {parameter} to {minimum:g}–{maximum:g}",
                        parameter=name,
                        minimum=lo,
                        maximum=hi,
                    ),
                )
        if issues:
            return issues
        d = MailDims(p, material, profile or load_profile("default"))
        if min(d.Li, d.Wi, d.Hi) <= 0:
            bad(
                translate(
                    "The inside dimensions are not positive after subtracting board thickness"
                ),
                translate("Increase the dimensions or use --basis inner"),
            )
            return issues

        def rng(name: str, v: float, lo: float, hi: float) -> None:
            if not lo - 1e-9 <= v <= hi + 1e-9:
                bad(
                    translate(
                        "{parameter}={value:.2f} is outside the provisional range {minimum:.2f}–{maximum:.2f}",
                        parameter=name,
                        value=v,
                        minimum=lo,
                        maximum=hi,
                    ),
                    translate(
                        "Set {parameter} to {minimum:.2f}–{maximum:.2f}",
                        parameter=name,
                        minimum=lo,
                        maximum=hi,
                    ),
                )

        rng("roll_gap", d.rg, 2 * t, 5 * t + 2)
        rng("inner_inset", d.ii, 0.5 * t, 3 * t + 2)
        rng("ear_offset", d.eo, 0.5 * t, d.rg - FRONT_INSET_EXTRA_MM)
        rng("ear_depth", d.ed, EAR_DEPTH_MIN_MM, 0.5 * d.Wi - EAR_CLEAR_MM)
        rng("ear_gap", d.eg, 0.5 * t, 3 * t + 2)
        rng("lug_w", d.lug_w, max(LUG_W_MIN_MM, d.sd + 1), 0.5 * d.wall_w)
        rng("lug_len", d.lug_len, t + 1, 3 * t + 5)
        rng("slot_depth", d.sd, t + 1, 4 * t + 2)
        rng("lid_flap_depth", d.lfd, LID_FLAP_DEPTH_MIN_MM, d.hr)
        rng("lid_flap_taper_deg", p.lid_flap_taper_deg, 0, 45)
        rng("tuck_inset", d.tki, 0.5 * t, d.rg)
        rng("tuck_len", d.tkl, TUCK_LEN_MIN_MM + TUCK_SHOULDER_MM + TUCK_CHAMFER_MM, d.hr + t)
        if issues:
            return issues
        rng("tuck_ear_len", d.tel, TUCK_EAR_MIN_MM, d.ted - 1)
        if issues:
            return issues
        if d.ear_free < EAR_FREE_MIN_MM:
            bad(
                translate(
                    "The front locking ear is only {height:.1f} mm high (minimum {minimum:g} mm)",
                    height=d.ear_free,
                    minimum=EAR_FREE_MIN_MM,
                ),
                translate("Reduce ear_gap or increase H"),
            )
        n = len(d.lug_y)
        need = n * d.lug_w + (n + 1) * LUG_SPACING_MM
        if need > d.wall_w:
            bad(
                translate(
                    "The tabs do not fit. {count} slots plus spacing need {needed:.1f} mm, but the inner side panel is only {available:.1f} mm wide.",
                    count=n,
                    needed=need,
                    available=d.wall_w,
                ),
                translate("Reduce lug_w or increase W"),
            )
        turn = math.radians(90 + p.lid_flap_taper_deg)  # 防尘翼外角的内角
        if d.lw - 2 * d.lfd * d.lft <= 2 * d.lfr / math.tan(turn / 2):
            bad(
                translate("The tapered lid dust flap is too narrow for the outside radius"),
                translate("Reduce lid_flap_depth or lid_flap_taper_deg"),
            )
        if p.thumb_notch and (
            2 * THUMB_NOTCH_R_MM >= d.F1 - d.F0 or THUMB_NOTCH_R_MM >= d.hf - d.t
        ):
            bad(
                translate("The thumb notch does not fit (radius 12 mm)"),
                translate("Disable thumb_notch or increase L or H"),
            )
        return issues

    def advisories(
        self, params: Any, material: Material, profile: Profile | None = None
    ) -> list[Issue]:
        return flute_advisory(
            material,
            USUAL_FLUTES,
            translate(
                "Roll-end mailer (thick board can crack at the rollover or block the locking tabs)"
            ),
        )

    def expected_panel_sizes(
        self, params: Any, material: Material, profile: Profile
    ) -> dict[str, tuple[float, float]]:
        d = MailDims(params, material, profile)
        out = {
            "base": (d.bl, d.bw), "front": (d.F1 - d.F0, d.hf), "back": (d.X1 - d.X0, d.hr),
            "side_a": (d.ho, d.bw), "side_b": (d.ho, d.bw), "rim_a": (d.rg, d.bw), "rim_b": (d.rg, d.bw),
            "side_a_in": (d.hin + d.lug_len, d.wall_w), "side_b_in": (d.hin + d.lug_len, d.wall_w),
            "top_lid": (d.C1 - d.C0, d.lw), "dust_la": (d.lfd, d.lw), "dust_lb": (d.lfd, d.lw),
            "top_tuck": (d.T1 - d.T0, d.tkl), "tuck_ear_a": (d.tel, d.ted), "tuck_ear_b": (d.tel, d.ted),
        }  # fmt: skip
        for ear in ("ear_fa", "ear_fb"):
            out[ear] = (d.ed, d.hf - d.eg)
        for ear in ("ear_ba", "ear_bb"):
            out[ear] = (d.ed, d.hr - d.eg - EAR_LID_GAP_MM)
        return out

    # ---- 生成 ----

    def _lugs(self, d: MailDims, xe: float) -> list[Vec]:
        """内侧板自由边（x = xe）上的插脚：梯形，根宽 lug_w、尖两边各收 lug_c。"""
        hw, ll, c = d.lug_w / 2, d.lug_len, d.lug_c
        pts: list[Vec] = []
        for yc in d.lug_y:
            pts += [Vec(xe, yc - hw), Vec(xe + ll, yc - hw + c), Vec(xe + ll, yc + hw - c),
                    Vec(xe, yc + hw)]  # fmt: skip
        return pts

    def _dust(self, d: MailDims) -> tuple[list[Vec], dict[int, float]]:
        """盖防尘翼（盖右边，自下而上）：根在盖的整个进深上，两头斜 lid_flap_taper_deg，外角圆角。"""
        x, dx = d.C1, d.lfd
        pts = [Vec(x, d.Lb), Vec(x + dx, d.Lb + dx * d.lft), Vec(x + dx, d.Lt - dx * d.lft),
               Vec(x, d.Lt)]  # fmt: skip
        return pts, {1: d.lfr, 2: d.lfr}

    def _tuck_ear(self, d: MailDims) -> list[Segment]:
        """右边的插耳（从插舌根往外、绕回压线远端）：直边 + 小圆角 + 四分之一圆。

        reference 是四分之一椭圆；D4 只用直线和圆弧，所以改成圆角半径 ted − tel 的小圆角接半径 tel 的
        四分之一圆，两头都和直边、压线相切，外形差不到 1 mm。"""
        x, y, tel = d.T1, d.Lt + TUCK_SHOULDER_MM, d.tel
        rc = d.ted - tel
        segs = _chain([Vec(x, y), Vec(x + tel - rc, y)])
        segs.append(arc_segment(Vec(x + tel - rc, y + rc), rc, -math.pi / 2, 0.0))
        segs.append(arc_segment(Vec(x, y + rc), tel, 0.0, math.pi / 2))
        return segs

    def _right_side(self, d: MailDims) -> list[Segment]:
        """外轮廓右半边：从前板自由边右端往上走到插舌自由边右端（左半边是它的镜像）。"""
        bl, bw, X1, F1, C1, T1 = d.bl, d.bw, d.X1, d.F1, d.C1, d.T1
        xr = bl + d.ho  # 外侧板顶边（卷边压线）
        xe = xr + d.rg + d.hin  # 内侧板自由边
        gl, sh, ch = EAR_LID_GAP_MM, TUCK_SHOULDER_MM, TUCK_CHAMFER_MM
        pts = [
            Vec(F1, -d.hf), Vec(F1 + d.ed, -d.hf), Vec(F1 + d.ed, -d.eg), Vec(F1, -d.eg),  # 前耳
            Vec(F1, 0), Vec(bl, 0), Vec(xr, 0), Vec(xr + d.rg, d.ii), Vec(xe, d.ii),
            *self._lugs(d, xe),
            Vec(xe, bw - d.ii), Vec(xr + d.rg, bw - d.ii), Vec(xr, bw), Vec(bl, bw),
            Vec(X1, bw), Vec(X1, bw + d.eg), Vec(X1 + d.ed, bw + d.eg),  # 后耳
            Vec(X1 + d.ed, d.Lb - gl), Vec(X1, d.Lb - gl), Vec(C1, d.Lb - gl),
        ]  # fmt: skip
        dust, radii = self._dust(d)
        segs = _chain([*pts, dust[0]]) + _rounded(dust, radii)
        segs += _chain([dust[-1], Vec(C1, d.Lt + sh), Vec(T1, d.Lt + sh)])
        ear = self._tuck_ear(d)
        segs += ear
        segs += _chain([ear[-1].end, Vec(T1, d.Lt + d.tkl - ch), Vec(T1 - ch, d.Lt + d.tkl)])
        return segs

    def generate(self, params: Any, material: Material, profile: Profile) -> Dieline:
        p: MailerParams = params
        d = MailDims(p, material, profile)
        bl, F0, F1 = d.bl, d.F0, d.F1
        right = self._right_side(d)
        cx, r = 0.5 * bl, THUMB_NOTCH_R_MM
        out: list[Segment] = []
        if p.thumb_notch:  # 前板自由边正中的半圆手指缺口（往板里凹）
            out += _chain([Vec(F0, -d.hf), Vec(cx - r, -d.hf)])
            out.append(arc_segment(Vec(cx, -d.hf), r, math.pi, 0.0))
            out += _chain([Vec(cx + r, -d.hf), Vec(F1, -d.hf)])
        else:
            out += _chain([Vec(F0, -d.hf), Vec(F1, -d.hf)])
        out += right
        top = d.Lt + d.tkl
        out += _chain([Vec(d.T1 - TUCK_CHAMFER_MM, top), Vec(d.T0 + TUCK_CHAMFER_MM, top)])
        out += _mirror(right, bl)
        outline = Path(out, closed=True)

        polys = self._panels(d, p.thumb_notch)
        creases = self._creases(d)
        slots = self._slots(d)
        fold = {"ear_fa": 1, "ear_fb": 1, "ear_ba": 1, "ear_bb": 1, "side_a": 2, "side_b": 2,
                "side_a_in": 3, "side_b_in": 3, "top_lid": 4, "tuck_ear_a": 5, "tuck_ear_b": 5}  # fmt: skip
        ups: dict[str, Up] = {"front": "down", "side_a": "right", "side_b": "left", "top_lid": "down",
                              "top_tuck": "down", "side_a_in": "left", "side_b_in": "right"}  # fmt: skip
        flute_note = ("Flute along the flat's y axis (parallel to the roll-over creases, vertical on front and back panels; inferred); not marked on the dieline"
                      if p.flute_dir == "y" else "Flute along the flat's x axis; not marked on the dieline")  # fmt: skip
        return assemble(
            style_id=self.id,
            params=p,
            material=material,
            profile=profile,
            outline=outline,
            polys=_ordered(polys, self.panel_order),
            creases=creases,
            inner_cuts=slots,
            ups=ups,
            provisional=set(polys) - {"back"},
            meta={
                "name_zh": self.name_zh,
                "refs": self.refs,
                "version": self.version,
                "compensation_applied": p.apply_compensation,
                "layout": "tray",  # 效果图：底板 + 四壁 + 盖（不是"盒身一圈"）
                "lid_between_rims": True,  # 盖卡在两条卷边中间：效果图顶面两边贴卷边顶
                "front_is_tuck": True,  # 插舌包在前板外面：效果图正面是插舌
                "open_only": ["front"],  # 前板合上被插舌挡住、打开盖才看得到
                # 平面图上下叠（LUMERA 测试 ISSUE-004）：盖 + 插舌、背板 + 底在刀版上相接、同一个朝向，画面
                # 跨折线连着画；两个侧面一样宽、各自成画。都接近方形，不再有 3.5:1 的细长条
                "flat_stacks": [
                    {"faces": ["top_lid", "top_tuck"], "joined": True},
                    {"faces": ["back", "base"], "joined": True},
                    {"faces": ["side_b", "side_a"], "joined": False},
                ],
                "faces": {
                    "front": "top_tuck",
                    "back": "back",
                    "side_a": "side_a",
                    "side_b": "side_b",
                    "top": "top_lid",
                    "bottom": "base",
                },  # fmt: skip
                "fold_order": fold,
                "dims": {
                    "Li": d.Li,
                    "Wi": d.Wi,
                    "Hi": d.Hi,
                    "base_l": bl,
                    "base_w": d.bw,
                    "wall_h": d.hr,
                    "lid_w": d.C1 - d.C0,
                    "lid_depth": d.lw,
                    "roll_gap": d.rg,
                },  # fmt: skip
                "flute_dir": p.flute_dir,
                "flute_note": flute_note,
                "print_note": "Offset print laminated to board (print the facing sheet, laminate to corrugated, then die-cut); bleed per profile",
                "lock": "The tuck wraps outside the front panel, its ears go between the side wall layers, plus inner side wall lugs (tuck-ear method)",
                "assumptions": [
                    "底板长 = Li + k_panel·t + 2·卷边 + t（两端卷边夹层放耳朵 + 插耳）【初值】",
                    "卷边宽 3t + 0.5；盖宽 = 底板 − 2·卷边，合上后卡在两条卷边中间【初值】",
                    "前板比后板低 t；插舌深 = 后板高、两端插耳深 = 插舌深 − 3.5、长 0.89 × 深【初值】",
                    "防尘翼深 H − t、两头斜 20°、外角圆角 min(15, H/4)【初值】",
                    "插舌包在前板外面：合上后正面是插舌，前板只在打开盖时看得到【初值】",
                    "图文方向：插舌、前板、盖从正面读正；侧板卷边在上；底板朝后",
                ],
            },
        )

    def _panels(self, d: MailDims, notch: bool) -> dict[str, tuple[PanelRole, Path]]:
        bl, bw, X0, X1, F0, F1 = d.bl, d.bw, d.X0, d.X1, d.F0, d.F1
        C0, C1, T0, T1, Lb, Lt = d.C0, d.C1, d.T0, d.T1, d.Lb, d.Lt
        gl, sh, ch = EAR_LID_GAP_MM, TUCK_SHOULDER_MM, TUCK_CHAMFER_MM
        xr = bl + d.ho
        xi0, xe = xr + d.rg, xr + d.rg + d.hin
        if notch:
            cx, r = 0.5 * bl, THUMB_NOTCH_R_MM
            front = Path([*_chain([Vec(F0, -d.hf), Vec(cx - r, -d.hf)]),
                          arc_segment(Vec(cx, -d.hf), r, math.pi, 0.0),
                          *_chain([Vec(cx + r, -d.hf), Vec(F1, -d.hf), Vec(F1, 0), Vec(F0, 0), Vec(F0, -d.hf)])],
                         closed=True)  # fmt: skip
        else:
            front = _rect(F0, -d.hf, F1, 0)
        inner = [Vec(xi0, d.ii), Vec(xe, d.ii), *self._lugs(d, xe), Vec(xe, bw - d.ii),
                 Vec(xi0, bw - d.ii)]  # fmt: skip
        inner_a = Path.from_points(_dedupe(inner), closed=True)
        rim_a = _trapezoid([(xr, 0), (xi0, d.ii), (xi0, bw - d.ii), (xr, bw)])
        dust_pts, radii = self._dust(d)
        dust = Path([*_rounded(dust_pts, radii), Segment(dust_pts[-1], dust_pts[0])], closed=True)
        ear = self._tuck_ear(d)
        tuck_ear = Path([*ear, Segment(ear[-1].end, ear[0].start)], closed=True)
        back = _trapezoid([(X0, bw), (X1, bw), (X1, Lb - gl), (C1, Lb - gl), (C1, Lb), (C0, Lb),
                           (C0, Lb - gl), (X0, Lb - gl)])  # fmt: skip
        tuck = _trapezoid([(C0, Lt), (C1, Lt), (C1, Lt + sh), (T1, Lt + sh), (T1, Lt + d.tkl - ch),
                           (T1 - ch, Lt + d.tkl), (T0 + ch, Lt + d.tkl), (T0, Lt + d.tkl - ch),
                           (T0, Lt + sh), (C0, Lt + sh)])  # fmt: skip

        def mirror_path(path: Path) -> Path:
            return Path(_mirror(path.segments, bl), closed=True)

        ear_f = _rect(F1, -d.hf, F1 + d.ed, -d.eg)
        ear_b = _rect(X1, bw + d.eg, X1 + d.ed, Lb - gl)
        return {
            "base": (PanelRole.BOTTOM, _rect(0, 0, bl, bw)),
            "front": (PanelRole.BODY, front),
            "back": (PanelRole.BODY, back),
            "side_a": (PanelRole.BODY, _rect(bl, 0, xr, bw)),
            "side_b": (PanelRole.BODY, _rect(-d.ho, 0, 0, bw)),
            "rim_a": (PanelRole.RIM, rim_a),
            "rim_b": (PanelRole.RIM, mirror_path(rim_a)),
            "side_a_in": (PanelRole.INNER_WALL, inner_a),
            "side_b_in": (PanelRole.INNER_WALL, mirror_path(inner_a)),
            "ear_fa": (PanelRole.DUST_FLAP, ear_f),
            "ear_fb": (PanelRole.DUST_FLAP, mirror_path(ear_f)),
            "ear_ba": (PanelRole.DUST_FLAP, ear_b),
            "ear_bb": (PanelRole.DUST_FLAP, mirror_path(ear_b)),
            "top_lid": (PanelRole.LID, _rect(C0, Lb, C1, Lt)),
            "dust_la": (PanelRole.DUST_FLAP, dust),
            "dust_lb": (PanelRole.DUST_FLAP, mirror_path(dust)),
            "top_tuck": (PanelRole.TUCK, tuck),
            "tuck_ear_a": (PanelRole.DUST_FLAP, tuck_ear),
            "tuck_ear_b": (PanelRole.DUST_FLAP, mirror_path(tuck_ear)),
        }

    def _end_breaks(self, d: MailDims) -> list[tuple[float, float]]:
        """底板端线被插孔断开后剩下的几段 (y0, y1)：压线到插孔直边的两头为止。"""
        rho = d.sd / 2
        cuts = [(yc - d.sw / 2 + rho, yc + d.sw / 2 - rho) for yc in d.lug_y]
        ys = [0.0] + [v for c in cuts for v in c] + [d.bw]
        return [(ys[i], ys[i + 1]) for i in range(0, len(ys), 2)]

    def _creases(self, d: MailDims) -> list[Edge]:
        bl, bw, X0, X1, F0, F1 = d.bl, d.bw, d.X0, d.X1, d.F0, d.F1
        C0, C1, T0, T1, Lb, Lt = d.C0, d.C1, d.T0, d.T1, d.Lb, d.Lt
        xr = bl + d.ho
        out = [
            crease("crease_base_front", (F0, 0), (F1, 0), ["base", "front"]),
            crease("crease_base_back", (X0, bw), (X1, bw), ["base", "back"]),
        ]
        for i, (y0, y1) in enumerate(self._end_breaks(d), 1):
            out.append(crease(f"crease_base_side_a_{i}", (bl, y0), (bl, y1), ["base", "side_a"]))
            out.append(crease(f"crease_base_side_b_{i}", (0, y0), (0, y1), ["base", "side_b"]))
        for side, x_out, x_in in (("a", xr, xr + d.rg), ("b", bl - xr, bl - xr - d.rg)):
            out.append(
                crease(f"crease_side_{side}_rim_{side}", (x_out, 0), (x_out, bw),
                       [f"side_{side}", f"rim_{side}"])
            )  # fmt: skip
            out.append(
                crease(f"crease_rim_{side}_side_{side}_in", (x_in, d.ii), (x_in, bw - d.ii),
                       [f"rim_{side}", f"side_{side}_in"])
            )  # fmt: skip
        for ear, x, y0, y1, parent in (
            ("ear_fa", F1, -d.hf, -d.eg, "front"), ("ear_fb", F0, -d.hf, -d.eg, "front"),
            ("ear_ba", X1, bw + d.eg, Lb - EAR_LID_GAP_MM, "back"),
            ("ear_bb", X0, bw + d.eg, Lb - EAR_LID_GAP_MM, "back"),
        ):  # fmt: skip
            out.append(crease(f"crease_{parent}_{ear}", (x, y0), (x, y1), [parent, ear]))
        y0, y1 = Lt + TUCK_SHOULDER_MM, Lt + TUCK_SHOULDER_MM + d.ted
        out += [
            crease("crease_back_top_lid", (C0, Lb), (C1, Lb), ["back", "top_lid"]),
            crease("crease_top_lid_dust_la", (C1, Lb), (C1, Lt), ["top_lid", "dust_la"]),
            crease("crease_top_lid_dust_lb", (C0, Lb), (C0, Lt), ["top_lid", "dust_lb"]),
            crease("crease_top_lid_tuck", (C0, Lt), (C1, Lt), ["top_lid", "top_tuck"]),
            crease("crease_top_tuck_tuck_ear_a", (T1, y0), (T1, y1), ["top_tuck", "tuck_ear_a"]),
            crease("crease_top_tuck_tuck_ear_b", (T0, y0), (T0, y1), ["top_tuck", "tuck_ear_b"]),
        ]
        return out

    def _slots(self, d: MailDims) -> list[Edge]:
        """插孔：开在底板两端的压线上、往底板里 slot_depth，两头半圆（内部切口）。"""
        rho, bl = d.sd / 2, d.bl
        out: list[Edge] = []
        for i, yc in enumerate(d.lug_y, 1):
            y0, y1 = yc - d.sw / 2 + rho, yc + d.sw / 2 - rho
            segs = _chain([Vec(bl, y0), Vec(bl, y1)])
            segs.append(arc_segment(Vec(bl - rho, y1), rho, 0.0, math.pi))
            segs += _chain([Vec(bl - d.sd, y1), Vec(bl - d.sd, y0)])
            segs.append(arc_segment(Vec(bl - rho, y0), rho, math.pi, 2 * math.pi))
            for side, path in (("a", segs), ("b", _mirror(segs, bl))):
                out.append(
                    Edge(
                        id=f"cut_slot_{side}{i}", type=EdgeType.CUT, segments=path, panels=["base"]
                    )
                )
        return sorted(out, key=lambda e: e.id)
