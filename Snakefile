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
source_label = cfg.pm25_source.label
temporal_freq = cfg.pm25_source.temporal_freq
script_overrides = " ".join(overrides)

output_pattern = (
    f"{base_path}/output/{polygon_name}_{temporal_freq}/"
    f"pm25__{source_label}__{polygon_name}_{temporal_freq}__{{year}}.parquet"
)


onstart:
    shell(f"python utils/create_dir_paths.py {script_overrides}")


rule all:
    input:
        expand(output_pattern, year=years),


rule aggregate_pm25:
    output:
        output_pattern,
    log:
        f"logs/aggregate_pm25_{polygon_name}_{temporal_freq}_{{year}}.log",
    shell:
        "PYTHONPATH=. python src/aggregate_pm25.py "
        f"{script_overrides} year={{wildcards.year}} &> {{log}}"
