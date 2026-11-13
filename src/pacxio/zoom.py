"""看清确认图上的小字：取一块原分辨率的局部（ ）。

开源版回传的确认图是 100 DPI 加水印的降级版；局部向服务器要（原图 200 DPI 裁切，加水印，有次数上限）。
本机装着闭源包时，确认图本来就是原图，直接从本机文件裁。
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Any

from PIL import Image

from pacxio.cloud import auth, config
from pacxio.cloud.client import Client, CloudError
from pacxio.cloud.sync import read_state
from pacxio.model.common import Issue, Result
from pacxio.notes import read_map, world_to_rel

ZOOM_DIR = "studio/zoom"
DEFAULT_IMAGE = "preview.png"
PAD_MM = 2.0  # 面四周多留一点，看得到边上的字


def _map_for(directory: Path, image: str) -> dict[str, Any] | None:
    """图的对照；确认图的图层（不带标注 / 不带刀线的那份）用确认图自己的对照。"""
    m = read_map(directory, image)
    if m is not None:
        return m
    name = Path(image).name
    for side in sorted((directory / image).parent.glob("*.map.json")):
        try:
            data = json.loads(side.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and name in (data.get("base_file"), data.get("art_file")):
            return data
    return None


def _fail(r: Result, id_: str, message: str, fix: str, code: int = 2) -> Result:
    return r.fail(Issue(id=id_, message=message, fix=fix), exit_code=code)


def zoom(directory: Path, *, panel: str | None = None, box: list[float] | None = None,
         image: str = DEFAULT_IMAGE) -> Result:  # fmt: skip
    r = Result(command="zoom", job=str(directory))
    if (panel is None) == (box is None):
        return _fail(r, "ARG", "要给 --panel（一个面）或 --box（x0,y0,x1,y1，0–1 相对位置）其中一个",
                     "例如 `pacxio zoom <job> --panel back`")  # fmt: skip
    m = _map_for(directory, image)
    if m is None or not (directory / image).is_file():
        return _fail(r, "ZOOM", f"{image} 没有对照文件，不能取局部", "先运行 `pacxio render <job>`")
    if panel is not None:
        found = next((p for p in m.get("panels", []) if p.get("id") == panel), None)
        if found is None:
            names = ", ".join(str(p.get("id")) for p in m.get("panels", []))
            return _fail(r, "ZOOM", f"{image} 上没有面 {panel}", f"可用的面：{names}")
        x0, y0, x1, y1 = found["bbox"]
        rel = world_to_rel(m, (x0 - PAD_MM, y0 - PAD_MM, x1 + PAD_MM, y1 + PAD_MM))
    else:
        assert box is not None
        rel = (box[0], box[1], box[2], box[3])
    rel = (max(0.0, rel[0]), max(0.0, rel[1]), min(1.0, rel[2]), min(1.0, rel[3]))
    if not (rel[0] < rel[2] and rel[1] < rel[3]):
        return _fail(r, "ARG", "--box 要满足 0 ≤ x0 < x1 ≤ 1、0 ≤ y0 < y1 ≤ 1（原点在图片左上角）",
                     "例如 --box 0.2,0.1,0.5,0.4")  # fmt: skip
    if config.use_cloud():
        state = read_state(directory)
        if state.get("url") != config.login().url or not isinstance(state.get("job"), str):
            return _fail(r, "ZOOM", "这个 job 还没在云端 render 过", "先运行 `pacxio render <job>`")
        try:
            login = auth.ensure()
            try:
                data = Client(login.url, login.token).crop(str(state["job"]), image, rel)
            except CloudError as e:
                raise auth.relogin_after_401(login) if e.id == "CLOUD_AUTH" else e from None
        except CloudError as e:
            return _fail(r, e.id, e.message, e.fix, 3 if e.id in ("CLOUD_NET", "CLOUD_AUTH", "CLOUD_LOGIN") else 1)
        source = "cloud"
    else:
        with Image.open(directory / image) as im:
            w, h = im.size
            part = im.crop((int(rel[0] * w), int(rel[1] * h), round(rel[2] * w), round(rel[3] * h)))
            buf = io.BytesIO()
            part.save(buf, format="PNG")
            data = buf.getvalue()
        source = "local"
    name = panel or hashlib.sha256(repr(rel).encode()).hexdigest()[:8]
    out = directory / ZOOM_DIR / f"{Path(image).stem}-{name}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    r.artifacts.append(str(out))
    r.data = {"image": image, "box": [round(v, 4) for v in rel], "source": source, "dpi": m.get("dpi")}
    return r
