# test.smk — section-scoped test Snakefile (DD-13 / skills/test_snakefile.md).
#
# Exercises the Standardize grids section for BOTH cadences declared in
# snakemake.yaml:
#   - yearly: list of years.
#   - daily:  inclusive date interval (start, end).
#
# Each cadence runs `unzip_* → build_grid_resample_lookup_<freq> → standardize_grid_<freq>`
# with cadence-suffixed rule names. The two cadences share the underlying scripts
# (`build_grid_resample_lookup.py`, `standardize_grid.py`); only the bundle and
# the wildcard expansion differ.
#
# `rule all` collects both cadences' targets. Snakemake's normal incremental
# behaviour skips outputs that already exist — so if yearly is already done and
# only daily 2018 is newly listed in snakemake.yaml, only the daily 2018 surfaces
# are generated on this run.
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

base_path = cfg_y.datapaths.base_path  # same across cadences (per source)
variable = list(cfg_y.grids.variables.values())[0]
yearly_overrides_str = " ".join(yearly_overrides)
daily_overrides_str = " ".join(daily_overrides)

# --- yearly artefacts ---
yearly_freq = cfg_y.grids.temporal_freq  # "yearly"
yearly_archive = f"{base_path}/raw/grids/annual-dat.zip"
yearly_dat_pattern = f"{base_path}/raw/grids/annual-dat/PM25-{{timestamp}}.dat"
yearly_lookup = (
    f"{base_path}/input/grids/{yearly_freq}/grid_resample_lookup.parquet"
)
yearly_grid_pattern = (
    f"{base_path}/input/grids/{yearly_freq}/{variable}__{{timestamp}}.tif"
)
yearly_timestamps = [str(y) for y in yearly_section["years"]]

# --- daily artefacts ---
daily_freq = cfg_d.grids.temporal_freq  # "daily"
daily_archive_pattern = (
    f"{base_path}/raw/grids/daily-dat/PM25-{{year}}-{{month}}.zip"
)
daily_dat_pattern = (
    f"{base_path}/raw/grids/daily-dat/PM25-{{year}}-{{month}}/"
    f"PM25-{{year}}-{{month}}-{{day}}.dat"
)
daily_lookup = (
    f"{base_path}/input/grids/{daily_freq}/grid_resample_lookup.parquet"
)
daily_grid_pattern = (
    f"{base_path}/input/grids/{daily_freq}/{variable}__{{timestamp}}.tif"
)


def _enum_dates(start, end) -> list[str]:
    """Enumerate YYYYMMDD timestamps from `start` to `end`, inclusive.
    Accepts datetime.date or ISO string."""
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


#### ####
# Standardize grids — yearly
#### ####

rule unzip_yearly:
    input:
        archive=yearly_archive,
    output:
        dat=yearly_dat_pattern,
    log:
        f"logs/unzip_yearly_{{timestamp}}.log",
    shell:
        "unzip -o {input.archive} 'annual-dat/PM25-{wildcards.timestamp}.dat' "
        f"-d {base_path}/raw/grids/ &> {{log}}"


rule build_grid_resample_lookup_yearly:
    output:
        lookup=yearly_lookup,
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
        f"{yearly_overrides_str} +timestamp={{wildcards.timestamp}} "
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
        "unzip -o {input.archive} "
        "'daily-dat/PM25-{wildcards.year}-{wildcards.month}/PM25-{wildcards.year}-{wildcards.month}-{wildcards.day}.dat' "
        f"-d {base_path}/raw/grids/ &> {{log}}"


rule build_grid_resample_lookup_daily:
    output:
        lookup=daily_lookup,
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
        f"{daily_overrides_str} +timestamp={{wildcards.timestamp}} "
        f"+dat_path={{input.dat}} &> {{log}}"
