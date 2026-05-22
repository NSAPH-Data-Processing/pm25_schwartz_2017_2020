# pm25_schwartz_2017_2020

SpaceAgg pipeline: spatial aggregations of the Schwartz et al. 2017–2020 PM2.5 product onto polygon sets.

The pipeline takes the publisher's raw centroid grid + per-day / per-year value files, standardises them into GeoTIFFs on a regular target grid, and aggregates them to ZCTA-level polygons via all-touched K-upsampled rasterization (SpaceAgg DD-11).

## Quickstart

```bash
# 1. Materialise the data tree (symlinks vs real dirs per `datapaths` choice)
python utils/create_dir_paths.py

# 2. place the raw grids and polygons in the corrensponding folders data/raw/grids and data/raw/polygons.

# 2. Run the full workflow (Standardize grids + Standardize polygons + Build mapping + Aggregate)
snakemake --cores 4
```

`snakemake.yaml` declares the target enumeration per cadence (`yearly.years`, `daily.start/end`). Edit it to change what gets generated.

## Known upstream quirks

The daily zip archives have **inconsistent internal layouts across years**:

- **2017–2018** nest `.dat` files under `daily-dat/PM25-YYYY-MM/...` inside the zip.
- **2019–2020** drop the `daily-dat/` prefix and use `PM25-YYYY-MM/...`.

The `unzip_daily` rule in the Snakefile handles both — it matches by basename (`*PM25-YYYY-MM-DD.dat`) and uses `unzip -j` to junk the in-zip path, so the file always lands at the canonical Snakemake destination. If you swap in a fresh upstream archive and unzip fails with `caution: filename not matched`, check whether the upstream changed the internal layout again and adjust the glob in `Snakefile`'s `unzip_daily` rule accordingly.

## What this pipeline produces

After a successful run, `data/input/` contains the standardised inputs, `data/intermediate/` the polygon→cell mapping cache, and `data/output/` the aggregated parquet outputs.

### `data/input/grids/<temporal_freq>/`

The standardised raster surfaces, one GeoTIFF per (variable, timestamp), aligned to the canonical target grid declared in `conf/grids/<temporal_freq>.yaml`:

```
data/input/grids/yearly/
├── grid.json                      # canonical grid sidecar — fingerprint + algorithm description (DD-16)
├── grid_resample_lookup.parquet   # precomputed nearest-source mapping (Case C)
├── pm25__2017.tif                 # <variable>__<timestamp>.tif (DD-8)
├── pm25__2018.tif
├── pm25__2019.tif
└── pm25__2020.tif
```

Each `.tif` is a regular raster in `target_crs` (4326 for Schwartz; see SpaceAgg DD-9). The `grid_resample_lookup.parquet` is a per-target-pixel "which source centroid contributes" lookup, computed once per cadence by `build_grid_resample_lookup.py` and reused by every per-surface `standardize_grid` invocation. The `grid.json` sidecar carries the canonical's fingerprint — same minimal two-field shape as the polygon sidecars (see the fingerprint section below).

### `data/input/polygons/`

The standardised polygon sets, one GeoPackage + sidecar JSON per data year:

```
data/input/polygons/
├── zcta__2017.gpkg    # GeoPackage, EPSG:4326, single column `zcta` + geometry
├── zcta__2017.json    # minimal sidecar — fingerprint + algorithm description
├── zcta__2018.gpkg
├── zcta__2018.json
├── …
```

Per SpaceAgg DD-16, the sidecar JSON is minimal:

```json
{
  "fingerprint": "68c0ec18c85a08ec",
  "fingerprint_description": "sha256-16 over '|'-joined: epsg=<int>, n=<feature_count>, id_col=<polygon_name>, ids=sha256-16(sorted(str(ids)).join('|')), geoms=sha256-16(sorted(sha1-hex(geometry.wkb)).join('|'))"
}
```

Everything else (CRS, feature count, bounds, ID column name, polygon name, year, derived_from) is *inferable* from the gpkg's metadata tables (sub-50 ms with `pyogrio.read_info`), the filename, or `conf/polygons/<polygon>.yaml`.

### What the fingerprint is and why it's there

The fingerprint is a **content-derived 16-character hash** that uniquely identifies a standardised artefact by its content (not by its filename). Two files with the same fingerprint hold the same logical data — even if their bytes differ (different compression, different feature ordering), and even if they're in different pipelines.

SpaceAgg uses fingerprints as the cache key for the **polygon→cell mapping** (the expensive precomputation that maps every polygon to its overlapping grid cells with multiplicities; SpaceAgg DD-11). The cache key is `(grid_fingerprint × polygons_fingerprint × K)`:

- Compute the mapping once for a given `(grid, polygons, K)` triple.
- Reuse it across all surfaces in the cadence (365 daily aggregations all share one mapping).
- Reuse it across pipelines that happen to share a grid + polygon set + K (no cross-pipeline coordination required).

