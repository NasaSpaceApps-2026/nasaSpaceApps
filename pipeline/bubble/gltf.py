from __future__ import annotations

from io import BytesIO
from pathlib import Path
import json
import struct

import numpy as np
from PIL import Image

from bubble.geom import dem_normals, grid_xy


def _png_bytes(texture: np.ndarray) -> bytes:
    buf = BytesIO()
    Image.fromarray(np.asarray(texture, dtype=np.uint8), mode="RGB").save(buf, format="PNG")
    return buf.getvalue()


def write_glb(path: Path, dem, texture, grid) -> None:
    height, width = dem.shape
    xs, ys = grid_xy(grid, height, width)
    pos = np.stack(
        [xs - grid.center_x, np.asarray(dem, dtype=np.float64), -(ys - grid.center_y)],
        axis=-1,
    ).reshape(-1, 3).astype(np.float32)
    nx, ny, nz = dem_normals(dem, grid.res_m)
    nrm = np.stack([nx, nz, -ny], axis=-1).reshape(-1, 3).astype(np.float32)
    nlen = np.linalg.norm(nrm, axis=1, keepdims=True)
    nrm = (nrm / np.maximum(nlen, 1e-8)).astype(np.float32)
    uu = np.broadcast_to(np.linspace(0, 1, width, dtype=np.float32), (height, width)).copy()
    vv = np.broadcast_to(np.linspace(1, 0, height, dtype=np.float32)[:, None], (height, width)).copy()
    uv = np.stack([uu, vv], axis=-1).reshape(-1, 2).astype(np.float32)
    idx = []
    for r in range(height - 1):
        for c in range(width - 1):
            i00 = r * width + c
            i10 = (r + 1) * width + c
            i01 = r * width + c + 1
            i11 = (r + 1) * width + c + 1
            idx.extend([i00, i10, i01, i01, i10, i11])
    indices = np.asarray(idx, dtype=np.uint32)
    png = _png_bytes(texture)
    blobs = [pos.tobytes(), nrm.tobytes(), uv.tobytes(), indices.tobytes(), png]
    parts = []
    offsets = []
    lengths = []
    cursor = 0
    for blob in blobs:
        offsets.append(cursor)
        lengths.append(len(blob))
        parts.append(blob)
        cursor += len(blob)
        pad = (4 - (len(blob) % 4)) % 4
        parts.append(b"\x00" * pad)
        cursor += pad
    bin_blob = b"".join(parts)
    accessors = [
        {
            "bufferView": 0,
            "componentType": 5126,
            "count": int(pos.shape[0]),
            "type": "VEC3",
            "min": pos.min(axis=0).astype(float).tolist(),
            "max": pos.max(axis=0).astype(float).tolist(),
        },
        {"bufferView": 1, "componentType": 5126, "count": int(pos.shape[0]), "type": "VEC3"},
        {"bufferView": 2, "componentType": 5126, "count": int(pos.shape[0]), "type": "VEC2"},
        {"bufferView": 3, "componentType": 5125, "count": int(indices.size), "type": "SCALAR"},
    ]
    targets = [34962, 34962, 34962, 34963, None]
    views = []
    for i, _blob in enumerate(blobs):
        view = {"buffer": 0, "byteOffset": offsets[i], "byteLength": lengths[i]}
        if targets[i] is not None:
            view["target"] = targets[i]
        views.append(view)
    gltf = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": len(bin_blob)}],
        "bufferViews": views,
        "accessors": accessors,
        "images": [{"bufferView": 4, "mimeType": "image/png"}],
        "samplers": [{"magFilter": 9729, "minFilter": 9729, "wrapS": 33071, "wrapT": 33071}],
        "textures": [{"sampler": 0, "source": 0}],
        "materials": [
            {
                "doubleSided": True,
                "pbrMetallicRoughness": {
                    "baseColorTexture": {"index": 0},
                    "metallicFactor": 0.0,
                    "roughnessFactor": 1.0,
                },
            }
        ],
        "meshes": [
            {
                "primitives": [
                    {
                        "attributes": {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2},
                        "indices": 3,
                        "material": 0,
                    }
                ]
            }
        ],
        "nodes": [{"mesh": 0}],
        "scenes": [{"nodes": [0]}],
        "scene": 0,
    }
    js = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    js += b" " * ((4 - (len(js) % 4)) % 4)
    total = 12 + 8 + len(js) + 8 + len(bin_blob)
    out = bytearray()
    out += struct.pack("<III", 0x46546C67, 2, total)
    out += struct.pack("<II", len(js), 0x4E4F534A)
    out += js
    out += struct.pack("<II", len(bin_blob), 0x004E4942)
    out += bin_blob
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(out)


def _chunks(path: Path):
    data = path.read_bytes()
    if data[:4] != b"glTF":
        raise ValueError("not a glb")
    offset = 12
    chunks = []
    while offset < len(data):
        length, ctype = struct.unpack_from("<II", data, offset)
        chunks.append((ctype, data[offset + 8 : offset + 8 + length]))
        offset += 8 + length
    meta = json.loads(chunks[0][1].decode("utf-8").rstrip())
    return meta, chunks[1][1]


def glb_positions(path: Path) -> np.ndarray:
    meta, blob = _chunks(path)
    acc = meta["accessors"][meta["meshes"][0]["primitives"][0]["attributes"]["POSITION"]]
    view = meta["bufferViews"][acc["bufferView"]]
    count = acc["count"] * 3
    arr = np.frombuffer(blob, dtype="<f4", count=count, offset=view["byteOffset"])
    return arr.reshape(-1, 3).copy()


def glb_png(path: Path) -> bytes:
    meta, blob = _chunks(path)
    view = meta["bufferViews"][meta["images"][0]["bufferView"]]
    start = view["byteOffset"]
    return bytes(blob[start : start + view["byteLength"]])


def position_delta(path_a: Path, path_b: Path) -> float:
    a = glb_positions(path_a).astype(np.float64)
    b = glb_positions(path_b).astype(np.float64)
    if a.shape != b.shape:
        return float("inf")
    return float(np.max(np.abs(a - b)))
