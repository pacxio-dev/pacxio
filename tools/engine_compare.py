"""几何引擎 vs 现有入口（标准答案）：把结构基线的每一组改写成配方，用引擎生成，逐面、逐线比刀版几何。

    uv run python tools/engine_compare.py [--suite legacy|c1] [--json]

比的是面（id、角色、轮廓的每一段）和线（id、类型、每一段），坐标容差 1e-6 mm；引擎还要能按自带的折法折、
筒身的折回自检偏差 ≤ 0.5 mm + 纸厚。对不上配方的（多件：天地盖、抽屉；平面卡片）单独列出，是后面几步的事。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from structure_baseline import cases  # noqa: E402

from pacxio.model.dieline import Dieline  # noqa: E402
from pacxio.model.profile import load_profile, resolve_material  # noqa: E402
from pacxio.styles import get_style  # noqa: E402

TOL = 1e-6
KEEP = ("dim_basis", "apply_compensation")
TUBE_ENDS: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {
    "rte": ({"kind": "lid_tuck", "on": 0}, {"kind": "lid_tuck", "on": 2}),
    "ste": ({"kind": "lid_tuck", "on": 0}, {"kind": "lid_tuck", "on": 0}),
    "snaplock": ({"kind": "lid_tuck", "on": 0}, {"kind": "snaplock"}),
    "autolock": ({"kind": "lid_tuck", "on": 0}, {"kind": "autolock"}),
    "seal_end": ({"kind": "seal"}, {"kind": "seal"}),
    "rte_hanger": ({"kind": "hanger"}, {"kind": "lid_tuck", "on": 0}),
    "ste_hanger": ({"kind": "hanger"}, {"kind": "lid_tuck", "on": 2}),
    "snaplock_hanger": ({"kind": "hanger"}, {"kind": "snaplock"}),
    "autolock_hanger": ({"kind": "hanger"}, {"kind": "autolock"}),
    "fefco_0201": ({"kind": "flaps"}, {"kind": "flaps"}),
    "fefco_0200": ({"kind": "open"}, {"kind": "flaps"}),
    "fefco_0202": ({"kind": "flaps", "flap": "overlap"}, {"kind": "flaps", "flap": "overlap"}),
    "fefco_0203": ({"kind": "flaps", "flap": "full"}, {"kind": "flaps", "flap": "full"}),
}


def recipe_for(style: str, values: dict[str, Any]) -> dict[str, Any] | None:
    """现有入口 + 参数 → 等价的配方；多件 / 平面卡片还没有配方，返回 None。"""
    body = {"base": "rect", "L": values["L"], "W": values["W"], "H": values["H"]}
    extra = {k: v for k, v in values.items() if k not in ("L", "W", "H", *KEEP)}
    common = {k: values[k] for k in KEEP if k in values} | ({"tuning": extra} if extra else {})
    if style in TUBE_ENDS:
        top, bottom = TUBE_ENDS[style]
        return {"body": body, "top": top, "bottom": bottom} | common
    if style == "fefco_0427":
        return {
            "body": body | {"form": "tray"},
            "top": {"kind": "mailer"},
            "bottom": {"kind": "open"},
        } | common
    return None


def geometry(dl: Dieline) -> dict[str, Any]:
    def segs(path_segments: Any) -> list[tuple[float, ...]]:
        return [(s.start.x, s.start.y, s.end.x, s.end.y, s.bulge) for s in path_segments]

    return {
        "panels": [(p.id, p.role.value, segs(p.polygon.segments)) for p in dl.panels],
        "edges": [(e.id, e.type.value, segs(e.segments)) for e in dl.edges],
        "bbox": dl.bbox,
    }


def same(a: Any, b: Any) -> bool:
    if isinstance(a, float | int) and isinstance(b, float | int):
        return abs(a - b) <= TOL
    if isinstance(a, list | tuple) and isinstance(b, list | tuple):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b, strict=True))
    return bool(a == b)


def compare(suite: str) -> dict[str, Any]:
    profile = load_profile("default")
    engine = get_style("structure")
    rows = []
    for style_id, material_id, values in cases(suite):
        recipe = recipe_for(style_id, values)
        row: dict[str, Any] = {"style": style_id, "material": material_id, "params": values}
        if recipe is None:
            rows.append(row | {"status": "no_recipe"})
            continue
        material = resolve_material(material_id)
        old_style = get_style(style_id)
        old = old_style.generate(old_style.param_schema()(**values), material, profile)
        p = engine.param_schema()(**recipe)
        issues = engine.validate(p, material, profile)
        if issues:
            rows.append(row | {"status": "invalid", "issues": [i.message for i in issues]})
            continue
        new = engine.generate(p, material, profile)
        ok = same(geometry(old), geometry(new))
        dev = new.style_meta.get("structure", {}).get("fold_deviation_mm")
        folds = "fold_plan" in new.style_meta
        tol = 0.5 + material.caliper_mm
        status = "same" if ok and folds and (dev is None or dev <= tol) else "different"
        rows.append(
            row
            | {"status": status, "geometry_same": ok, "fold_plan": folds, "fold_deviation_mm": dev}
        )
    summary: dict[str, int] = {}
    for r in rows:
        summary[r["status"]] = summary.get(r["status"], 0) + 1
    return {"suite": suite, "summary": summary, "rows": rows}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--suite", choices=("legacy", "c1"), default="legacy")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    result = compare(args.suite)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return
    print(f"{args.suite}: {result['summary']}")
    for r in result["rows"]:
        if r["status"] not in ("same",):
            print(
                f"  {r['status']:10s} {r['style']:16s} {r['material']:9s} {r['params']} {r.get('issues', '')}"
            )


if __name__ == "__main__":
    main()
