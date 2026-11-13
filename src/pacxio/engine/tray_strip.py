"""长条包裹式折叠盒（FEFCO 0400、0443、0444、0447、0473、0474 等；按示意图推读，数值【初值】）。

一条长片沿 L 绕盒子的横截面（W × H）一圈：从底板开始，往右依次是后墙、顶、前墙、底（第二层）……，
往左依次是前墙、顶、后墙……，每过一条压线转 90°（同一个转向），所以配方只写每块面的宽（整面 / 一半 /
插舌深）和两头端翼的种类，坐标和折角都由引擎算。

端翼（每块面在 L 两头的边上，折 90° 进端面）：
- wall  整面端墙（深 = 端面在这块面法向上的尺寸：底 / 顶上是 H，前 / 后墙上是 W），可带 rim 往里折平的窄边
- half  一半深
- wing  夹层翼：深 = 整面 − 间隙，贴在端墙里面（分层在端墙里）
- dust  梯形防尘翼：深 0.8 × 整面，两头斜 15°
刀版：长条沿 x，L 沿 y（0..Lb），端翼在 y < 0 和 y > Lb；立体 X 沿 L，Y 前 → 后，Z 向上。
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import numpy as np

from pacxio.engine.tray_kit import Kit, Rigid, rect
from pacxio.messages import t as translate
from pacxio.model.common import Issue
from pacxio.model.dieline import PanelRole

if TYPE_CHECKING:
    from pacxio.engine.tray import Tray

FACES = ("bottom", "back", "top", "front")  # 往右绕的顺序
DUST_RATIO, DUST_TAPER_DEG = 0.8, 15.0
CLEAR_T, CLEAR_EXTRA = 1.0, 1.0  # 端翼两边让 t + 1
RIM_RATIO, RIM_MAX = 0.12, 25.0  # 端墙窄边 min(0.12·W, 25)
OVER_RATIO = 0.35  # 端墙顶再接一段盖过顶面：0.35·L（两头对盖不碰）
ROLE = {
    "bottom": PanelRole.BOTTOM,
    "back": PanelRole.BODY,
    "top": PanelRole.LID,
    "front": PanelRole.BODY,
}
INWARD = {"bottom": (0, 0, 1), "back": (0, -1, 0), "top": (0, 0, -1), "front": (0, 1, 0)}


def _extent(face: str, W: float, H: float) -> float:
    """这块面横过长条的整宽（底 / 顶是 W，前 / 后墙是 H）。"""
    return W if face in ("bottom", "top") else H


def _end_full(face: str, W: float, H: float) -> float:
    """端翼折进端面后，沿这块面的内法向能伸多深。"""
    return H if face in ("bottom", "top") else W


def validate(spec: list[dict[str, Any]], W: float, H: float, L: float) -> list[Issue]:
    if not spec or spec[0].get("face", "bottom") != "bottom":
        return [
            Issue(
                id="S03",
                message=translate("The first panel of the strip must be the base"),
                fix="strip[0].face = bottom",
            )
        ]
    if L < 30 or W < 20 or H < 20:
        return [
            Issue(
                id="S03",
                message=translate("The strip box dimensions are too small"),
                fix=translate("Increase L, W, or H"),
            )
        ]
    return []


def build_strip(spec: list[dict[str, Any]], Lb: float, Wb: float, h: float, t: float) -> Tray:
    """spec：[{side: right / left（第一块是底板，不写）, size: full / half / tuck / 数, ends: 端翼种类, layer}]。"""
    pose = Rigid(np.array([0.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0]), np.array([1.0, 0.0, 0.0]))
    kit = Kit.root("base", ROLE["bottom"], rect(0, 0, Wb, Lb), pose)
    _ends(kit, "base", "bottom", spec[0].get("ends"), 0.0, Wb, Wb, h, Lb, t, 2)
    step = 1
    primary = _primary_faces(spec)
    for side, sx in (("right", 1.0), ("left", -1.0)):
        prev, face_i, x = "base", 0, (Wb if sx > 0 else 0.0)
        n = 0
        for item in spec[1:]:
            if item.get("side", "right") != side:
                continue
            n += 1
            face_i = (face_i + (1 if sx > 0 else -1)) % 4
            face = FACES[face_i]
            full = _extent(face, Wb, h)
            size = item.get("size", "full")
            w = full if size == "full" else full / 2 if size == "half" else float(size) if not isinstance(size, str) \
                else min(0.5 * h, 40.0)  # fmt: skip
            pid = item.get("id") or f"{side}_{n}_{face}"
            x1 = x + sx * w
            # 立体：这块面从铰链往哪伸 = 上一块面的内法向（绕截面转 90°）
            toward = INWARD[FACES[(face_i - (1 if sx > 0 else -1)) % 4]]
            role = PanelRole.TUCK if size == "tuck" else ROLE[face]
            # 同一面的第二层（内衬 / 搭边 / 插舌）：效果图错开；插舌插进前 / 后墙里面往盒里，其余往外
            repeat = primary.get(face) != id(item)
            auto = (
                (3.0 if size == "tuck" and face in ("front", "back") else -2.0) if repeat else 0.0
            )
            # 内衬 / 搭边 / 插舌（同一面的第二层）两头让开端墙和端面里的翼
            second = primary.get(face) != id(item)
            e = (
                (4 * t + CLEAR_EXTRA)
                if size == "tuck"
                else (t + CLEAR_EXTRA)
                if item.get("inset") or second
                else 0.0
            )
            kit.add(pid, role, rect(min(x, x1), e, max(x, x1), Lb - e), prev, ((x, e), (x, Lb - e)), toward, step + n,
                    layer=auto)  # fmt: skip
            if face == "top" and not any(f["key"] == "top" for f in kit.faces):
                kit.face("top", pid)
            _ends(
                kit, pid, face, item.get("ends"), min(x, x1), max(x, x1), Wb, h, Lb, t, step + n + 1
            )
            prev, x = pid, x1
    kit.face("bottom", "base")
    return kit.tray()


def _faces(spec: list[dict[str, Any]]) -> list[tuple[dict[str, Any], str, int]]:
    """每块面落在哪个面上、离底板第几块。"""
    out: list[tuple[dict[str, Any], str, int]] = [(spec[0], "bottom", 0)]
    for side, step in (("right", 1), ("left", -1)):
        i = n = 0
        for item in spec[1:]:
            if item.get("side", "right") == side:
                i, n = (i + step) % 4, n + 1
                out.append((item, FACES[i], n))
    return out


def _primary_faces(spec: list[dict[str, Any]]) -> dict[str, int]:
    """每个面上当外层的那块：离底板最近的整面、不让位的（底板永远是底面的外层）。"""
    out: dict[str, int] = {"bottom": id(spec[0])}
    for item, face, _ in sorted(_faces(spec)[1:], key=lambda f: f[2]):
        if face not in out and item.get("size", "full") == "full" and not item.get("inset"):
            out[face] = id(item)
    return out


def _ends(kit: Kit, pid: str, face: str, kind: str | None, x0: float, x1: float, Wb: float, h: float,
          Lb: float, t: float, step: int) -> None:  # fmt: skip
    """这块面在 L 两头（刀版 y = 0 和 y = Lb）的端翼：折 90° 进端面，沿这块面的内法向伸。"""
    if not kind:
        return
    c = CLEAR_T * t + CLEAR_EXTRA
    full = h if face in ("bottom", "top") else Wb - c  # 端翼在端面里能伸多深
    base = kind.split("+")[0]
    depth = {"wall": full, "half": full / 2, "wing": full - c, "dust": DUST_RATIO * full}[base]
    layer = {"wall": 0.0, "half": 1.0, "wing": 3.0, "dust": 2.0}[base]  # 效果图：端面里各层错开
    for end, y0, sy in (("a", 0.0, -1.0), ("b", Lb, 1.0)):
        fid = f"{pid}_{end}"
        if base == "dust":
            k = depth * math.tan(math.radians(DUST_TAPER_DEG))
            pts = [
                (x0 + c, y0),
                (x0 + c + k, y0 + sy * depth),
                (x1 - c - k, y0 + sy * depth),
                (x1 - c, y0),
            ]
        else:
            pts = rect(x0 + c, y0, x1 - c, y0 + sy * depth)
        kit.add(
            fid,
            PanelRole.DUST_FLAP,
            pts,
            pid,
            ((x0 + c, y0), (x1 - c, y0)),
            INWARD[face],
            step,
            layer=layer,
        )
        if (
            "+rim" in kind or "+over" in kind
        ):  # 端墙顶再接一段：窄边 / 盖过顶面的翼，沿 L 往盒里折平
            v = min(RIM_RATIO * Wb, RIM_MAX) if "+rim" in kind else OVER_RATIO * Lb
            ye = y0 + sy * depth
            kit.add(f"{fid}_rim", PanelRole.DUST_FLAP, rect(x0 + c, ye, x1 - c, ye + sy * v), fid,
                    ((x0 + c, ye), (x1 - c, ye)), (-sy, 0.0, 0.0), step + 1, layer=-1.0)  # fmt: skip
