# test.smk — section-scoped test Snakefile (DD-13 / skills/test_snakefile.md).
#
# Exercises the Standardize grids + Standardize polygons sections for both
# cadences declared in snakemake.yaml.
#
# Variables: the cfg shape is the multi-variable map declared in DD-15
# (cfg.grids.variables.<std_name>.{zip,file}). This Snakefile reads the
# templates from cfg but currently handles only the single-variable case
# (Schwartz). Multi-variable expansion (one rule per variable) is a
# skills/snakefile.md follow-up.
#
# Run with: snakemake --snakefile test.smk --cores N

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

rule all:
    input:
        expand(yearly_grid_pattern, timestamp=yearly_timestamps),
        expand(daily_grid_pattern, timestamp=daily_timestamps),
        expand(polygons_gpkg_pattern, year=polygon_years),
        expand(polygons_json_pattern, year=polygon_years),


#### ####
# Standardize grids — yearly
#### ####

rule unzip_yearly:
    input:
        archive=yearly_archive,
    output:
        dat=yearly_dat_pattern,
    params:
        member=_yearly_file_template.replace("{year}", "{wildcards.timestamp}"),
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
    params:
        member=_daily_file_template,  # has {year}/{month}/{day} — Snakemake substitutes from wildcards
    log:
        f"logs/unzip_daily_{{year}}-{{month}}-{{day}}.log",
    shell:
        "unzip -o {input.archive} '{params.member}' "
        f"-d {base_path}/raw/grids/ &> {{log}}"


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
