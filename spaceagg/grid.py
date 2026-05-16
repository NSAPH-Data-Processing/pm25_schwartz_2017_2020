"""Raster grid metadata: read, fingerprint, persist.

The grid fingerprint is the cache key axis for the polygon→cell mapping (DD-9).
It hashes the triple (CRS, affine transform, shape) — the properties that
determine which raster cells exist and where they sit in space.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Mapping


def grid_fingerprint(crs, transform, shape) -> str:
    """Stable 16-character hash of (CRS, affine transform, shape).

    The transform is reduced to its 6-tuple form before hashing so that
    Affine and tuple representations produce the same fingerprint.
    """
    payload = json.dumps(
        {
            "crs": str(crs),
            "transform": list(transform)[:6],
            "shape": list(shape),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def read_grid(raster_path: str | Path) -> dict:
    """Read CRS, transform, shape, nodata, bounds from a GeoTIFF.

    Returns a dict suitable for ``write_grid_json``. The ``fingerprint`` field
    is derived from CRS + transform + shape; ``bounds`` and ``nodata`` are
    informational and not part of the fingerprint.
    """
    import rasterio

    with rasterio.open(raster_path) as src:
        crs = src.crs.to_string() if src.crs else None
        transform = list(src.transform)[:6]
        shape = [src.height, src.width]
        nodata = src.nodata
        bounds = list(src.bounds)
    return {
        "crs": crs,
        "transform": transform,
        "shape": shape,
        "nodata": nodata,
        "bounds": bounds,
        "fingerprint": grid_fingerprint(crs, transform, shape),
    }


def write_grid_json(path: str | Path, grid: Mapping, derived_from: str | None = None) -> None:
    """Persist grid metadata to disk as JSON.

    ``derived_from`` records which raw file seeded the canonical grid — useful
    when debugging an inconsistent variant (DD-9 edge case).
    """
    out = dict(grid)
    if derived_from is not None:
        out["derived_from"] = str(derived_from)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(out, f, indent=2, default=str)


def read_grid_json(path: str | Path) -> dict:
    """Load a previously-written grid.json sidecar."""
    with open(path) as f:
        return json.load(f)
