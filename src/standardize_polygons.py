"""Standardise the publisher's raw polygon vintages into per-data-year GeoPackages
plus their sidecar JSONs.

Per DD-10: each data year in the pipeline matrix gets its own standardised
`{polygon_name}__{year}.gpkg` + paired `{polygon_name}__{year}.json` under
`input/polygons/`. The vintage policy (exact | nearest_below) maps
data_year → raw_vintage; when several data years share the same raw vintage
they get independent copies (Snakefile stays wildcard-uniform).

For the Schwartz pipeline (vintage_policy: exact), each data year 2017-2020
has its own raw shapefile; the script is a 1:1 reproject + rename + write step,
followed by JSON emission with a stable fingerprint.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

import geopandas as gpd
import hydra
import numpy as np
from omegaconf import DictConfig

LOGGER = logging.getLogger(__name__)


def _list_available_vintages(raw_polygons_dir: Path, filename_pattern: str) -> list[int]:
    """Return the sorted list of integer vintages discoverable via the pattern."""
    # `{vintage}` may appear multiple times (e.g., folder name repeated in file
    # name); only the first occurrence is the named group, the rest are plain
    # `\d{4}` to avoid Python's "redefinition of group name" error.
    escaped = re.escape(filename_pattern)
    escaped = escaped.replace(r"\{vintage\}", r"(?P<vintage>\d{4})", 1)
    escaped = escaped.replace(r"\{vintage\}", r"\d{4}")
    regex = re.compile("^" + escaped + "$")
    vintages: set[int] = set()
    for path in raw_polygons_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(raw_polygons_dir).as_posix()
        m = regex.match(rel)
        if m:
            vintages.add(int(m.group("vintage")))
    return sorted(vintages)


def _resolve_vintage(data_year: int, policy: str, available: list[int]) -> int:
    if policy == "exact":
        if data_year not in available:
            raise FileNotFoundError(
                f"vintage_policy=exact: no raw vintage for data_year={data_year}; "
                f"available={available}"
            )
        return data_year
    elif policy == "nearest_below":
        candidates = [v for v in available if v <= data_year]
        if not candidates:
            raise FileNotFoundError(
                f"vintage_policy=nearest_below: no raw vintage <= {data_year}; "
                f"available={available}"
            )
        return max(candidates)
    else:
        raise ValueError(f"unknown vintage_policy={policy!r}; expected exact|nearest_below")


_FINGERPRINT_DESCRIPTION = (
    "sha256-16 over '|'-joined: "
    "epsg=<int>, n=<feature_count>, id_col=<polygon_name>, "
    "ids=sha256-16(sorted(str(ids)).join('|')), "
    "geoms=sha256-16(sorted(sha1-hex(geometry.wkb)).join('|'))"
)


def _fingerprint(gdf: gpd.GeoDataFrame, polygon_name: str, target_epsg: int) -> str:
    """Stable 16-char hash of (CRS, feature count, sorted IDs, sorted geometry WKB hashes).

    Algorithm documented in `_FINGERPRINT_DESCRIPTION` and emitted alongside the
    fingerprint value in the sidecar JSON (DD-16) so downstream consumers can
    verify or re-implement.
    """
    ids = sorted(str(v) for v in gdf[polygon_name].tolist())
    geom_hashes = sorted(
        hashlib.sha1(g.wkb).hexdigest() for g in gdf.geometry.tolist() if g is not None
    )
    parts = [
        f"epsg={target_epsg}",
        f"n={len(gdf)}",
        f"id_col={polygon_name}",
        f"ids={hashlib.sha256('|'.join(ids).encode()).hexdigest()[:16]}",
        f"geoms={hashlib.sha256('|'.join(geom_hashes).encode()).hexdigest()[:16]}",
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _emit_json(
    gdf: gpd.GeoDataFrame,
    polygon_name: str,
    target_epsg: int,
    out_path: Path,
) -> None:
    """Emit the minimal sidecar JSON (DD-16): fingerprint + algorithm description only.

    Everything else (polygon_name, year, crs, feature_count, bounds, id_column,
    derived_from) is inferable from the gpkg + filename + cfg — we don't repeat it.
    """
    fp = _fingerprint(gdf, polygon_name, target_epsg)
    meta = {
        "fingerprint": fp,
        "fingerprint_description": _FINGERPRINT_DESCRIPTION,
    }
    out_path.write_text(json.dumps(meta, indent=2))
    LOGGER.info("wrote %s (fingerprint=%s)", out_path, fp)


def standardize(cfg: DictConfig, year: int) -> None:
    base_path = Path(cfg.datapaths.base_path)
    polygon_name: str = cfg.polygons.polygon_name
    raw = cfg.polygons.raw
    raw_polygons_dir = base_path / "raw" / "polygons"
    filename_pattern: str = raw.filename_pattern
    id_column: str = raw.id_column
    target_epsg: int = int(cfg.polygons.input.target_crs)
    policy: str = cfg.polygons.vintage_policy

    out_gpkg = base_path / "input" / "polygons" / f"{polygon_name}__{year}.gpkg"
    out_json = base_path / "input" / "polygons" / f"{polygon_name}__{year}.json"

    LOGGER.info(
        "standardize_polygons polygon=%s year=%d target_crs=EPSG:%d policy=%s",
        polygon_name,
        year,
        target_epsg,
        policy,
    )

    available = _list_available_vintages(raw_polygons_dir, filename_pattern)
    LOGGER.info("available vintages: %s", available)
    vintage = _resolve_vintage(year, policy, available)
    LOGGER.info("resolved vintage: data_year=%d → vintage=%d", year, vintage)

    raw_path = raw_polygons_dir / filename_pattern.format(vintage=vintage)
    if not raw_path.exists():
        raise FileNotFoundError(f"raw polygon file not found: {raw_path}")

    LOGGER.info("reading raw: %s", raw_path)
    gdf = gpd.read_file(raw_path)

    # ID column canonicalisation: rename publisher's column → polygon_name.
    if id_column != polygon_name:
        if id_column not in gdf.columns:
            raise KeyError(
                f"raw.id_column={id_column!r} not in raw shapefile columns; "
                f"got {[c for c in gdf.columns if c != 'geometry']}"
            )
        gdf = gdf.rename(columns={id_column: polygon_name})
    elif polygon_name not in gdf.columns:
        raise KeyError(
            f"polygon_name={polygon_name!r} not in raw shapefile columns; "
            f"got {[c for c in gdf.columns if c != 'geometry']}"
        )

    # Drop unused attributes — keep only the canonical ID column + geometry.
    gdf = gdf[[polygon_name, "geometry"]]

    # Infer origin CRS from the raw shapefile. Fail loudly if the file declares no CRS.
    if gdf.crs is None:
        raise ValueError(
            f"{raw_path}: raw polygons declare no CRS (missing .prj?). "
            "SpaceAgg expects every publisher artefact to declare its CRS."
        )
    origin_epsg = gdf.crs.to_epsg()
    LOGGER.info("inferred origin_crs=EPSG:%s from raw file", origin_epsg)

    if origin_epsg != target_epsg:
        LOGGER.info("reprojecting EPSG:%s → EPSG:%d", origin_epsg, target_epsg)
        gdf = gdf.to_crs(epsg=target_epsg)

    out_gpkg.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(out_gpkg, driver="GPKG", layer=polygon_name)
    LOGGER.info(
        "wrote %s (%d features, target_crs=EPSG:%d)",
        out_gpkg,
        len(gdf),
        target_epsg,
    )

    _emit_json(gdf, polygon_name, target_epsg, out_json)


@hydra.main(config_path="../conf", config_name="config", version_base=None)
def main(cfg: DictConfig) -> None:
    year = int(cfg.year)
    standardize(cfg, year)


if __name__ == "__main__":
    main()
