"""锁底盒（，结构规格草案 ）：盒身、顶部与 RTE 相同，只换底部。

- snaplock 扣底盒（1-2-3 底，ECMA A55.20.01.01 待核对；瓦楞对应 FEFCO 0215）：四片翼不用胶。
  v2（2026-10-22，对照 FEFCO 0215 与用户找的标准图重做，）：
  ① 先折糊口那一侧主面（back）上的 U 形缺口翼；② 两片侧翼折进去、塞到它下面（侧翼斜边朝缺口翼，
  末端有倒钩）；③ 最后折 front 上的 V 形锁舌翼，锁舌塞到缺口翼下面卡住；
- autolock 自动锁底盒（crash lock，ECMA A60.20.00.01 待核对；瓦楞对应 FEFCO 0713）。
  v2（2026-10-22，对照 FEFCO 0713，按 reference 免费生成器出的 80×50×120 矢量刀版校准，
   节）：两片主翼右端各有一条过右下角的 45° 斜压线（角平分线），
  分出糊胶三角；糊盒时三角的里面粘在下一片侧翼的外面。主翼自由边左段全深（齿），正中一个 45° 斜肩
  + 竖直台阶，台阶底深 = 侧翼深 = 侧面宽的一半（两条角平分线的交点）。需要工厂糊盒机做
  "侧缝 + 底部两处"糊胶。

ECMA 的标准图不公开；所有数值都是【初值】，须折纸样验证、结构顾问审核后才能标"已验证"。

底部记号（归一化之前）：底部压线在盒身下沿 y = 0，各翼向下伸（y 为负）；侧翼压线在 y = off
（和 RTE 的防尘翼一样）。外轮廓逆时针：沿底部从左（back 左下角）走到右（side_b 右下角）。
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import BaseModel

from pacxio.messages import t
from pacxio.model.common import Issue
from pacxio.styles.components.lockbottom import (
    AUTO_MAJOR_WI_RATIO as AUTO_MAJOR_WI_RATIO,
)
from pacxio.styles.components.lockbottom import (
    AUTO_TAPER_DEG_RANGE as AUTO_TAPER_DEG_RANGE,
)
from pacxio.styles.components.lockbottom import (
    AUTO_TOOTH_INSET_MM as AUTO_TOOTH_INSET_MM,
)
from pacxio.styles.components.lockbottom import (
    AUTO_TOOTH_INSET_WI_RATIO as AUTO_TOOTH_INSET_WI_RATIO,
)
from pacxio.styles.components.lockbottom import (
    AUTOLOCK_MAX_L_OVER_W as AUTOLOCK_MAX_L_OVER_W,
)
from pacxio.styles.components.lockbottom import (
    AUTOLOCK_MIN_W_MM as AUTOLOCK_MIN_W_MM,
)
from pacxio.styles.components.lockbottom import (
    CUT_MARGIN_MM as CUT_MARGIN_MM,
)
from pacxio.styles.components.lockbottom import (
    HOOK_CAP_MM as HOOK_CAP_MM,
)
from pacxio.styles.components.lockbottom import (
    HOOK_WS_RATIO as HOOK_WS_RATIO,
)
from pacxio.styles.components.lockbottom import (
    LOCK_STEP_CAP_MM as LOCK_STEP_CAP_MM,
)
from pacxio.styles.components.lockbottom import (
    LOCK_STEP_SHOULDER_RATIO as LOCK_STEP_SHOULDER_RATIO,
)
from pacxio.styles.components.lockbottom import (
    MAJOR_DEPTH_RANGE_WI as MAJOR_DEPTH_RANGE_WI,
)
from pacxio.styles.components.lockbottom import (
    MAJOR_DEPTH_WI_RATIO as MAJOR_DEPTH_WI_RATIO,
)
from pacxio.styles.components.lockbottom import (
    MAJOR_RELIEF_EXTRA_MM as MAJOR_RELIEF_EXTRA_MM,
)
from pacxio.styles.components.lockbottom import (
    MAJOR_RELIEF_T_RATIO as MAJOR_RELIEF_T_RATIO,
)
from pacxio.styles.components.lockbottom import (
    MIN_LOCK_BOX_DIM_MM as MIN_LOCK_BOX_DIM_MM,
)
from pacxio.styles.components.lockbottom import (
    MINOR_DEPTH_MARGIN_MM as MINOR_DEPTH_MARGIN_MM,
)
from pacxio.styles.components.lockbottom import (
    MINOR_DEPTH_MIN_MM as MINOR_DEPTH_MIN_MM,
)
from pacxio.styles.components.lockbottom import (
    MINOR_DEPTH_WI_RATIO as MINOR_DEPTH_WI_RATIO,
)
from pacxio.styles.components.lockbottom import (
    MINOR_SLANT_DEPTH_RATIO as MINOR_SLANT_DEPTH_RATIO,
)
from pacxio.styles.components.lockbottom import (
    MINOR_SLANT_WS_RATIO as MINOR_SLANT_WS_RATIO,
)
from pacxio.styles.components.lockbottom import (
    MINOR_TAPER_RATIO as MINOR_TAPER_RATIO,
)
from pacxio.styles.components.lockbottom import (
    NOTCH_D_MAJOR_RATIO as NOTCH_D_MAJOR_RATIO,
)
from pacxio.styles.components.lockbottom import (
    NOTCH_LEG_MIN_MM as NOTCH_LEG_MIN_MM,
)
from pacxio.styles.components.lockbottom import (
    NOTCH_W_LI_RATIO as NOTCH_W_LI_RATIO,
)
from pacxio.styles.components.lockbottom import (
    NOTCH_W_RANGE_LI as NOTCH_W_RANGE_LI,
)
from pacxio.styles.components.lockbottom import (
    SHOULDER_MIN_MM as SHOULDER_MIN_MM,
)
from pacxio.styles.components.lockbottom import (
    TONGUE_CLEAR_MM as TONGUE_CLEAR_MM,
)
from pacxio.styles.components.lockbottom import (
    TONGUE_LEN_MAJOR_RATIO as TONGUE_LEN_MAJOR_RATIO,
)
from pacxio.styles.components.lockbottom import (
    TONGUE_LEN_MIN_MM as TONGUE_LEN_MIN_MM,
)
from pacxio.styles.components.lockbottom import (
    TONGUE_R_CAP_MM as TONGUE_R_CAP_MM,
)
from pacxio.styles.components.lockbottom import (
    TONGUE_W_LI_RATIO as TONGUE_W_LI_RATIO,
)
from pacxio.styles.components.lockbottom import (
    TONGUE_W_RANGE_LI as TONGUE_W_RANGE_LI,
)
from pacxio.styles.components.lockbottom import (
    TOOTH_MIN_MM as TOOTH_MIN_MM,
)
from pacxio.styles.components.lockbottom import (
    WEB_BOTTOM_MIN_MM as WEB_BOTTOM_MIN_MM,
)
from pacxio.styles.components.lockbottom import (
    WEB_CLEAR_MM as WEB_CLEAR_MM,
)
from pacxio.styles.components.lockbottom import (
    WEB_CORNER_MM as WEB_CORNER_MM,
)
from pacxio.styles.components.lockbottom import (
    AutoDims as AutoDims,
)
from pacxio.styles.components.lockbottom import (
    AutolockParams as AutolockParams,
)
from pacxio.styles.components.lockbottom import (
    CrashBottom as CrashBottom,
)
from pacxio.styles.components.lockbottom import (
    SnapBottom as SnapBottom,
)
from pacxio.styles.components.lockbottom import (
    SnapDims as SnapDims,
)
from pacxio.styles.components.lockbottom import (
    SnaplockParams as SnaplockParams,
)
from pacxio.styles.components.lockbottom import (
    _closed as _closed,
)
from pacxio.styles.components.lockbottom import (
    _common_checks as _common_checks,
)
from pacxio.styles.components.lockbottom import (
    _points_chain as _points_chain,
)
from pacxio.styles.components.lockbottom import (
    _relief as _relief,
)
from pacxio.styles.components.lockbottom import (
    auto_dims as auto_dims,
)
from pacxio.styles.components.lockbottom import (
    snap_dims as snap_dims,
)
from pacxio.styles.rte import (
    BodyX,
    BottomPart,
    RteDims,
    RteParams,
    RteStyle,
)


class SnaplockStyle(RteStyle):
    """扣底盒（1-2-3 底）：底部四片翼互扣，不用胶（v2，对照 FEFCO 0215）。"""

    id: ClassVar[str] = "snaplock"
    name_zh: ClassVar[str] = "扣底盒"
    refs: ClassVar[dict[str, str]] = {"ecma": "A55.20.01.01（待核对）", "fefco": "0215（瓦楞对应）"}
    # 0.2.0：底部按 FEFCO 0215 重做；0.3.0：顶部插舌头按标准刀版；0.4.0：糊口端头（都是 2026-10-22）；
    # 0.5.0：插舌压线两端改成切口（2026-11-07）
    version: ClassVar[str] = "0.5.0"
    glue_bottom: ClassVar[Literal["bevel", "arc"]] = "arc"
    panel_order: ClassVar[tuple[str, ...]] = (
        "glue", "back", "side_a", "front", "side_b", "top_lid", "top_tuck", "dust_ta", "dust_tb",
        "btm_back", "btm_sa", "btm_front", "btm_sb",
    )  # fmt: skip
    crease_order: ClassVar[tuple[str, ...]] = (
        "crease_glue_back", "crease_back_side_a", "crease_side_a_front", "crease_front_side_b",
        "crease_back_top_lid", "crease_top_lid_tuck", "crease_side_a_dust_ta", "crease_side_b_dust_tb",
        "crease_back_btm_back", "crease_side_a_btm_sa", "crease_front_btm_front", "crease_side_b_btm_sb",
    )  # fmt: skip
    bottom_assumptions: ClassVar[tuple[str, ...]] = (
        "扣底（1-2-3，v2）：① back 上的 U 形缺口翼 → ② 两片侧翼（斜边朝缺口翼、带倒钩）塞到它下面 → "
        "③ front 上的 V 形锁舌翼，锁舌塞到缺口翼下面；结构对照 FEFCO 0215 与标准扣底图，"
        "比例取自标准图，尺寸是【初值】，未经折样（）",
    )

    def param_schema(self) -> type[BaseModel]:
        return SnaplockParams

    def _bottom_meta(self, d: RteDims) -> dict[str, Any]:
        return {"bottom": {"type": "snaplock", "halves": ["btm_front", "btm_back"],
                           "fold_order": {"btm_back": 1, "btm_sa": 2, "btm_sb": 2, "btm_front": 3}}}  # fmt: skip

    def _bottom(self, p: RteParams, d: RteDims, xs: BodyX) -> BottomPart:
        return SnapBottom().build(p, d, xs)

    def _bottom_sizes(self, p: RteParams, d: RteDims) -> dict[str, tuple[float, float]]:
        assert isinstance(p, SnaplockParams)
        s = snap_dims(p, d)
        w = d.wb - 2 * s.relief
        return {"btm_back": (w, s.major), "btm_front": (w, s.major),
                "btm_sa": (d.ws - s.relief, s.minor), "btm_sb": (d.ws - s.relief, s.minor)}  # fmt: skip

    def _validate_bottom(self, p: RteParams, d: RteDims) -> list[Issue]:
        assert isinstance(p, SnaplockParams)
        issues: list[Issue] = []

        def bad(msg: str, fix: str) -> None:
            issues.append(Issue(id="S03", message=msg, fix=fix))

        _common_checks(self, p, bad)
        s = snap_dims(p, d)
        span = d.wb - 2 * s.relief  # 主翼宽
        lo, hi = MAJOR_DEPTH_RANGE_WI[0] * d.Wi, MAJOR_DEPTH_RANGE_WI[1] * d.Wi
        if not lo <= s.major <= hi:
            bad(t("major_depth={value:.2f} is outside 0.5·Wi–0.95·Wi ({minimum:.2f}–{maximum:.2f}). Too shallow prevents overlap at the centre. Too deep reaches the opposite wall.", value=s.major, minimum=lo, maximum=hi),
                t("Set major_depth to {minimum:.2f}–{maximum:.2f}", minimum=lo, maximum=hi))  # fmt: skip
        lo, hi = NOTCH_W_RANGE_LI[0] * d.Li, NOTCH_W_RANGE_LI[1] * d.Li
        if not lo <= s.notch_w <= hi or (span - s.notch_w) / 2 < NOTCH_LEG_MIN_MM:
            bad(t("notch_w={value:.2f} is outside 0.3·Li–0.7·Li ({minimum:.2f}–{maximum:.2f}) or leaves too little board on either side", value=s.notch_w, minimum=lo, maximum=hi),
                t("Set notch_w to {minimum:.2f}–{maximum:.2f}", minimum=lo, maximum=min(hi, span - 2 * NOTCH_LEG_MIN_MM)))  # fmt: skip
        lo, hi = TONGUE_W_RANGE_LI[0] * d.Li, TONGUE_W_RANGE_LI[1] * d.Li
        if not lo <= s.tongue_w <= hi:
            bad(t("tongue_w={value:.2f} is outside 0.25·Li–0.6·Li ({minimum:.2f}–{maximum:.2f})", value=s.tongue_w, minimum=lo, maximum=hi),
                t("Set tongue_w to {minimum:.2f}–{maximum:.2f}", minimum=lo, maximum=hi))  # fmt: skip
        if s.tongue_w > s.notch_w - TONGUE_CLEAR_MM:
            bad(t("The locking tab width {width:.2f} needs at least {clearance} clearance to fit the notch width {notch:.2f}", width=s.tongue_w, notch=s.notch_w, clearance=TONGUE_CLEAR_MM),
                t("Reduce tongue_w or increase notch_w"))  # fmt: skip
        if not CUT_MARGIN_MM <= s.notch_d <= s.major - CUT_MARGIN_MM:
            bad(t("notch_d={value:.2f} is outside {minimum} to major flap depth minus {margin}", value=s.notch_d, minimum=CUT_MARGIN_MM, margin=CUT_MARGIN_MM),
                t("Set notch_d to {minimum}–{maximum:.2f}", minimum=CUT_MARGIN_MM, maximum=s.major - CUT_MARGIN_MM))  # fmt: skip
        if not TONGUE_LEN_MIN_MM <= s.tongue_len <= s.major - CUT_MARGIN_MM:
            bad(t("tongue_len={value:.2f} is outside {minimum} to major flap depth minus {margin}", value=s.tongue_len, minimum=TONGUE_LEN_MIN_MM, margin=CUT_MARGIN_MM),
                t("Set tongue_len to {minimum}–{maximum:.2f}", minimum=TONGUE_LEN_MIN_MM, maximum=s.major - CUT_MARGIN_MM))  # fmt: skip
        if not 0 <= s.tongue_r <= s.tongue_len / 2 or 2 * s.tongue_r >= s.tongue_w:
            bad(
                t("tongue_r={value:.2f} is too large", value=s.tongue_r),
                t("Set tongue_r to at most tongue_len / 2"),
            )
        max_minor = 0.5 * d.Li - MINOR_DEPTH_MARGIN_MM
        if not MINOR_DEPTH_MIN_MM <= s.minor <= max_minor:
            bad(t("minor_depth={value:.2f} must be between {minimum} and 0.5·Li − 1 ({maximum:.2f}). Too shallow prevents locking. Too deep makes the side flaps collide.", value=s.minor, minimum=MINOR_DEPTH_MIN_MM, maximum=max_minor),
                t("Set minor_depth to {minimum}–{maximum:.2f}", minimum=MINOR_DEPTH_MIN_MM, maximum=max_minor))  # fmt: skip
        if not 0 <= s.hook < s.slant / 2:
            bad(t("hook={value:.2f} exceeds half the side-flap bevel inset ({maximum:.2f})", value=s.hook, maximum=s.slant / 2),
                t("Set hook to 0–{maximum:.2f}", maximum=s.slant / 2))  # fmt: skip
        return issues


class AutolockStyle(RteStyle):
    """自动锁底盒（crash lock）：底部预先糊好，打开成方筒时自己落位（v2，对照 FEFCO 0713）。"""

    id: ClassVar[str] = "autolock"
    name_zh: ClassVar[str] = "自动锁底盒"
    refs: ClassVar[dict[str, str]] = {"ecma": "A60.20.00.01（待核对）", "fefco": "0713（瓦楞对应）"}
    # 0.2.0：糊胶三角改到主翼上；0.3.0：顶部插舌头、主翼外侧斜边；0.4.0：糊口端头（都是 2026-10-22）
    version: ClassVar[str] = "0.5.0"  # 0.5.0：插舌压线两端改成切口（2026-11-07）
    glue_bottom: ClassVar[Literal["bevel", "arc"]] = "arc"
    panel_order: ClassVar[tuple[str, ...]] = (
        "glue", "back", "side_a", "front", "side_b", "top_lid", "top_tuck", "dust_ta", "dust_tb",
        "btm_back", "glue_back", "btm_sa", "btm_front", "glue_front", "btm_sb",
    )  # fmt: skip
    crease_order: ClassVar[tuple[str, ...]] = (
        "crease_glue_back", "crease_back_side_a", "crease_side_a_front", "crease_front_side_b",
        "crease_back_top_lid", "crease_top_lid_tuck", "crease_side_a_dust_ta", "crease_side_b_dust_tb",
        "crease_back_btm_back", "crease_btm_back_glue_back", "crease_side_a_btm_sa",
        "crease_front_btm_front", "crease_btm_front_glue_front", "crease_side_b_btm_sb",
    )  # fmt: skip
    bottom_assumptions: ClassVar[tuple[str, ...]] = (
        "自动锁底（v2）：两片主翼右端各一条过角的 45° 斜压线分出糊胶三角；主翼自由边左段全深、正中一个"
        " 45° 斜肩 + 竖直台阶；侧翼深 = 侧面宽的一半；结构对照 FEFCO 0713，比例按 reference 生成的"
        " 80×50×120 刀版校准，尺寸是【初值】，未经折样（）",
        "糊胶：糊胶三角的里面（不印刷）粘在侧翼的外面（印刷面）上；涂胶区在刀版 PDF 信息层与 DXF GLUE"
        " 图层标出；侧翼涂胶区暂不禁墨，要不要禁墨待工厂确认",
        "需要工厂糊盒机做侧缝 + 底部两处糊胶",
    )

    def param_schema(self) -> type[BaseModel]:
        return AutolockParams

    def _bottom_meta(self, d: RteDims) -> dict[str, Any]:
        return {"bottom": {"type": "autolock", "halves": ["btm_front", "btm_back"],
                           "glue": {"glue_back": "btm_sa", "glue_front": "btm_sb",
                                    "faces": "tab_inside_to_flap_outside"}}}  # fmt: skip

    def _bottom(self, p: RteParams, d: RteDims, xs: BodyX) -> BottomPart:
        return CrashBottom().build(p, d, xs)

    def _bottom_sizes(self, p: RteParams, d: RteDims) -> dict[str, tuple[float, float]]:
        assert isinstance(p, AutolockParams)
        a = auto_dims(p, d)
        main, web = (d.wb - a.relief, a.major), (a.half - a.clear, a.major - a.corner)
        return {"btm_back": main, "btm_front": main, "glue_back": web, "glue_front": web,
                "btm_sa": (d.ws, a.half), "btm_sb": (d.ws, a.half)}  # fmt: skip

    def _validate_bottom(self, p: RteParams, d: RteDims) -> list[Issue]:
        assert isinstance(p, AutolockParams)
        issues: list[Issue] = []

        def bad(msg: str, fix: str) -> None:
            issues.append(Issue(id="S03", message=msg, fix=fix))

        _common_checks(self, p, bad)
        if d.Wi < AUTOLOCK_MIN_W_MM:
            bad(t("The auto-lock bottom requires W ≥ {minimum} to leave room for the glue triangles (provisional)", minimum=AUTOLOCK_MIN_W_MM),
                t("Set W to at least {minimum}", minimum=AUTOLOCK_MIN_W_MM))  # fmt: skip
            return issues
        if d.Li < d.Wi:
            bad(t("The auto-lock bottom requires L ≥ W, but L {length:.2f} < W {width:.2f}. The major flaps attach to the long sides. Otherwise the bisectors cross past the flap centreline and the locking step cannot be formed.", length=d.Li, width=d.Wi),
                t("Swap L and W so the front becomes the narrower panel, or use snaplock"))  # fmt: skip
            return issues
        if d.Li / d.Wi > AUTOLOCK_MAX_L_OVER_W:
            bad(
                t(
                    "L / W = {ratio:.2f} exceeds the provisional limit {maximum}",
                    ratio=d.Li / d.Wi,
                    maximum=AUTOLOCK_MAX_L_OVER_W,
                ),
                t("Reduce L or increase W"),
            )
        a = auto_dims(p, d)
        lo, hi = a.half + SHOULDER_MIN_MM, 2 * a.half - a.clear - WEB_BOTTOM_MIN_MM
        if not lo <= a.major <= hi:
            bad(t("major_depth={value:.2f} is outside {minimum:.2f}–{maximum:.2f}. Too shallow leaves too little engagement at the step. Too deep removes the base of the glue triangle.", value=a.major, minimum=lo, maximum=hi),
                t("Set major_depth to {minimum:.2f}–{maximum:.2f}", minimum=lo, maximum=hi))  # fmt: skip
        elif not 0 <= a.step <= 0.5 * (a.major - a.half):
            bad(t("lock_step={value:.2f} is outside 0–{maximum:.2f}. The step cannot exceed the angled shoulder.", value=a.step, maximum=0.5 * (a.major - a.half)),
                t("Set lock_step to 0–{maximum:.2f}", maximum=0.5 * (a.major - a.half)))  # fmt: skip
        lo, hi = AUTO_TAPER_DEG_RANGE
        if p.major_taper_deg is not None and not lo <= p.major_taper_deg <= hi:
            bad(t("major_taper_deg={value} is outside {minimum:g}–{maximum:g}", value=p.major_taper_deg, minimum=lo, maximum=hi),
                t("Set major_taper_deg to {minimum:g}–{maximum:g} or leave it unset to use 0.2·Wi − 2 from the reference dieline", minimum=lo, maximum=hi))  # fmt: skip
        elif a.tooth(d.wb) < TOOTH_MIN_MM:
            bad(t("The tooth on the left section of the major flap is only {width:.2f} mm wide (minimum {minimum}), so the two major flaps cannot lock", width=a.tooth(d.wb), minimum=TOOTH_MIN_MM),
                t("Reduce major_taper_deg or major_depth"))  # fmt: skip
        return issues
