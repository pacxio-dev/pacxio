"""工作台 S3 确认按钮、S4 可转动 3D（、5 节）。"""

from __future__ import annotations

import http.client
import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from typer.testing import CliRunner

from pacxio.cli import app
from pacxio.concept.service import import_concept, read_chosen
from pacxio.inventory import content_add, content_confirm, read_confirmation
from pacxio.mockup.approval import read_approval
from pacxio.mockup.flat import make_flat
from pacxio.mockup.foldscene import FOLD_SCENE_VERSION, FOLD_TEX_MAX
from pacxio.mockup.service import approve_design, make_mockup
from pacxio.studio import server
from pacxio.studio.server import BY_STUDIO, Studio
from pacxio.studio.state import find_action, job_state
from tests.unit.fontkit import make_font

runner = CliRunner()


def cli(*args: str) -> tuple[int, dict[str, Any]]:
    res = runner.invoke(app, [*args, "--json"])
    try:
        return res.exit_code, json.loads(res.output)
    except ValueError as e:
        raise AssertionError(res.output) from e


@pytest.fixture
def job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    fonts = tmp_path / "nofonts"
    make_font(fonts / "TestFont.ttf", "TestFont")
    monkeypatch.setenv("PACXIO_FONT_DIR", str(fonts))
    monkeypatch.chdir(tmp_path)
    d = tmp_path / "tea"
    assert cli("job", "init", str(d), "--L", "80", "--W", "50", "--H", "180")[0] == 0
    assert cli("art", "add", str(d), "--kind", "fill", "--color", "10,0,20,0")[0] == 0
    return d


def stage(directory: Path, sid: str) -> Any:
    return next(s for s in job_state(directory).stages if s.id == sid)


# ---------------- S3：哪些确认能点 ----------------


@pytest.mark.pro
def test_content_action_follows_confirmation(job: Path) -> None:
    assert stage(job, "content").actions == []
    assert content_add(job, {"id": "brand", "category": "brand", "text": "山茶"}).ok
    assert [a.label for a in stage(job, "content").actions] == ["确认措辞"]
    assert content_confirm(job, by="测试").ok
    assert stage(job, "content").actions == []
    assert content_add(job, {"id": "name", "category": "product_name", "text": "乌龙"}).ok
    assert [a.label for a in stage(job, "content").actions] == ["重新确认措辞"]


@pytest.mark.pro
def test_concept_cards_offer_choosing_only_for_main_views(job: Path, tmp_path: Path) -> None:
    for i, (name, view, faces) in enumerate((("c1", None, None), ("c2", None, None),
                                             ("c1-back1", "back", ["back", "side_a"]))):  # fmt: skip
        src = tmp_path / f"{name}.png"
        Image.new("RGB", (300, 200), (i * 40, 120, 90)).save(src)
        assert import_concept(job, src, name=name, view=view, faces=faces).ok
    actions = {c.id: c.action for c in stage(job, "concept").cards}
    assert actions["concept:c1"] is not None and actions["concept:c1"].target == "c1"
    assert actions["concept:c1-back1"] is None  # 背面视角不能选定为方向
    st = job_state(job)
    assert (
        find_action(st, "concept", "c2") is not None
        and find_action(st, "concept", "c1-back1") is None
    )


@pytest.mark.pro
def test_design_shows_agent_diffs_while_the_design_is_unchanged(job: Path) -> None:
    assert cli("render", str(job))[0] == 0
    assert make_flat(job, diffs=["标题比平面图小"]).ok  # agent 对照平面图后写的差别
    cards = {c.id: c for c in stage(job, "design").cards}
    assert cards["design:diffs"].data == {"lines": ["标题比平面图小"]}
    assert (
        cli("art", "add", str(job), "--kind", "fill", "--color", "0,0,0,10", "--scope", "front")[0]
        == 0
    )
    assert "design:diffs" not in {c.id for c in stage(job, "design").cards}  # 设计又改了：不算数
    assert [s.id for s in job_state(job).stages].count("flat") == 0  # 没有"平铺确认"这一步了


