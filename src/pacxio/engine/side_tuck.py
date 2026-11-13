"""侧插盖（ECMA 40）：盖两侧带翼（翼合上后插进两侧墙里面），对面一片梯形内翼，侧墙不带翼。

原在彩盒拼装器（engine/carton.py）里，2026-11-07 结构合并第三阶段挪出来，开槽箱拼装器（唯一的长方筒身拼装器）
直接用；卡纸、瓦楞同一份，纸厚相关的让位按 t 算（GEOMETRY_ENGINE.md §20）。数值全部【初值】。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from pacxio.geometry import Affine, Path, Segment, Vec, arc_segment
from pacxio.messages import t
from pacxio.model.dieline import Edge, EdgeType, PanelRole
from pacxio.styles.components.base import Piece
from pacxio.styles.components.carton import RteDims, _backwards, _chain, _crease
from pacxio.styles.components.folds import FoldAction, action
from pacxio.styles.components.tube4 import BodyX

Wall = Literal["back", "front"]
OPPOSITE = {"back": "front", "front": "back"}
SIDE_TUCK_CLEAR_T = 1.5  # 侧插翼离盖远端、根部切口长 = max(1, 1.5·t)
SIDE_TUCK_RADIUS_MAX_MM = 5.0  # 翼远端外角圆角
INNER_TAPER_DEG = 10.0  # 侧插的内翼两边往里斜
SIDE_TUCK_GLUE_T = 1.0  # 纸厚到这个（瓦楞）且盖挂正面时，side_b 那片翼远端让开糊口（§20）
INNER_MIN_TOP_MM = 10.0  # 内翼远端至少这么宽


@dataclass(frozen=True)
class SideTuck:
    """侧插（ECMA 40）：盖两侧带翼（翼合上后插进两侧墙里面），对面一片梯形内翼，侧墙不带翼。

    先在局部坐标里画顶端（铰链线 y = 0、翼往 +y、从右往左走），顶端平移到盒身上沿，底端沿 y 镜像再反着走。
    翼的根边和旁边侧墙的上沿共用一刀（不留细条废料），根部沿翼压线切开一小段让位；挨着糊口那片翼的根边是外轮廓。
    """

    dims: RteDims
    wall: Wall
    wing: float
    clear: float
    radius: float
    inset: float
    taper_deg: float
    short_b: float = 0.0  # side_b 那片翼远端收短（瓦楞盖挂正面：翼远端会压在糊口上）

    def wing_len(self, wid: str) -> float:
        return self.dims.ld - self.clear - (self.short_b if wid.endswith("wing_b") else 0.0)

    def inner_depth(self) -> float:
        return self.dims.ld - self.clear

    def inner_top(self) -> float:
        d = self.dims
        return (
            d.wb - 2 * self.inset - 2 * self.inner_depth() * math.tan(math.radians(self.taper_deg))
        )

    def problems(self) -> list[tuple[str, str]]:
        d, out = self.dims, []
        if self.wing > d.ws - 1:
            out.append(
                (
                    t(
                        "Side-tuck flap width {flap_width:g} exceeds side-panel width {panel_width:.1f} minus 1",
                        flap_width=self.wing,
                        panel_width=d.ws,
                    ),
                    t("Reduce wing or increase W"),
                )
            )
        if self.wing > d.Hi - 2:
            out.append(
                (
                    t("Side-tuck flap width {width:g} exceeds box height minus 2", width=self.wing),
                    t("Reduce wing width"),
                )
            )
        if d.ld - 2 * self.clear < 2 * self.radius + 1:
            out.append(
                (t("The box is too shallow to fit the side-tuck flaps"), t("Increase the width W"))
            )
        elif self.wing_len("wing_b") - self.clear < 2 * self.radius + 1:
            out.append((t("With a corrugated side-tuck lid at the front, the side_b flap must clear the glue seam. The box is too shallow."),
                        t("Attach the lid to the back (on=0) or increase W")))  # fmt: skip
        if self.inner_top() < INNER_MIN_TOP_MM:
            out.append(
                (
                    t(
                        "Only {width:.1f} remains at the tip of the inner side-tuck flap",
                        width=self.inner_top(),
                    ),
                    t("Increase L or reduce the taper of the inner flap"),
                )
            )
        return out

    def sizes(self, prefix: str) -> dict[str, tuple[float, float]]:
        d = self.dims
        return {f"{prefix}_lid": (d.wb, d.ld), f"{prefix}_wing_a": (self.wing, self.wing_len("wing_a")),
                f"{prefix}_wing_b": (self.wing, self.wing_len("wing_b")),
                f"{prefix}_inner": (d.wb - 2 * self.inset, self.inner_depth())}  # fmt: skip

    def _local(self, xs: BodyX, prefix: str) -> Piece:
        d = self.dims
        xb0, xb1, xa1, xf1, xs1 = xs
        ranges = {"back": (xb0, xb1), "front": (xa1, xf1)}
        lx0, lx1 = ranges[self.wall]
        ox0, ox1 = ranges[OPPOSITE[self.wall]]
        w, c, r, e, ld = self.wing, self.clear, self.radius, self.inset, d.ld
        lid, inner = f"{prefix}_lid", f"{prefix}_inner"
        # 盖左边的翼插进哪面侧墙：背面的左边经糊口接 side_b；正面的左边是 side_a
        left_id, right_id = (
            (f"{prefix}_wing_b", f"{prefix}_wing_a")
            if self.wall == "back"
            else (f"{prefix}_wing_a", f"{prefix}_wing_b")
        )
        ty_r, ty_l = self.wing_len(right_id), self.wing_len(left_id)  # 两片翼的远边
        V = Vec

        # 右翼（往 +x）：从根部外角往上走到远边、再回到盖边
        xr = lx1 + w
        right_out = _chain([V(xr, 0), V(xr, ty_r - r)])
        right_out.append(arc_segment(V(xr - r, ty_r - r), r, 0.0, math.pi / 2))
        right_out += _chain([V(xr - r, ty_r), V(lx1, ty_r)])
        # 左翼（往 −x）：从盖边出发，经远边下到根部外角
        xl = lx0 - w
        left_out = _chain([V(lx0, ty_l), V(xl + r, ty_l)])
        left_out.append(arc_segment(V(xl + r, ty_l - r), r, math.pi / 2, math.pi))
        left_out += _chain([V(xl, ty_l - r), V(xl, 0)])

        tan = math.tan(math.radians(self.taper_deg))
        D = self.inner_depth()
        inner_out = _chain([V(ox1, 0), V(ox1 - e, 0), V(ox1 - e - D * tan, D), V(ox0 + e + D * tan, D),
                            V(ox0 + e, 0), V(ox0, 0)])  # fmt: skip
        lid_out = _chain([V(lx1, ty_r), V(lx1, ld), V(lx0, ld), V(lx0, ty_l)])

        out: list[Segment] = []
        if (
            self.wall == "back"
        ):  # 右往左：side_b → front（内翼）→ side_a（右翼）→ 盖 → 左翼 → 根边到 xb0
            out += _chain([V(xs1, 0), V(xf1, 0)]) + inner_out + _chain([V(xa1, 0), V(xr, 0)])
            out += right_out + lid_out + left_out + _chain([V(xl, 0), V(lx0, 0)])
        else:  # side_b（右翼）→ 盖 → 左翼 → side_a → back（内翼）
            out += _chain([V(xs1, 0), V(xr, 0)]) + right_out + lid_out + left_out
            out += _chain([V(xl, 0), V(xb1, 0)]) + inner_out

        def closed(segs: list[Segment]) -> Path:
            return Path([*segs, Segment(segs[-1].end, segs[0].start)], closed=True)

        n = c  # 根部沿翼压线切开的长度
        panels = {
            lid: (PanelRole.LID if prefix == "top" else PanelRole.BOTTOM,
                  Path.rect(lx0, 0, lx1 - lx0, ld)),
            right_id: (PanelRole.TUCK, closed(_chain([V(lx1, 0), V(xr, 0)]) + right_out)),
            left_id: (PanelRole.TUCK, closed(left_out + _chain([V(xl, 0), V(lx0, 0)]))),
            inner: (PanelRole.DUST_FLAP, closed(inner_out[1:-1])),
        }  # fmt: skip
        creases = [
            _crease(f"crease_{self.wall}_{lid}", (lx0, 0), (lx1, 0), [self.wall, lid]),
            _crease(f"crease_{lid}_{right_id[-6:]}", (lx1, n), (lx1, ty_r), [lid, right_id]),
            _crease(f"crease_{lid}_{left_id[-6:]}", (lx0, n), (lx0, ty_l), [lid, left_id]),
            _crease(f"crease_{OPPOSITE[self.wall]}_{inner}", (ox0 + e, 0), (ox1 - e, 0),
                    [OPPOSITE[self.wall], inner]),
        ]  # fmt: skip
        right_side = "side_a" if self.wall == "back" else "side_b"
        cuts = [
            Edge(id=f"cut_{right_id}", type=EdgeType.CUT,
                 segments=_chain([V(xr, 0), V(lx1, 0), V(lx1, n)]), panels=[right_id, right_side]),
        ]  # fmt: skip
        if self.wall == "back":  # 挨着糊口那片翼：根边在外轮廓上，只切根部一小段
            cuts.append(Edge(id=f"cut_{left_id}", type=EdgeType.CUT,
                             segments=_chain([V(lx0, 0), V(lx0, n)]), panels=[left_id, lid]))  # fmt: skip
        else:
            cuts.append(Edge(id=f"cut_{left_id}", type=EdgeType.CUT,
                             segments=_chain([V(xl, 0), V(lx0, 0), V(lx0, n)]),
                             panels=[left_id, "side_a"]))  # fmt: skip
        return Piece(outline=out, panels=panels, creases=creases, inner_edges=cuts)

    def build(self, xs: BodyX, top: bool) -> Piece:
        prefix = "top" if top else "btm"
        local = self._local(xs, prefix)
        if top:
            return local.transformed(Affine.translate(0, self.dims.hb))
        mirrored = local.transformed(Affine(1, 0, 0, -1, 0, 0))
        mirrored.outline = _backwards(mirrored.outline)
        return mirrored


def side_tuck(wall: Wall, wing: float | None, d: RteDims) -> SideTuck:
    c = max(1.0, SIDE_TUCK_CLEAR_T * d.t)
    w = wing if wing is not None else d.tl
    # 糊口贴在 side_b 里面、靠背面那头：盖挂背面时 side_b 那片翼从糊口那头插进去，叠在糊口里面一层就行；
    # 盖挂正面时翼远端伸到糊口上、近端又顶着正面墙，瓦楞的纸厚两头都让不开——远端收短让开糊口（§20）
    short_b = d.gw + d.t if wall == "front" and d.t >= SIDE_TUCK_GLUE_T else 0.0
    return SideTuck(d, wall, w, c, min(SIDE_TUCK_RADIUS_MAX_MM, w / 3), c + d.t,
                    INNER_TAPER_DEG, short_b)  # fmt: skip


def folds(st: SideTuck, prefix: str) -> dict[str, FoldAction]:
    return {
        f"{prefix}_inner": action(0, layer=1),
        f"{prefix}_lid": action(1),
        f"{prefix}_wing_a": FoldAction(((0, 45.0), (1, 90.0)), 1),
        # side_b 那片翼和糊口叠在侧墙里面：翼放在糊口里面一层（只影响折叠分层）；
        # 远端已收短让开糊口的（瓦楞盖挂正面）贴侧墙就行
        f"{prefix}_wing_b": FoldAction(((0, 45.0), (1, 90.0)), 1 if st.short_b else 2),
    }


def poses(st: SideTuck, prefix: str) -> dict[str, tuple[str, str]]:
    wall, opp = st.wall, OPPOSITE[st.wall]
    return {f"{prefix}_lid": ("flat", wall), f"{prefix}_inner": ("flat", opp),
            f"{prefix}_wing_a": ("drop_u", wall), f"{prefix}_wing_b": ("drop_u", wall)}  # fmt: skip


__all__ = ["SideTuck", "folds", "poses", "side_tuck"]
