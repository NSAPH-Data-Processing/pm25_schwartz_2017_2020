from hydra import compose, initialize

conda: "requirements.yaml"
configfile: "snakemake.yaml"

global_choice = config["global_"]
years = config["years"]

overrides = [f"_global={global_choice}"]
with initialize(version_base=None, config_path="conf"):
    cfg = compose(config_name="config", overrides=overrides)

base_path = cfg.datapaths.base_path
polygon_name = cfg.shapefiles.polygon_name
variable = cfg.input.variable
temporal_freq = cfg.input.temporal_freq
script_overrides = " ".join(overrides)

grid_json = f"{base_path}/input/{temporal_freq}/grid.json"

raster_pattern = (
    f"{base_path}/input/{temporal_freq}/"
    f"{variable}__{temporal_freq}__{{year}}.tif"
)

shapefile_pattern = f"{base_path}/input/shapefiles/{polygon_name}__{{year}}.gpkg"
shapefile_json_pattern = f"{base_path}/input/shapefiles/{polygon_name}__{{year}}.json"

output_pattern = (
    f"{base_path}/output/{temporal_freq}/"
    f"{variable}__{polygon_name}_{temporal_freq}__{{year}}.parquet"
)


onstart:
    shell(f"python utils/create_dir_paths.py {script_overrides}")


rule all:
    input:
        expand(output_pattern, year=years),


rule generate_grid_json:
    output:
        grid_json,
    log:
        f"logs/generate_grid_json_{temporal_freq}.log",
    shell:
        "PYTHONPATH=. python src/generate_grid_json.py "
        f"{script_overrides} &> {{log}}"


rule standardize_raster:
    input:
        grid_json,
    output:
        raster_pattern,
    log:
        f"logs/standardize_raster_{temporal_freq}_{{year}}.log",
    shell:
        "PYTHONPATH=. python src/standardize_raster.py "
        f"{script_overrides} year={{wildcards.year}} &> {{log}}"


rule standardize_shapefile:
    output:
        shapefile_pattern,
    log:
        f"logs/standardize_shapefile_{polygon_name}_{{year}}.log",
    shell:
        "PYTHONPATH=. python src/standardize_shapefile.py "
        f"{script_overrides} year={{wildcards.year}} &> {{log}}"


rule generate_shapefile_json:
    input:
        shapefile_pattern,
    output:
        shapefile_json_pattern,
    log:
        f"logs/generate_shapefile_json_{polygon_name}_{{year}}.log",
    shell:
        "PYTHONPATH=. python src/generate_shapefile_json.py "
        f"{script_overrides} year={{wildcards.year}} &> {{log}}"


rule aggregate:
    input:
        raster         = raster_pattern,
        shapefile      = shapefile_pattern,
        shapefile_json = shapefile_json_pattern,
        grid_json      = grid_json,
    output:
        output_pattern,
    log:
        f"logs/aggregate_{polygon_name}_{temporal_freq}_{{year}}.log",
    shell:
        "PYTHONPATH=. python src/aggregate.py "
        f"{script_overrides} year={{wildcards.year}} &> {{log}}"
