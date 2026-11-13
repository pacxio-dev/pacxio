"""结构配方入口（style = "structure"，几何引擎第 0 步）：配方 → Dieline + 自动折法 + 折叠自检。

这不是又一个手写盒型：盒身、封口、折法都由引擎按配方算。现有 39 个入口照旧（用户 2026-11-05：
全部做完再迁成预设），引擎的结果拿它们当标准答案对照。
"""

from __future__ import annotations

from typing import Any, ClassVar

import numpy as np
from pydantic import BaseModel

from pacxio.engine.calibrated import assembled_params, calibrated_params
from pacxio.engine.net import Net, base_bbox, build_net, outline
from pacxio.engine.recipe import StructureParams
from pacxio.engine.targets import closure_targets, cover_faces, tube_targets
from pacxio.messages import t
from pacxio.model.common import Issue
from pacxio.model.dieline import Dieline
from pacxio.model.profile import Material, MaterialType, Profile, load_profile
from pacxio.styles.base import get_style
from pacxio.styles.compensation import coefficients
from pacxio.styles.corrugated import assemble

FOLD_TOL_MM = 0.5  # 折好以后墙和盖离立体目标的最大偏差（纸厚分层另算）
END_ZH = {"open": "开口", "flaps": "摇盖", "lid_tuck": "插舌盖", "lid_glue": "粘合盖"}


def fold_plan_meta(net: Net) -> dict[str, Any]:
    return {
        "root": net.root,
        "moves": {
            pid: {"keys": [list(k) for k in m.keys], "layer": m.layer}
            for pid, m in net.moves.items()
        },
        "steps": net.steps,
        "open_step": net.open_step,
        "pose_step": 2,
        "ground_step": 2,
    }


def fold_deviation(
    dl: Dieline,
    targets: list[tuple[str, tuple[float, float], tuple[float, float, float]]],
    layers: bool = True,
) -> float:
    """按自动折法折到最后一步，墙、盖上的点和立体目标对齐后的最大偏差（mm）。
    layers=False：不算分层错开的纸厚（封口积木的目标是零厚度的立体）。"""
    from dataclasses import replace

    from pacxio.mockup.folding import (
        Move,
        apply,
        build_part,
        fit_pose,
        panel_matrices,
        plan_for,
    )

    plan = plan_for(dl)
    assert plan is not None
    if not layers:
        plan = replace(plan, moves={k: Move(m.keys) for k, m in plan.moves.items()})
    part = build_part(dl, plan)
    mats = panel_matrices(part, plan.steps)
    pairs = [(apply(mats[pid], x, y), tgt) for pid, (x, y), tgt in targets]
    walls = [
        pair
        for pair, (pid, _, _) in zip(pairs, targets, strict=True)
        if dl.panel(pid).role.value == "body"
    ]
    pose = fit_pose(walls)
    dev = 0.0
    for src, tgt in pairs:
        q = pose @ np.array([*src, 1.0])
        dev = max(dev, float(np.linalg.norm(q[:3] - np.array(tgt))))
    return dev


NATIVE_KINDS = frozenset({"open", "flaps", "lid_tuck", "lid_glue"})


def plan_meta(plan: Any) -> dict[str, Any]:
    """FoldPlan → 刀版自带的折法（JSON）。"""
    return {
        "root": plan.root,
        "moves": {
            pid: {"keys": [list(k) for k in m.keys], "layer": m.layer}
            for pid, m in plan.moves.items()
        },
        "steps": plan.steps,
        "open_step": plan.open_step,
        "pose_step": plan.pose_step,
        "ground_step": plan.ground_step,
    }


def _assembler(params: Any) -> Any:
    """积木拼装的参数 → 拼装器：只有一套长方筒身拼装器（卡纸、瓦楞都用，§20 第三阶段）。"""
    from pacxio.engine.slotted import SlottedAssembled

    return SlottedAssembled()


def _slot_wall(spec: Any) -> str:
    from pacxio.engine.slotted import wall_on

    return wall_on(spec)


MATURITY_ZH = {"calibrated": "已校准", "standard": "标准款（按示意图推读）", "custom": "自定义结构"}


