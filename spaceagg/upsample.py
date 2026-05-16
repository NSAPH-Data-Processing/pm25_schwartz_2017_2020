"""Choose the upsample factor K from the polygon-area distribution.

Implements the rule of thumb from 08_spatial_aggregations.md §2b:

    K = clip( ceil( sqrt(N_target * A_cell / A_p_q) ), K_min, K_max )

where ``A_p_q`` is the q-th-quantile polygon area. K is sized so that 100*(1-q)%
of polygons reach at least ``N_target`` touched sub-cells in the upsampled grid.
"""

from __future__ import annotations

import math
from typing import Iterable


def derive_k(
    polygon_areas: Iterable[float],
    cell_area: float,
    target_samples: int = 100,
    quantile: float = 0.10,
    k_min: int = 1,
    k_max: int = 16,
) -> dict:
    """Compute K and the derivation metadata for sidecar storage.

    Parameters
    ----------
    polygon_areas
        Area of every polygon in the polygon set, in the same units as ``cell_area``.
    cell_area
        Area of one native raster cell.
    target_samples
        Minimum desired total multiplicity (Σ n_i) per polygon at the chosen quantile.
    quantile
        Area quantile to satisfy. The bottom ``quantile`` fraction of polygons
        may have fewer than ``target_samples`` and is flagged downstream via
        ``effective_samples`` rather than silently mis-aggregated.
    k_min, k_max
        Clip range for K. ``k_max`` caps the per-polygon rasterization cost.

    Returns
    -------
    dict
        Derivation record suitable for the polygon→cell mapping sidecar JSON.
    """
    areas = sorted(float(a) for a in polygon_areas)
    if not areas:
        raise ValueError("polygon_areas is empty")
    if cell_area <= 0:
        raise ValueError(f"cell_area must be positive, got {cell_area}")
    if not 0.0 <= quantile <= 1.0:
        raise ValueError(f"quantile must be in [0, 1], got {quantile}")
    if k_min < 1 or k_max < k_min:
        raise ValueError(f"need 1 ≤ k_min ≤ k_max, got k_min={k_min}, k_max={k_max}")

    idx = max(0, min(len(areas) - 1, int(quantile * (len(areas) - 1))))
    a_p_q = areas[idx]
    if a_p_q <= 0:
        raise ValueError(f"non-positive polygon area at quantile {quantile}: {a_p_q}")

    k_required_raw = math.ceil(math.sqrt(target_samples * cell_area / a_p_q))
    if k_required_raw < k_min:
        k = k_min
        clipped_to: str | None = "k_min"
    elif k_required_raw > k_max:
        k = k_max
        clipped_to = "k_max"
    else:
        k = k_required_raw
        clipped_to = None

    return {
        "k": int(k),
        "n_target": int(target_samples),
        "quantile": float(quantile),
        "a_cell": float(cell_area),
        "a_p_quantile": float(a_p_q),
        "k_required_raw": int(k_required_raw),
        "clipped_to": clipped_to,
        "k_min": int(k_min),
        "k_max": int(k_max),
        "n_polygons": len(areas),
    }
