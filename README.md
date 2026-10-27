# dabs-rx: Controlled Substance Surveillance Lakehouse

> **All data in this project is synthetic.** No real patients, staff, cabinets, or hospitals are represented. Names and IDs are generated.

A Databricks Asset Bundle that models controlled substance monitoring for a two-hospital inpatient pharmacy, using synthetic data. It ingests simulated automated dispensing cabinet (ADC) activity, flags compliance events, scores users against peer groups for diversion risk, tracks inventory and shortage exposure, and serves daily and monthly reports on an AI/BI dashboard. It runs on Databricks Free Edition.

## Status

Checkpoint 2 (simulator). The bundle defines the Unity Catalog schemas and the landing volume. The local generator builds the formulary and shortage files and simulates 180 days of synthetic hospital activity. The pipeline, scoring, and dashboard come in later checkpoints.

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

## Synthetic data

`generator/simulate.py` builds a synthetic two-hospital system and writes what its systems would send to the lakehouse. Every patient, staff member, and cabinet event is made up. Settings are in the `simulation` section of `generator/config.yaml`, and a fixed seed gives the same files every run.

| | |
|---|---|
| Facilities | `MAIN` (Med/Surg A, Med/Surg B, ED) and `COMM` (Med/Surg A, Behavioral Health) |
| Cabinets | 8 automated dispensing cabinets, 1 or 2 per unit |
| Staff | 120 nurses with a home unit and a day or night shift, 10 of whom transfer once. Also 6 pharmacists and 6 pharmacy techs. |
| Volume | About 3,000 admissions and 186,000 cabinet events over 180 days (2026-03-01 to 2026-08-27) |

Each source is written as one CSV per day under `data/landing/<source>/`, so files can be loaded a day at a time:

| Source | What it holds |
|---|---|
| `adc_events` | Cabinet transactions: `PULL`, `OVERRIDE`, `WASTE`, `RETURN`, `CANCEL`, `RESTOCK`, `COUNT`, `DISCREPANCY_RESOLVED` |
| `adt_events` | Admit, transfer, and discharge events |
| `order_events` | Controlled substance orders: new, pharmacist verified, discontinued |
| `staff_assignments` | Each staff member's unit and shift. Day 1 has everyone; later files hold only transfers. |

Timestamps are local facility time with no time zone. Static tables (`units.csv`, `cabinets.csv`, `cabinet_par.csv`, `dispense_units.csv`) go to `data/reference/`.

**Diversion-like patterns.** About 3% of nurses (4 of 120) get one or two injected patterns starting partway through the window:
- high waste ratio
- unwitnessed waste
- the same witness on most wastes
- long delay between pull and waste
- pulls with no order on file
- pulls after the patient was discharged or transferred
- a high pull-then-cancel rate

The answer key is in `data/labels/` and is read only by the evaluation step.

**Benign differences that are not diversion:**
- ED nurses override and waste far more often, because ED doses are often given before pharmacist verification and come from vials.
- Night shift has more PRN volume.
- Four high-acuity nurses pull about 1.7 times the median while keeping normal ratios.
- Every flag has a small base rate for everyone.

A screening method has to compare nurses with their peers to get past these.

**Bad rows.** 0.5% of cabinet rows are deliberately broken, each with one defect: a missing user or cabinet, a negative quantity, a timestamp outside the window, an unknown NDC, or an unknown cabinet. These exercise the pipeline's data quality checks. Counts per defect are in `data/sim_manifest.json`.

```bash
uv run python -m generator.simulate            # full 180 days into data/
uv run python -m generator.simulate --days 14  # quick look
```

## Commands

```bash
uv sync
uv run pytest
uv run python -m generator.fetch_reference
uv run python -m generator.build_formulary
uv run python -m generator.simulate
databricks bundle validate -t dev --profile <profile>
databricks bundle deploy -t dev --profile <profile>
```
