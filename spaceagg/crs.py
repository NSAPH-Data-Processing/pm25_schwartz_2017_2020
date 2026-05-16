"""CRS alignment between rasters and polygons.

Direction: polygons → raster, never the other way (see 08_spatial_aggregations.md §1).
Vector reprojection is cheap and lossless; raster reprojection requires resampling,
which is lossy and slow.
"""

from __future__ import annotations

import geopandas as gpd
import pyproj


def align_polygons_to_raster(polygons: gpd.GeoDataFrame, raster_crs) -> gpd.GeoDataFrame:
    """Reproject ``polygons`` into the raster's CRS.

    Parameters
    ----------
    polygons : GeoDataFrame
        Must have a non-null ``.crs``.
    raster_crs : str | pyproj.CRS | rasterio.CRS
        Anything ``pyproj.CRS(...)`` can consume.

    Returns
    -------
    GeoDataFrame
        Same frame, reprojected. Returned unchanged when the CRSs already match.
    """
    if polygons.crs is None:
        raise ValueError("polygons have no CRS set")
    target = pyproj.CRS(raster_crs)
    if pyproj.CRS(polygons.crs).equals(target):
        return polygons
    return polygons.to_crs(target)
