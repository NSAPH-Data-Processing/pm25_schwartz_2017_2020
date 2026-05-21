"""Build the polygon→cell multiplicity mapping for a (polygon, year, grid) triple.

Per DD-11: for each polygon, bbox-crop the canonical grid window, K-upsample,
all-touched rasterize the polygon at K-upsampled resolution, sum the resulting
K×K binary mask back to native cell granularity → integer multiplicities n_i ∈ [0, K²].
Emit sparse `(polygon_id, row, col, n_i)` triples — only cells with n_i > 0.

The output is reused across every aggregation that consumes the same
(grid, polygons, K) triple — see DD-11 cache key.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import geopandas as gpd
import hydra
import numpy as np
import pandas as pd
import rasterio.features
from affine import Affine
from omegaconf import DictConfig

LOGGER = logging.getLogger(__name__)


def _polygon_window(geom, transform: Affine, height: int, width: int) -> tuple[int, int, int, int]:
    """Native-grid (r0, c0, r1, c1) window enclosing the polygon's geometric bbox.

    `r1` and `c1` are exclusive upper bounds (Python slice convention). Clipped
    to the canonical grid extent so polygons extending past the edge are cleanly
    cut.
    """
    minx, miny, maxx, maxy = geom.bounds
    inv = ~transform
    col_min, row_max = inv * (minx, miny)
    col_max, row_min = inv * (maxx, maxy)
    r0 = max(0, int(np.floor(row_min)))
    r1 = min(height, int(np.ceil(row_max)) + 1)
    c0 = max(0, int(np.floor(col_min)))
    c1 = min(width, int(np.ceil(col_max)) + 1)
    return r0, c0, r1, c1


def _multiplicities_for_polygon(
    geom,
    grid_transform: Affine,
    height: int,
    width: int,
    K: int,
) -> tuple[int, int, np.ndarray]:
    """Compute the multiplicity array for one polygon over its bbox window.

    Returns (r0, c0, n_i_array). `n_i_array` is shape `(r1-r0, c1-c0)` with
    integer counts in `[0, K**2]` for each native cell in the window.
    """
    r0, c0, r1, c1 = _polygon_window(geom, grid_transform, height, width)
    if r0 >= r1 or c0 >= c1:
        # Polygon falls entirely outside the canonical grid extent.
        return r0, c0, np.zeros((0, 0), dtype=np.int32)

    # K-upsampled affine for the window: translate origin to (c0, r0) in native,
    # then scale by 1/K so each native cell becomes K×K sub-cells.
    window_transform = grid_transform * Affine.translation(c0, r0) * Affine.scale(1.0 / K, 1.0 / K)
    sub_h = (r1 - r0) * K
    sub_w = (c1 - c0) * K

    mask = rasterio.features.rasterize(
        [(geom, 1)],
        out_shape=(sub_h, sub_w),
        transform=window_transform,
        fill=0,
        all_touched=True,
        dtype=np.uint8,
    )
    # Sum each K×K block → n_i per native cell.
    n_i = (
        mask.reshape(r1 - r0, K, c1 - c0, K).sum(axis=(1, 3)).astype(np.int32)
    )
    return r0, c0, n_i


def build(cfg: DictConfig, polygon: str, year: int, temporal_freq: str) -> None:
    base_path = Path(cfg.datapaths.base_path)

    # --- canonical grid (declared in cfg.grids.input.grid) ---
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
    width = int(round((xmax - xmin) / cell_size))
    height = int(round((ymax - ymin) / cell_size))
    transform = Affine(cell_size, 0.0, xmin, 0.0, -cell_size, ymax)

    # --- K (explicit; auto-derivation deferred per DD-11) ---
    K = int(cfg.upsample.k)
    if K < 1:
        raise ValueError(f"cfg.upsample.k must be >= 1; got {K}")

    # --- fingerprints (for cache provenance; logged + emitted in sidecar) ---
    grid_json_path = (
        base_path / "input" / "grids" / temporal_freq / "grid.json"
    )
    grid_meta = json.loads(grid_json_path.read_text())
    grid_fp = grid_meta["fingerprint"]

    polygons_json_path = (
        base_path / "input" / "polygons" / f"{polygon}__{year}.json"
    )
    polygons_meta = json.loads(polygons_json_path.read_text())
    polygons_fp = polygons_meta["fingerprint"]

    # --- output paths ---
    # Per [[feedback-per-cadence-intermediates]]: the mapping depends on the
    # canonical grid, which is per-cadence, so the cache lives under
    # intermediate/<temporal_freq>/ — never a cadence-agnostic path.
    cache_dir = base_path / "intermediate" / temporal_freq
    out_parquet = cache_dir / f"mapping__{polygon}__{year}__k{K}.parquet"
    out_json = cache_dir / f"mapping__{polygon}__{year}__k{K}.json"

    if out_parquet.exists() and out_json.exists():
        LOGGER.info(
            "mapping cache exists at %s — skipping (delete the file to force rebuild)",
            out_parquet,
        )
        return

    LOGGER.info(
        "build_polygon_cell_mapping polygon=%s year=%d temporal_freq=%s K=%d "
        "grid_fp=%s polygons_fp=%s",
        polygon,
        year,
        temporal_freq,
        K,
        grid_fp,
        polygons_fp,
    )
    LOGGER.info(
        "canonical grid: EPSG:%d shape=(%d,%d) cell_size=%g bounds=(%g,%g,%g,%g)",
        target_epsg,
        height,
        width,
        cell_size,
        xmin,
        ymin,
        xmax,
        ymax,
    )

    # --- read polygons; reproject to grid CRS if needed ---
    polygons_gpkg = base_path / "input" / "polygons" / f"{polygon}__{year}.gpkg"
    LOGGER.info("reading polygons: %s", polygons_gpkg)
    gdf = gpd.read_file(polygons_gpkg)
    if gdf.crs.to_epsg() != target_epsg:
        LOGGER.info(
            "reprojecting polygons EPSG:%s → EPSG:%d",
            gdf.crs.to_epsg(),
            target_epsg,
        )
        gdf = gdf.to_crs(epsg=target_epsg)
    n_polygons = len(gdf)
    LOGGER.info("polygons: %d", n_polygons)

    # --- per-polygon rasterize + multiplicity sum ---
    polygon_ids: list = []
    rows: list[int] = []
    cols: list[int] = []
    n_is: list[int] = []

    pid_col = gdf.columns[0] if polygon in gdf.columns else polygon
    # Be defensive: prefer the explicit polygon-name column.
    if polygon in gdf.columns:
        pid_col = polygon
    elif "id" in gdf.columns:
        pid_col = "id"
    else:
        raise KeyError(
            f"could not find ID column (expected '{polygon}' or 'id') in "
            f"{list(gdf.columns)}"
        )

    for idx, geom_row in gdf.iterrows():
        geom = geom_row.geometry
        if geom is None or geom.is_empty:
            continue
        pid = geom_row[pid_col]
        r0, c0, n_i = _multiplicities_for_polygon(geom, transform, height, width, K)
        if n_i.size == 0:
            continue
        nz = np.nonzero(n_i)
        if len(nz[0]) == 0:
            continue
        local_rs, local_cs = nz
        for lr, lc in zip(local_rs.tolist(), local_cs.tolist()):
            polygon_ids.append(pid)
            rows.append(r0 + lr)
            cols.append(c0 + lc)
            n_is.append(int(n_i[lr, lc]))

        if (idx + 1) % 5000 == 0:
            LOGGER.info("processed %d / %d polygons; mapping rows so far: %d",
                        idx + 1, n_polygons, len(polygon_ids))

    LOGGER.info(
        "total mapping rows: %d (avg %.1f per polygon)",
        len(polygon_ids),
        len(polygon_ids) / max(1, n_polygons),
    )

    df = pd.DataFrame(
        {
            "polygon_id": polygon_ids,
            "row": np.array(rows, dtype=np.int32),
            "col": np.array(cols, dtype=np.int32),
            "n_i": np.array(n_is, dtype=np.int32),
        }
    )

    cache_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_parquet, index=False)
    LOGGER.info("wrote %s (%d rows)", out_parquet, len(df))

    # --- sidecar JSON with provenance + K (DD-16 spirit; richer than the grid/polygon
    # sidecars because the K value is the only non-content axis of the cache key) ---
    sidecar = {
        "K": K,
        "grid_fingerprint": grid_fp,
        "polygons_fingerprint": polygons_fp,
        "polygon_name": polygon,
        "year": year,
        "n_polygons": n_polygons,
        "n_rows": len(df),
    }
    out_json.write_text(json.dumps(sidecar, indent=2))
    LOGGER.info("wrote %s", out_json)


@hydra.main(config_path="../conf", config_name="config", version_base=None)
def main(cfg: DictConfig) -> None:
    polygon = str(cfg.polygon)
    year = int(cfg.year)
    temporal_freq = str(cfg.temporal_freq)
    build(cfg, polygon, year, temporal_freq)


if __name__ == "__main__":
    main()
