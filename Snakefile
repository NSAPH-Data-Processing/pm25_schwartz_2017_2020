# Snakefile — full pm25_schwartz_2017_2020 pipeline (DD-13 / skills/snakefile.md).
#
# Sections, in DAG order:
#   onstart                    → create_dir_paths (DD-5)
#   Standardize grids          → unzip + build_grid_resample_lookup + standardize_grid
#                                (one rule set per cadence: yearly + daily)
#   Standardize polygons       → standardize_polygons (DD-10)
#   Build polygon→cell mapping → build_polygon_cell_mapping (DD-11 cache)
#   Aggregate                  → aggregate_yearly + aggregate_daily
#
# Cadences. Per-cadence sections of `snakemake.yaml` declare what runs:
#   `yearly.years`    — explicit list of data years.
#   `daily.start/end` — date interval.
# Each section selects its own Hydra bundle via `_global:` (DD-2); the
# Snakefile composes once per cadence (cfg_y, cfg_d). Multi-bundle composition
# means the bundles can differ in CRS, K, polygon set, even canonical grid —
# they just share datapaths so artefacts live in one tree.
#
# Variables. The cfg shape is the multi-variable map declared in DD-15
# (cfg.grids.variables.<std_name>.{zip,file}). This Snakefile reads the
# templates from cfg but currently handles only the single-variable case
# (Schwartz). Multi-variable expansion (one rule per variable) is a
# skills/snakefile.md follow-up.
#
# Run with: snakemake --cores N

from datetime import date, timedelta

from hydra import compose, initialize

conda: "requirements.yaml"
configfile: "snakemake.yaml"

#### ####
# Setup
#### ####

# --- Yearly cadence ---
yearly_section = config["yearly"]
yearly_overrides = [f"_global={yearly_section['global_']}"]
with initialize(version_base=None, config_path="conf"):
    cfg_y = compose(config_name="config", overrides=yearly_overrides)

# --- Daily cadence ---
daily_section = config["daily"]
daily_overrides = [f"_global={daily_section['global_']}"]
with initialize(version_base=None, config_path="conf"):
    cfg_d = compose(config_name="config", overrides=daily_overrides)

base_path = cfg_y.datapaths.base_path
yearly_overrides_str = " ".join(yearly_overrides)
daily_overrides_str = " ".join(daily_overrides)

# --- variables (per cadence): single-variable Schwartz; multi-variable would
#     expand here (see DD-15 + skills/snakefile.md) ---
yearly_variables = list(cfg_y.grids.variables.keys())
daily_variables = list(cfg_d.grids.variables.keys())
assert len(yearly_variables) == 1, (
    f"single-variable Snakefile; got {yearly_variables!r}. "
    "Multi-variable expansion is a skills/snakefile.md follow-up."
)
assert len(daily_variables) == 1, (
    f"single-variable Snakefile; got {daily_variables!r}."
)
yearly_variable = yearly_variables[0]
daily_variable = daily_variables[0]

# --- yearly artefacts (filename templates from cfg per DD-15) ---
yearly_freq = cfg_y.grids.temporal_freq
yearly_archive = f"{base_path}/raw/grids/annual-dat.zip"
_yearly_file_template = cfg_y.grids.variables[yearly_variable].file
yearly_dat_pattern = (
    f"{base_path}/raw/grids/"
    + _yearly_file_template.replace("{year}", "{timestamp}")
)
yearly_lookup = f"{base_path}/input/grids/{yearly_freq}/grid_resample_lookup.parquet"
yearly_grid_json = f"{base_path}/input/grids/{yearly_freq}/grid.json"
yearly_grid_pattern = (
    f"{base_path}/input/grids/{yearly_freq}/{yearly_variable}__{{timestamp}}.tif"
)
yearly_timestamps = [str(y) for y in yearly_section["years"]]

# --- daily artefacts ---
daily_freq = cfg_d.grids.temporal_freq
_daily_zip_template = cfg_d.grids.variables[daily_variable].zip
_daily_file_template = cfg_d.grids.variables[daily_variable].file
daily_archive_pattern = (
    f"{base_path}/raw/grids/" + _daily_zip_template
)
daily_dat_pattern = (
    f"{base_path}/raw/grids/" + _daily_file_template
)
daily_lookup = f"{base_path}/input/grids/{daily_freq}/grid_resample_lookup.parquet"
daily_grid_json = f"{base_path}/input/grids/{daily_freq}/grid.json"
daily_grid_pattern = (
    f"{base_path}/input/grids/{daily_freq}/{daily_variable}__{{timestamp}}.tif"
)


