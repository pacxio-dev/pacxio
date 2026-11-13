"""几何引擎验收：目标清单（）里的每一款，按配方生成、过检查、折回去核对。

    uv run python tools/engine_targets.py [--set ecma|fefco] [--json]

每一款在几组常见尺寸下都要：配方校验通过、刀版生成、S01（外轮廓闭合不自交、切口不出界、无重复线）、
压线两侧都有面、S02（面尺寸和参数推的一样）、带折法、按折法折好后每块面离目标 ≤ 0.5 mm（不算分层纸厚）；
部分托盘另外核对带分层折叠后的内壁距离和插脚 / 底槽对位；最后做机构功能核对（S07，`engine/function_check.py`：
折好后合不合得上、扣不扣得上、有没有面互相穿过），几何过了但核对不过记成 unusable。不代替实物验证。
某个尺寸下配方校验不通过（放不下）单独记一笔，不算失败——那是尺寸超出这种结构的适用范围。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pacxio.engine import ecma, fefco, function_check  # noqa: E402
from pacxio.engine.style import FOLD_TOL_MM  # noqa: E402
from pacxio.mockup.folding import plan_for  # noqa: E402
from pacxio.model.profile import load_profile, resolve_material  # noqa: E402
from pacxio.preflight.rules import check_crease_sides, check_s01, check_s02  # noqa: E402
from pacxio.styles import get_style  # noqa: E402

# 彩盒常见尺寸（内尺寸 L × W × H，mm）：小药盒、牙膏型长条、方盒、扁宽盒、大一点的礼盒
CARTON_SIZES = ((40, 25, 80), (180, 35, 40), (60, 60, 120), (150, 40, 100), (200, 120, 250))
# 瓦楞箱常见尺寸：小电商箱、标准箱、大箱、方箱（L = W）、扁长箱
CORRUGATED_SIZES = (
    (200, 150, 100),
    (400, 300, 250),
    (600, 400, 400),
    (300, 300, 300),
    (500, 150, 120),
)
# 托盘（FEFCO 04）常见尺寸：水果托、浅盘、标准托、方托、深一点的托
TRAY_SIZES = ((400, 300, 100), (600, 400, 150), (300, 200, 60), (300, 300, 80), (400, 300, 160))
# 内衬托板两头卷的管（0942 一类）：管高 H 远小于宽，要 W > 2H；常用箱子尺寸全都放不下时改用这几组
LOW_INSERT_SIZES = ((400, 600, 100), (600, 400, 150), (300, 500, 80), (500, 800, 200))
SETS = {
    "ecma": (ecma.PUBLIC_A, ecma.recipe, CARTON_SIZES, "sbs_350"),
    "fefco": (
        tuple(fefco.SHEETS)
        + tuple(fefco.STYLES)
        + tuple(fefco.TRAYS)
        + tuple(fefco.SLEEVES)
        + tuple(fefco.SETS)
        + tuple(fefco.MAILERS)
        + tuple(fefco.FOLDERS)
        + tuple(fefco.INSERTS)
        + tuple(fefco.NATIVE),
        fefco.recipe,
        CORRUGATED_SIZES,
        "flute_b",
    ),
}


def check_set(code: str, size: tuple[float, float, float]) -> dict[str, Any]:
    """多件套（FEFCO 03 / 05）：每件都过刀版检查和折回自检，套合尺寸对，反查出的编号就是这一款。"""
    from pacxio.engine.sets import SetParams, check_fit, generate_set, part_params, validate_set

    profile, mat, style = (
        load_profile("default"),
        resolve_material("flute_b"),
        get_style("structure"),
    )
    sp = SetParams(**fefco.set_recipe(code, *size))
    issues = validate_set(sp, mat, profile, style)
    if issues:
        return {"status": "out_of_range", "issues": [i.message for i in issues]}
    try:
        parts = generate_set(sp, mat, profile, style)
    except Exception as e:  # noqa: BLE001 — 验收要把任何生成错误都记下来
        return {"status": "error", "issues": [f"{type(e).__name__}: {e}"]}
    pps = part_params(sp, mat, profile, style)
    problems: list[str] = []
    devs = {}
    for name, dl in parts.items():
        errs = (
            check_s01(dl)
            + check_crease_sides(dl)
            + check_s02(dl, style.expected_panel_sizes(pps[name], mat, profile))
        )
        problems += [f"{name}：{e.message}" for e in errs]
        devs[name] = dev = dl.style_meta.get("structure", {}).get("fold_deviation_mm")
        if plan_for(dl) is None:
            problems.append(f"{name}：没有折法")
        if dev is None or dev > FOLD_TOL_MM:
            problems.append(f"{name}：折回偏差 {dev}")
    problems += [i.message for i in check_fit(sp, parts)]
    if fefco.set_code(sp) != code:
        problems.append(f"编号对不上：反查出 {fefco.set_code(sp)}")
    function = [i.message for name, dl in parts.items()
                for i in function_check.issues(dl, function_check.part_recipe(sp, name))]  # fmt: skip
    return {"status": _status(problems, function), "path": "set", "fold_deviation_mm": devs,
            "issues": problems + function}  # fmt: skip


def _status(problems: list[str], function: list[str]) -> str:
    """几何检查不过 = fail；几何过了但折好后机构不成立（S07）= unusable；都过 = pass。"""
    return "fail" if problems else "unusable" if function else "pass"


def check(code: str, size: tuple[float, float, float], which: str = "ecma") -> dict[str, Any]:
    if which == "fefco" and code in fefco.SETS:
        return check_set(code, size)
    _, recipe, _, material = SETS[which]
    profile = load_profile("default")
    mat = resolve_material(material)
    style = get_style("structure")
    p = style.param_schema()(**recipe(code, *size))
    issues = style.validate(p, mat, profile)
    if issues:
        return {"status": "out_of_range", "issues": [i.message for i in issues]}
    try:
        dl = style.generate(p, mat, profile)
    except Exception as e:  # noqa: BLE001 — 验收要把任何生成错误都记下来
        return {"status": "error", "issues": [f"{type(e).__name__}: {e}"]}
    errs = check_s01(dl) + check_crease_sides(dl)
    errs += check_s02(dl, style.expected_panel_sizes(p, mat, profile))
    info = dl.style_meta["structure"]
    dev = info.get("fold_deviation_mm")
    problems = [i.message for i in errs]
    if plan_for(dl) is None:
        problems.append("没有折法")
    if dev is None or dev > FOLD_TOL_MM:
        problems.append(f"折回偏差 {dev}")
    alignment = info.get("lug_alignment")
    if alignment and alignment.get("status") == "fail":
        problems.append(
            f"带分层的插脚 / 底槽偏离 {alignment['max_miss_mm']} mm：{alignment['misses_mm']}"
        )
    inner = info.get("inner_clearance")
    if inner and inner.get("status") == "fail":
        problems.append(f"带分层的内壁尺寸偏差 {inner['deviation_mm']} mm")
    path = info.get("calibrated_from") or ("assembled" if info.get("assembled") else "tray" if info.get("tray")
                                           else "native")  # fmt: skip
    if (info.get("assembled") or info.get("tray")) and info.get(which) != code:
        problems.append(f"编号对不上：生成的是 {info.get(which)}")
    function = [i.message for i in function_check.issues(dl, p.model_dump())]
    return {"status": _status(problems, function), "path": path, "fold_deviation_mm": dev,
            "issues": problems + function}  # fmt: skip


def run(which: str) -> dict[str, Any]:
    codes, _, sizes, _ = SETS[which]
    rows = []
    for code in codes:
        use = (
            TRAY_SIZES
            if which == "fefco" and (code in fefco.TRAYS or code in fefco.MAILERS)
            else sizes
        )
        per = {"x".join(f"{v:g}" for v in size): check(code, size, which) for size in use}
        if (
            which == "fefco"
            and code in fefco.INSERTS
            and {r["status"] for r in per.values()} == {"out_of_range"}
        ):
            per |= {
                "x".join(f"{v:g}" for v in size): check(code, size, which)
                for size in LOW_INSERT_SIZES
            }
        statuses = {r["status"] for r in per.values()}
        status = (
            "fail"
            if statuses & {"fail", "error"}
            else "unusable"
            if "unusable" in statuses
            else "pass"
            if "pass" in statuses
            else "out_of_range"
        )
        rows.append({"code": code, "status": status, "sizes": per})
    summary: dict[str, int] = {}
    for r in rows:
        summary[r["status"]] = summary.get(r["status"], 0) + 1
    return {"targets": len(codes), "summary": summary, "rows": rows}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--set", choices=tuple(SETS), default="ecma")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    result = run(args.set)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    name = {"ecma": "ECMA 公开", "fefco": "FEFCO 已写成配方的"}[args.set]
    print(f"{name} {result['targets']} 款：{result['summary']}")
    skipped: dict[str, int] = {}
    for r in result["rows"]:
        for size, c in r["sizes"].items():
            if c["status"] in ("fail", "error", "unusable"):
                print(f"  {c['status']:5s} {r['code']} {size}: {c['issues'][:3]}")
            elif c["status"] == "out_of_range":
                skipped[size] = skipped.get(size, 0) + 1
    if skipped:
        print(f"  尺寸放不下（不算失败）：{skipped}")


if __name__ == "__main__":
    main()
