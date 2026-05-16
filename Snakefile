from hydra import compose, initialize

conda: "requirements.yaml"
configfile: "snakemake.yaml"

global_choice = config["global_"]
years = config["years"]

overrides = [f"_global={global_choice}"]
with initialize(version_base=None, config_path="conf"):
    cfg = compose(config_name="config", overrides=overrides)

base_path = cfg.datapaths.base_path
polygon_name = cfg.polygons.polygon_name
variable = cfg.grids.variable
temporal_freq = cfg.grids.temporal_freq
script_overrides = " ".join(overrides)

grid_json = f"{base_path}/input/grids/{temporal_freq}/grid.json"

grid_pattern = (
    f"{base_path}/input/grids/{temporal_freq}/"
    f"{variable}__{temporal_freq}__{{year}}.tif"
)

polygons_pattern = f"{base_path}/input/polygons/{polygon_name}__{{year}}.gpkg"
polygons_json_pattern = f"{base_path}/input/polygons/{polygon_name}__{{year}}.json"

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


rule standardize_grid:
    input:
        grid_json,
    output:
        grid_pattern,
    log:
        f"logs/standardize_grid_{temporal_freq}_{{year}}.log",
    shell:
        "PYTHONPATH=. python src/standardize_grid.py "
        f"{script_overrides} year={{wildcards.year}} &> {{log}}"


rule standardize_polygons:
    output:
        polygons_pattern,
    log:
        f"logs/standardize_polygons_{polygon_name}_{{year}}.log",
    shell:
        "PYTHONPATH=. python src/standardize_polygons.py "
        f"{script_overrides} year={{wildcards.year}} &> {{log}}"


rule generate_polygons_json:
    input:
        polygons_pattern,
    output:
        polygons_json_pattern,
    log:
        f"logs/generate_polygons_json_{polygon_name}_{{year}}.log",
    shell:
        "PYTHONPATH=. python src/generate_polygons_json.py "
        f"{script_overrides} year={{wildcards.year}} &> {{log}}"


rule aggregate:
    input:
        grid          = grid_pattern,
        polygons      = polygons_pattern,
        polygons_json = polygons_json_pattern,
        grid_json     = grid_json,
    output:
        output_pattern,
    log:
        f"logs/aggregate_{polygon_name}_{temporal_freq}_{{year}}.log",
    shell:
        "PYTHONPATH=. python src/aggregate.py "
        f"{script_overrides} year={{wildcards.year}} &> {{log}}"
