"""Upsampled all-touched rasterization producing integer multiplicities n_i.

This is the core of SpaceAgg's aggregation method (see 08_spatial_aggregations.md
§2 and §2a). The polygon→cell weight is an integer count of touched sub-cells
in [0, K²], produced by `rasterio.features.rasterize(..., all_touched=True)` on
a K×K upsampled grid — *not* a continuous area fraction from vector overlay.
"""

from __future__ import annotations

from typing import Iterable, Tuple

import numpy as np
import rasterio.features
from affine import Affine


def upsampled_all_touched(
    polygons: Iterable[Tuple[object, object]],
    transform: Affine,
    shape: Tuple[int, int],
    k: int,
) -> list[tuple]:
    """Compute (polygon_id, row, col, n_i) multiplicity triples.

    Parameters
    ----------
    polygons
        Iterable of (polygon_id, geometry) pairs. ``geometry`` must be a
        Shapely geometry in the same CRS as the raster (use
        :func:`spaceagg.crs.align_polygons_to_raster` first).
    transform
        Native-resolution affine transform of the raster.
    shape
        (height, width) of the native-resolution raster.
    k
        Upsample factor. Each native cell is divided into k × k sub-cells.

    Returns
    -------
    list of (polygon_id, row, col, n_i)
        Sparse mapping: one entry per (polygon, native cell) pair with n_i > 0.
        ``n_i`` is an integer in (0, k**2].
    """
    if k < 1:
        raise ValueError(f"k must be ≥ 1, got {k}")
    height, width = shape
    triples: list[tuple] = []

    for polygon_id, geom in polygons:
        # Native-cell bounding box of the polygon
        minx, miny, maxx, maxy = geom.bounds
        col_min, row_max_f = ~transform * (minx, miny)
        col_max, row_min_f = ~transform * (maxx, maxy)
        r0 = max(0, int(np.floor(row_min_f)))
        r1 = min(height, int(np.ceil(row_max_f)) + 1)
        c0 = max(0, int(np.floor(col_min)))
        c1 = min(width, int(np.ceil(col_max)) + 1)
        if r1 <= r0 or c1 <= c0:
            continue

        native_h = r1 - r0
        native_w = c1 - c0
        upsampled_shape = (native_h * k, native_w * k)
        # Origin of the upsampled sub-window expressed as an affine
        sub_transform = transform * Affine.translation(c0, r0) * Affine.scale(1.0 / k)

        mask = rasterio.features.rasterize(
            [(geom, 1)],
            out_shape=upsampled_shape,
            transform=sub_transform,
            fill=0,
            all_touched=True,
            dtype=np.uint8,
        )

        # Sum k × k blocks → integer multiplicity per native cell
        n_native = mask.reshape(native_h, k, native_w, k).sum(axis=(1, 3))
        rows, cols = np.nonzero(n_native)
        for rr, cc in zip(rows, cols):
            triples.append(
                (polygon_id, int(rr + r0), int(cc + c0), int(n_native[rr, cc]))
            )

    return triples
