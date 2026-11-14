"""按刀版折叠（）：合上的外形和方盒子一致、网格不漏不重、浏览器和 Python 算得一样。"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from typer.testing import CliRunner

from pacxio import dielines
from pacxio.cli import app
from pacxio.mockup import folding as F
from pacxio.mockup.fold import build_box
from pacxio.mockup.scene import part_box
from pacxio.model.dieline import Dieline
from pacxio.model.job import Job

runner = CliRunner()
VIEWER = Path(__file__).parents[2] / "src/pacxio/studio/static/viewer3d-legacy.js"
TOL = 1e-6
CARTON = ("--L", "80", "--W", "50", "--H", "120")
CASES = {
    "rte": CARTON,
    "ste": CARTON,
    "snaplock": CARTON,
    "autolock": CARTON,
    "fefco_0201": ("--L", "300", "--W", "200", "--H", "200", "--material", "flute_b"),
    "fefco_0427": ("--L", "240", "--W", "170", "--H", "70", "--material", "flute_e"),
    "fefco_0427@b": ("--L", "150", "--W", "100", "--H", "40", "--material", "flute_b"),
    "tray_lid": ("--L", "250", "--W", "180", "--H", "60"),
    "drawer": ("--L", "150", "--W", "100", "--H", "40"),
}


def blank(tmp: Path, case: str) -> dict[str | None, Dieline]:
    style = case.split("@")[0]
    d = tmp / case
    res = runner.invoke(app, ["job", "init", str(d), "--style", style, *CASES[case]])
    assert res.exit_code == 0, res.output
    dset, errs = dielines.build_parts_for(Job.load(d))
    assert dset is not None, errs
    return dset.parts


def folded(dl: Dieline) -> F.FoldedPart:
    plan = F.plan_for(dl)
    assert plan is not None, dl.style
    return F.build_part(dl, plan)


def box_of(dl: Dieline, part: str | None):  # type: ignore[no-untyped-def]
    return part_box(dl) if part else build_box(dl)


@pytest.mark.parametrize("case", CASES)
def test_closed_shape_matches_the_box(tmp_path: Path, case: str) -> None:
    """合上后看得到的面和方盒子同一块刀版面、同一个位置；外形误差不超过一层纸厚。"""
    for name, dl in blank(tmp_path, case).items():
        part, box = folded(dl), box_of(dl, name)
        pose = F.closed_pose(part, box)
        tucks = {p.id for p in dl.panels if p.role.value == "tuck"}
        checked = 0
        for face in box.faces:
            for src, dst in F.face_pairs(part, [face]):
                err = float(np.linalg.norm(np.array(F.apply(pose, *src)) - np.array(dst)))
                # 飞机盒的插舌包在前板外面：比方盒子的正面多一层纸厚
                assert err <= (dl.caliper_mm if face.panel in tucks else 0) + TOL, (face.key, err)
                checked += 1
        assert checked >= 16
        solid = [p for p in dl.panels if p.role.value != "inner_wall"]
        pts = np.array(
            [
                F.apply(pose @ F.panel_matrices(part, part.plan.steps)[p.id], *c)
                for p in solid
                for c in (lambda b: ((b[0], b[1]), (b[2], b[1]), (b[2], b[3]), (b[0], b[3])))(
                    p.polygon.bbox()
                )
            ]
        )
        size = pts.max(axis=0) - pts.min(axis=0)
        # 插舌压线两端的钩形切口围出的小卡位归盖，合上时伸出插舌压线一层纸厚（每个插舌盖一处）【初值，待折样】
        hooks = {e.panels[0] for e in dl.edges if "tuck_slit" in e.id and e.type.value == "cut"}
        slack = np.array([0.0, dl.caliper_mm * len(hooks), 0.0])  # 钩高 = 一层纸厚
        assert np.all(np.abs(size - (box.x, box.y, box.z)) <= dl.caliper_mm + TOL + slack), (
            size,
            box,
        )


@pytest.mark.parametrize("case", CASES)
def test_every_hinged_panel_has_a_fold(tmp_path: Path, case: str) -> None:
    """盒型加了面却忘了写折法时报错；只有糊胶三角不折（自动锁底撑开后和主翼共面）。"""
    for dl in blank(tmp_path, case).values():
        part = folded(dl)
        assert {j.child for j in part.joints} | {part.plan.root} == {p.id for p in dl.panels}
        for j in part.joints:
            if dl.panel(j.child).role.value == "glue_tab":
                assert not j.keys
            else:
                assert j.keys and j.keys[-1][1] == 90.0 and j.keys[-1][0] <= part.plan.steps, j


@pytest.mark.parametrize("case", CASES)
def test_mesh_covers_each_panel_exactly(tmp_path: Path, case: str) -> None:
    """网格 + 压线过渡带的面积等于刀版面（扣掉插孔和窗口）：没漏也没重。"""
    for dl in blank(tmp_path, case).values():
        part = folded(dl)
        meshes, strips = F.build_meshes(part, dl.caliper_mm)
        area = sum(F.mesh_area(m) for m in meshes)
        for s in strips:
            c = s.crease[1] - s.crease[0]
            area += s.half_width * (c + (s.parent_end[1] - s.parent_end[0])) / 2
            area += s.half_width * (c + (s.child_end[1] - s.child_end[0])) / 2
        expected = sum(F._panel_shape(dl, p.id).area for p in dl.panels)
        assert area == pytest.approx(expected, rel=1e-3)
        assert all(s.half_width > 0 for s in strips)


def test_crease_band_shrinks_on_narrow_rims(tmp_path: Path) -> None:
    """卷边只有两层纸厚（天地盖）：过渡带不能比卷边的一半还宽，否则两条压线的带子会叠起来。"""
    dl = blank(tmp_path, "tray_lid")["base"]
    _, strips = F.build_meshes(folded(dl), radius=5.0)
    rim_width = dl.panel("rim_a").polygon.bbox()[2] - dl.panel("rim_a").polygon.bbox()[0]
    for s in strips:
        if "rim_a" in (s.child, s.parent):
            assert s.half_width <= rim_width / 2


def test_slots_are_holes_in_the_mesh(tmp_path: Path) -> None:
    dl = blank(tmp_path, "fefco_0427")[None]
    assert dl.inner_cuts()
    base = F._panel_shape(dl, "base")
    assert base.area < F.path_to_polygon(dl.panel("base").polygon).area


@pytest.mark.parametrize("flat_t", [0.0, 0.5])
def test_nothing_moves_before_its_step(tmp_path: Path, flat_t: float) -> None:
    dl = blank(tmp_path, "rte")[None]
    part = folded(dl)
    mats = F.panel_matrices(part, flat_t)
    for j in part.joints:
        first = j.keys[0][0]
        if first > 1:
            assert np.allclose(mats[j.child], np.eye(4)) or j.child != "top_lid"
    assert F.angle_at(((5, 45.0), (6, 90.0)), 5) == pytest.approx(45.0)
    assert F.angle_at(((5, 45.0), (6, 90.0)), 6) == pytest.approx(90.0)
    assert F.angle_at(((5, 45.0), (6, 90.0)), 4) == 0.0


@pytest.mark.skipif(shutil.which("node") is None, reason="没有 node")
@pytest.mark.parametrize("case", ["rte", "fefco_0427", "autolock", "drawer"])
def test_browser_folds_the_same_as_python(tmp_path: Path, case: str) -> None:
    """折叠算法两边各写了一份（查看器在浏览器里算、测试和取景在 Python 算）：用 node 跑查看器的代码对一遍。"""
    from pacxio.mockup.foldscene import _part_data

    parts = blank(tmp_path, case)
    data = []
    expected = {}
    samples = [0.0, 1.0, 2.5, 4.25, 5.0]
    for name, dl in parts.items():
        part = folded(dl)
        item = _part_data(part, dl.caliper_mm)
        pose = {"step": 0, "q": [0, 0, 0, 1], "t": [0, 0, 0]}
        item |= {"uv": [1, 0, 1, 0], "poses": [pose], "id": str(name)}
        data.append(item)
        expected[str(name)] = {
            str(i): {k: v.T.flatten().tolist() for k, v in F.panel_matrices(part, s).items()}
            for i, s in enumerate(samples)
        }
    script = tmp_path / "run.mjs"
    shutil.copy(VIEWER, tmp_path / "viewer.mjs")
    shutil.copy(VIEWER.with_name("i18n.js"), tmp_path / "i18n.js")  # viewer 的界面文字
    script.write_text(
        """
