"""Closed v3 mesh from the same panel/crease equations as the interactive viewer.

All scene coordinates stay in mm until emission. Float32 panel/strip buffers
match the WebGL source before the final transform. No Node or GPU is needed.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from typing import Any

import numpy as np
from numpy.typing import NDArray

from pacxio.mockup.folding import _rotation, angle_at

FloatArray = NDArray[Any]


def unit(a: FloatArray) -> FloatArray:
    return a / (np.linalg.norm(a) or 1.0)


def pose(pose: dict[str, Any]) -> FloatArray:
    x, y, z, w = pose["q"]
    m = np.eye(4)
    m[:3, :3] = [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ]
    m[:3, 3] = pose["t"]
    return m


def clip(poly: FloatArray, plane: FloatArray) -> FloatArray:
    """Clip complete vertices, preserving UVs/normals/paper coordinates."""
    out = []
    for a, b in zip(poly, np.roll(poly, -1, axis=0), strict=True):
        da = a[:3] @ plane[:3] + plane[3]
        db = b[:3] @ plane[:3] + plane[3]
        ia = da >= -1e-12
        ib = db >= -1e-12
        if ia:
            out.append(a)
        if ia != ib:
            v = a + (b - a) * (da / (da - db))
            v[:3] -= plane[:3] * ((v[:3] @ plane[:3] + plane[3]) / (plane[:3] @ plane[:3]))
            out.append(v)
    return np.asarray(out).reshape(-1, 12)


def triangles(poly: FloatArray) -> Iterator[FloatArray]:
    for i in range(1, len(poly) - 1):
        t = np.array([poly[0], poly[i], poly[i + 1]])
        cross = np.cross(t[1, :3] - t[0, :3], t[2, :3] - t[0, :3])
        if np.linalg.norm(cross) < 2e-16:
            continue
        if cross @ t[0, 3:6] < 0:
            t[[1, 2]] = t[[2, 1]]
        yield t


def closed_mesh(scene: dict[str, Any]) -> tuple[list[dict[str, Any]], FloatArray]:
    part = scene["parts"][0]
    panels = part["panels"]
    t = part["caliper"]
    sx, ox, sy, oy = part["uv"]
    final = pose(part["poses"][-1])
    local: list[FloatArray] = []
    world = []
    lo = np.full(3, np.inf)
    hi = -lo
    for pn in panels:
        m = np.eye(4)
        if pn["parent"] >= 0:
            angle = angle_at(pn["keys"], scene["steps"])
            m = local[pn["parent"]] @ _rotation(pn["origin"], pn["axis"], angle)
            last = pn["keys"][-1][1] if pn["keys"] else 0
            if pn["layer"] and last:
                shift = np.eye(4)
                shift[2, 3] = -pn["layer"] * t * angle / last
                m = m @ shift
        local.append(m)
        world.append(final @ m)
        for ring in pn["rings"]:
            xy = np.asarray(ring).reshape(-1, 2)
            points = np.column_stack((xy, np.zeros(len(xy)), np.ones(len(xy)))) @ world[-1].T
            minimum = points[:, :3].min(axis=0)
            if pn.get("below_ground"):
                minimum[2] = np.inf
            lo = np.minimum(lo, minimum)
            hi = np.maximum(hi, points[:, :3].max(axis=0))
    size = (hi - lo) * 0.001
    for m in world:
        m[:3, 3] -= lo
    meshes = []

    def emit(
        name: str,
        role: str,
        rows: list[Any],
        matrix: FloatArray | None = None,
        strip: int | None = None,
    ) -> None:
        if not len(rows):
            return
        old = np.asarray(rows, dtype=np.float32).astype(np.float64).reshape(-1, 8)
        data = np.ones((len(old), 12))
        data[:, 8:11] = old[:, :3] * 0.001
        data[:, :3] = (
            old[:, :3] if matrix is None else old[:, :3] @ matrix[:3, :3].T + matrix[:3, 3]
        )
        data[:, :3] *= 0.001
        data[:, 3:6] = old[:, 5:8] if matrix is None else old[:, 5:8] @ matrix[:3, :3].T
        data[:, 6] = old[:, 3]
        data[:, 7] = 1 - old[:, 4]
        clean: list[FloatArray] = []
        for tri in data.reshape(-1, 3, 12):
            clean.extend(triangles(clip(tri, np.array([0, 0, 1, 0.000001]))))
        meshes.append(
            {
                "name": name,
                "role": role,
                "strip": strip,
                "data": np.asarray(clean).reshape(-1, 3, 12),
            }
        )

    for pn, m in zip(panels, world, strict=True):
        xy = np.asarray(pn["vertices"]).reshape(-1, 2)[pn["triangles"]]
        front = []
        back = []
        walls = []
        for x, y in xy:
            front.append([x, y, 0, sx * x + ox, sy * y + oy, 0, 0, 1])
            back.append([x, y, -t, 0, 0, 0, 0, -1])
        for r in pn["rings"]:
            ring = np.asarray(r).reshape(-1, 2)
            for a, b in zip(ring, np.roll(ring, -1, axis=0), strict=True):
                dx, dy = b - a
                length = math.hypot(dx, dy) or 1
                for x, y, z in [(*a, 0), (*b, 0), (*b, -t), (*a, 0), (*b, -t), (*a, -t)]:
                    walls.append([x, y, z, 0, 0, dy / length, -dx / length, 0])
        emit(pn["id"] + "-print", "print", front, m)
        emit(pn["id"] + "-paper", "paper", back, m)
        emit(pn["id"] + "-cut", "cut", walls, m)
    for si, st in enumerate(part["strips"]):
        mp, mc = world[st["parent"]], world[st["child"]]
        o = np.array([*st["origin"], 0])
        n = np.array([*st["to_parent"], 0])
        axis = np.array([*st["along"], 0])
        h = st["half"]
        pp = mp[:3, :3] @ (o + n * h) + mp[:3, 3]
        pc = mc[:3, :3] @ (o - n * h) + mc[:3, 3]
        tp = mp[:3, :3] @ -n
        tc = mc[:3, :3] @ n
        np_ = mp[:3, 2]
        nc = mc[:3, 2]
        d = mp[:3, :3] @ axis
        theta = math.acos(np.clip(-tp @ tc, -1, 1))
        handle = (
            2 * h / 3 if theta < 1e-3 else 4 / 3 * math.tan(theta / 4) * (h / math.tan(theta / 2))
        )
        c1 = pp + tp * handle
        c2 = pc + tc * handle
        ring = []
        for i in range(7):
            u = i / 6
            w = 1 - u
            p = w**3 * pp + 3 * w * w * u * c1 + 3 * w * u * u * c2 + u**3 * pc
            normal = unit(np_ * w + nc * u)
            ra, rb, k = (
                (st["parent_end"], st["crease"], u * 2)
                if u < 0.5
                else (st["crease"], st["child_end"], u * 2 - 1)
            )
            lengths = np.asarray(ra) + (np.asarray(rb) - ra) * k

            uvs = []
            for length in lengths:
                x, y, _ = o + n * h * (1 - 2 * u) + axis * length
                uvs.append([sx * x + ox, sy * y + oy])
            ring.append((p + d * lengths[0], p + d * lengths[1], normal, uvs[0], uvs[1]))
        outer: list[list[float]] = []
        inner: list[list[float]] = []

        def put(
            rows: list[list[float]],
            points: list[FloatArray],
            normals: list[FloatArray],
            uvs: list[list[float]] | None = None,
        ) -> None:
            for j in [0, 1, 2, 0, 2, 3]:
                rows.append([*points[j], *(uvs[j] if uvs else [0, 0]), *normals[j]])

        for r0, r1 in zip(ring[:-1], ring[1:], strict=True):
            a, b, n0, ua, ub = r0
            c, d1, n1, uc, ud = r1
            put(outer, [a, b, d1, c], [n0, n0, n1, n1], [ua, ub, ud, uc])
            ia, ib, ic, id_ = a - n0 * t, b - n0 * t, c - n1 * t, d1 - n1 * t
            put(inner, [ia, ib, id_, ic], [-n0, -n0, -n1, -n1])
            put(inner, [a, c, ic, ia], [-d] * 4)
            put(inner, [b, d1, id_, ib], [d] * 4)
        emit("crease-outside", "print", outer, strip=si)
        emit("crease-inside", "paper", inner, strip=si)
    return meshes, size
