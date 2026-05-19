# `data/`

This folder starts empty. Its directory structure is created by:

```bash
python utils/create_dir_paths.py
```

The script uses the selected Hydra config from the `datapaths` config group:

```text
conf/datapaths/<datapaths_config>.yaml
```

For example:

```text
conf/datapaths/local.yaml
conf/datapaths/cannon_zcta.yaml
conf/datapaths/cannon_county.yaml
```

## Create the data directory structure

Use the default datapaths config, `local`:

```bash
python utils/create_dir_paths.py
```

Or choose a different config from the `datapaths` group:

```bash
python utils/create_dir_paths.py datapaths=cannon_zcta
python utils/create_dir_paths.py datapaths=cannon_county
```

## Safe to re-run

The script is idempotent. Re-running it will not delete or overwrite existing directories or symlinks. It only adds missing paths.

## Switching datapaths configs

It is safe to switch configs, for example from `cannon_county` to `cannon_zcta`, and rerun as long as the `basepath` argument is not the same. Either modify the `basepath` before creating a new data tree, or empty `data/` first and then re-run the script.

## Snakemake

This setup also runs automatically at the start of the Snakemake workflow through the `onstart:` hook.
