"""工作台 S2：批注（ 节）——图上位置换算到部件、面、毫米和元素，截图，状态，命令，服务的写接口。"""

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

from pacxio import notes as nt
from pacxio.cli import app
from pacxio.concept.service import import_concept
from pacxio.inventory import content_add
from pacxio.notes import (
    AGENT,
    USER,
    add_note,
    add_note_at_panel,
    delete_note,
    list_notes,
    locate,
    open_notes_issue,
    read_map,
    rel_to_world,
    reopen_note,
    reply_note,
    resolve_note,
    world_to_rel,
)
from pacxio.studio import server
from pacxio.studio.server import Studio
from pacxio.studio.state import job_state
from tests.unit.fontkit import make_font

runner = CliRunner()
TITLE_BOX = (10.0, 120.0, 60.0, 20.0)  # 正面标题的放置框（面局部 mm）


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
    box = ",".join(f"{v:g}" for v in TITLE_BOX)
    assert cli("art", "add", str(d), "--kind", "text", "--id", "title", "--panel", "front", "--box", box,
               "--text", "TITLE", "--font", "TestFont", "--size-pt", "24")[0] == 0  # fmt: skip
    assert cli("render", str(d))[0] == 0
    return d


def element(m: dict[str, Any], eid: str) -> dict[str, Any]:
    return next(e for e in m["elements"] if e["id"] == eid)


# ---------------- 对照与换算 ----------------


@pytest.mark.pro
def test_preview_map_lists_elements_with_world_boxes(job: Path) -> None:
    m = read_map(job, "preview.png")
    assert m is not None and m["space"] == "blank"
    front = next(p for p in m["panels"] if p["id"] == "front")
    assert front["local_bbox"][0] == 0 and front["local_bbox"][1] == 0 and len(front["frame"]) == 6
    title = element(m, "title")
    assert title["kind"] == "text" and title["panel"] == "front" and title["summary"] == "TITLE"
    fx0, fy0, fx1, fy1 = front["bbox"]
    x0, y0, x1, y1 = title["bbox"]
    assert fx0 <= x0 < x1 <= fx1 and fy0 <= y0 < y1 <= fy1
    # 墨迹包围盒在放置框里（面局部 → 刀版：front 的 frame 是平移）
    assert x0 >= fx0 + TITLE_BOX[0] - 0.5 and y1 <= fy0 + TITLE_BOX[1] + TITLE_BOX[3] + 0.5


@pytest.mark.pro
def test_box_on_preview_resolves_panel_mm_and_element(job: Path) -> None:
    m = read_map(job, "preview.png")
    assert m is not None
    title = element(m, "title")
    rel = world_to_rel(m, tuple(title["bbox"]))  # type: ignore[arg-type]
    t = locate(job, "design:preview.png", "preview.png", "box", rel)
    assert t.panel == "front" and t.part is None
    assert t.elements[0]["id"] == "title" and t.elements[0]["summary"] == "TITLE"
    assert t.box_mm is not None
    x, y, w, h = t.box_mm
    assert TITLE_BOX[0] - 0.5 <= x and x + w <= TITLE_BOX[0] + TITLE_BOX[2] + 0.5
    assert TITLE_BOX[1] - 0.5 <= y and y + h <= TITLE_BOX[1] + TITLE_BOX[3] + 0.5
    assert t.file_sha256 and len(t.file_sha256) == 64
    cx, cy = (rel[0] + rel[2]) / 2, (rel[1] + rel[3]) / 2  # 图钉点在标题中间
    pin = locate(job, "design:preview.png", "preview.png", "pin", (cx, cy, cx, cy))
    assert (
        pin.panel == "front"
        and pin.elements[0]["id"] == "title"
        and pin.box_mm
        and pin.box_mm[2:] == (0, 0)
    )
    blank = locate(job, "design:preview.png", "preview.png", "pin", (0.995, 0.005, 0.995, 0.005))
    assert blank.panel is None and blank.elements == []  # 刀版外面的空白


