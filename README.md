# dabs-rx: Controlled Substance Surveillance Lakehouse

> **All data in this project is synthetic.** No real patients, staff, cabinets, or hospitals are represented. Names and IDs are generated.

A Databricks Asset Bundle that models controlled substance monitoring for a two-hospital inpatient pharmacy, using synthetic data. It ingests simulated automated dispensing cabinet (ADC) activity, flags compliance events, scores users against peer groups for diversion risk, tracks inventory and shortage exposure, and serves daily and monthly reports on an AI/BI dashboard. It runs on Databricks Free Edition.

## Status

Checkpoint 1 (reference data). The bundle defines the Unity Catalog schemas and the landing volume, and the local generator builds the formulary and shortage files. The simulator, pipeline, scoring, and dashboard come in later checkpoints.

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

## Reference data

The formulary and shortage list come from public sources. Patients, staff, and cabinet activity are synthetic.

| Source | Used for |
|---|---|
| openFDA NDC directory (`api.fda.gov/drug/ndc.json`) | NDC and DEA schedule for each formulary item |
| openFDA drug shortages (`api.fda.gov/drug/shortages.json`) | Which formulary items are on the current FDA shortage list |
| NADAC, CMS Medicaid (`data.medicaid.gov`) | Unit cost per tablet or mL |

The fetch runs on a local machine, not on Databricks, because Free Edition limits outbound internet access. The files are uploaded to the landing volume later.

```bash
uv run python -m generator.fetch_reference   # network: raw responses to data/reference/
uv run python -m generator.build_formulary   # offline: formulary.csv and shortages.csv
```

The formulary has 20 controlled substances across Schedules II to V, defined in `generator/config.yaml`. For each item, the build picks one NDC from openFDA. It prefers a package with a NADAC generic price, then any NADAC price, then the lowest NDC. Items with no NADAC price get a seeded fallback cost and `cost_source = 'fallback'`. NADAC is an outpatient retail benchmark, so costs are an estimate of hospital acquisition cost, not a contract price. Both sources update weekly, so a later fetch can pick different NDCs or costs. `data/reference/manifest.json` records the fetch date and NADAC dataset used.

## Commands

```bash
uv sync
uv run pytest
uv run python -m generator.fetch_reference
uv run python -m generator.build_formulary
databricks bundle validate -t dev --profile <profile>
databricks bundle deploy -t dev --profile <profile>
```
