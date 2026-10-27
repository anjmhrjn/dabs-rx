# dabs-rx: Controlled Substance Surveillance Lakehouse

> **All data in this project is synthetic.** No real patients, staff, cabinets, or hospitals are represented. Names and IDs are generated.

A Databricks Asset Bundle that models controlled substance monitoring for a two-hospital inpatient pharmacy, using synthetic data. It ingests simulated automated dispensing cabinet (ADC) activity, flags compliance events, scores users against peer groups for diversion risk, tracks inventory and shortage exposure, and serves daily and monthly reports on an AI/BI dashboard. It runs on Databricks Free Edition.

## Status

Checkpoint 0 (scaffold). The bundle defines the Unity Catalog schemas and the landing volume. The synthetic data generator, pipeline, scoring, and dashboard come in later checkpoints.

## Layout

| Path | Purpose |
|---|---|
| `databricks.yml` | Bundle root, `catalog` variable, `dev` and `prod` targets |
| `resources/` | Schemas, landing volume, and later the pipeline, job, and dashboard |
| `generator/` | Local scripts that build the synthetic data and reference files |
| `src/` | Pipeline, scoring, snapshot, and evaluation code |
| `sql/governance/` | Column masks, row filters, and reporting views |
| `tests/` | Local tests (`uv run pytest`) |

## Schemas

Each target gets five schemas in the `catalog` variable's catalog: `rx_bronze`, `rx_silver`, `rx_gold`, `rx_eval`, `rx_reporting`. The `dev` target uses development mode, which adds a `dev_<user>_` prefix to each name, so dev and prod can share one workspace.

## Commands

```bash
uv sync
uv run pytest
databricks bundle validate -t dev --profile <profile>
databricks bundle deploy -t dev --profile <profile>
```