def _enum_dates(start, end) -> list[str]:
    if not isinstance(start, date):
        start = date.fromisoformat(str(start))
    if not isinstance(end, date):
        end = date.fromisoformat(str(end))
    out = []
    d = start
    while d <= end:
        out.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)
    return out


daily_timestamps = _enum_dates(daily_section["start"], daily_section["end"])

# --- polygon artefacts (independent of grid cadence) ---
polygon_name = cfg_y.polygons.polygon_name
polygons_gpkg_pattern = (
    f"{base_path}/input/polygons/{polygon_name}__{{year}}.gpkg"
)
polygons_json_pattern = (
    f"{base_path}/input/polygons/{polygon_name}__{{year}}.json"
)
_daily_start_year = date.fromisoformat(str(daily_section["start"])).year
_daily_end_year = date.fromisoformat(str(daily_section["end"])).year
polygon_years = sorted(
    set(yearly_section["years"])
    | set(range(_daily_start_year, _daily_end_year + 1))
)

# --- polygon→cell mapping cache (DD-11, per-cadence) ---
# Per [[feedback-per-cadence-intermediates]]: the mapping depends on the
# canonical grid, which is declared per-cadence (cfg.grids.input.grid in each
# bundle). Two cadences with different canonicals must NOT share a mapping file
# — putting them at a cadence-agnostic path would silently overwrite.
K = int(cfg_y.upsample.k)
yearly_mapping_pattern = (
    f"{base_path}/intermediate/{yearly_freq}/"
    f"mapping__{polygon_name}__{{year}}__k{K}.parquet"
)
yearly_mapping_json_pattern = (
    f"{base_path}/intermediate/{yearly_freq}/"
    f"mapping__{polygon_name}__{{year}}__k{K}.json"
)
daily_mapping_pattern = (
    f"{base_path}/intermediate/{daily_freq}/"
    f"mapping__{polygon_name}__{{year}}__k{K}.parquet"
)
daily_mapping_json_pattern = (
    f"{base_path}/intermediate/{daily_freq}/"
    f"mapping__{polygon_name}__{{year}}__k{K}.json"
)

# --- per-(variable, timestamp) aggregate intermediates (DD-11 compute step) ---
# aggregate.py writes here; reshape.py reads them to produce the LEGO-compliant
# per-year output below. See [[feedback-separate-compute-from-layout]].
yearly_intermediate_pattern = (
    f"{base_path}/intermediate/{yearly_freq}/"
    f"{{variable}}__{polygon_name}__{{timestamp}}.parquet"
)
daily_intermediate_pattern = (
    f"{base_path}/intermediate/{daily_freq}/"
    f"{{variable}}__{polygon_name}__{{timestamp}}.parquet"
)

# --- LEGO-compliant final output (DD-17a filename + DD-17c output_name template) ---
yearly_output_name = cfg_y.output_name.format(
    polygon_name=polygon_name,
    temporal_freq=yearly_freq,
)
daily_output_name = cfg_d.output_name.format(
    polygon_name=polygon_name,
    temporal_freq=daily_freq,
)
yearly_output_pattern = (
    f"{base_path}/output/{yearly_freq}/{yearly_output_name}__{{year}}.parquet"
)
daily_output_pattern = (
    f"{base_path}/output/{daily_freq}/{daily_output_name}__{{year}}.parquet"
)


wildcard_constraints:
    timestamp=r"\d{4,8}",
    year=r"\d{4}",
    month=r"\d{2}",
    day=r"\d{2}",


onstart:
    shell(f"python utils/create_dir_paths.py {yearly_overrides_str}")


#### ####
# Targets
#### ####

_daily_years = sorted({ts[:4] for ts in daily_timestamps})


rule all:
    input:
        # Standardised inputs (grids + polygons)
        expand(yearly_grid_pattern, timestamp=yearly_timestamps),
        expand(daily_grid_pattern, timestamp=daily_timestamps),
        expand(polygons_gpkg_pattern, year=polygon_years),
        expand(polygons_json_pattern, year=polygon_years),
        # LEGO-compliant per-year outputs (DD-17). The reshape step pulls the
        # per-(variable, timestamp) aggregate intermediates into one consolidated
        # parquet per data year.
        expand(yearly_output_pattern, year=yearly_timestamps),
        expand(daily_output_pattern, year=_daily_years),


#### ####
# Standardize grids — yearly
#### ####