**Why content-derived, not filename-derived.** A filename like `zcta__2017.gpkg` is a *label* that can lie — two files with that name might differ in CRS, feature count, or geometry. A content-derived fingerprint:

- **Across re-downloads**, a new publisher release with added/removed ZCTAs changes the fingerprint, invalidating the cache automatically.
- **Across pipelines**, two pipelines that standardise the same publisher release produce the same fingerprint and share the cache.
- **Across feature reordering**, sorting IDs and geometry hashes before combining makes the fingerprint order-independent.

mtime-based caches (Snakemake's default) can't do any of this; the fingerprint is the safety net.

**Why `fingerprint_description` lives next to it.** The description documents the exact algorithm so a downstream consumer can verify by recomputation or detect cross-version drift (e.g., if a future SpaceAgg version switches from SHA-1 to SHA-256 for geometry hashes, the description string differs and the consumer knows the two fingerprints aren't directly comparable).

**What it isn't.**

- *Not a version number.* Version numbers are author-chosen labels; fingerprints are computed.
- *Not a checksum of file bytes.* GPKG / GeoTIFF binaries include implementation details (compression, internal ordering) that aren't part of the logical content. The fingerprint hashes the *logical* content (sorted IDs, sorted geometry hashes) so two byte-different files holding the same data still match.
- *Not cryptographically secure.* 64 bits is enough for accidental-collision detection across the pipeline's lifetime of artefacts; SpaceAgg's threat model is "developer mistakes and silent publisher changes", not adversarial collisions.

### `data/intermediate/<temporal_freq>/`

Per-cadence intermediates, two kinds of artefact in the same folder:

```
data/intermediate/yearly/
├── mapping__zcta__2018__k14.parquet   # polygon→cell mapping (DD-11), one per (polygon, year, K)
├── mapping__zcta__2018__k14.json      # mapping sidecar — K, fingerprints, derivation params
├── pm25__zcta__2018.parquet           # per-(variable, timestamp) aggregate intermediate
└── …
```

- **Mapping cache** — `mapping__<polygon>__<year>__k<K>.{parquet,json}`, built once per `(polygon, year, K)` by `build_polygon_cell_mapping.py` and reused for every surface that shares the triple. Cadence-partitioned because the mapping depends on the canonical grid, which is declared per-cadence (see SpaceAgg DD-11).
- **Per-(variable, timestamp) aggregates** — `<variable>__<polygon>__<timestamp>.parquet`, written by `aggregate.py`. Carry the per-polygon value plus quality columns (`valid_fraction`, `effective_samples`) for debugging. These are consumed by `reshape.py` to produce the LEGO-compliant per-year output below.

### `data/output/<temporal_freq>/`

The LEGO-compliant final outputs (SpaceAgg DD-17), one parquet per data year:

```
data/output/yearly/
├── pm25__schwartz__zcta_yearly__2017.parquet
├── pm25__schwartz__zcta_yearly__2018.parquet
├── pm25__schwartz__zcta_yearly__2019.parquet
└── pm25__schwartz__zcta_yearly__2020.parquet
data/output/daily/
└── pm25__schwartz__zcta_daily__2018.parquet   # 33,144 polygons × 90 days
```

Filename pattern: `<output_name>__<year>.parquet`, where `<output_name>` is the templated prefix declared in `conf/config.yaml` (`pm25__schwartz__{polygon_name}_{temporal_freq}` resolved at Snakefile compose time). Per-year partitioning is mandatory regardless of cadence — sub-yearly cadences encode the time-slice as a column inside the file.

Column schema (DD-17b): `[<polygon_name>, <time>, <variable>(s)]`, in that order. Time column varies with cadence — `year` (int32) for yearly, `year + month` for monthly, `date` (timestamp ms) for daily. Variable columns are float32. **Quality columns (`valid_fraction`, `effective_samples`) are intentionally NOT in the output** — they live in the per-(variable, timestamp) intermediates above.

## Layout of the repo

```
pm25_schwartz_2017_2020/
├── README.md                # this file
├── Snakefile                # full pipeline (DD-13 sections, yearly + daily)
├── snakemake.yaml           # per-cadence sections (yearly years, daily interval)
├── requirements.yaml        # conda environment
├── conf/                    # Hydra config tree (datapaths/, grids/, polygons/, _global/)
├── src/                     # standardisation + aggregation scripts
│   ├── build_grid_resample_lookup.py
│   ├── standardize_grid.py
│   ├── standardize_polygons.py
│   ├── build_polygon_cell_mapping.py
│   ├── aggregate.py           # per-(variable, timestamp) intermediates
│   └── reshape.py             # intermediates → LEGO per-year output (DD-17)
├── utils/
│   └── create_dir_paths.py  # one-time data tree materialisation (DD-5 / DD-6)
└── data/                    # the data tree — see data/README.md
```

For the full design rationale behind every choice in this pipeline, see `../spaceagg.md`.