@pytest.mark.pro
def test_rel_and_world_round_trip_for_both_map_kinds(job: Path) -> None:
    assert cli("plan", str(job))[0] == 0
    maps = json.loads((job / "plan" / "maps.json").read_text(encoding="utf-8"))["images"]
    assert maps["plan/faces/front.png"]["space"] == "panel"
    assert maps["plan/wireframe.png"]["space"] == "blank"
    assert any(k.startswith("plan/pairs/") for k in maps)
    rel = (0.2, 0.3, 0.6, 0.5)
    for file in (
        "preview.png",
        "plan/faces/front.png",
        "plan/faces/top_lid.png",
        "plan/wireframe.png",
    ):
        m = read_map(job, file)
        assert m is not None, file
        back = world_to_rel(m, rel_to_world(m, rel))
        assert all(abs(a - b) < 1e-6 for a, b in zip(back, rel, strict=True)), file


@pytest.mark.pro
def test_note_on_layout_draft_face_maps_to_panel_coordinates(job: Path) -> None:
    assert cli("plan", str(job))[0] == 0
    m = read_map(job, "plan/faces/front.png")
    assert m is not None
    u0, v0, u1, v1 = m["local_bbox"]
    x, y, w, h = TITLE_BOX
    rel = (
        (x - u0) / (u1 - u0),
        (v1 - (y + h)) / (v1 - v0),
        (x + w - u0) / (u1 - u0),
        (v1 - y) / (v1 - v0),
    )
    t = locate(job, "plan:faces/front.png", "plan/faces/front.png", "box", rel)
    assert t.panel == "front" and t.box_mm is not None
    assert all(abs(a - b) < 0.2 for a, b in zip(t.box_mm, TITLE_BOX, strict=True))
    assert "title" in [e["id"] for e in t.elements]


@pytest.mark.pro
def test_reference_images_get_hints_not_coordinates(job: Path, tmp_path: Path) -> None:
    src = tmp_path / "flat.png"
    Image.new("RGB", (400, 300), (220, 210, 190)).save(src)
    assert import_concept(job, src, name="f1", view="flat", faces=["front", "side_b"]).ok
    t = locate(job, "concept:f1", "concepts/f1.png", "pin", (0.3, 0.3, 0.3, 0.3))
    assert (
        t.faces == ["front", "side_b"]
        and t.panel is None
        and t.box_mm is None
        and "只作参考" in (t.hint or "")
    )
    assert locate(
        job, "flat:vs_flat_side_a.png", "flat/vs_flat_side_a.png", "pin", (0.5, 0.5, 0.5, 0.5)
    ).faces == ["side_a"]
    assert "正面 + 右侧" in (locate(job, "mockup:view_front_right.png", None, "none").hint or "")


# ---------------- 批注的生命周期 ----------------


@pytest.mark.pro
def test_user_note_flow_reminds_agent_until_answered(job: Path) -> None:
    m = read_map(job, "preview.png")
    assert m is not None
    rel = world_to_rel(m, tuple(element(m, "title")["bbox"]))  # type: ignore[arg-type]
    r = add_note(job, "  标题再大一点  ", USER, "design:preview.png", "preview.png", "box", rel)
    assert r.ok, r.errors
    note = r.data["note"]
    assert note["id"] == "n1" and note["status"] == "open" and note["text"] == "标题再大一点"
    assert "front" in note["where"] and "title「TITLE」" in note["where"]
    snap = job / note["snapshot"]
    with Image.open(snap) as im:
        assert im.width >= nt.SNAP_MIN_PX
    issue = open_notes_issue(job)
    assert issue is not None and issue.id == "NOTE" and "n1" in issue.message
    code, out = cli("render", str(job))
    assert code == 0 and "NOTE" in {w["id"] for w in out["warnings"]}

    r = reply_note(job, "n1", "标题从 24pt 放大到 30pt", by=AGENT)
    assert r.data["note"]["status"] == "answered" and open_notes_issue(job) is None
    r = reply_note(job, "n1", "还是小", by=USER)
    assert r.data["note"]["status"] == "open"  # 用户接着说：又轮到 agent
    r = reply_note(job, "n1", "收到，先改别的", by=AGENT, keep_open=True)
    assert r.data["note"]["status"] == "open"
    assert resolve_note(job, "n1").data["note"]["status"] == "resolved"
    assert open_notes_issue(job) is None
    assert reopen_note(job, "n1").data["note"]["status"] == "open"
    book = nt.load_notes(job)
    assert [r.by for r in book.notes[0].replies] == [AGENT, USER, AGENT]
    # 不进 job.json：设计指纹不受影响
    assert "review" not in (job / "job.json").read_text(encoding="utf-8")


