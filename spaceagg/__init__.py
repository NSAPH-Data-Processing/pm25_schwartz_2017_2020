"""SpaceAgg library: raster-to-polygon aggregation via all-touched rasterization.

Implements the design decisions from 08_spatial_aggregations.md:
  - CRS alignment (polygons → raster).
  - Upsampled all-touched rasterization producing integer multiplicities n_i.
  - K selection from the polygon-area distribution (Step 10b).
  - Polygon→cell mapping cache keyed by (grid_fp × polygons_fp × k).
  - Multiplicity-weighted aggregation with no-data handling.

Submodules are loaded on demand to keep heavy dependencies (rasterio, geopandas,
pyarrow) out of the import path when callers only need lightweight pieces.

Import directly from submodules:

    from spaceagg.aggregate import weighted
    from spaceagg.grid import grid_fingerprint, read_grid
    from spaceagg.upsample import derive_k
    from spaceagg.crs import align_polygons_to_raster
    from spaceagg.rasterize import upsampled_all_touched
    from spaceagg.cache import mapping_paths, save_mapping, load_mapping
"""