rule unzip_yearly:
    input:
        archive=yearly_archive,
    output:
        dat=yearly_dat_pattern,
    params:
        member=lambda w: _yearly_file_template.format(year=w.timestamp),
    log:
        f"logs/unzip_yearly_{{timestamp}}.log",
    shell:
        "unzip -o {input.archive} '{params.member}' "
        f"-d {base_path}/raw/grids/ &> {{log}}"


rule build_grid_resample_lookup_yearly:
    output:
        lookup=yearly_lookup,
        json=yearly_grid_json,
    log:
        "logs/build_grid_resample_lookup_yearly.log",
    shell:
        "PYTHONPATH=. python src/build_grid_resample_lookup.py "
        f"{yearly_overrides_str} &> {{log}}"


rule standardize_grid_yearly:
    input:
        dat=yearly_dat_pattern,
        lookup=yearly_lookup,
    output:
        yearly_grid_pattern,
    log:
        f"logs/standardize_grid_yearly_{{timestamp}}.log",
    shell:
        "PYTHONPATH=. python src/standardize_grid.py "
        f"{yearly_overrides_str} +variable={yearly_variable} "
        f"+timestamp={{wildcards.timestamp}} "
        f"+dat_path={{input.dat}} &> {{log}}"


#### ####
# Standardize grids — daily
#### ####

rule unzip_daily:
    input:
        archive=daily_archive_pattern,
    output:
        dat=daily_dat_pattern,
    log:
        f"logs/unzip_daily_{{year}}-{{month}}-{{day}}.log",
    shell:
        # Upstream daily zips have inconsistent internal layouts:
        # 2017–2018 nest under 'daily-dat/PM25-YYYY-MM/'; 2019–2020 use
        # 'PM25-YYYY-MM/'. Match by basename in either location and junk
        # paths so the file lands at the canonical destination.
        "unzip -joq {input.archive} "
        "'*PM25-{wildcards.year}-{wildcards.month}-{wildcards.day}.dat' "
        "-d $(dirname {output.dat}) &> {log}"


rule build_grid_resample_lookup_daily:
    output:
        lookup=daily_lookup,
        json=daily_grid_json,
    log:
        "logs/build_grid_resample_lookup_daily.log",
    shell:
        "PYTHONPATH=. python src/build_grid_resample_lookup.py "
        f"{daily_overrides_str} &> {{log}}"


def _daily_dat_for_timestamp(wildcards):
    ts = wildcards.timestamp
    return daily_dat_pattern.format(year=ts[:4], month=ts[4:6], day=ts[6:8])


rule standardize_grid_daily:
    input:
        dat=_daily_dat_for_timestamp,
        lookup=daily_lookup,
    output:
        daily_grid_pattern,
    log:
        f"logs/standardize_grid_daily_{{timestamp}}.log",
    shell:
        "PYTHONPATH=. python src/standardize_grid.py "
        f"{daily_overrides_str} +variable={daily_variable} "
        f"+timestamp={{wildcards.timestamp}} "
        f"+dat_path={{input.dat}} &> {{log}}"


#### ####
# Standardize polygons
#### ####

rule standardize_polygons:
    output:
        gpkg=polygons_gpkg_pattern,
        json=polygons_json_pattern,
    log:
        f"logs/standardize_polygons_{polygon_name}_{{year}}.log",
    shell:
        "PYTHONPATH=. python src/standardize_polygons.py "
        f"{yearly_overrides_str} +year={{wildcards.year}} &> {{log}}"


#### ####
# Build polygon→cell mapping (DD-11)
#### ####
# One mapping per (polygon, year, K). Depends on the canonical grid (grid.json)
# and the polygon set (gpkg + json). Reused by every aggregate invocation that
# shares the (grid_fp, polygons_fp, K) cache key.
#
# For Schwartz, daily and yearly canonicals are identical (same fingerprint),
# so the mapping built against either bundle is interchangeable. We compose
# against the yearly bundle below; the resulting parquet serves both cadences.

rule build_polygon_cell_mapping_yearly:
    input:
        polygons_gpkg = polygons_gpkg_pattern,
        polygons_json = polygons_json_pattern,
        grid_json = yearly_grid_json,
    output:
        mapping = yearly_mapping_pattern,
        sidecar = yearly_mapping_json_pattern,
    log:
        f"logs/build_polygon_cell_mapping_yearly_{polygon_name}_{{year}}_k{K}.log",
    shell:
        "PYTHONPATH=. python src/build_polygon_cell_mapping.py "
        f"{yearly_overrides_str} +polygon={polygon_name} "
        f"+year={{wildcards.year}} +temporal_freq={yearly_freq} &> {{log}}"


