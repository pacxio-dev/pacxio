"""实用性检查（几何引擎第 5 步 ④，GEOMETRY_ENGINE.md §1.1 第 3 步）：几何能折之外，这个结构好不好用。

查五件事，逐条对照用户的需求：
- 机构功能核对（`function_check`）：折好后能不能合上、扣上，有没有面互相穿过——不过就是这组尺寸下不能用；
- 靠什么合上、能不能反复开合、开口那头和底要不要胶带 / 胶（从配方推出来，和结构目录同一套说法）；
- 折好后有没有面互相穿插（`fold_clashes`：按刀版折到最后一步，查面和面穿过去的地方。目前也会把
  “几层纸叠在一起”的渲染近似算进去，所以只作提示，不当错误；已校准的飞机盒 0427 也有几处）；
- 成熟度（已校准 / 标准款按示意图推读 / 自定义）；
- 上机、承重：引擎不算，如实说要工厂确认。
"""

from __future__ import annotations

from typing import Any

from pacxio.messages import display_name, lang, t
from pacxio.model.dieline import Dieline


def check(style_id: str, params: dict[str, Any], dls: list[Dieline], needs: list[str] | None = None,
          load_kg: float | None = None) -> dict[str, Any]:  # fmt: skip
    """配方 + 生成好的刀版 → 实用性报告 {closure, reclosable, no_tape, maturity, clashes, needs, advice}。"""
    from pacxio.engine import catalog, function_check
    from pacxio.engine.sets import PART_ZH
    from pacxio.mockup.foldcheck import fold_clashes

    if style_id == "structure_set":
        kinds = [catalog.features(part)[1:4] for part in params["parts"].values()]
        closure = t("; ").join(
            t("{part}: {closure}", part=display_name(PART_ZH.get(name, name)), closure=_closure(k[0]))
            for name, k in zip(params["parts"], kinds, strict=True)
        )
        reclosable: bool | None = params["set"] in ("telescope", "caps", "slide") or None
        no_tape = all(k[2] for k in kinds)
    else:
        _, closure, reclosable, no_tape, _ = catalog.features(params)
        closure = _closure(closure)
    levels = [(dl.style_meta.get("structure") or {}).get("maturity", {}) for dl in dls]
    level = "calibrated" if levels and all(m.get("level") == "calibrated" for m in levels) else (
        "custom" if any(m.get("level") == "custom" for m in levels) else "standard")  # fmt: skip
    clashes = []
    for dl in dls:
        part = dl.style_meta.get("part")
        for c in fold_clashes(dl):
            clashes.append({"part": part, "a": c.a, "b": c.b, "area_mm2": round(c.area_mm2, 1)})
    function = []  # 机构功能核对（折好后能不能合上 / 扣上，function_check）
    for dl in dls:
        part = dl.style_meta.get("part")
        recipe = params["parts"][part] if part is not None and "parts" in params else params
        res = function_check.check(dl, recipe)
        function += [{"part": part, **x} for x in res["findings"]]
    rows: list[dict[str, Any]] = []
    for need in needs or []:
        if need == "reclosable":
            label = t("Can be reopened and closed again")
            ok = bool(reclosable)
            why = closure if ok else t("{closure} — closing needs tape / glue, or it opens only once", closure=closure)
        elif need == "no_tape":
            label = t("Closes without tape or glue")
            ok = bool(no_tape)
            why = closure
        elif need == "single":
            label = t("Single piece (no multi-part set)")
            ok = style_id != "structure_set"
            why = t("Single piece") if ok else t("Multi-part set")
        else:
            rows.append({"need": need, "met": None, "why": t("The engine does not recognise this need")})
            continue
        rows.append({"need": need, "need_name": label, "met": ok, "why": why})
    advice = []
    if function:
        advice.append(t("{n} mechanism check(s) failed (does not close / lock, or panels pass through each other once folded): "
                        "not usable at this size; preflight will report S07 and no delivery pack is made. Change the size or choose another structure",
                        n=len(function)))  # fmt: skip
    if level != "calibrated":
        advice.append(t("Not calibrated by a folded sample (tuck fit, lock bottom and set clearances are provisional): make a proof before ordering"))
    if level == "custom":
        advice.append(t("Custom structure: mark it \"non-standard\" and confirm with the factory whether they can make it and how they price it"))
    if clashes:
        advice.append(
            t("{n} place(s) where panels intersect once folded (may be an approximation of stacked board layers, or a real collision): check the 3D view and confirm with a proof", n=len(clashes))
        )
    advice.append(t("Whether it runs on machines (folder-gluer, auto forming) is for the factory to confirm; 07 pre-glued structures need a folder-gluer"))
    if load_kg is not None:
        advice.append(t("Load {load} kg: the engine does not calculate compression / load strength; ask the factory to check it against the flute and board grade", load=f"{load_kg:g}"))
    return {"closure": closure, "reclosable": reclosable, "no_tape": no_tape, "maturity": level,
            "usable": not function, "function": function, "clashes": clashes, "needs": rows, "advice": advice,
            "all_needs_met": all(r["met"] for r in rows if r["met"] is not None)}  # fmt: skip


def _closure(text: str) -> str:
    """目录说法“怎么合上”是中文片段拼的；英文时按片段翻。"""
    if lang() != "en":
        return text
    from pacxio.engine.i18n import closure_en

    return closure_en(text)


__all__ = ["check"]