@pytest.mark.pro
def test_approve_action_states(job: Path) -> None:
    assert stage(job, "design").actions == []
    assert make_mockup(job).ok
    assert stage(job, "design").actions == [] and stage(job, "mockup").actions == []  # 3D 只看
    assert cli("render", str(job))[0] == 0
    (a,) = stage(job, "design").actions
    assert a.id == "approve" and a.enabled and a.label == "确认刀版图"
    assert approve_design(job, by="测试").ok
    assert stage(job, "design").actions == []
    assert (
        cli("art", "add", str(job), "--kind", "fill", "--color", "0,0,0,10", "--scope", "back")[0]
        == 0
    )
    (a,) = stage(job, "design").actions
    assert not a.enabled and "render" in (a.reason or "") and a.label == "重新确认刀版图"
    assert make_mockup(job, require=["品牌名"]).ok  # 必须出现的信息没齐：不能点
    assert cli("render", str(job))[0] == 0
    st = stage(job, "design")
    (a,) = st.actions
    assert not a.enabled and "品牌名" in (a.reason or "")
    assert (
        st.status == "blocked" and st.tone == "warn" and st.label == "待补信息"
    )  # 不显示成"等你确认"
    assert st.todo == "user" and "品牌名" in (st.action or "")


# ---------------- S3：确认接口 ----------------


@pytest.fixture
def studio(job: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Studio]:
    monkeypatch.setattr(server, "POLL_S", 0.05)
    s = Studio(job)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s
    s.shutdown()


def post(s: Studio, body: Any, token: str | None = None) -> tuple[int, dict[str, Any]]:
    conn = http.client.HTTPConnection("127.0.0.1", s.port, timeout=60)
    conn.request("POST", "/api/confirm", body=json.dumps(body).encode("utf-8"),
                 headers={"Content-Type": "application/json", "X-Studio-Token": token or s.token})  # fmt: skip
    resp = conn.getresponse()
    data = json.loads(resp.read() or b"{}")
    conn.close()
    return resp.status, data


@pytest.mark.pro
def test_confirm_endpoint_checks_availability_and_records_studio(studio: Studio, job: Path) -> None:
    assert post(studio, {"action": "content"}, token="wrong")[0] == 403
    status, data = post(studio, {"action": "content"})
    assert status == 400 and "没有这一项" in data["errors"][0]["message"]  # 还没有信息清单
    assert content_add(job, {"id": "brand", "category": "brand", "text": "山茶"}).ok
    status, data = post(studio, {"action": "content", "note": "看过了"})
    assert status == 200 and data["ok"]
    conf = read_confirmation(job)
    assert conf is not None and conf.by == BY_STUDIO and conf.note == "看过了"
    assert post(studio, {"action": "nope"})[0] == 400
    assert post(studio, {"action": 3})[0] == 400


@pytest.mark.pro
def test_confirm_endpoint_approve_and_choose(studio: Studio, job: Path, tmp_path: Path) -> None:
    assert cli("render", str(job))[0] == 0
    status, data = post(studio, {"action": "approve"})
    assert status == 200 and data["data"]["status"] == "approved"
    approval = read_approval(job)
    assert approval is not None and approval.by == BY_STUDIO
    assert post(studio, {"action": "approve"})[0] == 400  # 已经确认过了：没有这一项
    src = tmp_path / "c.png"
    Image.new("RGB", (300, 200), (200, 180, 150)).save(src)
    assert import_concept(job, src, name="c1").ok
    assert post(studio, {"action": "concept", "target": "c9"})[0] == 400
    status, _ = post(studio, {"action": "concept", "target": "c1"})
    chosen = read_chosen(job)
    assert status == 200 and chosen is not None and chosen.id == "c1" and chosen.by == BY_STUDIO


