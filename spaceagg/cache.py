"""Polygon→cell mapping cache.

Keyed by (grid_fingerprint × shapefile_fingerprint × k). Two files per cache
entry: a parquet carrying the (polygon_id, row, col, n) triples, and a JSON
sidecar carrying the K-derivation metadata (see 08_spatial_aggregations.md
§"Persisting the chosen K").
"""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq


def mapping_paths(
    intermediate_root: str | Path,
    grid_fp: str,
    shape_fp: str,
    k: int,
) -> tuple[Path, Path]:
    """Return the (parquet, json) paths for this cache key."""
    stem = f"{grid_fp}__{shape_fp}__k{k}"
    root = Path(intermediate_root) / ".mappings"
    return root / f"{stem}.parquet", root / f"{stem}.json"


def save_mapping(
    parquet_path: str | Path,
    mapping: list[tuple],
    derivation: dict,
) -> None:
    """Persist the mapping triples (parquet) and derivation metadata (JSON)."""
    parquet_path = Path(parquet_path)
    parquet_path.parent.mkdir(parents=True, exist_ok=True)

    table = pa.Table.from_pylist(
        [
            {"polygon_id": p, "row": r, "col": c, "n": n}
            for (p, r, c, n) in mapping
        ]
    )
    pq.write_table(table, parquet_path)

    json_path = parquet_path.with_suffix(".json")
    with open(json_path, "w") as f:
        json.dump(derivation, f, indent=2, default=str)


def load_mapping(parquet_path: str | Path) -> tuple[list[tuple], dict | None]:
    """Load mapping triples and derivation metadata.

    Returns ``(mapping, derivation)``. ``derivation`` is ``None`` if the
    sidecar JSON is missing (older cache files).
    """
    parquet_path = Path(parquet_path)
    table = pq.read_table(parquet_path)
    mapping = [
        (row["polygon_id"], row["row"], row["col"], row["n"])
        for row in table.to_pylist()
    ]

    json_path = parquet_path.with_suffix(".json")
    derivation: dict | None = None
    if json_path.exists():
        with open(json_path) as f:
            derivation = json.load(f)
    return mapping, derivation
