"""Standardise one raw raster surface into a canonical GeoTIFF.

For Case-C-style centroid sources (publisher ships POINT centroids + per-surface
.dat value tables), this script:

1. Reads the precomputed resample lookup from `input/grids/<temporal_freq>/grid_resample_lookup.parquet`
   (built once by `build_grid_resample_lookup.py`).
2. Reads the per-surface .dat (one float per line, fid-indexed; line N = value at fid N).
3. Scatters values onto the canonical (height, width) array via the lookup.
4. Writes the result as a GeoTIFF via rioxarray with cfg.tif properties.

The lookup encodes the resample (nearest-neighbour with max-distance coverage
mask) — no KDTree or interpolation work per surface; just a parquet read + array
indexing. ~1–3 s per surface vs ~80 s if the tree were rebuilt each time.

Note: the canonical target grid is declared in cfg (`target_crs`, `cell_size`,
`bounds`). The publisher's origin CRS is inferred at lookup-build time by
inspecting the raw file directly — not declared in cfg.
"""
from __future__ import annotations

import logging
from pathlib import Path

import hydra
import numpy as np
import pandas as pd
import rioxarray  # noqa: F401 — registers .rio accessor
import xarray as xr
from affine import Affine
from omegaconf import DictConfig

LOGGER = logging.getLogger(__name__)


def standardize(cfg: DictConfig, variable: str, timestamp: str, dat_path: Path) -> None:
    base_path = Path(cfg.datapaths.base_path)

    # --- inputs ---
    temporal_freq = cfg.grids.temporal_freq
    lookup_path = (
        base_path / "input" / "grids" / temporal_freq / "grid_resample_lookup.parquet"
    )

    # --- canonical / output side ---
    grid_in = cfg.grids.input.grid
    target_epsg = int(grid_in.target_crs)
    cell_size = float(grid_in.cell_size)
    bounds = grid_in.bounds
    xmin, ymin, xmax, ymax = (
        float(bounds.xmin),
        float(bounds.ymin),
        float(bounds.xmax),
        float(bounds.ymax),
    )

    tif = grid_in.tif
    out_dtype = tif.dtype
    out_nodata = float("nan") if tif.nodata == "nan" else float(tif.nodata)
    out_compression = tif.compression
    out_tiled = bool(tif.tiled)
    out_blockx = int(tif.blockxsize)
    out_blocky = int(tif.blockysize)

    if variable not in cfg.grids.variables:
        raise KeyError(
            f"variable={variable!r} not in cfg.grids.variables "
            f"(keys: {list(cfg.grids.variables.keys())})"
        )
    out_path = (
        base_path
        / "input"
        / "grids"
        / temporal_freq
        / f"{variable}__{timestamp}.tif"
    )

    LOGGER.info(
        "standardize_grid variable=%s timestamp=%s target_crs=EPSG:%d "
        "cell_size=%g bounds=(%g,%g,%g,%g)",
        variable,
        timestamp,
        target_epsg,
        cell_size,
        xmin,
        ymin,
        xmax,
        ymax,
    )

    # --- canonical grid shape + transform ---
    width = int(round((xmax - xmin) / cell_size))
    height = int(round((ymax - ymin) / cell_size))
    transform = Affine(cell_size, 0.0, xmin, 0.0, -cell_size, ymax)

    # --- load lookup + values ---
    LOGGER.info("reading lookup: %s", lookup_path)
    lookup = pd.read_parquet(lookup_path)
    LOGGER.info("lookup rows: %d", len(lookup))

    LOGGER.info("reading .dat: %s", dat_path)
    values = np.loadtxt(dat_path, dtype=np.float64)
    LOGGER.info("source values: %d", len(values))

    # --- scatter values onto canonical grid via lookup ---
    # lookup.fid is 1-based (publisher's idx + .dat line number); values is 0-indexed.
    arr = np.full((height, width), out_nodata, dtype=out_dtype)
    arr[lookup.row.to_numpy(), lookup.col.to_numpy()] = values[lookup.fid.to_numpy() - 1]

    nodata_mask = np.isnan(arr) if np.isnan(out_nodata) else (arr == out_nodata)
    LOGGER.info(
        "resampled: min=%.4f max=%.4f, nodata_fraction=%.3f",
        float(np.nanmin(arr)) if not nodata_mask.all() else float("nan"),
        float(np.nanmax(arr)) if not nodata_mask.all() else float("nan"),
        float(nodata_mask.mean()),
    )

    # --- wrap as xarray + write GeoTIFF via rioxarray ---
    target_x = xmin + (np.arange(width) + 0.5) * cell_size
    target_y = ymax - (np.arange(height) + 0.5) * cell_size
    da = xr.DataArray(
        arr,
        dims=("y", "x"),
        coords={"y": target_y, "x": target_x},
        name=variable,
    )
    da.rio.write_crs(f"EPSG:{target_epsg}", inplace=True)
    da.rio.write_transform(transform, inplace=True)
    da.rio.write_nodata(out_nodata, inplace=True)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    da.rio.to_raster(
        out_path,
        dtype=out_dtype,
        compress=out_compression,
        tiled=out_tiled,
        blockxsize=out_blockx,
        blockysize=out_blocky,
    )
    LOGGER.info("wrote %s", out_path)


@hydra.main(config_path="../conf", config_name="config", version_base=None)
def main(cfg: DictConfig) -> None:
    variable = str(cfg.variable)
    timestamp = str(cfg.timestamp)
    dat_path = Path(cfg.dat_path)
    standardize(cfg, variable, timestamp, dat_path)


if __name__ == "__main__":
    main()