@pytest.mark.pro
def test_delete_only_own_unanswered_notes(job: Path) -> None:
    assert add_note(job, "写错了", USER).ok
    assert add_note(job, "我是 agent 的留言", AGENT).ok
    assert delete_note(job, "n2", by=USER).ok is False  # 不能删 agent 的
    assert delete_note(job, "n1", by=USER).ok
    assert [n.id for n in nt.load_notes(job).notes] == ["n2"]
    assert add_note(job, "第三条", USER).data["note"]["id"] == "n3"  # 编号不回收
    assert reply_note(job, "n3", "好的").ok
    assert delete_note(job, "n3", by=USER).ok is False  # 有回复了就只能关


@pytest.mark.pro
def test_agent_note_at_panel_lands_on_preview(job: Path) -> None:
    r = add_note_at_panel(job, "这里两种都试了，你选一个", "front", TITLE_BOX)
    assert r.ok, r.errors
    t = r.data["note"]["target"]
    assert r.data["note"]["status"] == "answered" and r.data["note"]["by"] == AGENT
    assert t["card"] == "design:preview.png" and t["panel"] == "front"
    assert all(abs(a - b) < 0.2 for a, b in zip(t["box_mm"], TITLE_BOX, strict=True))
    assert (job / r.data["note"]["snapshot"]).is_file()
    bad = add_note_at_panel(job, "x", "nope", TITLE_BOX)
    assert not bad.ok and "front" in (bad.errors[0].fix or "")
    (job / "preview.map.json").unlink()
    assert not add_note_at_panel(job, "x", "front", TITLE_BOX).ok


@pytest.mark.pro
def test_bad_inputs_are_refused(job: Path) -> None:
    assert add_note(job, "   ").exit_code == 2
    assert add_note(job, "x" * (nt.MAX_TEXT + 1)).exit_code == 2
    assert (
        add_note(
            job, "x", USER, "design:preview.png", "preview.png", "box", (0, 0, 1.5, 1)
        ).exit_code
        == 2
    )
    assert (
        add_note(job, "x", USER, "c", "../outside.png", "pin", (0.1, 0.1, 0.1, 0.1)).exit_code == 2
    )
    assert reply_note(job, "n9", "x").exit_code == 2
    (job / "review").mkdir(exist_ok=True)
    (job / "review" / "notes.json").write_text("{broken", encoding="utf-8")
    r = list_notes(job)
    assert not r.ok and r.errors[0].id == "NOTE"
    assert open_notes_issue(job) is None
    assert any(e.id == "NOTE" for e in job_state(job).errors)


@pytest.mark.pro
def test_item_note_and_state_counts(job: Path) -> None:
    assert content_add(job, {"id": "brand", "category": "brand", "text": "山茶"}).ok
    assert add_note(job, "品牌名要不要加拼音", USER, "content:list", None, "item", None, "brand").ok
    assert add_note(
        job, "预览上的意见", USER, "design:preview.png", "preview.png", "pin", (0.5, 0.5, 0.5, 0.5)
    ).ok
    assert add_note(job, "整体意见", USER).ok
    st = job_state(job)
    by_id = {s.id: s for s in st.stages}
    assert by_id["content"].notes_open == 1 and by_id["design"].notes_open == 1
    notes = {n["id"]: n for n in st.notes}
    assert notes["n1"]["stage"] == "content" and "条目 brand" in notes["n1"]["where"]
    assert notes["n3"]["stage"] is None and notes["n3"]["where"].startswith("（没有位置")
    assert resolve_note(job, "n2").ok
    assert next(s for s in job_state(job).stages if s.id == "design").notes_open == 0


@pytest.mark.pro
def test_cli_notes_commands(job: Path) -> None:
    code, out = cli("notes", "list", str(job))
    assert code == 0 and out["data"]["notes"] == []
    code, out = cli(
        "notes", "add", str(job), "--text", "看看这里", "--panel", "front", "--box", "10,120,60,20"
    )
    assert code == 0 and out["data"]["note"]["target"]["panel"] == "front"
    code, out = cli("notes", "add", str(job), "--text", "x", "--panel", "front")
    assert code == 2
    assert add_note(job, "用户的意见", USER).ok
    code, out = cli("notes", "list", str(job), "--open")
    assert [n["id"] for n in out["data"]["notes"]] == ["n2"]
    code, out = cli("notes", "reply", str(job), "n2", "--text", "改好了")
    assert code == 0 and out["data"]["note"]["status"] == "answered"
    code, out = cli("notes", "resolve", str(job), "n2")
    assert code == 0 and out["data"]["note"]["resolved_by"] == AGENT
    code, out = cli("notes", "list", str(job), "--all")
    assert out["data"]["counts"] == {"open": 0, "answered": 1, "resolved": 1}
    res = runner.invoke(app, ["notes", "list", str(job), "--all"])
    assert "n1 [等用户看] agent：看看这里" in res.output and "位置：正式稿 · front" in res.output


