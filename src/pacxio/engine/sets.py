"""多件套（几何引擎第 2 步，FEFCO 03 套盖 / 05 套盒）：一套配方 → 几张刀版 + 套合检查。

每个部件是一份普通的结构配方（托盘、筒身……），由同一个结构配方引擎生成。套在别的部件外面的那件
（天盖、盖帽、外套）不写长宽：按被套那件的外尺寸 + 间隙算出自己的内尺寸，所以改地盒尺寸，盖跟着变。

- telescope 天地盖：地盒 base + 天盖 lid（lid 套在 base 外面，深 = 盖深）
- caps      两头帽 + 中间件：中间件 body（套筒 / 半截开槽箱）+ 盖帽 cap（套在 body 两头，同一张刀版做两个）
- kit       组件（第 3 步，FEFCO 07 / 09）：几件按同一组盒子内尺寸各自生成、不套合——隔板组（纵 / 横隔板互相插）、
            盒 + 隔板、两片 U 形套交叉、阶梯内衬；counts 记每件做几片
接进流水线 / 工作台（部件随配方变）放到第 5 步；现在验收和测试直接调 generate_set。数值全部【初值】。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from pacxio.engine.recipe import StructureParams
from pacxio.messages import lang
from pacxio.messages import t as translate
from pacxio.model.common import Issue
from pacxio.model.dieline import Dieline
from pacxio.model.profile import Material, Profile

CLEARANCE_DEFAULT_MM = 3.0  # 套合间隙（盖内 − 被套件外）【初值】
SET_ZH = {"telescope": "天地盖", "caps": "两头帽", "slide": "套盒", "kit": "组件"}
PART_ZH = {
    "base": "地盒",
    "lid": "天盖",
    "body": "中间件",
    "cap": "盖帽",
    "inner": "里件",
    "outer": "外件",
    "box": "盒",
    "long": "纵隔板",
    "cross": "横隔板",
    "cross_low": "矮横隔板",
    "side": "侧板",
    "tread": "踏板",
    "wrap_l": "横套",
    "wrap_h": "竖套",
}


class Fit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    over: str = Field(description="套在哪个部件外面")
    clearance: float = Field(
        default=CLEARANCE_DEFAULT_MM, ge=0, description="盖内尺寸比被套件外尺寸大多少"
    )
    count: int = Field(default=1, ge=1, le=2, description="这张刀版做几个（盖帽上下各一个 = 2）")
    cover: Literal["LW", "box"] = Field(
        default="LW",
        description="LW 只按被套件的长宽定（天盖、盖帽，深度自己定）；box 长宽高都按被套件外形定（交叉套筒 0504）",
    )


class SetParams(BaseModel):
    """多件套配方。parts 里每件是一份结构配方；fits 里列出的件，长宽按被套件算（配方里写的长宽不用）。"""

    model_config = ConfigDict(extra="forbid")
    set: Literal["telescope", "caps", "slide", "kit"]
    parts: dict[str, StructureParams]
    fits: dict[str, Fit] = Field(default_factory=dict)
    counts: dict[str, int] = Field(
        default_factory=dict,
        description="组件（kit）：每件做几片（隔板组的纵 / 横隔板数）；不写 = 1，套合件按 fits.count",
    )


def outer_of(dl: Dieline) -> tuple[float, float]:
    """部件的外尺寸（长、宽）：压线中心距 + 一层纸厚。"""
    d, t = dl.style_meta["dims"], dl.caliper_mm
    if "outer" in d:
        return float(d["outer"][0]), float(d["outer"][1])
    if "base_l" in d:
        return d["base_l"] + t, d["base_w"] + t
    if (
        "panel_back_front" not in d
    ):  # 多边形筒：内对边距 + 两层纸厚；正多边形的盖帽按正多边形套（取小的那个方向）
        info = dl.style_meta.get("structure", {})
        base = info.get("base") or info.get("recipe", {}).get("body", {}).get("base")
        if base == "regular":
            a = min(float(d["Li"]), float(d["Wi"])) + 2 * t
            return a, a
        return float(d["Li"]) + 2 * t, float(d["Wi"]) + 2 * t
    return d["panel_back_front"] + t, d["panel_side"] + t


def inner_of(dl: Dieline) -> tuple[float, float]:
    d = dl.style_meta["dims"]
    return float(d["Li"]), float(d["Wi"])


def outer_box(dl: Dieline) -> tuple[float, float, float]:
    """部件外形（按产品的 L / W / H 方向）：压线中心距 + 一层纸厚；筒身按筒长方向（body.axis）换回来。"""
    d, t = dl.style_meta["dims"], dl.caliper_mm
    if "base_l" in d:
        outer = d.get("outer")
        return (
            (float(outer[0]), float(outer[1]), float(outer[2]))
            if outer
            else (d["base_l"] + t, d["base_w"] + t, d["wall_h"] + t)
        )
    a, b, h = d["panel_back_front"] + t, d["panel_side"] + t, d["body_height"]
    axis = dl.style_meta.get("structure", {}).get("recipe", {}).get("body", {}).get("axis", "H")
    return {"H": (a, b, h), "W": (a, h, b), "L": (h, a, b)}[axis]


def _fit(p: StructureParams, over: Dieline, fit: Fit) -> StructureParams:
    c = fit.clearance
    if fit.cover == "box":
        L, W, H = outer_box(over)
        body = p.body.model_copy(update={"L": L + c, "W": W + c, "H": H + c})
    else:
        outer = outer_of(over)
        body = p.body.model_copy(update={"L": outer[0] + c, "W": outer[1] + c})
    return p.model_copy(update={"body": body, "dim_basis": "inner"})


def _order(sp: SetParams) -> list[str]:
    """被套的件排在前面；套合关系绕成一圈时报错。"""
    done: list[str] = []
    while len(done) < len(sp.parts):
        ready = [n for n in sp.parts if n not in done
                 and (n not in sp.fits or sp.fits[n].over in done)]  # fmt: skip
        if not ready:
            raise ValueError(translate("The fit relationships form a cycle"))
        done += ready
    return done


def resolve(sp: SetParams, build: Any) -> dict[str, tuple[StructureParams, Dieline]]:
    """按套合顺序生成：先生成被套的件，再按它的外尺寸定盖的内尺寸。build(params) → Dieline。"""
    out: dict[str, tuple[StructureParams, Dieline]] = {}
    for name in _order(sp):
        p = sp.parts[name]
        fit = sp.fits.get(name)
        if fit is not None:
            p = _fit(p, out[fit.over][1], fit)
        out[name] = (p, build(p))
    return out


def problems(sp: SetParams) -> list[Issue]:
    out = []
    for name, n in sp.counts.items():
        if name not in sp.parts or not 1 <= n <= 20:
            out.append(Issue(id="S03", message=translate("counts.{part} = {count}: the part is missing from parts or the count is outside 1–20", part=name, count=n),
                             fix=translate("Correct counts")))  # fmt: skip
    for name, fit in sp.fits.items():
        if name not in sp.parts or fit.over not in sp.parts:
            out.append(
                Issue(
                    id="S03",
                    message=translate(
                        "Fit {part} → {over}: a part is missing from parts",
                        part=name,
                        over=fit.over,
                    ),
                    fix=translate("Correct fits"),
                )
            )
        elif name == fit.over:
            out.append(
                Issue(
                    id="S03",
                    message=translate("Part {part} cannot fit over itself", part=name),
                    fix=translate("Correct fits"),
                )
            )
    if not out:
        try:
            _order(sp)
        except ValueError as e:
            out.append(Issue(id="S03", message=str(e), fix=translate("Correct fits")))
    return out


def validate_set(sp: SetParams, material: Material, profile: Profile, style: Any) -> list[Issue]:
    """套的关系 + 每件按结构配方校验（套在外面的件按被套件生成后的外尺寸算）。"""
    out = problems(sp)
    if out:
        return out
    built: dict[str, Dieline] = {}
    for name in _order(sp):
        p = sp.parts[name]
        fit = sp.fits.get(name)
        if fit is not None:
            p = _fit(p, built[fit.over], fit)
        issues = style.validate(p, material, profile)
        if issues:
            return [i.model_copy(update={"message": translate("{part}: {message}", part=(f"{PART_ZH.get(name, name)} {name}" if lang() == "zh" else name), message=i.message)})
                    for i in issues]  # fmt: skip
        built[name] = style.generate(p, material, profile)
    return []


def part_params(
    sp: SetParams, material: Material, profile: Profile, style: Any
) -> dict[str, StructureParams]:
    """每件实际用的配方（套在外面的件已按被套件定好长宽）。"""
    return {
        k: p for k, (p, _) in resolve(sp, lambda q: style.generate(q, material, profile)).items()
    }


def check_fit(sp: SetParams, parts: dict[str, Dieline]) -> list[Issue]:
    """S05：按各件尺寸表核对配合——盖的内尺寸 − 被套件外尺寸 = 间隙（±0.1）。"""
    out = []
    for name, fit in sp.fits.items():
        if fit.cover == "box":  # 按配方的产品尺寸（内尺寸）比被套件外形
            body = parts[name].style_meta["structure"]["recipe"]["body"]
            pairs = list(zip(("长", "宽", "高"), (body["L"], body["W"], body["H"]), outer_box(parts[fit.over]),
                             strict=True))  # fmt: skip
        else:
            inner, outer = inner_of(parts[name]), outer_of(parts[fit.over])
            pairs = [("长", inner[0], outer[0]), ("宽", inner[1], outer[1])]
        for axis, a, b in pairs:
            if abs((a - b) - fit.clearance) > 0.1:
                out.append(Issue(id="S05", message=f"{name} 内{axis} {a:.1f} − {fit.over} 外{axis} {b:.1f} "
                                                   f"≠ 间隙 {fit.clearance:g}", fix="检查套合"))  # fmt: skip
    return out


def generate_set(
    sp: SetParams, material: Material, profile: Profile, style: Any
) -> dict[str, Dieline]:
    """每件按结构配方生成，套合的件按被套件外尺寸定尺寸；每张刀版记上这套的信息。"""
    built = resolve(sp, lambda p: style.generate(p, material, profile))
    info = {"set": sp.set, "name_zh": SET_ZH[sp.set], "parts": list(sp.parts),
            "fits": {k: v.model_dump() for k, v in sp.fits.items()}}  # fmt: skip
    out = {}
    for name, (_, dl) in built.items():
        dl.style_meta["set"] = info | {"part": name, "part_zh": PART_ZH.get(name, name),
                                       "count": sp.fits[name].count if name in sp.fits
                                       else sp.counts.get(name, 1)}  # fmt: skip
        out[name] = dl
    return out


__all__ = [
    "Fit",
    "SetParams",
    "check_fit",
    "generate_set",
    "inner_of",
    "outer_of",
    "problems",
    "resolve",
]