rule build_polygon_cell_mapping_daily:
    input:
        polygons_gpkg = polygons_gpkg_pattern,
        polygons_json = polygons_json_pattern,
        grid_json = daily_grid_json,
    output:
        mapping = daily_mapping_pattern,
        sidecar = daily_mapping_json_pattern,
    log:
        f"logs/build_polygon_cell_mapping_daily_{polygon_name}_{{year}}_k{K}.log",
    shell:
        "PYTHONPATH=. python src/build_polygon_cell_mapping.py "
        f"{daily_overrides_str} +polygon={polygon_name} "
        f"+year={{wildcards.year}} +temporal_freq={daily_freq} &> {{log}}"


#### ####
# Aggregate (DD-11 compute step)
#### ####
# Per (variable, timestamp, polygon). Reads the GeoTIFF + the polygon-cell
# mapping, computes the multiplicity-weighted mean per polygon, writes the
# per-(variable, timestamp) intermediate parquet. The Reshape step downstream
# stacks these into the LEGO-compliant per-year output (DD-17).

def _yearly_mapping_for(wildcards):
    # For yearly: polygon vintage year == timestamp.
    return yearly_mapping_pattern.format(year=wildcards.timestamp)


def _daily_mapping_for(wildcards):
    # For daily: polygon vintage year is the year portion of YYYYMMDD.
    return daily_mapping_pattern.format(year=wildcards.timestamp[:4])


rule aggregate_yearly:
    input:
        grid = yearly_grid_pattern,
        mapping = _yearly_mapping_for,
    output:
        yearly_intermediate_pattern,
    log:
        f"logs/aggregate_yearly_{polygon_name}_{{variable}}_{{timestamp}}.log",
    shell:
        "PYTHONPATH=. python src/aggregate.py "
        f"{yearly_overrides_str} +variable={{wildcards.variable}} "
        f"+timestamp={{wildcards.timestamp}} +polygon={polygon_name} "
        f"+year={{wildcards.timestamp}} +temporal_freq={yearly_freq} &> {{log}}"


rule aggregate_daily:
    input:
        grid = daily_grid_pattern,
        mapping = _daily_mapping_for,
    output:
        daily_intermediate_pattern,
    log:
        f"logs/aggregate_daily_{polygon_name}_{{variable}}_{{timestamp}}.log",
    shell:
        # Polygon vintage year derived from the timestamp's first 4 chars.
        "PYTHONPATH=. python src/aggregate.py "
        f"{daily_overrides_str} +variable={{wildcards.variable}} "
        f"+timestamp={{wildcards.timestamp}} +polygon={polygon_name} "
        f"+year=$(echo {{wildcards.timestamp}} | cut -c1-4) "
        f"+temporal_freq={daily_freq} &> {{log}}"


#### ####
# Reshape (DD-17a/b/c/d — LEGO-compliant per-year output)
#### ####
# Per (polygon, year, temporal_freq). Reads all per-(variable, timestamp)
# intermediates for that (year, all-variables, all-timestamps-in-year) and
# writes one consolidated LEGO-compliant parquet at
# data/output/<temporal_freq>/<output_name>__<year>.parquet.

def _reshape_yearly_inputs(wildcards):
    return expand(
        yearly_intermediate_pattern,
        variable=yearly_variables,
        timestamp=[wildcards.year],   # yearly: timestamp == year
    )


def _reshape_daily_inputs(wildcards):
    days_in_year = [ts for ts in daily_timestamps if ts.startswith(wildcards.year)]
    return expand(
        daily_intermediate_pattern,
        variable=daily_variables,
        timestamp=days_in_year,
    )


rule reshape_yearly:
    input:
        _reshape_yearly_inputs,
    output:
        yearly_output_pattern,
    log:
        f"logs/reshape_yearly_{polygon_name}_{{year}}.log",
    shell:
        "PYTHONPATH=. python src/reshape.py "
        f"{yearly_overrides_str} +polygon={polygon_name} "
        f"+year={{wildcards.year}} +temporal_freq={yearly_freq} &> {{log}}"


rule reshape_daily:
    input:
        _reshape_daily_inputs,
    output:
        daily_output_pattern,
    log:
        f"logs/reshape_daily_{polygon_name}_{{year}}.log",
    shell:
        "PYTHONPATH=. python src/reshape.py "
        f"{daily_overrides_str} +polygon={polygon_name} "
        f"+year={{wildcards.year}} +temporal_freq={daily_freq} &> {{log}}"