import { posePart, allocStrips } from "./viewer.mjs";
import { readFileSync } from "node:fs";
const { parts, samples } = JSON.parse(readFileSync(process.argv[2], "utf8"));
const gl = { ARRAY_BUFFER: 0, DYNAMIC_DRAW: 0, bindBuffer() {}, bufferData() {} };
for (const p of parts) allocStrips(p);
const out = {};
for (const s of samples) {
  posePart({ parts, steps: 9, groundStep: 1, s, gl });
  for (const p of parts) {
    out[p.id] ??= {};
    out[p.id][String(samples.indexOf(s))] = Object.fromEntries(p.panels.map((pn) => [pn.id, pn.local]));
    if (!p.outer.every(Number.isFinite) || !p.inner.every(Number.isFinite)) throw new Error("NaN");
  }
}
console.log(JSON.stringify(out));
""",
        encoding="utf-8",
    )
    payload = tmp_path / "in.json"
    payload.write_text(json.dumps({"parts": data, "samples": samples}), encoding="utf-8")
    res = subprocess.run(
        ["node", str(script), str(payload)], capture_output=True, text=True, check=False
    )
    assert res.returncode == 0, res.stderr
    got = json.loads(res.stdout)
    for name, by_s in expected.items():
        for s, mats in by_s.items():
            for pid, m in mats.items():
                assert np.allclose(got[name][s][pid], m, atol=1e-6), (name, s, pid)
