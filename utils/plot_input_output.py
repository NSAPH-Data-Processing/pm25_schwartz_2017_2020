"""Side-by-side map: standardized input raster | aggregated polygon output.

Usage:
    python utils/plot_input_output.py <temporal_freq> <timestamp>

Examples:
    python utils/plot_input_output.py yearly 2018
    python utils/plot_input_output.py daily  20180101

Writes `<variable>__<polygon>__<timestamp>.png` to the current working directory.
Both panels share one colormap + value range so the aggregation can be visually
checked against the input. Cropped to a CONUS bounding box so Puerto Rico /
Hawaii / Alaska polygons don't distort the view.
"""

import glob
import sys
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd
import rioxarray  # noqa: F401  — registers the .rio xarray accessor
import xarray as xr
from matplotlib.colors import Normalize

CONUS_BBOX = (-125.0, 24.0, -66.5, 50.0)  # xmin, ymin, xmax, ymax (EPSG:4326)


def _glob_one(pattern: str) -> Path:
    matches = sorted(glob.glob(pattern))
    if not matches:
        raise FileNotFoundError(f"No file matches: {pattern}")
    if len(matches) > 1:
        raise RuntimeError(f"{pattern!r} matched {len(matches)} files: {matches}")
    return Path(matches[0])


def _filter_output_to_timestamp(df: pd.DataFrame, freq: str, timestamp: str) -> pd.DataFrame:
    if freq == "yearly":
        return df  # already a single year per file
    if freq == "monthly":
        return df[
            (df["year"] == int(timestamp[:4])) & (df["month"] == int(timestamp[4:6]))
        ]
    if freq == "daily":
        target = pd.Timestamp(f"{timestamp[:4]}-{timestamp[4:6]}-{timestamp[6:8]}")
        return df[df["date"] == target]
    raise ValueError(f"unknown freq {freq!r}")


def main() -> None:
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)

    freq, timestamp = sys.argv[1], sys.argv[2]
    if freq not in {"yearly", "monthly", "daily"}:
        sys.exit(f"freq must be yearly|monthly|daily, got {freq!r}")

    year = timestamp if freq == "yearly" else timestamp[:4]

    raster_path = _glob_one(f"data/input/grids/{freq}/*__{timestamp}.tif")
    variable = raster_path.stem.split("__")[0]
    polygons_path = _glob_one(f"data/input/polygons/*__{year}.gpkg")
    polygon_name = polygons_path.stem.split("__")[0]
    output_parquet = _glob_one(f"data/output/{freq}/*__{year}.parquet")

    raster: xr.DataArray = rioxarray.open_rasterio(raster_path, masked=True).squeeze()
    raster_conus = raster.rio.clip_box(*CONUS_BBOX)

    polys = gpd.read_file(polygons_path)
    polys_conus = polys.cx[CONUS_BBOX[0]:CONUS_BBOX[2], CONUS_BBOX[1]:CONUS_BBOX[3]]

    df = _filter_output_to_timestamp(pd.read_parquet(output_parquet), freq, timestamp)
    if df.empty:
        sys.exit(f"No rows in {output_parquet.name} matching timestamp {timestamp}")

    polys_with_values = polys_conus.merge(
        df[[polygon_name, variable]], on=polygon_name, how="left"
    )

    # Shared color scale clipped to the 1–99% percentile to ignore outliers.
    raster_finite = pd.Series(raster_conus.values.ravel()).dropna()
    poly_finite = polys_with_values[variable].dropna()
    all_vals = pd.concat([raster_finite, poly_finite], ignore_index=True)
    vmin, vmax = float(all_vals.quantile(0.01)), float(all_vals.quantile(0.99))
    norm = Normalize(vmin=vmin, vmax=vmax)
    cmap = "viridis"

    fig, (ax_l, ax_r) = plt.subplots(1, 2, figsize=(16, 6), sharex=True, sharey=True)

    raster_conus.plot.imshow(ax=ax_l, cmap=cmap, norm=norm, add_colorbar=False)
    ax_l.set_title(f"Input grid · {variable} · {timestamp}")
    ax_l.set_xlabel("")
    ax_l.set_ylabel("")
    ax_l.set_aspect("equal")

    polys_with_values.plot(
        column=variable,
        ax=ax_r,
        cmap=cmap,
        norm=norm,
        edgecolor="none",
        missing_kwds={"color": "lightgrey"},
    )
    ax_r.set_title(f"Aggregated · {polygon_name} · {timestamp}")
    ax_r.set_xlabel("")
    ax_r.set_ylabel("")
    ax_r.set_aspect("equal")

    sm = plt.cm.ScalarMappable(cmap=cmap, norm=norm)
    sm.set_array([])
    fig.colorbar(sm, ax=[ax_l, ax_r], orientation="vertical", fraction=0.025, label=variable)

    output_png = Path.cwd() / f"{variable}__{polygon_name}__{timestamp}.png"
    fig.savefig(output_png, dpi=150, bbox_inches="tight")
    print(f"Wrote {output_png}")


if __name__ == "__main__":
    main()
