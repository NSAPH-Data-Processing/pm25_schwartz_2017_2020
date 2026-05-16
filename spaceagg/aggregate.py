"""Multiplicity-weighted aggregation with no-data handling.

Implements the formula from 08_spatial_aggregations.md §"The aggregation, in
one sentence":

    mean(r, p) = Σ (n_i × v_i) for valid i / Σ n_i for valid i

where n_i is the *integer* sub-cell multiplicity from all-touched rasterization
on an upsampled grid (see :mod:`spaceagg.rasterize`).

No-data handling per §4: cells matching the raster's declared nodata sentinel
are excluded from both numerator and denominator. The denominator's reduction
is reported as ``valid_fraction`` so downstream consumers can filter
low-coverage polygons.
"""

from __future__ import annotations

import math
from typing import Iterable

import numpy as np


def _is_nan_sentinel(nodata) -> bool:
    return isinstance(nodata, float) and math.isnan(nodata)


def weighted(
    values: Iterable[float] | np.ndarray,
    multiplicities: Iterable[int] | np.ndarray,
    stat: str = "mean",
    nodata=None,
) -> tuple[float, float]:
    """Multiplicity-weighted aggregation over one polygon's cells.

    Parameters
    ----------
    values
        Raster values for the cells the polygon touches.
    multiplicities
        Integer sub-cell counts n_i for those same cells (same ordering). Use
        :func:`spaceagg.rasterize.upsampled_all_touched` to compute them.
    stat
        One of ``"mean"``, ``"sum"``, ``"max"``, ``"min"``.
    nodata
        The raster's declared no-data sentinel. Pass ``None`` to treat NaNs
        as no-data; pass an explicit value (e.g., ``-9999``) for integer rasters.

    Returns
    -------
    (value, valid_fraction)
        ``value`` is the aggregated statistic, or ``NaN`` when no valid cells.
        ``valid_fraction`` is the fraction of total multiplicity that came
        from valid cells, in [0, 1].
    """
    values = np.asarray(values, dtype=float)
    n = np.asarray(multiplicities, dtype=float)
    if values.shape != n.shape:
        raise ValueError(
            f"values and multiplicities must have the same shape, "
            f"got {values.shape} vs {n.shape}"
        )

    total_n = n.sum()
    if total_n == 0:
        return float("nan"), 0.0

    if nodata is None or _is_nan_sentinel(nodata):
        valid_mask = ~np.isnan(values)
    else:
        valid_mask = values != nodata

    valid_n = n[valid_mask]
    valid_v = values[valid_mask]
    valid_total = valid_n.sum()
    valid_fraction = float(valid_total / total_n)

    if valid_total == 0:
        return float("nan"), 0.0

    if stat == "mean":
        agg = float((valid_v * valid_n).sum() / valid_total)
    elif stat == "sum":
        agg = float((valid_v * valid_n).sum())
    elif stat == "max":
        agg = float(valid_v.max())
    elif stat == "min":
        agg = float(valid_v.min())
    else:
        raise ValueError(f"unknown stat: {stat!r}")

    return agg, valid_fraction
