"""工作台结构面板（ 第二步）：可编辑项、试改（不写文件）、保存（改 job.json、重出刀版）。"""

from __future__ import annotations

import http.client
import json
import re
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from pacxio import api
from pacxio.engine import combos
from pacxio.studio import server
from pacxio.studio.server import Studio


@pytest.fixture
def job(tmp_path: Path) -> Path:
    d = tmp_path / "display"
    assert api.init_job(
        d, "structure", combos.recipe("tear_display_tray", 150, 150, 40), "sbs_350"
    ).ok
    return d


@pytest.fixture
def studio(job: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Studio]:
    monkeypatch.setattr(server, "POLL_S", 0.05)
    s = Studio(job)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s
    s.shutdown()


def call(
    s: Studio, method: str, path: str, body: Any = None, token: str | None = None
) -> tuple[int, dict[str, Any]]:
    conn = http.client.HTTPConnection("127.0.0.1", s.port, timeout=120)
    headers = {"Content-Type": "application/json", "X-Studio-Token": token or s.token}
    conn.request(
        method,
        path,
        body=json.dumps(body).encode("utf-8") if body is not None else None,
        headers=headers,
    )
    resp = conn.getresponse()
    data = json.loads(resp.read() or b"{}")
    conn.close()
    return resp.status, data


def test_panel_lists_tries_and_saves(studio: Studio, job: Path) -> None:
    status, info = call(studio, "GET", "/api/structure")
    assert status == 200 and info["ok"]
    paths = {p["path"] for p in info["data"]["parameters"]}
    assert {"top.display_lid", "body.corner_depth"} <= paths
    status, english = call(studio, "GET", "/api/structure?lang=en")
    assert status == 200 and english["ok"]
    assert {p["path"] for p in english["data"]["parameters"]} == paths
    materials = {m["id"]: m["name"] for m in english["data"]["basics"]["materials"]}
    assert materials["sbs_350"] == "SBS 350 g/m²"
    assert call(studio, "GET", "/api/structure?lang=zh")[1] == info
    before = (job / "job.json").read_text(encoding="utf-8")
    # 试改：出刀版和折好的预览，不写文件
    status, tried = call(
        studio, "POST", "/api/structure/try", {"patch": {"top": {"display_lid": "rect"}}}
    )
    assert status == 200 and tried["ok"]
    assert (
        tried["data"]["dieline_svg"].startswith("<svg")
        and tried["data"]["scene"]  # 能转的 3D（静态图只在没有折法时给）
        and "<svg" in tried["data"]["svg"]
    )
    assert (job / "job.json").read_text(encoding="utf-8") == before
    # 不成立的改动：说原因，不写文件
    invalid = {"patch": {"body": {"corner_depth": 90}}}
    status, bad = call(studio, "POST", "/api/structure/try?lang=zh", invalid)
    assert status == 400 and bad["errors"]
    status, bad_en = call(studio, "POST", "/api/structure/try?lang=en", invalid)
    assert status == 400 and bad_en["errors"][0]["id"] == bad["errors"][0]["id"]
    assert not re.search(r"[㐀-鿿]", bad_en["errors"][0]["message"])
    assert bad_en["errors"][0]["message"] != bad["errors"][0]["message"]
    status, bad = call(
        studio, "POST", "/api/structure/save", {"patch": {"body": {"corner_depth": 90}}}
    )
    assert status == 400
    assert (job / "job.json").read_text(encoding="utf-8") == before
    # 保存：写 job.json
    status, saved = call(
        studio, "POST", "/api/structure/save", {"patch": {"top": {"display_lid": "rect"}}}
    )
    assert status == 200 and saved["ok"]
    assert (
        json.loads((job / "job.json").read_text(encoding="utf-8"))["params"]["top"]["display_lid"]
        == "rect"
    )


def test_panel_writes_need_the_page_token(studio: Studio) -> None:
    patch = {"patch": {"top": {"display_lid": "rect"}}}
    assert call(studio, "POST", "/api/structure/save", patch, token="wrong")[0] == 403
    assert call(studio, "POST", "/api/structure/try", {"patch": 3})[0] == 400


def test_basics_and_material_swap(studio: Studio, job: Path) -> None:
    status, info = call(studio, "GET", "/api/structure")
    basics = info["data"]["basics"]
    assert (
        basics["paths"] == {"L": "body.L", "W": "body.W", "H": "body.H"}
        and basics["basis"] == "inner"
    )
    assert {"sbs_350", "flute_e"} <= {m["id"] for m in basics["materials"]}
    assert not any(
        m["type"] == "label_stock" for m in basics["materials"]
    )  # 盒子只在卡纸、瓦楞里换
    status, tried = call(
        studio, "POST", "/api/structure/try", {"patch": {"body": {"L": 180}}, "material": "flute_e"}
    )
    assert status == 200 and tried["data"]["material"] == "flute_e"
    assert tried["data"]["sizes"]["inner"][0] == 180 and "lid" in tried["data"]["panels_px"]
    # 刀版上的长宽高尺寸线（压线尺寸）和能转的 3D 场景（贴图内嵌，不写文件）
    labels = [d["label"] for d in tried["data"]["dims_px"]]
    assert len(labels) == 3 and labels[0].startswith("18")
    scene = tried["data"]["scene"]
    assert scene["version"] == 3 and scene["parts"][0]["textures"]["base"].startswith(
        "data:image/png"
    )
    assert (
        call(studio, "POST", "/api/structure/try", {"patch": {}, "material": "label_coated"})[0]
        == 400
    )
    status, saved = call(
        studio,
        "POST",
        "/api/structure/save",
        {"patch": {"body": {"L": 180}}, "material": "flute_e"},
    )
    assert status == 200 and saved["ok"]
    data = json.loads((job / "job.json").read_text(encoding="utf-8"))
    assert data["params"]["body"]["L"] == 180 and data["material"]["preset"] == "flute_e"


def test_corner_finish_try_and_save(studio: Studio, job: Path) -> None:
    status, info = call(studio, "GET", "/api/structure")
    assert info["data"]["corners"] == {
        "set": None,
        "default": [2.0, 1.0],
        "round": [2.0, 1.0],
    }  # 卡纸默认外 2 / 内 1
    status, tried = call(
        studio, "POST", "/api/structure/try", {"patch": {}, "corners": {"inner": 0}}
    )
    c = tried["data"]["corners"]
    assert status == 200 and c["inner"] == 0 and c["inner_corners"] == 0 and c["outer"] == 2.0
    status, flute = call(studio, "POST", "/api/structure/try", {"patch": {}, "material": "flute_e"})
    assert flute["data"]["corners"]["default"] == [0.0, 0.0]  # 瓦楞默认不倒
    assert (
        call(studio, "POST", "/api/structure/try", {"patch": {}, "corners": {"outer": 50}})[0]
        == 400
    )
    status, saved = call(
        studio, "POST", "/api/structure/save", {"patch": {}, "corners": {"outer": 0}}
    )
    assert status == 200 and saved["ok"]
    data = json.loads((job / "job.json").read_text(encoding="utf-8"))
    assert data["corner_finish"] == {"outer": 0.0}
