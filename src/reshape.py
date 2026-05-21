"""Reshape per-(variable, timestamp) intermediates into a LEGO-compliant output.

Per DD-17 + [[feedback-separate-compute-from-layout]]:

- `src/aggregate.py` writes per-(variable, timestamp) intermediates under
  `data/intermediate/<temporal_freq>/<variable>__<polygon>__<timestamp>.parquet`
  carrying `[polygon_id, value, valid_fraction, effective_samples]`.
- This script (`reshape.py`) reads all such intermediates for one
  (polygon, year, temporal_freq) — across all variables and all timestamps in
  the year — and writes one consolidated per-year output parquet at:

      data/output/<temporal_freq>/<output_name>__<year>.parquet

  where `<output_name>` is the resolved `cfg.output_name` template (DD-17c).

Schema of the output (DD-17b, revised):

1. `<polygon_name>` — string (e.g., `zcta`). leading zeros preserved.
2. Time column(s), depending on `temporal_freq`:
   - `yearly`  → `year` (int32)
   - `monthly` → `year` (int32) + `month` (int32, 1–12)
   - `daily`   → `date` (pyarrow timestamp[ms])
3. Variable column(s) — float32, one per variable in `cfg.grids.variables`.

The LEGO output is a consumer-facing materialized view: polygon × time × variable(s).
It deliberately does NOT carry `valid_fraction` or `effective_samples` — those are
per-variable quality columns and would be misleading as single columns in a
multi-variable table. They live in the per-(variable, timestamp) intermediates
under `data/intermediate/<temporal_freq>/<variable>__<polygon>__<timestamp>.parquet`
for debugging and quality-aware tooling.

For multi-variable sources (DD-17d), all variables land as columns in the same
file (consolidated; no per-variable split).

Invoked per (polygon, year, temporal_freq) via Hydra overrides:
- `+polygon=<polygon_name>`
- `+year=<int>`
- `+temporal_freq=<yearly|monthly|daily>`
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import hydra
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from omegaconf import DictConfig

LOGGER = logging.getLogger(__name__)

_INTERMEDIATE_RE = re.compile(r"^(?P<variable>[^_]+(?:_[^_]+)*?)__(?P<polygon>[^_]+(?:_[^_]+)*?)__(?P<timestamp>\d+)\.parquet$")


def _list_intermediates(intermediate_dir: Path, variables: list[str], polygon: str, year: int) -> list[tuple[Path, str, str]]:
    """Find intermediate parquets for the (polygon, year, all-variables).

    Returns a list of (path, variable, timestamp) tuples. Timestamps are the
    raw filename token (4/6/8 chars per cadence); the caller maps them into
    the right time-column values.
    """
    found: list[tuple[Path, str, str]] = []
    for path in sorted(intermediate_dir.glob(f"*__{polygon}__*.parquet")):
        m = _INTERMEDIATE_RE.match(path.name)
        if not m:
            continue
        var = m["variable"]
        ts = m["timestamp"]
        if var not in variables:
            continue
        # Filter by year: timestamp always starts with the 4-digit year.
        if not ts.startswith(str(year)):
            continue
        found.append((path, var, ts))
    return found


def _time_columns(temporal_freq: str, timestamps: pd.Series) -> pd.DataFrame:
    """Map an intermediate's raw timestamp string into the cadence-appropriate
    time column(s) per DD-17b.
    """
    if temporal_freq == "yearly":
        return pd.DataFrame({"year": timestamps.astype(int)})
    if temporal_freq == "monthly":
        ts_str = timestamps.astype(str)
        return pd.DataFrame({
            "year": ts_str.str[:4].astype(int),
            "month": ts_str.str[4:6].astype(int),
        })
    if temporal_freq == "daily":
        return pd.DataFrame({
            "date": pd.to_datetime(timestamps.astype(str), format="%Y%m%d"),
        })
    raise ValueError(f"unsupported temporal_freq={temporal_freq!r}")


def reshape(cfg: DictConfig, polygon: str, year: int, temporal_freq: str) -> None:
    base_path = Path(cfg.datapaths.base_path)
    polygon_name = str(cfg.polygons.polygon_name)
    variables = list(cfg.grids.variables.keys())

    intermediate_dir = base_path / "intermediate" / temporal_freq

    LOGGER.info(
        "reshape polygon=%s year=%d temporal_freq=%s variables=%s",
        polygon, year, temporal_freq, variables,
    )

    # --- discover intermediates ---
    items = _list_intermediates(intermediate_dir, variables, polygon, year)
    LOGGER.info("found %d intermediates under %s", len(items), intermediate_dir)
    if not items:
        raise FileNotFoundError(
            f"no intermediates found at {intermediate_dir} matching "
            f"(polygon={polygon}, year={year}, variables={variables}). "
            "Run aggregate.py upstream."
        )

    # --- load and tag each ---
    parts: list[pd.DataFrame] = []
    for path, var, ts in items:
        df = pd.read_parquet(path)
        df["__variable"] = var
        df["__timestamp"] = ts
        parts.append(df)
    long = pd.concat(parts, ignore_index=True)
    LOGGER.info("long-form rows: %d (across %d timestamps × %d variables)",
                len(long), long["__timestamp"].nunique(), long["__variable"].nunique())

    # --- pivot variable values to wide form ---
    # dropna=False is critical — without it, pivot_table silently drops rows
    # where the pivoted value is NaN, which would lose the outside-grid and
    # all-nodata polygons we deliberately carry per DD-17b.
    wide_values = long.pivot_table(
        index=["polygon_id", "__timestamp"],
        columns="__variable",
        values="value",
        aggfunc="first",
        dropna=False,
    ).reset_index()
    wide_values.columns.name = None
    # wide_values: [polygon_id, __timestamp, <variables...>]

    # Per DD-17b (revised): the LEGO output table may incorporate multiple
    # variables; carrying single `valid_fraction` / `effective_samples` columns
    # would be misleading (the quality is per-variable). Those columns are
    # available in the per-(variable, timestamp) intermediates for debugging
    # and downstream quality-aware tooling. The output table is consumer-facing
    # and stays just polygon × time × variable(s).
    out = wide_values

    # --- expand __timestamp into time column(s) ---
    time_df = _time_columns(temporal_freq, out["__timestamp"])
    out = pd.concat([out.drop(columns=["__timestamp"]), time_df], axis=1)

    # --- rename polygon_id → polygon_name ---
    out = out.rename(columns={"polygon_id": polygon_name})

    # --- reorder columns: polygon, time, variables ---
    if temporal_freq == "yearly":
        time_cols = ["year"]
    elif temporal_freq == "monthly":
        time_cols = ["year", "month"]
    else:
        time_cols = ["date"]
    out = out[[polygon_name, *time_cols, *variables]]

    # --- sort by (time, polygon_name) — outer time, inner polygon ---
    out = out.sort_values([*time_cols, polygon_name]).reset_index(drop=True)

    # --- dtype casts ---
    out[polygon_name] = out[polygon_name].astype("string")
    for v in variables:
        out[v] = out[v].astype("float32")
    if "year" in time_cols:
        out["year"] = out["year"].astype("int32")
    if "month" in time_cols:
        out["month"] = out["month"].astype("int32")

    # --- resolve output filename via cfg.output_name template (DD-17a / 17c) ---
    resolved = cfg.output_name.format(
        polygon_name=polygon_name,
        temporal_freq=temporal_freq,
    )
    out_path = base_path / "output" / temporal_freq / f"{resolved}__{year}.parquet"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # --- write with pyarrow so date stays timestamp[ms] per DD-17b ---
    schema_fields: list[pa.Field] = []
    for col in out.columns:
        if col == polygon_name:
            schema_fields.append(pa.field(polygon_name, pa.string()))
        elif col == "date":
            schema_fields.append(pa.field("date", pa.timestamp("ms")))
        elif col in ("year", "month"):
            schema_fields.append(pa.field(col, pa.int32()))
        else:
            schema_fields.append(pa.field(col, pa.float32()))
    schema = pa.schema(schema_fields)
    table = pa.Table.from_pandas(out, schema=schema, preserve_index=False)
    pq.write_table(table, out_path)

    LOGGER.info("wrote %s (%d rows, cols=%s)", out_path, len(out), list(out.columns))

    # --- diagnostics ---
    # The output no longer carries effective_samples/valid_fraction (those live
    # in the per-(variable, timestamp) intermediates). We can still report
    # has-value vs NaN per variable from the output table itself.
    for v in variables:
        has_value = int(out[v].notna().sum())
        LOGGER.info(
            "%d rows total, %s has_value=%d (%d NaN)",
            len(out), v, has_value, len(out) - has_value,
        )


@hydra.main(config_path="../conf", config_name="config", version_base=None)
def main(cfg: DictConfig) -> None:
    polygon = str(cfg.polygon)
    year = int(cfg.year)
    temporal_freq = str(cfg.temporal_freq)
    reshape(cfg, polygon, year, temporal_freq)


if __name__ == "__main__":
    main()
