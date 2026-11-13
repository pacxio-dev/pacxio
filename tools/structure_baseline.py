"""Capture/compare synthetic structure outputs before C1 migrations.

Usage: .venv/bin/python tools/structure_baseline.py capture briefs/c1-baseline
       .venv/bin/python tools/structure_baseline.py compare briefs/c1-baseline

No existing golden snapshots are rewritten. Includes resolved geometry,
identifiers, ordering, SVG/DXF/CF2 bytes and intermediate/final fold matrices.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from pacxio.export.cf2 import dieline_to_cf2
from pacxio.export.dxf import dieline_to_dxf
from pacxio.export.svg import dieline_to_svg
from pacxio.mockup.folding import build_part, panel_matrices, plan_for
from pacxio.model.profile import load_profile, resolve_material
from pacxio.styles import get_style


def cases(suite: str = "legacy") -> list[tuple[str, str, dict[str, Any]]]:
    """Carton variants exercise the shared body; other families guard isolation."""
    result: list[tuple[str, str, dict[str, Any]]] = []
    for style in ("rte", "ste", "snaplock", "autolock"):
        for material in ("sbs_350", "sbs_400"):
            for dims in ((80, 50, 180), (100, 60, 120), (150, 60, 80)):
                for options in (
                    {},
                    {"dim_basis": "outer"},
                    {"apply_compensation": False},
                    {"friction_lock": False, "dust_offset": 0.6},
                ):
                    result.append(
                        (style, material, dict(zip(("L", "W", "H"), dims, strict=True)) | options)
                    )
    for style, material, dims in (
        ("fefco_0427", "flute_e", (240, 170, 70)),
        ("fefco_0427", "flute_b", (150, 100, 40)),
        ("fefco_0201", "flute_b", (300, 200, 200)),
        ("fefco_0201", "flute_bc", (600, 400, 400)),
        ("tray_lid", "sbs_350", (200, 150, 60)),
        ("tray_lid", "flute_e", (300, 200, 100)),
        ("drawer", "sbs_350", (150, 100, 40)),
        ("drawer", "sbs_400", (200, 200, 60)),
    ):
        result.append((style, material, dict(zip(("L", "W", "H"), dims, strict=True))))
    if suite == "c1":
        for sid in (
            "seal_end",
            "rte_hanger",
            "ste_hanger",
            "snaplock_hanger",
            "autolock_hanger",
            "fefco_0200",
            "fefco_0202",
            "fefco_0203",
            "card",
            "header_card",
        ):
            for index in range(3):
                corr = sid.startswith("fefco_")
                flat = sid in ("card", "header_card")
                dims = (
                    ((300, 200, 200), (400, 300, 250), (600, 400, 400))[index]
                    if corr
                    else ((80, 80, 1), (120, 100, 1), (160, 120, 1))[index]
                    if flat
                    else ((80, 50, 120), (100, 60, 180), (150, 60, 80))[index]
                )
                material = (
                    ("flute_b", "flute_c", "flute_bc")[index]
                    if corr
                    else ("sbs_350", "sbs_400", "sbs_350")[index]
                )
                result.append((sid, material, dict(zip(("L", "W", "H"), dims, strict=True))))
    return result


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def generate(directory: Path, suite: str = "legacy") -> list[dict[str, Any]]:
    directory.mkdir(parents=True, exist_ok=True)
    profile = load_profile("default")
    records = []
    for index, (style_id, material_id, values) in enumerate(cases(suite)):
        style = get_style(style_id)
        params, material = style.param_schema()(**values), resolve_material(material_id)
        issues = style.validate(params, material, profile)
        assert not issues, (style_id, values, issues)
        multipart = getattr(style, "generate_parts", None)
        parts = (
            multipart(params, material, profile)
            if multipart
            else {"single": style.generate(params, material, profile)}
        )
        for part, dl in parts.items():
            name = f"{index:03}-{style_id}-{material_id}-{part}"
            dxf = directory / f"{name}.dxf"
            dieline_to_dxf(dl, profile, dxf)
            # 新加的可空字段（keep_clear，C5）为空时不进摘要：盒子的基线不受影响
            skip = {"keep_clear"} if dl.keep_clear is None else None
            geometry = dl.model_dump_json(exclude=skip).encode()
            svg, cf2 = dieline_to_svg(dl).encode(), dieline_to_cf2(dl, profile).encode()
            (directory / f"{name}.json").write_bytes(geometry)
            (directory / f"{name}.svg").write_bytes(svg)
            (directory / f"{name}.cf2").write_bytes(cf2)
            plan = plan_for(dl)
            poses = {}
            if plan is not None:
                folded = build_part(dl, plan)
                for step in sorted({0.0, 0.5, float(plan.open_step), float(plan.steps)}):
                    poses[str(step)] = {
                        pid: matrix.tolist() for pid, matrix in panel_matrices(folded, step).items()
                    }
            records.append(
                {
                    "case": name,
                    "params": values,
                    "version": style.version,
                    "geometry": digest(geometry),
                    "svg": digest(svg),
                    "dxf": digest(dxf.read_bytes()),
                    "cf2": digest(cf2),
                    "folds": digest(json.dumps(poses).encode()),
                }
            )
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("capture", "compare"))
    parser.add_argument("directory", type=Path)
    parser.add_argument("--suite", choices=("legacy", "c1"), default="legacy")
    args = parser.parse_args()
    suite = args.suite
    manifest = args.directory / "manifest.json"
    if args.mode == "capture":
        if args.directory.exists():
            parser.error(
                "capture requires a new directory; an existing baseline is never overwritten"
            )
        records = generate(args.directory, suite)
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], check=True, text=True, capture_output=True
        ).stdout.strip()
        manifest.write_text(
            json.dumps({"revision": revision, "suite": suite, "records": records}, indent=2) + "\n"
        )
        print(
            json.dumps({"captured": len(records), "cases": len(cases(suite)), "revision": revision})
        )
        return
    baseline = json.loads(manifest.read_text())
    suite = baseline.get("suite", "legacy")
    with tempfile.TemporaryDirectory(prefix="pacxio-c1-compare-") as tmp:
        records = generate(Path(tmp), suite)
    changed = []
    old = {r["case"]: r for r in baseline["records"]}
    for record in records:
        before = old.pop(record["case"], None)
        if before != record:
            changed.append(
                {
                    "case": record["case"],
                    "fields": [k for k in record if before is None or before.get(k) != record[k]],
                }
            )
    changed += [{"case": name, "fields": ["removed"]} for name in old]
    report = {
        "baseline_revision": baseline["revision"],
        "cases": len(cases(suite)),
        "parts": len(records),
        "identical": not changed,
        "changed": changed,
    }
    (args.directory / "comparison.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    raise SystemExit(bool(changed))


if __name__ == "__main__":
    main()