def maturity(info: dict[str, Any]) -> dict[str, str]:
    """结构成熟度（）：已校准 = 走校准过的做法（对过参考刀版）；标准款 = 认出了 FEFCO / ECMA 编号，
    但几何按目录示意图 / 公开资料推读、数值【初值】；自定义 = 引擎按配方生成、不是标准款。"""
    src = info.get("calibrated_from")
    code = info.get("fefco") or info.get("ecma")
    catalog = "FEFCO" if info.get("fefco") else "ECMA" if info.get("ecma") else ""
    # plain：不带编号的说法，给规格书 / 检查报告用（：发给工厂的规格书不写参照编号）
    if src:
        tail = f"，对应 {catalog} {code}" if code else ""
        return {"level": "calibrated", "label": f"已校准（沿用 {src} 的参考刀版比例{tail}）",
                "plain": "已校准（对过参考刀版）", "code": code or ""}  # fmt: skip
    if code:
        return {"level": "standard", "label": f"标准款 {catalog} {code}（按目录示意图 / 公开资料推读，数值【初值】）",
                "plain": "标准结构（按目录示意图推读，数值【初值】）", "code": code}  # fmt: skip
    return {"level": "custom", "label": "自定义结构（引擎按配方生成，不是目录标准款）",
            "plain": "自定义结构（引擎按配方生成）", "code": ""}  # fmt: skip


