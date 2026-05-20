"""Build the resample lookup for `standardize_grid.py`.

For Case-C-style centroid sources (publisher ships POINT centroids; target grid
is a regular raster declared in config), this script precomputes the per-target-
pixel nearest-source mapping with a max-distance coverage mask. The result is a
sparse `(row, col, fid)` parquet that downstream `standardize_grid` invocations
read once and use to gather values from each .dat file without re-running the
KDTree query.

Run once per pipeline (or whenever the publisher's grid or the canonical bounds
change). Output: `{base_path}/intermediate/.cache/grid_resample_lookup.parquet`.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import geopandas as gpd
import hydra
import numpy as np
import pandas as pd
from affine import Affine
from omegaconf import DictConfig
from scipy.spatial import cKDTree

LOGGER = logging.getLogger(__name__)

_GRID_FINGERPRINT_DESCRIPTION = (
    "sha256-16 over '|'-joined: epsg=<int>, "
    "transform=(a,b,c,d,e,f) with %.6f formatting, "
    "shape=(height,width)"
)


def _grid_fingerprint(epsg: int, transform: Affine, shape: tuple[int, int]) -> str:
    """Stable 16-char hash of (CRS, transform, shape).

    Algorithm documented in `_GRID_FINGERPRINT_DESCRIPTION` and emitted alongside
    the fingerprint in `grid.json` (DD-16) so downstream consumers can verify
    or re-implement.
    """
    parts = [
        f"epsg={epsg}",
        f"a={transform.a:.6f}",
        f"b={transform.b:.6f}",
        f"c={transform.c:.6f}",
        f"d={transform.d:.6f}",
        f"e={transform.e:.6f}",
        f"f={transform.f:.6f}",
        f"h={shape[0]}",
        f"w={shape[1]}",
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def build(cfg: DictConfig) -> None:
    base_path = Path(cfg.datapaths.base_path)

    # --- raw side ---
    raw_grid_cfg = cfg.grids.raw.grid
    raw_grid_path = base_path / "raw" / "grids" / raw_grid_cfg.filename

    # --- canonical side ---
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

    resample_method = cfg.grids.resample_method if cfg.grids.resample else "nearest"
    if resample_method != "nearest":
        raise NotImplementedError(
            f"resample_method={resample_method!r} not yet supported; "
            "only 'nearest' is implemented"
        )

    temporal_freq = cfg.grids.temporal_freq
    out_path = (
        base_path / "input" / "grids" / temporal_freq / "grid_resample_lookup.parquet"
    )
    grid_json_path = (
        base_path / "input" / "grids" / temporal_freq / "grid.json"
    )

    LOGGER.info(
        "build_grid_resample_lookup target_crs=EPSG:%d cell_size=%g "
        "bounds=(%g,%g,%g,%g) method=%s",
        target_epsg,
        cell_size,
        xmin,
        ymin,
        xmax,
        ymax,
        resample_method,
    )

    # --- canonical grid shape + transform + fingerprint ---
    width = int(round((xmax - xmin) / cell_size))
    height = int(round((ymax - ymin) / cell_size))
    transform = Affine(cell_size, 0.0, xmin, 0.0, -cell_size, ymax)
    grid_fp = _grid_fingerprint(target_epsg, transform, (height, width))
    LOGGER.info(
        "canonical shape=(%d, %d), transform=%s, fingerprint=%s",
        height,
        width,
        transform,
        grid_fp,
    )

    # --- read source centroids ---
    LOGGER.info("reading raw grid: %s", raw_grid_path)
    gdf = gpd.read_file(raw_grid_path)
    if not (gdf.geom_type == "Point").all():
        raise ValueError(f"{raw_grid_path}: expected POINT centroids")
    # Infer origin CRS from the raw file. Fail loudly if the file declares no CRS.
    if gdf.crs is None:
        raise ValueError(
            f"{raw_grid_path}: raw grid declares no CRS. "
            "SpaceAgg expects the publisher's distribution file to declare a CRS; "
            "if your publisher omits it, set the CRS manually with a one-off "
            "preprocessing step or report it as a per-source workaround."
        )
    origin_epsg = gdf.crs.to_epsg()
    LOGGER.info("inferred origin_crs=EPSG:%s from raw file", origin_epsg)

    if "fid" not in gdf.columns:
        gdf = gdf.reset_index().rename(columns={"index": "fid"})
        gdf["fid"] = gdf["fid"] + 1  # publisher's `idx` is 1-based; align with .dat line number

    if origin_epsg != target_epsg:
        LOGGER.info("reprojecting centroids EPSG:%s → EPSG:%d", origin_epsg, target_epsg)
        gdf = gdf.to_crs(epsg=target_epsg)

    src_x = gdf.geometry.x.to_numpy()
    src_y = gdf.geometry.y.to_numpy()
    src_fid = gdf["fid"].to_numpy(dtype=np.int64)
    n_centroids = len(gdf)
    LOGGER.info("source centroids: %d", n_centroids)

    # --- build KDTree ---
    LOGGER.info("building cKDTree on %d source centroids", n_centroids)
    tree = cKDTree(np.column_stack([src_x, src_y]))

    # --- target pixel centers ---
    target_x = xmin + (np.arange(width) + 0.5) * cell_size
    target_y = ymax - (np.arange(height) + 0.5) * cell_size
    grid_xx, grid_yy = np.meshgrid(target_x, target_y)
    target_xy = np.column_stack([grid_xx.ravel(), grid_yy.ravel()])
    LOGGER.info("querying nearest source for %d target pixels", len(target_xy))
    dist, idx = tree.query(target_xy, k=1, workers=-1)

    # --- max-distance coverage mask ---
    # 1.5 × median nearest-neighbour distance among source centroids.
    sample_n = min(10_000, n_centroids)
    sample_idx = np.random.default_rng(42).choice(n_centroids, sample_n, replace=False)
    sample_dist, _ = tree.query(
        np.column_stack([src_x[sample_idx], src_y[sample_idx]]),
        k=2,
        workers=-1,
    )
    median_nn = float(np.median(sample_dist[:, 1]))
    max_dist = 1.5 * median_nn
    LOGGER.info(
        "coverage mask: median source NN distance=%.6f, max_dist threshold=%.6f",
        median_nn,
        max_dist,
    )

    valid = dist <= max_dist
    n_valid = int(valid.sum())
    LOGGER.info(
        "valid target pixels: %d / %d (%.1f%%)",
        n_valid,
        len(target_xy),
        100 * n_valid / len(target_xy),
    )

    rows = np.repeat(np.arange(height, dtype=np.int32), width)[valid]
    cols = np.tile(np.arange(width, dtype=np.int32), height)[valid]
    fids = src_fid[idx[valid]]

    lookup = pd.DataFrame({"row": rows, "col": cols, "fid": fids})
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lookup.to_parquet(out_path, index=False)
    LOGGER.info("wrote %s (%d rows)", out_path, len(lookup))

    # --- emit grid.json (minimal per DD-16) ---
    grid_json_path.write_text(json.dumps({
        "fingerprint": grid_fp,
        "fingerprint_description": _GRID_FINGERPRINT_DESCRIPTION,
    }, indent=2))
    LOGGER.info("wrote %s (fingerprint=%s)", grid_json_path, grid_fp)


@hydra.main(config_path="../conf", config_name="config", version_base=None)
def main(cfg: DictConfig) -> None:
    build(cfg)


if __name__ == "__main__":
    main()
