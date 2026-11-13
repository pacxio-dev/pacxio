"""CF2 导出（）。只含直线和圆弧，线型编号取自配置档。

格式依据：开源转换器 treeDiM/DXF2Diecut 的 ExporterCF2.cs 的写法（记录结构与线型码表）。
该实现没有解释全部字段含义（如圆弧记录末尾的 `1,0,0.0`、`UM`），单位也未写入文件。
因此本输出【未经任何刀模厂软件验证】—— 要求必须由刀模厂软件实际打开后才算完成。
圆弧一律按逆时针写出（顺时针弧交换起止点），这是对字段 `1` 的假设。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from pacxio import __version__
from pacxio.geometry import Segment, Vec
from pacxio.messages import t
from pacxio.model.dieline import Dieline
from pacxio.model.profile import Profile

AUTHORING_TOOL = f"pacxio {__version__}"
BLOCK_ID = "MODEL1"
PEN_TYPE = 1  # DXF2Diecut 的 pt 字段恒为 1
EOL = "\r\n"  # DXF2Diecut 用平台换行；刀模软件多为 Windows，固定 CRLF 以保证输出可复现
COORD_DECIMALS = 4


def _n(v: float) -> str:
    s = f"{round(v, COORD_DECIMALS):.{COORD_DECIMALS}f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _ccw_endpoints(s: Segment) -> tuple[Vec, Vec, Vec]:
    center, _, _, _, ccw = s.arc_info()
    return (s.start, s.end, center) if ccw else (s.end, s.start, center)


def dieline_to_cf2(dl: Dieline, profile: Profile) -> str:
    lt = profile.cf2.linetypes
    used = {e.type.value for e in dl.edges}
    missing = used - set(lt)
    if missing:
        raise KeyError(t("Profile cf2.linetypes is missing line types: {types}", types=sorted(missing)))

    x0, y0, x1, y1 = dl.bbox
    lines = [
        "$BOF",
        "V2",
        "ORDER",
        f"%1 : {AUTHORING_TOOL}",
        "END",
        f"MAIN,{AUTHORING_TOOL}",
        "UM",
        f"LL,{_n(x0)},{_n(y0)}",
        f"UR,{_n(x1)},{_n(y1)}",
        "SCALE,1,1",
        f"C,{BLOCK_ID},0,0,0,1,1",
        "END",
        f"SUB,{BLOCK_ID}",
    ]
    for e in dl.edges:
        tool = lt[e.type.value]
        for s in e.segments:
            if s.is_line:
                a, b = s.start, s.end
                lines.append(f"L,{PEN_TYPE},{tool},0,{_n(a.x)},{_n(a.y)},{_n(b.x)},{_n(b.y)},0,0.0")
            else:
                a, b, c = _ccw_endpoints(s)
                lines.append(
                    f"A,{PEN_TYPE},{tool},0,{_n(a.x)},{_n(a.y)},{_n(b.x)},{_n(b.y)},"
                    f"{_n(c.x)},{_n(c.y)},1,0,0.0"
                )
    lines.append("END")
    return EOL.join(lines) + EOL


def write_cf2(dl: Dieline, profile: Profile, path: Path) -> None:
    # 一字节一字符，无 BOM（同 DXF2Diecut 的 GetResultByteArray）
    path.write_bytes(dieline_to_cf2(dl, profile).encode("ascii"))


@dataclass(frozen=True)
class Cf2Entity:
    kind: str  # "L" | "A"
    tool: int
    points: tuple[float, ...]  # L: x0,y0,x1,y1；A: 起 x,y 终 x,y 圆心 x,y


def read_cf2(text: str) -> list[Cf2Entity]:
    """解析 SUB 块里的 L/A 记录，用于往返校验（）。"""
    out: list[Cf2Entity] = []
    in_sub = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("SUB,"):
            in_sub = True
        elif line == "END":
            in_sub = False
        elif in_sub and line[:2] in ("L,", "A,"):
            f = line.split(",")
            n = 4 if f[0] == "L" else 6
            out.append(Cf2Entity(f[0], int(f[2]), tuple(float(v) for v in f[4 : 4 + n])))
    return out


def arc_sweep_deg(e: Cf2Entity) -> float:
    """圆弧（逆时针）扫过的角度，用于校验。"""
    xs, ys, xe, ye, xc, yc = e.points
    a0 = math.atan2(ys - yc, xs - xc)
    a1 = math.atan2(ye - yc, xe - xc)
    return math.degrees((a1 - a0) % (2 * math.pi))