class StructureStyle:
    id: ClassVar[str] = "structure"
    name_zh: ClassVar[str] = "结构配方（几何引擎）"
    refs: ClassVar[dict[str, str]] = {
        "design": "几何引擎按配方生成（），第 0 步"
    }
    material_types: ClassVar[frozenset[MaterialType]] = frozenset({"folding_carton", "corrugated"})
    version: ClassVar[str] = "0.0.3"  # 0.0.3：卡纸插舌盖的压线两端改成切口（2026-11-07）
    experimental: ClassVar[bool] = True  # 第 0 步：不进 styles list / 结构库

    def param_schema(self) -> type[BaseModel]:
        return StructureParams

    def advisories(
        self, params: Any, material: Material, profile: Profile | None = None
    ) -> list[Issue]:
        p: StructureParams = params
        if (  # 没写接头的横放开槽箱（旧 0410 job）按糊口做；明写 seam.kind=glue 是有意粘接，不提示
            "seam" not in p.model_fields_set
            and material.type == "corrugated"
            and p.body.form == "tube"
            and p.body.base == "rect"
            and p.body.axis == "L"
            and p.seam.kind == "glue"
            and p.seam.offset is None
            and not p.seam.partition
            and all(
                e.kind == "flaps"
                and e.flap == "half"
                and not e.slot_options()
                and e.on in (None, 2)
                for e in (p.top, p.bottom)
            )
        ):
            return [Issue(id="S04", message=t("No joint was specified for the horizontal slotted container. A glue flap is used. The FEFCO 0410 drawing shows an unglued tuck tab (pending verification)."),
                          fix=t("Set seam.kind=tuck to match the catalogue and reconfirm the dieline. Set seam.kind=glue to specify a glued joint."))]  # fmt: skip
        found = calibrated_params(params, material)
        out: list[Issue] = []
        if isinstance(found, tuple):
            from pacxio.styles.base import style_advisories

            out = style_advisories(
                get_style(found[0]), found[1], material, profile or load_profile("default")
            )
        try:
            m = self.generate(params, material, profile or load_profile("default")).style_meta[
                "structure"
            ]["maturity"]
        except Exception:  # noqa: BLE001 — 生成不了的配方由 validate 报错，这里不重复
            return out
        if m["level"] != "calibrated":
            out.append(Issue(id="S04", message=t("Structure: {maturity}. Tab fit, bottom locking, and dimensions are provisional pending a folded sample.", maturity=(t("Standard structure with provisional values inferred from the catalogue drawing") if m["level"] == "standard" else t("Custom structure generated from the recipe"))),
                             fix=t("Make a 1:1 or factory sample before ordering to confirm that the carton folds and locks")))  # fmt: skip
        return out

    def _net(self, p: StructureParams, material: Material, profile: Profile) -> Net:
        k = coefficients(profile, material, p.apply_compensation)
        return build_net(p, material.caliper_mm, k, material.type == "corrugated")

    def validate(
        self, params: Any, material: Material, profile: Profile | None = None
    ) -> list[Issue]:
        """各做法自己的参数范围检查；都过了再试生成一次——极端尺寸 / 材料下面和面的轮廓会交叠
        （墙很高时角翼、斜压线交叉，卡纸做瓦楞托盘时插脚和底槽对不上……），这里改成说得清的 S03，
        不让生成时才抛异常（能力边界测试 2026-11-06）。"""
        issues = self._validate(params, material, profile)
        if issues:
            return issues
        try:
            dl = self.generate(params, material, profile or load_profile("default"))
        except Exception as e:  # noqa: BLE001 — 任何生成错误都转成说得清的拒绝
            from shapely.errors import GEOSException

            why = (
                t("The panel outlines overlap and cannot form a valid dieline")
                if isinstance(e, GEOSException)
                else str(e)
            )
            hint = (
                t(" (common when using folding carton board for a corrugated structure)")
                if material.type == "folding_carton"
                else ""
            )
            return [Issue(id="S03", message=t("This structure cannot be built at these dimensions with this material: {reason}{hint}", reason=why, hint=hint),
                          fix=t("Change the dimension ratios, choose a different material, or select another structure"))]  # fmt: skip
        if (
            params.body.form == "sheet"
        ):  # 片材两头卷的管 / 折角：尺寸不够时折好会撞在一起（0942 要 W > 2H）
            from pacxio.engine import function_check

            folded = function_check.fold(dl)
            hits = function_check.piercing(folded) if folded is not None else []
            if hits:
                a, b = hits[0].panels
                return [Issue(id="S03", message=t("At these dimensions, {first} and {second} collide when folded. The rolls or folded corners at the ends lack clearance.", first=a, second=b),
                              fix=t("Increase the middle span (for example W) or reduce the end roll size (for example H). For 0942 and similar structures, W must be comfortably greater than 2H."))]  # fmt: skip
        return []

    def _validate(
        self, params: Any, material: Material, profile: Profile | None = None
    ) -> list[Issue]:
        p: StructureParams = params
        if material.type not in self.material_types:
            return [Issue(id="S03", message=t("Structure recipes support folding carton and corrugated board. The current material type is {material}.", material=material.type),
                          fix=t("Use folding carton or corrugated board"))]  # fmt: skip
        if p.body.form == "sheet":
            from pacxio.engine import sheet

            return sheet.problems(p, material.caliper_mm)
        tray = self._tray_params(p, material)
        if isinstance(tray, str):
            return [
                Issue(
                    id="S03",
                    message=tray,
                    fix=t("Adjust the recipe to the tray parameters' allowed ranges"),
                )
            ]
        if tray is not None:
            from pacxio.engine import tray as tray_mod

            return tray_mod.validate(tray, material, profile or load_profile("default"))
        from pacxio.engine.calibrated import slotted_features

        options = slotted_features(p)
        rect_tube = p.body.form == "tube" and p.body.base == "rect"
        if options and not rect_tube:  # 这些只在长方筒身上有（彩盒、瓦楞都行，走开槽箱拼装器）
            return [Issue(id="S03", message=t("{options} apply only to rectangular tube bodies", options=', '.join(options)),
                          fix=t("Remove these options or use a rectangular tube body"))]  # fmt: skip
        found = calibrated_params(p, material)
        if isinstance(found, str):
            return [
                Issue(
                    id="S03",
                    message=found,
                    fix=t(
                        "Adjust the recipe or tuning to the allowed ranges for this construction"
                    ),
                )
            ]
        if found is not None:
            return get_style(found[0]).validate(found[1], material, profile)
        blocks = assembled_params(p, material)
        if isinstance(blocks, str):
            return [
                Issue(
                    id="S03",
                    message=blocks,
                    fix=t("Adjust the recipe or tuning to the allowed ranges for these components"),
                )
            ]
        if blocks is not None:
            issues: list[Issue] = _assembler(blocks).validate(blocks, material, profile)
            return issues
        kinds = {p.top.kind, p.bottom.kind}
        if p.body.form != "tube" or not kinds <= NATIVE_KINDS or p.dim_basis != "inner" or p.tuning:
            return [Issue(id="S03", message=t("The recipe matches neither a calibrated construction nor the rectangular carton components (folding carton with closures on the front or back). Native generation supports tube bodies with open, flap, tuck, or glued ends, using inside dimensions and no tuning."),
                          fix=t("Use rectangular carton components (open, flaps, reduced, seal, lid_tuck, hanger, side_tuck, snaplock, autolock, or gable), or use a supported native closure for a polygonal body"))]  # fmt: skip
        try:
            net = self._net(p, material, profile or load_profile("default"))
            outline(net)
        except ValueError as e:
            return [
                Issue(
                    id="S03",
                    message=str(e),
                    fix=t(
                        "Check the body dimensions, the wall each closure attaches to, and the flap depth"
                    ),
                )
            ]
        out = []
        for end_name, end in (("top", p.top), ("bottom", p.bottom)):
            if (
                end.kind in ("lid_tuck", "lid_glue")
                and end.on is not None
                and end.on >= net.prism.n
            ):
                out.append(Issue(id="S03", message=t("The {end} lid attaches to wall {wall}, but the body has only {count} walls", end=end_name, wall=end.on, count=net.prism.n),
                                 fix=t("Set on to 0–{maximum}", maximum=net.prism.n - 1)))  # fmt: skip
        return out

    def _tray_params(self, p: StructureParams, material: Material) -> Any | str | None:
        """几何引擎托盘：长方底 + 四面墙 + 墙角机构、开顶，而且对不上校准过的做法。"""
        from pydantic import ValidationError

        from pacxio.engine.tray import TrayParams

        b = p.body
        if (
            b.form != "tray"
            or b.base != "rect"
            or p.top.kind not in ("open", "hinged_lid", "flaps")
        ):
            return None
        if p.top.kind == "flaps":
            extra = set(p.top.model_dump(exclude_defaults=True)) - {"kind", "flap", "depth"}
            if p.top.flap not in ("half", "center", "full", "long") or extra:
                return t("Tray flaps support half, center, full, or long, with optional depth")
        if calibrated_params(p, material) is not None:
            return None
        corner = "flap" if b.corners == "glue" else b.corners
        if corner == "lock":
            return t(
                "Locking-tab corners currently require the calibrated tray_lock folding carton"
            )
        try:
            return TrayParams(L=b.L, W=b.W, H=b.H, dim_basis=p.dim_basis,
                              apply_compensation=p.apply_compensation, corner=corner,
                              corner_on=b.corner_on, corner_side=b.corner_side,
                              corner_depth=b.corner_depth, corner_diagonal=b.corner_diagonal,
                              wall_diagonal=b.wall_diagonal, rim=b.rim, rim_on=b.rim_on, lugs=b.lugs,
                              ear_shape=b.ear_shape,
                              double_walls=b.double_walls, inner_ears=b.inner_ears,
                              inner_foot=b.inner_foot, rim_ends=b.rim_ends,
                              triangle_posts=b.triangle_posts, inner_wings=b.inner_wings,
                              edge_slots=b.edge_slots, corner_meet=b.corner_meet, stack=b.stack,
                              lug_count=b.lug_count, stack_on=b.stack_on, end_height=b.end_height,
                              end_double=b.end_double, flap_drops=b.flap_drops, end_window=b.end_window,
                              corner_posts=b.corner_posts, front_double=b.front_double,
                              back_double=b.back_double, hand_holes=b.hand_holes, flap_slots=b.flap_slots,
                              front_return=b.front_return, flap_ears=b.flap_ears, end_roll=b.end_roll,
                              end_foot=b.end_foot, end_slits=b.end_slits, bevel=b.bevel,
                              bevel_regular=b.bevel_regular, bevel_on=b.bevel_on, bevel_join=b.bevel_join,
                              corner_hooks=b.corner_hooks,
                              strip=[s.model_dump() for s in b.strip] if b.strip is not None else None,
                              lid_front=p.top.lid_front, dust_ears=p.top.dust_ears, lid_on=p.top.lid_on,
                              lid_header=p.top.lid_header, dust_lock=p.top.dust_lock,
                              tear_window=p.top.tear_window, display_lid=p.top.display_lid,
                              cup_holes=b.cup_holes, front_low=b.front_low,
                              poly_sides=b.poly_sides, poly_flaps=b.poly_flaps, inside=b.inside,
                              base_holes=b.base_holes, open_taper=b.open_taper,
                              cross_flaps=("none" if p.top.kind != "flaps" else p.top.flap),
                              cross_side_depth=p.top.depth if p.top.kind == "flaps" else None,
                              lid=p.top.kind == "hinged_lid", lid_dust=p.top.dust, tuck=p.top.tuck_len,
                              dust_depth=p.top.dust_depth,
                              **dict(p.tuning))  # fmt: skip
        except ValidationError as e:
            bad = "；".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors())
            return t("Invalid tray parameters: {detail}", detail=bad)

    def expected_panel_sizes(
        self, params: Any, material: Material, profile: Profile
    ) -> dict[str, Any]:
        tray = self._tray_params(params, material)
        if tray is not None and not isinstance(tray, str):
            from pacxio.engine import tray as tray_mod

            return tray_mod.sizes(tray, material, profile)
        if params.body.form == "sheet":
            from pacxio.engine import sheet

            along, horiz = sheet.along_len(params), sheet.horizontal(params)
            return {f"panel_{i + 1}": (along, w) if horiz else (w, along)
                    for i, w in enumerate(sheet.strips(params))}  # fmt: skip
        found = calibrated_params(params, material)
        if isinstance(found, tuple):
            return get_style(found[0]).expected_panel_sizes(found[1], material, profile)
        blocks = assembled_params(params, material)
        if blocks is not None and not isinstance(blocks, str):
            sizes: dict[str, Any] = _assembler(blocks).expected_panel_sizes(
                blocks, material, profile
            )
            return sizes
        net = self._net(params, material, profile)
        return {pid: (net.prism.width(i), net.prism.h) for i, pid in enumerate(net.names)}

    def generate(self, params: Any, material: Material, profile: Profile) -> Dieline:
        dl = self._generate(params, material, profile)
        info = dl.style_meta.setdefault("structure", {})
        if "calibrated_from" not in info:
            from pacxio.engine.catalog import features
            from pacxio.engine.i18n import closure_en

            try:
                closure = closure_en(features(params.model_dump(mode="json"))[1])
            except KeyError:  # A display name should never block generating a dieline.
                dl.style_meta["name_en"] = "Structure recipe"
            else:
                dl.style_meta["name_en"] = f"Structure recipe ({closure})"
        info["maturity"] = maturity(info)
        if params.corner_radius:  # 外轮廓自由角倒圆角（engine/rounding.py）
            from pacxio.engine.rounding import round_corners

            info["rounded_corners"] = round_corners(dl, params.corner_radius)
        return dl

    def _generate(self, params: Any, material: Material, profile: Profile) -> Dieline:
        p: StructureParams = params
        if p.body.form == "sheet":
            return self._sheet(p, material, profile)
        tray = self._tray_params(p, material)
        if tray is not None and not isinstance(tray, str):
            return self._tray(p, tray, material, profile)
        found = calibrated_params(p, material)
        if isinstance(found, tuple):
            return self._calibrated(p, found[0], found[1], material, profile)
        blocks = assembled_params(p, material)
        if blocks is not None and not isinstance(blocks, str):
            return self._assembled(p, blocks, material, profile)
        return self._native(p, material, profile)

    def _assembled(self, p: StructureParams, params: Any, material: Material,
                   profile: Profile) -> Dieline:  # fmt: skip
        """长方筒身 + 封口积木：积木声明折法和折好后每块面的位置，引擎折一遍核对。"""
        from pacxio.engine import ecma, fefco
        from pacxio.engine.slotted import SlottedAssembled
        from pacxio.engine.slotted import fold_plan as slotted_plan

        dl, blocks = SlottedAssembled().generate(params, material, profile)
        bottom, top = blocks["bottom"], blocks["top"]
        tube = dl.style_meta.pop("tube", None)  # 接头在墙中间：墙分两截
        dl.style_meta["fold_plan"] = slotted_plan(
            bottom, top, any(q.id == "glue" for q in dl.panels), tube["ring"] if tube else None
        )
        end_specs = {k: e.model_dump(exclude_none=True) | {"on": e.on or wall}
                for k, e, wall in (("top", params.top_end, _slot_wall(params.top_end)),
                                   ("bottom", params.bottom_end, _slot_wall(params.bottom_end)))}  # fmt: skip
        info = dl.style_meta["structure"] = {"assembled": True, "box": "targets",
                                             "walls": ["back", "side_a", "front", "side_b"], **end_specs}  # fmt: skip
        if tube:
            info["tube"] = tube
        poses = {**bottom.poses, **top.poses}
        covers = {k: b.cover for k, b in (("top", top), ("bottom", bottom)) if b.cover}
        joint = tube["joint"] if tube else "free_tab" if params.joint_free_tab else None
        code = fefco.code_of(
            info["top"], info["bottom"], joint, p.body.axis, fefco.body_extras(p.body)
        )
        if code is None and info["top"]["kind"] == info["bottom"]["kind"] == "open" and not tube:
            code = fefco.sleeve_code(p.body.axis)
        catalog = "fefco"
        plain = joint is None and p.body.axis == "H" and not fefco.body_extras(p.body)
        ecma_code = ecma.code_of(info["top"], info["bottom"]) if plain else None
        if ecma_code:  # 同一个拼装器：FEFCO、ECMA 编号都能认（§20 第三阶段）
            info["ecma"] = ecma_code
            dl.style_meta["refs"] = dl.style_meta["refs"] | {
                "ecma": f"{ecma_code}（按公开资料对照）"
            }
            if code is None:
                code, catalog = ecma_code, "ecma"
        tube = info.get("tube")
        walls = tube_targets(dl, tube)
        ends = closure_targets(dl, poses, tube)
        info["recipe"] = p.model_dump(mode="json")
        info[catalog] = code
        info["targets"] = [[pid, round(x, 6), round(y, 6), *(round(c, 6) for c in t)]
                           for pid, (x, y), t in walls + ends]  # fmt: skip
        info["faces"] = cover_faces(dl, poses, covers, tube)
        info["fold_deviation_mm"] = round(fold_deviation(dl, walls + ends, layers=False), 4)
        if code:
            note = "按公开资料对照" if catalog == "ecma" else "按 FEFCO 目录第 12 版对照"
            dl.style_meta["refs"] = dl.style_meta["refs"] | {catalog: f"{code}（{note}）"}
        return dl

    def _tray(self, p: StructureParams, tray: Any, material: Material, profile: Profile) -> Dieline:
        """几何引擎托盘：积木给每块面的立体目标，按自带折法折一遍核对。"""
        from pacxio.engine import fefco
        from pacxio.engine import tray as tray_mod

        dl = tray_mod.generate(tray, material, profile, self.id)
        info = dl.style_meta["structure"]
        info["recipe"] = p.model_dump(mode="json")
        targets = [(pid, (x, y), (X, Y, Z)) for pid, x, y, X, Y, Z in info["targets"]]
        info["fold_deviation_mm"] = round(fold_deviation(dl, targets, layers=False), 4)
        pair_inner = p.body.double_walls == "pair" and p.body.inner_ears
        if p.body.double_walls == "all" or p.body.ear_shape == "full" or pair_inner:
            from pacxio.engine.assembly import tray_inner_clearance, tray_lug_alignment

            if p.body.double_walls == "all" or (pair_inner and p.body.lugs):
                info["lug_alignment"] = tray_lug_alignment(dl)
            info["inner_clearance"] = tray_inner_clearance(dl, tray.L, tray.W)
        info["fefco"] = fefco.tray_code(p)
        if info["fefco"]:
            dl.style_meta["refs"] = dl.style_meta["refs"] | {
                "fefco": f"{info['fefco']}（按 FEFCO 目录第 12 版对照）"
            }
        return dl

    def _sheet(self, p: StructureParams, material: Material, profile: Profile) -> Dieline:
        """片材（FEFCO 01）：一张板 + 平行折线；按横截面核对折好的样子。"""
        from pacxio.engine import fefco, sheet

        dl, targets = sheet.generate(p, material, profile, self.id)
        names = [q.id for q in dl.panels]
        info: dict[str, Any] = {"sheet": True, "box": "targets", "walls": names,
                                "recipe": p.model_dump(mode="json"), "roll": p.body.roll}  # fmt: skip
        info["targets"] = [[pid, round(x, 6), round(y, 6), *(round(c, 6) for c in t)]
                           for pid, (x, y), t in targets]  # fmt: skip
        dl.style_meta["structure"] = info
        info["fold_deviation_mm"] = round(fold_deviation(dl, targets, layers=False), 4)
        info["fefco"] = fefco.sheet_code(p)
        if info["fefco"]:
            dl.style_meta["refs"] = dl.style_meta["refs"] | {
                "fefco": f"{info['fefco']}（按 FEFCO 目录第 12 版对照）"
            }
        return dl

    def _calibrated(self, p: StructureParams, style_id: str, params: Any, material: Material,
                    profile: Profile) -> Dieline:  # fmt: skip
        """校准过的做法：几何由现有算法出（数值不重写），引擎补折法、立体目标和折回自检。"""
        from pacxio.mockup.folding import plan_for

        dl = get_style(style_id).generate(params, material, profile)
        plan = plan_for(dl)
        dl = dl.model_copy(update={"style": self.id})
        meta = dl.style_meta
        meta["structure"] = {"calibrated_from": style_id, "form": p.body.form, "top": p.top.kind,
                             "bottom": p.bottom.kind, "recipe": p.model_dump(mode="json")}  # fmt: skip
        if plan is not None:
            meta["fold_plan"] = plan_meta(plan)
        if p.body.form == "tube" and plan is not None:
            targets = tube_targets(dl)
            meta["structure"]["fold_deviation_mm"] = round(fold_deviation(dl, targets), 4)
        if (
            style_id == "fefco_0427" and plan is not None
        ):  # 校准过的飞机盒：按面名给立体目标、折一遍核对
            from pacxio.engine.tray import mailer_targets

            targets = mailer_targets(dl)
            info = meta["structure"]
            info["targets"] = [[pid, round(x, 6), round(y, 6), *(round(c, 6) for c in t)]
                               for pid, (x, y), t in targets]  # fmt: skip
            info["fold_deviation_mm"] = round(fold_deviation(dl, targets, layers=False), 4)
            info["fefco"] = "0427"
        if style_id == "crashlock_rsc":  # 校准过的自动锁底开槽箱就是 FEFCO 0711（½W 顶 + 钩齿锁底）
            meta["structure"]["fefco"] = "0711"
        return dl

    def _native(self, p: StructureParams, material: Material, profile: Profile) -> Dieline:
        net = self._net(p, material, profile)
        shape = outline(net)
        minx, miny, _, _ = shape.bbox()
        inner = base_bbox(p)
        meta: dict[str, Any] = {
            "name_zh": f"{self.name_zh}（{END_ZH[p.top.kind]}顶 + {END_ZH[p.bottom.kind]}底）",
            "refs": self.refs,
            "version": self.version,
            "accuracy_level": "L0",
            "compensation_applied": p.apply_compensation,
            "dims": {"Li": inner[0], "Wi": inner[1], "Hi": p.body.H},
            "fold_plan": fold_plan_meta(net),
            "structure": {
                "native": True,
                "walls": net.names,
                "sides": net.prism.n,
                "base": p.body.base,
                "top": p.top.kind,
                "bottom": p.bottom.kind,
            }
            | net.meta,  # fmt: skip
            "assumptions": [
                "几何引擎第 0 步：盒身、封口、折法都按配方算，机构尺寸全部【初值】，要折样确认",
            ],
        }
        dl = assemble(style_id=self.id, params=p, material=material, profile=profile, outline=shape,
                      polys=net.panels, creases=net.creases, inner_cuts=[], ups={},
                      provisional=set(net.panels), meta=meta)  # fmt: skip
        targets = [(pid, (x - minx, y - miny), tgt) for pid, (x, y), tgt in net.targets]
        ox = min(t[0] for _, _, t in targets)
        oy = min(t[1] for _, _, t in targets)
        dl.style_meta["structure"]["targets"] = [
            [
                pid,
                round(x, 6),
                round(y, 6),
                round(t[0] - ox, 6),
                round(t[1] - oy, 6),
                round(t[2], 6),
            ]
            for pid, (x, y), t in targets
        ]  # 刀版上一点 → 立体上的位置（原点在立体的左前下角）：摆正效果图、检查折叠用
        dl.style_meta["structure"]["fold_deviation_mm"] = round(fold_deviation(dl, targets), 4)
        return dl


END_ZH.update({"reduced": "缩小翼", "side_tuck": "侧插盖", "seal": "粘合封口", "snaplock": "扣底", "autolock": "自动锁底", "hanger": "挂板插舌",
               "gable": "山形手提", "mailer": "飞机盒盖", "hinged_lid": "连体翻盖"})  # fmt: skip


__all__ = ["FOLD_TOL_MM", "StructureStyle", "fold_deviation", "tube_targets"]
