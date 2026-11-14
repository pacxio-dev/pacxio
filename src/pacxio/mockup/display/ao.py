"""48-ray cosine AO with a NumPy packet BVH, independent of artwork/UVs."""

from __future__ import annotations

from typing import Any

import numpy as np
from numpy.typing import NDArray


def bake(data: NDArray[Any]) -> NDArray[Any]:
    triangles = data[:, :3].reshape(-1, 3, 3).astype(np.float64)
    low = triangles.min(axis=1)
    high = triangles.max(axis=1)
    centres = (low + high) / 2
    nodes: list[tuple[Any, Any, Any, Any] | None] = []

    def tree(ids: NDArray[Any]) -> int:
        lo, hi = low[ids].min(axis=0), high[ids].max(axis=0)
        idx = len(nodes)
        nodes.append(None)
        if len(ids) <= 8:
            nodes[idx] = (lo, hi, ids, None)
        else:
            axis = np.argmax(np.ptp(centres[ids], axis=0))
            ordered = ids[np.argsort(centres[ids, axis], kind="stable")]
            mid = len(ids) // 2
            nodes[idx] = (lo, hi, tree(ordered[:mid]), tree(ordered[mid:]))
        return idx

    tree(np.arange(len(triangles)))
    normal = data[:, 3:6].astype(float)
    normal /= np.maximum(np.linalg.norm(normal, axis=1, keepdims=True), 1e-30)
    values = np.column_stack((data[:, :3], normal))
    _, indices, inverse = np.unique(
        np.floor(values * 1e6 + 0.5), axis=0, return_index=True, return_inverse=True
    )
    pos = values[indices, :3]
    normal = normal[indices]
    tangent = np.zeros_like(normal)
    tangent[:, 2] = abs(normal[:, 2]) < 0.9
    tangent[:, 0] = abs(normal[:, 2]) >= 0.9
    tangent = np.cross(tangent, normal)
    tangent /= np.linalg.norm(tangent, axis=1, keepdims=True)
    bitangent = np.cross(normal, tangent)
    j = np.arange(48)
    r = np.sqrt((j + 0.5) / 48)
    phi = j * 2.399963229728653
    dirs = (
        tangent[:, None, :] * (r * np.cos(phi))[None, :, None]
        + bitangent[:, None, :] * (r * np.sin(phi))[None, :, None]
        + normal[:, None, :] * np.sqrt(1 - r * r)[None, :, None]
    ).reshape(-1, 3)
    dirs /= np.linalg.norm(dirs, axis=1, keepdims=True)
    origin = np.repeat(pos + normal * 0.00005, 48, axis=0)
    inv = np.divide(1.0, dirs, out=np.full_like(dirs, 1e30), where=abs(dirs) > 1e-30)
    nearest = np.full(len(dirs), 0.005)
    stack = [(0, np.arange(len(dirs)))]
    e1 = triangles[:, 1] - triangles[:, 0]
    e2 = triangles[:, 2] - triangles[:, 0]
    while stack:
        ni, ids = stack.pop()
        node = nodes[ni]
        assert node is not None
        lo, hi, a, b = node
        t1 = (lo - origin[ids]) * inv[ids]
        t2 = (hi - origin[ids]) * inv[ids]
        near = np.minimum(t1, t2).max(axis=1)
        far = np.maximum(t1, t2).min(axis=1)
        ids = ids[(far >= np.maximum(near, 0.00001)) & (near <= nearest[ids])]
        if not len(ids):
            continue
        if b is not None:
            stack.extend([(a, ids), (b, ids)])
            continue
        h = np.cross(dirs[ids, None, :], e2[a])
        det = np.einsum("rti,ti->rt", h, e1[a])
        ok = abs(det) > 1e-15
        invdet = np.divide(1.0, det, out=np.zeros_like(det), where=ok)
        s = origin[ids, None, :] - triangles[a, 0]
        u = np.einsum("rti,rti->rt", s, h) * invdet
        q = np.cross(s, e1[a])
        v = np.einsum("ri,rti->rt", dirs[ids], q) * invdet
        distance = np.einsum("ti,rti->rt", e2[a], q) * invdet
        hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (distance >= 0.00001) & (distance <= 0.005)
        nearest[ids] = np.minimum(nearest[ids], np.where(hit, distance, 0.005).min(axis=1))
    values = (nearest.reshape(-1, 48) / 0.005) ** 0.55
    data[:, 11] = values.mean(axis=1)[inverse]
    return data