@pytest.mark.pro
def test_old_versions_fold_and_can_be_switched_back_from_the_studio(
    studio: Studio, job: Path, tmp_path: Path
) -> None:
    for i, (name, view) in enumerate((("c1", None), ("c1-back1", "back"), ("c1-back2", "back"))):
        src = tmp_path / f"{name}.png"
        Image.new("RGB", (300, 200), (i * 40, 120, 90)).save(src)
        assert import_concept(job, src, name=name, view=view).ok
    cards = {c.id: c for c in stage(job, "concept").cards}
    assert cards["concept:c1-back1"].fold is None  # 还没选方向：都摆出来
    assert post(studio, {"action": "concept", "target": "c1"})[0] == 200
    cards = {c.id: c for c in stage(job, "concept").cards}
    new, old = cards["concept:c1-back2"], cards["concept:c1-back1"]
    assert new.fold is None and "在用" in new.badges and new.action is None
    assert old.fold == "背面视角旧版（没在用）" and old.action is not None
    assert old.action.id == "use" and old.action.label == "改用这张"
    assert post(studio, {"action": "use", "target": "c1-back2"})[0] == 400  # 已经在用：没有这一项
    status, data = post(studio, {"action": "use", "target": "c1-back1"})
    assert status == 200 and data["data"]["previous"] == "c1-back2"
    cards = {c.id: c for c in stage(job, "concept").cards}
    assert "在用" in cards["concept:c1-back1"].badges
    assert cards["concept:c1-back2"].fold == "背面视角旧版（没在用）"
    pick = json.loads((job / "concepts" / "in_use.json").read_text(encoding="utf-8"))
    assert pick["back"]["by"] == BY_STUDIO


# ---------------- S4：可转动 3D 的场景 ----------------


@pytest.mark.pro
def test_mockup_writes_scene_matching_static_render(job: Path) -> None:
    r = make_mockup(job)
    assert r.ok
    scene = json.loads((job / "mockup" / "scene.json").read_text(encoding="utf-8"))
    assert scene["version"] == FOLD_SCENE_VERSION and len(scene["fingerprint"]) == 64
    assert list(scene["states"]) == ["flat", "open", "closed"]
    assert scene["states"]["flat"]["t"] == 0 and scene["states"]["closed"]["t"] == 1
    (part,) = scene["parts"]
    assert {p["id"] for p in part["panels"]} >= {"front", "back", "side_a", "side_b", "top_lid"}
    for path in part["textures"].values():
        with Image.open(job / "mockup" / path) as im:
            assert max(im.size) <= FOLD_TEX_MAX
    (card,) = stage(job, "mockup").cards  # ⑥ 只有可转动的 3D（确认页、静态视角图不上画布）
    assert (
        card.kind == "viewer3d"
        and card.data == {"states": ["flat", "open", "closed"], "version": 3, "regions": []}
        and not card.stale
    )
    (job / "mockup" / "scene.json").unlink()  # 没有 3D（旧效果图）：退回静态视角图
    cards = stage(job, "mockup").cards
    assert cards and all(c.kind == "image" and c.fold is None for c in cards)
    assert str(job / "mockup" / "scene.json") in r.artifacts


@pytest.mark.pro
def test_multipart_scene_has_open_state(tmp_path: Path) -> None:
    d = tmp_path / "set"
    assert (
        cli("job", "init", str(d), "--style", "tray_lid", "--L", "200", "--W", "150", "--H", "60")[
            0
        ]
        == 0
    )
    assert make_mockup(d).ok
    scene = json.loads((d / "mockup" / "scene.json").read_text(encoding="utf-8"))
    assert set(scene["states"]) == {"flat", "closed", "open"}
    assert 0 < scene["states"]["open"]["t"] < 1
    assert [p["id"] for p in scene["parts"]] == ["base", "lid"]
    # 打开和合上只差部件的摆放：最后一步才合到一起
    for part in scene["parts"]:
        assert part["poses"][-2]["step"] == scene["steps"] - 1
        assert part["poses"][-1]["step"] == scene["steps"]
        assert scene["states"]["open"]["t"] == (scene["steps"] - 1) / scene["steps"]
    # 天地盖的打开是上下的：盖子竖直抬起，地盒不动，朝向不变
    base, lid = scene["parts"]
    opened, shut = lid["poses"][-2], lid["poses"][-1]
    assert opened["t"][:2] == shut["t"][:2] and opened["t"][2] > shut["t"][2] + 60
    assert opened["q"] == shut["q"]
    assert base["poses"][-2]["t"] == base["poses"][-1]["t"]
    texture_files = {p.name for p in (d / "mockup" / "scene").glob("*.png")}
    assert texture_files == {
        Path(path).name for part in scene["parts"] for path in part["textures"].values()
    }
