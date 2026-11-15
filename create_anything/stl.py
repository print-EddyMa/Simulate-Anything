"""Import an STL file as a ``mesh`` part (binary or ASCII)."""
from __future__ import annotations

import struct

import numpy as np

MAX_FACES = 40_000


def parse(data: bytes) -> np.ndarray:
    """Triangles as an (n, 3, 3) array in the file's units."""
    if len(data) >= 84:
        n = struct.unpack_from("<I", data, 80)[0]
        if 84 + 50 * n == len(data):
            rec = np.frombuffer(data, dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)),
                                                      ("a", "<u2")]), count=n, offset=84)
            return rec["v"].astype(float)
    text = data.decode("utf-8", errors="ignore")
    verts = [list(map(float, line.split()[1:4])) for line in text.splitlines()
             if line.strip().startswith("vertex")]
    if not verts or len(verts) % 3:
        raise ValueError("not a valid STL file")
    return np.array(verts, float).reshape(-1, 3, 3)


def _simplify(tri: np.ndarray, limit: int) -> np.ndarray:
    """Vertex clustering until the triangle count fits the limit."""
    lo, hi = tri.reshape(-1, 3).min(0), tri.reshape(-1, 3).max(0)
    span = float(max(hi - lo)) or 1.0
    cells = 256
    while len(tri) > limit and cells > 4:
        q = np.round((tri - lo) / span * cells)
        snapped = q / cells * span + lo
        a, b, c = snapped[:, 0], snapped[:, 1], snapped[:, 2]
        keep = np.linalg.norm(np.cross(b - a, c - a), axis=1) > 1e-12
        tri = snapped[keep]
        cells //= 2
    return tri


def to_part(data: bytes, name: str = "Imported part", units: str = "mm") -> dict:
    tri = parse(data)
    scale = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4}.get(units, 1.0)
    tri = tri * scale
    a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
    volume = abs(float(np.sum(np.einsum("ij,ij->i", a, np.cross(b, c)))) / 6.0)
    lo, hi = tri.reshape(-1, 3).min(0), tri.reshape(-1, 3).max(0)
    centre = (lo + hi) / 2
    tri = tri - centre
    if len(tri) > MAX_FACES:
        tri = _simplify(tri, MAX_FACES)
    flat = tri.reshape(-1, 3)
    uniq, inverse = np.unique(np.round(flat, 4), axis=0, return_inverse=True)
    size = [float(v) for v in hi - lo]
    if volume <= 0:
        volume = 0.3 * size[0] * size[1] * size[2]
    pid = "".join(ch if ch.isalnum() else "_" for ch in name.lower()).strip("_")[:30] or "imported"
    return {"id": pid, "name": name, "shape": "mesh", "position": [0, 0, size[2] / 2],
            "material": "pla", "group": "Imported",
            "mesh": {"vertices": [round(float(v), 3) for v in uniq.ravel()],
                     "faces": [int(i) for i in inverse.ravel()], "size": size,
                     "volume": volume},
            "purpose": "Imported from an STL file"}
