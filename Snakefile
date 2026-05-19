from hydra import compose, initialize

conda: "requirements.yaml"
configfile: "snakemake.yaml"

#### ####
# Setup: compose Hydra config, resolve names and patterns used by all rules.
#### ####

# Yearly section of snakemake.yaml (DD-7 per-cadence config).
global_choice = config["yearly"]["global_"]
years = config["yearly"]["years"]

overrides = [f"_global={global_choice}"]
with initialize(version_base=None, config_path="conf"):
    cfg = compose(config_name="config", overrides=overrides)

base_path = cfg.datapaths.base_path
polygon_name = cfg.polygons.polygon_name
# Standardised variable name for filename construction. Single-variable bundles
# only for now.
variable = list(cfg.grids.variables.values())[0]
temporal_freq = cfg.grids.temporal_freq
script_overrides = " ".join(overrides)

# Raw per-year .dat extracted from the publisher's annual-dat.zip archive.
yearly_dat_pattern = f"{base_path}/raw/grids/annual-dat/PM25-{{year}}.dat"
yearly_archive = f"{base_path}/raw/grids/annual-dat.zip"

# Precomputed resample lookup (built once per cadence; reused per surface).
grid_resample_lookup = f"{base_path}/input/grids/{temporal_freq}/grid_resample_lookup.parquet"

grid_pattern = (
    f"{base_path}/input/grids/{temporal_freq}/"
    f"{variable}__{{year}}.tif"
)

polygons_pattern = f"{base_path}/input/polygons/{polygon_name}__{{year}}.gpkg"
polygons_json_pattern = f"{base_path}/input/polygons/{polygon_name}__{{year}}.json"

output_pattern = (
    f"{base_path}/output/{temporal_freq}/"
    f"{variable}__{polygon_name}__{{year}}.parquet"
)


onstart:
    shell(f"python utils/create_dir_paths.py {script_overrides}")


#### ####
# Targets
#### ####

rule all:
    input:
        expand(output_pattern, year=years),


#### ####
# Standardize grids
#### ####

# Unzip the publisher's annual archive to per-year .dat files. One archive →
# many .dat outputs; we declare a per-{year} output so Snakemake can track each
# .dat as an independent artefact and chain it into standardize_grid 1:1.
rule unzip_yearly:
    input:
        archive=yearly_archive,
    output:
        dat=yearly_dat_pattern,
    log:
        f"logs/unzip_yearly_{{year}}.log",
    shell:
        "unzip -o {input.archive} 'annual-dat/PM25-{wildcards.year}.dat' "
        f"-d {base_path}/raw/grids/ &> {{log}}"


rule build_grid_resample_lookup:
    output:
        lookup=grid_resample_lookup,
    log:
        "logs/build_grid_resample_lookup.log",
    shell:
        "PYTHONPATH=. python src/build_grid_resample_lookup.py "
        f"{script_overrides} &> {{log}}"


rule standardize_grid:
    input:
        dat=yearly_dat_pattern,
        lookup=grid_resample_lookup,
    output:
        grid_pattern,
    log:
        f"logs/standardize_grid_{temporal_freq}_{{year}}.log",
    shell:
        "PYTHONPATH=. python src/standardize_grid.py "
        f"{script_overrides} +year={{wildcards.year}} &> {{log}}"


#### ####
# Standardize polygons
#### ####

rule standardize_polygons:
    output:
        polygons_pattern,
    log:
        f"logs/standardize_polygons_{polygon_name}_{{year}}.log",
    shell:
        "PYTHONPATH=. python src/standardize_polygons.py "
        f"{script_overrides} +year={{wildcards.year}} &> {{log}}"


rule generate_polygons_json:
    input:
        polygons_pattern,
    output:
        polygons_json_pattern,
    log:
        f"logs/generate_polygons_json_{polygon_name}_{{year}}.log",
    shell:
        "PYTHONPATH=. python src/generate_polygons_json.py "
        f"{script_overrides} +year={{wildcards.year}} &> {{log}}"


#### ####
# Aggregate
#### ####

rule aggregate:
    input:
        grid=grid_pattern,
        polygons=polygons_pattern,
        polygons_json=polygons_json_pattern,
    output:
        output_pattern,
    log:
        f"logs/aggregate_{polygon_name}_{temporal_freq}_{{year}}.log",
    shell:
        "PYTHONPATH=. python src/aggregate.py "
        f"{script_overrides} +year={{wildcards.year}} &> {{log}}"
