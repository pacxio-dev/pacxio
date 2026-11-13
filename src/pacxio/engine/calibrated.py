"""校准过的机构（几何引擎第 0 步收尾）：配方能对上现有校准过的做法时，直接用现有算法出刀版——
数值不重写（插舌头 v2、防尘翼卡口、开槽箱摇盖间隙、飞机盒卷边……都是按标准刀版校准过的），
引擎再补上自动折法、立体目标和折回自检。对不上的（多边形、新组合）走引擎原生生成（net.py）。

对得上的：
- 长方彩盒筒身：顶 插舌盖（挂背面）/ 挂板插舌 / 四翼粘合 / 山形手提 × 底 插舌盖（反插 / 同向插）/ 扣底 / 自动锁底 / 四翼粘合
- 长方瓦楞筒身：两端摇盖（对口 0201、部分重叠 0202、全重叠 0203）、开顶 0200、自动锁底 + 摇盖顶（自动锁底开槽箱）
- 长方托盘：瓦楞飞机盒 0427；纸盒糊角托盘、扣舌托盘、连体翻盖托盘
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from pacxio.engine.recipe import End, StructureParams
from pacxio.messages import t as translate
from pacxio.model.profile import Material

# 筒身墙序：0 背、1 侧 a、2 正、3 侧 b（同 solid.rect_polygon）
BACK, FRONT = 0, 2


def _carton_top(end: End) -> str | None:
    """现有组合彩盒的顶：插舌盖挂背面、挂板在背面（盖在正面）、粘合外翼在正面、山形；别的面号走积木拼装。"""
    if end.kind == "lid_tuck" and end.on in (None, BACK):
        return "tuck"
    if end.kind == "hanger" and end.on in (None, BACK) and end.hanger_layers != 2:
        return "hanger"  # 现有挂钩盒是单层挂板；双层走积木拼装
    if end.kind == "seal" and end.on in (None, FRONT):
        return "seal"
    return "gable" if end.kind == "gable" else None


def _carton_bottom(end: End, top: str) -> str | None:
    if end.kind in ("snaplock", "seal") and end.on not in (None, FRONT):
        return None  # 扣底锁舌翼 / 粘合外翼挂背面：现有做法没有，走积木拼装
    if end.kind in ("snaplock", "autolock", "seal"):
        return end.kind
    if end.kind != "lid_tuck":
        return None
    top_wall = FRONT if top == "hanger" else BACK  # 插舌顶盖挂的面；粘合 / 山形顶没有盖，按挂背面算
    reverse = FRONT if top_wall == BACK else BACK
    on = reverse if end.on is None else end.on
    if on == reverse:
        return "tuck"
    if on == top_wall:
        return "tuck_same"
    return None


def match(p: StructureParams, material: Material) -> tuple[str, dict[str, Any]] | None:
    """配方 → (校准过的做法, 它的参数)；对不上返回 None。"""
    from pacxio.styles.carton import EQUIVALENT

    b = p.body
    if b.double_walls != "pair" or b.inner_ears:
        return None
    if b.base != "rect" or p.seam.kind != "glue" or b.form == "sheet":
        return None
    corrugated = material.type == "corrugated"
    params: dict[str, Any] = {"L": b.L, "W": b.W, "H": b.H, "dim_basis": p.dim_basis,
                              "apply_compensation": p.apply_compensation}  # fmt: skip
    style: str | None = None
    if b.form == "tube" and not corrugated:
        top = _carton_top(p.top)
        bottom = _carton_bottom(p.bottom, top) if top else None
        if top is None or bottom is None:
            return None
        style = EQUIVALENT.get((top, bottom), "carton")
        if style == "carton":
            params |= {"top": top, "bottom": bottom}
        if p.seam.width is not None:
            params["glue_w"] = p.seam.width
        tucks = {e.tuck_len for e in (p.top, p.bottom) if e.tuck_len is not None}
        dusts = {e.dust_depth for e in (p.top, p.bottom) if e.dust_depth is not None}
        if len(tucks) > 1 or len(dusts) > 1:
            return None  # 现有彩盒两端插舌 / 防尘翼一样长
        params |= {"tuck_len": tucks.pop()} if tucks else {}
        params |= {"dust_depth": dusts.pop()} if dusts else {}
    elif b.form == "tube":
        te, be = p.top, p.bottom
        if (te.slot_options() or be.slot_options() or p.seam.offset is not None or p.seam.partition
                or b.axis != "H" or b.window is not None or b.tear or be.autolock_form != "hook"):  # fmt: skip
            return None  # 侧翼 / 拇指缺口 / 锁舌 / 不带端摇盖 / 接头在墙中间 / 筒横放：现有做法没有，走积木拼装
        if (
            te.kind == "flaps"
            and be.kind == "flaps"
            and te.flap == be.flap
            and te.flap in ("half", "overlap", "full")
            and te.overlap == be.overlap
            and te.on in (None, FRONT)
            and be.on in (None, FRONT)
        ):
            style = {"half": "fefco_0201", "overlap": "fefco_0202", "full": "fefco_0203"}[te.flap]
            if te.flap == "overlap" and te.overlap > 0:
                params["flap_overlap"] = te.overlap  # 不给按做法的默认（20）
        elif (
            te.kind == "open"
            and be.kind == "flaps"
            and be.flap == "half"
            and be.on in (None, FRONT)
        ):
            style = "fefco_0200"
        elif (
            te.kind == "flaps"
            and te.flap == "half"
            and te.on in (None, FRONT)
            and be.kind == "autolock"
        ):
            style = "crashlock_rsc"
        if style and p.seam.width is not None:
            params["joint_w"] = p.seam.width
    else:  # 托盘
        if corrugated and p.top.kind == "mailer":
            style = "fefco_0427"
        elif not corrugated and p.top.kind == "open" and b.corners in ("glue", "lock"):
            style = "tray_lock" if b.corners == "lock" else "tray_glued"
        elif not corrugated and p.top.kind == "hinged_lid" and b.corners == "glue":
            style = "hinged_lid"
    if style is None:
        return None
    return style, params | dict(p.tuning)


def _bottom_wall(p: StructureParams) -> int | None:
    """底部插舌盖没说挂哪面：和顶盖相对（反插）；顶上没有盖（粘合、山形、开口……）挂正面。"""
    b = p.bottom
    if b.kind != "lid_tuck" or b.on is not None:
        return b.on
    t = p.top
    if t.kind in ("lid_tuck", "side_tuck"):
        return FRONT if t.on in (None, BACK) else BACK
    if t.kind == "hanger":  # 挂板的盖在挂板对面
        return BACK if t.on in (None, BACK) else FRONT
    return FRONT


# 只有开槽箱拼装器才会做的东西（墙上开窗、接头位置……）：只用在长方筒身上（§20）
SLOTTED_ONLY_KINDS = frozenset({"display", "handle", "hang_mid", "hang_tab", "slope"})
CARTON_FLAP_RULES = frozenset({"half", "overlap", "full", "full_long"})


def slotted_features(p: StructureParams) -> list[str]:
    """配方里只有开槽箱拼装器才会做的东西（封口、摇盖规则、接头、开窗……）。"""
    out = [f"{name}.kind={e.kind}" for name, e in (("top", p.top), ("bottom", p.bottom))
           if e.kind in SLOTTED_ONLY_KINDS]  # fmt: skip
    out += [
        f"{name}.{o}"
        for name, e in (("top", p.top), ("bottom", p.bottom))
        for o in e.slot_options()
    ]
    out += [f"{name}.flap={e.flap}" for name, e in (("top", p.top), ("bottom", p.bottom))
            if e.kind == "flaps" and e.flap not in CARTON_FLAP_RULES]  # fmt: skip
    out += [
        f"seam.{k}"
        for k in ("offset", "partition", "center_open", "full_overlap")
        if getattr(p.seam, k)
    ]
    if p.seam.kind != "glue":
        out.append(f"seam.kind={p.seam.kind}")
    out += ["body.axis"] if p.body.axis != "H" else []
    out += [f"body.{k}" for k in ("window", "tear") if getattr(p.body, k)]
    out += [
        f"{n}.autolock_form"
        for n, e in (("top", p.top), ("bottom", p.bottom))
        if e.autolock_form != "hook"
    ]
    out += [f"{n}.tear_strip" for n, e in (("top", p.top), ("bottom", p.bottom)) if e.tear_strip]
    return out


def assembled_params(p: StructureParams, material: Material) -> Any | str | None:
    """长方筒身 + 封口积木：对得上返回参数，参数不合返回错误说明，对不上返回 None。
    只有一套拼装器（engine/slotted.py），卡纸、瓦楞都走它（§20 第三阶段）。"""
    b = p.body
    if b.form != "tube" or b.base != "rect":
        return None
    return _slotted_params(
        p, material
    )  # 唯一的长方筒身拼装器（§20 第三阶段）：不再按材料 / 配方分两套


def tube_dims(b: Any) -> tuple[float, float, float]:
    """筒身方向（axis）：哪个产品尺寸是筒长。H 竖着（常规）；W / L 横放——四面墙绕另外两个尺寸围一圈
    （FEFCO 0502 的面是 L、H、L、H，筒长 W；0503 的面是 W、H、W、H，筒长 L）。"""
    if b.axis == "W":
        return b.L, b.H, b.W
    if b.axis == "L":
        return b.W, b.H, b.L
    return b.L, b.W, b.H


def _slotted_params(p: StructureParams, material: Material | None = None) -> Any | str | None:
    """瓦楞开槽箱筒身 + 封口积木（engine/slotted.py）。"""
    from pacxio.engine.slotted import KINDS, SlottedAssembledParams

    if not {p.top.kind, p.bottom.kind} <= KINDS or p.seam.kind not in ("glue", "none", "tuck"):
        return None
    b = p.body
    walls = {0: "back", 2: "front", None: None}
    ends = []
    for end, on in ((p.top, p.top.on), (p.bottom, _bottom_wall(p))):
        if on not in walls:
            return None
        if end.thumb not in walls:
            return translate(
                "A thumb notch can only go on a long-side flap (thumb=0 back, thumb=2 front)"
            )
        spec: dict[str, Any] = {"kind": end.kind, "on": walls[on], "flap": end.flap,
                                "depth": end.depth, "drop": end.drop, "fold": end.fold,
                                "thumb": walls[end.thumb]}  # fmt: skip
        if end.kind == "flaps":
            spec |= {"wing": end.wing, "lock": end.lock, "end_flaps": end.end_flaps}
        if end.kind == "side_tuck":
            spec["wing"] = end.wing
        if end.glue is not None:
            spec["glue"] = end.glue
        if end.kind == "autolock":
            spec["form"] = end.autolock_form
        if end.tear_strip:
            spec["tear_strip"] = True
        if end.hanger_layers is not None:
            spec["hanger_layers"] = end.hanger_layers
        if end.flap == "overlap" and end.overlap > 0:
            spec["overlap"] = end.overlap
        ends.append({k: v for k, v in spec.items() if v is not None})
    L, W, H = tube_dims(b)
    values: dict[str, Any] = {"L": L, "W": W, "H": H, "dim_basis": p.dim_basis,
                              "apply_compensation": p.apply_compensation,
                              "top_end": ends[0], "bottom_end": ends[1]}  # fmt: skip
    if p.seam.width is not None:
        values["joint_w"] = p.seam.width
    elif (
        material is not None and p.seam.kind == "glue"
    ):  # 接舌宽按材料默认，小盒按侧面宽收窄（§20）
        from pacxio.engine.slotted import JOINT_W_ANY_RANGE, JOINT_W_SIDE_RATIO
        from pacxio.styles.fefco_0201 import (
            DOUBLE_WALL_FLUTES,
            JOINT_W_CARTON_MM,
            JOINT_W_DEFAULT_MM,
            JOINT_W_DOUBLE_WALL_MM,
        )

        base = (JOINT_W_CARTON_MM if material.type == "folding_carton"
                else JOINT_W_DOUBLE_WALL_MM if (material.flute or "").upper() in DOUBLE_WALL_FLUTES
                else JOINT_W_DEFAULT_MM)  # fmt: skip
        side = (b.L if b.axis == "W" else b.W) if b.W is not None and b.L is not None else None
        if side is not None:
            jw = min(base, max(JOINT_W_ANY_RANGE[0], JOINT_W_SIDE_RATIO * side))
            if jw < base:
                values["joint_w"] = round(jw, 2)
    if p.seam.kind == "tuck":
        values["joint_free_tab"] = True
    if b.window is not None or b.tear:
        values |= {"window": b.window, "window_shape": b.window_shape, "tear": b.tear}
    if p.seam.offset is not None or p.seam.partition or p.seam.center_open or p.seam.full_overlap:
        values |= {"joint_offset": p.seam.offset, "joint_partition": p.seam.partition,
                   "joint_center_open": p.seam.center_open,
                   "joint_full_overlap": p.seam.full_overlap,
                   "joint_wall": "back" if p.seam.wall == "long" else "side_b"}  # fmt: skip
    for name in ("tuck_len", "dust_depth"):
        found = {getattr(e, name) for e in (p.top, p.bottom) if getattr(e, name) is not None}
        if len(found) > 1:
            return translate("Both ends must use the same {parameter}", parameter=name)
        if found:
            values[name] = found.pop()
    try:
        return SlottedAssembledParams(**(values | dict(p.tuning)))
    except ValidationError as e:
        bad = "；".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors())
        return translate("Invalid slotted container component parameters: {detail}", detail=bad)


def calibrated_params(p: StructureParams, material: Material) -> tuple[str, Any] | str | None:
    """对上了返回 (做法, 校验过的参数)；参数不合那种做法时返回错误说明；对不上返回 None。"""
    from pacxio.styles import get_style

    found = match(p, material)
    if found is None:
        return None
    style_id, raw = found
    schema = get_style(style_id).param_schema()
    try:
        return style_id, schema(**{k: v for k, v in raw.items() if v is not None})
    except ValidationError as e:
        bad = "；".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors())
        return translate(
            "The recipe matches calibrated style {style}, but its parameters are invalid: {detail}",
            style=style_id,
            detail=bad,
        )


__all__ = ["assembled_params", "calibrated_params", "match"]
