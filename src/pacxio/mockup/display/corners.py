"""Display-only mailer corner joins, generalized from the reviewed Tee prototype.

No dieline change. Planes, strip indices and height come from this scene; UVs
are barycentrically interpolated. Unexpected topology rejects the enhancement.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from shapely import constrained_delaunay_triangles
from shapely.geometry import LineString, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import polygonize, unary_union

from .geometry import FloatArray, clip, triangles


def repair(
    meshes: list[dict[str, Any]], scene: dict[str, Any], size: FloatArray
) -> list[dict[str, Any]]:
    part = scene["parts"][0]
    ids = {p["id"]: i for i, p in enumerate(part["panels"])}

    def index(parent: str, child: str) -> int:
        return next(
            i
            for i, s in enumerate(part["strips"])
            if s["parent"] == ids[parent] and s["child"] == ids[child]
        )

    left, right, front = (
        index("top_lid", "dust_la"),
        index("top_lid", "dust_lb"),
        index("top_lid", "top_tuck"),
    )
    raw = {(m["name"], m["strip"]): m["data"].copy() for m in meshes if m["strip"] is not None}
    lid = next(m["data"] for m in meshes if m["name"] == "top_lid-print").reshape(-1, 12)
    half = part["strips"][front]["half"] * 0.001
    front_y = lid[:, 1].min() - half
    lx = lid[:, 0].min() - half
    rx = lid[:, 0].max() + half
    L = np.array([-1, 1, 0, lx - front_y])
    R = np.array([1, 1, 0, -rx - front_y])
    planes = {left: [L], right: [R], front: [-L, -R]}
    caliper = part["caliper"] * 0.001
    bottom = size[2] - caliper
    for m in meshes:
        if m["strip"] not in planes:
            continue
        out: list[FloatArray] = []
        for tri in m["data"]:
            poly = tri
            for p in planes[m["strip"]]:
                poly = clip(poly, p)
            out.extend(triangles(poly))
        m["data"] = np.asarray(out).reshape(-1, 3, 12)

    def section(si: int, plane: FloatArray, xorigin: float) -> BaseGeometry:
        outer = raw["crease-outside", si].reshape(-1, 12)
        all_tri = [*raw["crease-outside", si][:, :, :3], *raw["crease-inside", si][:, :, :3]]
        for normal in [outer[0, 3:6], outer[-1, 3:6]]:
            pts = outer[np.all(abs(outer[:, 3:6] - normal) < 1e-7, axis=1), :3]
            unique = {tuple(np.round(v, 10)): v for v in pts}
            if len(unique) != 2:
                raise ValueError("Unexpected crease interface")
            a, b = unique.values()
            c = b - normal * caliper
            d = a - normal * caliper
            all_tri.extend([np.array([a, b, c]), np.array([a, c, d])])
        edges = {}
        for tri in all_tri:
            dist = tri @ plane[:3] + plane[3]
            if np.all(abs(dist) < 1e-12):
                continue
            pts = []
            for i in range(3):
                a, b = tri[i], tri[(i + 1) % 3]
                da, db = dist[i], dist[(i + 1) % 3]
                if abs(da) < 1e-12:
                    pts.append(a)
                if da * db < 0:
                    pts.append(a + (b - a) * (da / (da - db)))
            points = {
                tuple(np.round([(v[0] - xorigin) * 1000, (v[2] - bottom) * 1000], 8)) for v in pts
            }
            if len(points) != 2:
                continue
            a, b = sorted(points)
            if math.dist(a, b) < 1e-7:
                continue
            edges[a, b] = LineString([a, b])
        polys = list(polygonize(list(edges.values())))
        if not polys:
            raise ValueError("Open crease section")
        return unary_union(polys)

    cap: list[FloatArray] = []
    for si, plane, xorigin in [(left, L, lx), (right, R, rx)]:
        section_a, section_b = section(si, plane, xorigin), section(front, plane, xorigin)
        for side, piece in enumerate(
            [section_a.difference(section_b), section_b.difference(section_a)]
        ):
            normal = plane[:3] / math.sqrt(2) * (-1 if side == 0 else 1)
            for poly in constrained_delaunay_triangles(piece).geoms:
                assert isinstance(poly, Polygon)
                tri = np.ones((3, 12))
                for j, (u, v) in enumerate(list(poly.exterior.coords)[:3]):
                    x = xorigin + u / 1000
                    z = bottom + v / 1000
                    y = (-plane[3] - plane[0] * x) / plane[1]
                    tri[j] = [x, y, z, *normal, 0, 0, x, y, z, 1]
                cap.extend(triangles(tri))
    meshes.append(
        {
            "name": "crease-miter-closure",
            "role": "paper",
            "strip": None,
            "data": np.asarray(cap).reshape(-1, 3, 12),
        }
    )
    # The 1 mm free-end compression uses topology IDs instead of fixed strip indices.
    pairs = {
        frozenset((part["panels"][s["parent"]]["id"], part["panels"][s["child"]]["id"])): i
        for i, s in enumerate(part["strips"])
    }
    rim_strips = {
        pairs[frozenset(pair)]
        for pair in [
            ("side_a", "rim_a"),
            ("side_b", "rim_b"),
            ("rim_a", "side_a_in"),
            ("rim_b", "side_b_in"),
        ]
    }
    return_strips = {
        pairs[frozenset(pair)] for pair in [("rim_a", "side_a_in"), ("rim_b", "side_b_in")]
    }
    radius = min(0.001, caliper * 2 / 3)
    top = size[2]
    width = size[0]
    steps = [radius * (1 - math.cos(i * math.pi / 16)) for i in range(9)]
    # Original setback profile expressed relative to caliper, not example dimensions.
    knots = (
        np.array(
            [
                [0, 1],
                [0.0357259114583333, 1],
                [0.13653564453125, 1],
                [0.2928873697916667, 1],
                [0.4952799479166667, 1.1],
                [0.7341512044270834, 1.2],
                [1, 1.3],
            ]
        )
        * caliper
    )
    for m in meshes:
        if not m["name"].startswith(("rim_a-", "rim_b-")) and m["strip"] not in rim_strips:
            continue
        out = []
        for poly in m["data"]:
            left_side = poly[:, 0].mean() < width / 2
            sign = 1 if left_side else -1
            intercept = 0 if left_side else width
            if m["name"].startswith("rim_") or m["strip"] in return_strips:
                a = 0.3 * sign
                b = caliper + 0.3 * intercept
            else:
                us = sign * poly[:, 0] + intercept
                u = (us.min() + us.max()) / 2
                j = 0
                while j < len(knots) - 2 and u > knots[j + 1, 0]:
                    j += 1
                k0, k1 = knots[j], knots[j + 1]
                slope = (k1[1] - k0[1]) / (k1[0] - k0[0])
                a = slope * sign
                b = k0[1] - slope * k0[0] + slope * intercept
            near = np.any(poly[:, 2] > bottom + 1e-12) and np.any(
                poly[:, 1] - a * poly[:, 0] - b < radius - 1e-12
            )
            pieces = []
            if near:
                for j in range(len(steps) + 1):
                    q = poly
                    if j:
                        q = clip(q, np.array([-a, 1, 0, -b - steps[j - 1]]))
                    if j < len(steps):
                        q = clip(q, np.array([a, -1, 0, b + steps[j]]))
                    if len(q) > 2:
                        pieces.append(q)
            else:
                pieces = [poly]
            for piece in pieces:
                for n in range(1, len(piece) - 1):
                    tri = np.array([piece[0], piece[n], piece[n + 1]])
                    for v in tri:
                        if not near or v[2] <= bottom:
                            continue
                        d = v[1] - a * v[0] - b
                        if d >= radius:
                            continue
                        q = radius - max(0, d)
                        root = math.sqrt(max(0, radius**2 - q * q))
                        scale = (top - radius + root - bottom) / (top - bottom)
                        height = v[2] - bottom
                        deriv = q / max(root, 1e-8) / (top - bottom)
                        dzdx = -height * deriv * a
                        dzdy = height * deriv
                        normal = np.array(
                            [v[3] - dzdx / scale * v[5], v[4] - dzdy / scale * v[5], v[5] / scale]
                        )
                        v[3:6] = normal / np.linalg.norm(normal)
                        v[2] = bottom + height * scale
                    out.extend(triangles(tri))
        m["data"] = np.asarray(out).reshape(-1, 3, 12)
    return meshes