# ---------------- 服务的写接口 ----------------


@pytest.fixture
def studio(job: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Studio]:
    monkeypatch.setattr(server, "POLL_S", 0.05)
    s = Studio(job)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s
    s.shutdown()


def post(
    s: Studio, path: str, body: Any, headers: dict[str, str] | None = None
) -> tuple[int, dict[str, Any]]:
    h = {"Content-Type": "application/json", "X-Studio-Token": s.token, **(headers or {})}
    conn = http.client.HTTPConnection("127.0.0.1", s.port, timeout=10)
    raw = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
    conn.request("POST", path, body=raw, headers=h)
    resp = conn.getresponse()
    data = json.loads(resp.read() or b"{}")
    conn.close()
    return resp.status, data


@pytest.mark.pro
def test_post_requires_token_origin_and_json(studio: Studio) -> None:
    body = {"text": "x"}
    assert post(studio, "/api/notes", body, {"X-Studio-Token": "wrong"})[0] == 403
    assert post(studio, "/api/notes", body, {"Origin": "http://evil.example"})[0] == 403
    assert post(studio, "/api/notes", body, {"Content-Type": "text/plain"})[0] == 403
    assert post(studio, "/api/notes", body, {"Sec-Fetch-Site": "cross-site"})[0] == 403
    assert post(studio, "/api/notes", b"[1,2]")[0] == 400
    assert post(studio, "/api/notes", body, {"Origin": f"http://127.0.0.1:{studio.port}"})[0] == 200


@pytest.mark.pro
def test_note_endpoints_round_trip(studio: Studio, job: Path) -> None:
    status, data = post(studio, "/api/notes", {"text": "标题太小", "card": "design:preview.png", "file": "preview.png",
                                               "shape": "pin", "rel": [0.6, 0.4, 0.6, 0.4]})  # fmt: skip
    assert status == 200 and data["ok"] and data["data"]["note"]["by"] == USER
    nid = data["data"]["note"]["id"]
    assert post(studio, "/api/notes", {"text": "x", "rel": [0, 0, 1]})[0] == 400
    assert (
        post(studio, f"/api/notes/{nid}/reply", {"text": "补一句"})[1]["data"]["note"]["status"]
        == "open"
    )
    assert post(studio, f"/api/notes/{nid}/resolve", {})[1]["data"]["note"]["status"] == "resolved"
    assert post(studio, f"/api/notes/{nid}/reopen", {})[1]["data"]["note"]["status"] == "open"
    assert post(studio, f"/api/notes/{nid}/delete", {})[0] == 400  # 有回复了：只能关
    assert post(studio, f"/api/notes/{nid}/explode", {})[0] == 404
    assert post(studio, "/api/notes/../../x/reply", {"text": "x"})[0] == 404
    status, data = post(studio, "/api/notes", {"text": "点错了"})
    assert post(studio, f"/api/notes/{data['data']['note']['id']}/delete", {})[0] == 200
    assert [n.id for n in nt.load_notes(job).notes] == [nid]


@pytest.mark.pro
def test_multipart_notes_know_their_part(tmp_path: Path) -> None:
    d = tmp_path / "set"
    assert (
        cli("job", "init", str(d), "--style", "tray_lid", "--L", "200", "--W", "150", "--H", "60")[
            0
        ]
        == 0
    )
    assert cli("render", str(d))[0] == 0
    r = add_note_at_panel(d, "盖顶的 Logo 再往上", "lid.top", (40, 40, 60, 30))
    assert r.ok, r.errors
    t = r.data["note"]["target"]
    assert t["card"] == "design:preview_lid.png" and t["part"] == "lid" and t["panel"] == "top"
    assert all(abs(a - b) < 0.2 for a, b in zip(t["box_mm"], (40, 40, 60, 30), strict=True))
    assert "lid.top" in r.data["note"]["where"]
