"""Aggregate a standardised surface to polygons via the precomputed mapping.

For each polygon, compute the multiplicity-weighted mean over its touched
native cells:

    mean(r, p) = Σ (n_i × v_i) for valid i  /  Σ n_i for valid i

where `n_i` comes from the polygon→cell mapping (DD-11) and `v_i` from the
standardised GeoTIFF. Cells where `v_i == nodata` are excluded from both
numerator and denominator. `valid_fraction = Σ n_i (valid) / Σ n_i` and
`effective_samples = Σ n_i` are emitted alongside the value (DD-11 §2c, §4).

Inputs (via Hydra overrides at invocation):
- `+variable=<std_name>` — which variable's GeoTIFF to aggregate.
- `+timestamp=<str>` — the surface's timestamp (YYYYMMDD, YYYYMM, YYYY).
- `+polygon=<polygon_name>` — the polygon set.
- `+year=<int>` — the polygon vintage year (the same as `timestamp` for yearly).
- `+temporal_freq=<str>` — cadence ('daily' / 'monthly' / 'yearly').
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import hydra
import numpy as np
import pandas as pd
import rasterio
from omegaconf import DictConfig

LOGGER = logging.getLogger(__name__)


def _read_polygon_ids(gpkg_path: Path, polygon_name: str) -> list:
    """Read the full polygon ID list directly from the gpkg (sqlite3 fast path).

    Avoids loading geometries; ~50 ms for 30k features vs ~2 s with geopandas.
    Returns IDs in their declared dtype (typically str for ZIP-style codes).
    """
    con = sqlite3.connect(str(gpkg_path))
    try:
        cursor = con.execute(f'SELECT "{polygon_name}" FROM "{polygon_name}"')
        return [row[0] for row in cursor.fetchall()]
    finally:
        con.close()


def aggregate(
    cfg: DictConfig,
    variable: str,
    timestamp: str,
    polygon: str,
    year: int,
    temporal_freq: str,
) -> None:
    base_path = Path(cfg.datapaths.base_path)
    K = int(cfg.upsample.k)

    # --- inputs ---
    tif_path = (
        base_path / "input" / "grids" / temporal_freq / f"{variable}__{timestamp}.tif"
    )
    mapping_path = (
        base_path / "intermediate" / temporal_freq / f"mapping__{polygon}__{year}__k{K}.parquet"
    )
    polygons_gpkg_path = (
        base_path / "input" / "polygons" / f"{polygon}__{year}.gpkg"
    )

    # --- output ---
    # Per the compute-vs-layout split: aggregate.py writes a per-(variable,
    # timestamp) intermediate under intermediate/<temporal_freq>/ (the slot
    # already established in conf/datapaths/local.yaml). src/reshape.py later
    # stacks all intermediates for a (year, all-variables, all-timestamps-in-year)
    # into the LEGO-compliant output parquet under data/output/.
    out_path = (
        base_path
        / "intermediate"
        / temporal_freq
        / f"{variable}__{polygon}__{timestamp}.parquet"
    )

    LOGGER.info(
        "aggregate variable=%s timestamp=%s polygon=%s year=%d temporal_freq=%s K=%d",
        variable,
        timestamp,
        polygon,
        year,
        temporal_freq,
        K,
    )

    # --- read mapping ---
    LOGGER.info("reading mapping: %s", mapping_path)
    mapping = pd.read_parquet(mapping_path)
    LOGGER.info("mapping rows: %d", len(mapping))

    # --- read GeoTIFF values + nodata sentinel ---
    LOGGER.info("reading GeoTIFF: %s", tif_path)
    with rasterio.open(tif_path) as src:
        values = src.read(1)
        nodata = src.nodata
        if nodata is None:
            LOGGER.warning("GeoTIFF declares no nodata sentinel; assuming NaN")
            nodata = np.nan
    LOGGER.info(
        "GeoTIFF: shape=%s dtype=%s nodata=%s",
        values.shape,
        values.dtype,
        nodata,
    )

    # --- gather values at (row, col) from mapping ---
    v_i = values[mapping.row.to_numpy(), mapping.col.to_numpy()]
    n_i = mapping.n_i.to_numpy(dtype=np.float64)
    pids = mapping.polygon_id.to_numpy()

    # --- mask invalid cells (nodata) ---
    if isinstance(nodata, float) and np.isnan(nodata):
        valid_mask = ~np.isnan(v_i)
    else:
        valid_mask = v_i != nodata
    n_i_valid = np.where(valid_mask, n_i, 0.0)
    v_i_valid = np.where(valid_mask, v_i.astype(np.float64), 0.0)

    # --- per-polygon weighted-mean aggregation ---
    df = pd.DataFrame(
        {
            "polygon_id": pids,
            "n_total": n_i,
            "n_valid": n_i_valid,
            "weighted_sum": n_i_valid * v_i_valid,
        }
    )
    grouped = df.groupby("polygon_id", sort=False).agg(
        n_total=("n_total", "sum"),
        n_valid=("n_valid", "sum"),
        weighted_sum=("weighted_sum", "sum"),
    )

    grouped["value"] = grouped["weighted_sum"] / grouped["n_valid"].where(
        grouped["n_valid"] > 0, np.nan
    )
    grouped["valid_fraction"] = grouped["n_valid"] / grouped["n_total"].where(
        grouped["n_total"] > 0, np.nan
    )
    grouped["effective_samples"] = grouped["n_total"]

    result = grouped.reset_index()[
        ["polygon_id", "value", "valid_fraction", "effective_samples"]
    ]

    # --- include polygons that fell entirely outside the canonical grid ---
    # The mapping has 0 rows for those polygons, so the groupby misses them.
    # Add them back with effective_samples=0, valid_fraction=0, value=NaN so the
    # output has one row per polygon in the source gpkg.
    LOGGER.info("reading full polygon ID list from %s", polygons_gpkg_path)
    all_ids = _read_polygon_ids(polygons_gpkg_path, polygon)
    LOGGER.info("polygon gpkg has %d features; mapping covered %d", len(all_ids), len(result))

    full = pd.DataFrame({"polygon_id": all_ids}).merge(result, on="polygon_id", how="left")
    full["valid_fraction"] = full["valid_fraction"].fillna(0.0)
    full["effective_samples"] = full["effective_samples"].fillna(0).astype(np.int64)
    # value stays NaN for polygons outside the grid AND for polygons with all-nodata cells.
    result = full

    # --- diagnostics ---
    out_of_grid = int((result["effective_samples"] == 0).sum())
    all_nodata = int(
        ((result["effective_samples"] > 0) & result["value"].isna()).sum()
    )
    has_value = int(result["value"].notna().sum())
    valid_vals = result["value"].to_numpy()
    LOGGER.info(
        "aggregated %d polygons total: %d with value, %d all-nodata-in-grid, %d outside-grid",
        len(result),
        has_value,
        all_nodata,
        out_of_grid,
    )
    if has_value > 0:
        LOGGER.info(
            "value range over polygons with value: min=%.4f max=%.4f mean=%.4f",
            float(np.nanmin(valid_vals)),
            float(np.nanmax(valid_vals)),
            float(np.nanmean(valid_vals)),
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_parquet(out_path, index=False)
    LOGGER.info("wrote %s (%d rows)", out_path, len(result))


@hydra.main(config_path="../conf", config_name="config", version_base=None)
def main(cfg: DictConfig) -> None:
    variable = str(cfg.variable)
    timestamp = str(cfg.timestamp)
    polygon = str(cfg.polygon)
    year = int(cfg.year)
    temporal_freq = str(cfg.temporal_freq)
    aggregate(cfg, variable, timestamp, polygon, year, temporal_freq)


if __name__ == "__main__":
    main()
