# dabs-rx: Controlled Substance Surveillance Lakehouse

> **All data in this project is synthetic.** No real patients, staff, cabinets, or hospitals are represented. Names and IDs are generated.

A Databricks Asset Bundle that models controlled substance monitoring for a two-hospital inpatient pharmacy, using synthetic data. It ingests simulated automated dispensing cabinet (ADC) activity, flags compliance events, scores users against peer groups for diversion risk, tracks inventory and shortage exposure, and serves daily and monthly reports on an AI/BI dashboard. It runs on Databricks Free Edition.

## Status

Checkpoint 6 (scoring and evaluation). The local generator builds the formulary and shortage files and simulates 180 days of synthetic hospital activity. Days 1 to 150 are loaded by the pipeline into bronze, checked and flagged in silver, and summarized in gold. A job then scores each nurse against peers, saves the monthly review list, and measures how well the score finds the injected patterns. The dashboard comes in a later checkpoint.

## Layout

| Path | Purpose |
|---|---|
| `databricks.yml` | Bundle root, `catalog` variable, `dev` and `prod` targets |
| `resources/` | Schemas, landing volume, pipeline, job, and later the dashboard |
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

## Load to Databricks

`generator/upload.sh <first_day> <last_day>` copies the simulated daily files for that day range, plus the reference files, into the bundle's landing volume. Days are numbered from 1, starting 2026-03-01. The script reads the volume path from the bundle, so it works for either target. It never uploads `data/labels/` and never overwrites a daily file that is already there.

```bash
databricks bundle deploy -t dev
DATABRICKS_CONFIG_PROFILE=<profile> generator/upload.sh 1 150    # first load
databricks bundle run rx_job -t dev
DATABRICKS_CONFIG_PROFILE=<profile> generator/upload.sh 151 180  # later, incremental load
databricks bundle run rx_job -t dev
```

The pipeline (`resources/pipeline.yml`) runs on serverless compute. Its bronze layer (`src/pipeline/bronze.py`) has two kinds of tables:
- **Streaming tables** for the four daily feeds, read with Auto Loader. Each run reads only files it has not seen before.
- **Batch tables** (`ref_*`) for the reference files.

Bronze keeps every source column as text. It adds `_ingested_at` and `_source_file`, and Auto Loader adds `_rescued_data`. Values are checked and typed in silver.

## Silver: data quality and compliance flags

Silver (`src/pipeline/silver.py`) applies two different kinds of rules.

**Data quality rules** catch bad data. The rules are defined once in `src/pipeline/rules.py`:
- missing event, user, or cabinet ID
- negative quantity
- timestamp outside the simulation window
- unknown NDC or cabinet

They run as pipeline expectations, so pass and fail counts appear in the pipeline event log. A row that fails goes to `adc_events_quarantine` with the names of the rules it failed. No row is dropped without a record.

**Compliance flags** mark valid events that a pharmacist may want to review. They are columns on `adc_events`, and the events stay in the table, because they really happened.

| Flag | Meaning |
|---|---|
| `is_override` | Pulled before a pharmacist verified the order |
| `is_unwitnessed_waste` | Waste recorded with no witness |
| `is_delayed_waste` | Waste more than `waste_delay_minutes` (default 30) after the pull |
| `is_pull_without_active_order` | No order on file for that drug, or the order was not active at the time |
| `is_pull_after_discharge` | Pulled after the patient was discharged |
| `is_pull_after_transfer` | Pulled on a unit the patient had already left |

Each event also carries the nurse's unit and shift at the time of the event (`assigned_unit_id`, `assigned_shift`). These come from `staff_unit_scd2`, a full history of staff assignments (SCD Type 2), so a nurse who transfers is compared with the right peers before and after the move.

Thresholds and the simulation window are bundle variables in `databricks.yml`.

## Gold: report tables

Gold (`src/pipeline/gold.py`) builds the tables that reports and scoring read. All are materialized views over silver.

| Table | One row per | What it answers |
|---|---|---|
| `daily_discrepancies` | day, cabinet, drug | Which count discrepancies happened, and which are still unresolved |
| `daily_compliance_events` | day, unit, flag type | How many overrides, unwitnessed wastes, late wastes, and pulls without an order or after the patient left |
| `user_activity_monthly` | nurse, month, peer group | Each nurse's monthly activity (see the list below) |
| `inventory_daily` | day, cabinet, drug | End-of-day on hand, average daily use, days on hand, and whether the pocket is below its restock level |
| `shortage_exposure` | drug, facility | Formulary items on the current FDA shortage list, with on hand and days on hand |
| `procurement_monthly` | month, facility, drug | Units restocked and estimated spend |

The monthly activity measures in `user_activity_monthly` are:
- pulls per patient-day
- waste ratio
- unwitnessed wastes
- median minutes to waste
- override and cancel rates
- pulls after discharge or transfer
- witness concentration: the share of wastes signed by the same witness

A nurse's peer group is facility, unit, and shift at the time of each event.

**On hand** is taken from the cabinet's own last count of the day, plus any movements after that count. No starting stock is assumed. Days on hand is cabinet stock divided by average daily use over the last 28 days (`usage_lookback_days`). Pharmacy central stock is not modeled, so these numbers describe what is in the cabinet.

**Estimated spend** is units restocked times the NADAC unit cost, or the fallback cost, converted to the unit the cabinet dispenses (for example, per vial). It is an estimate, not an invoice amount.

## Scoring and evaluation

The job `rx_job` runs four tasks in order:

| Task | Code | Output |
|---|---|---|
| `refresh_pipeline` | `src/pipeline/` | bronze, silver, gold |
| `score_users` | `src/scoring/` | `gold.user_risk_monthly` |
| `snapshot_audit` | `src/snapshot/` | `gold.monthly_audit_snapshot` |
| `evaluate` | `src/eval/` | `eval.injected_labels`, `eval.metrics` |

**How a nurse is scored.** Each month, every measure in `user_activity_monthly` is compared with the other nurses in the same peer group (facility, unit, shift) as a z-score: how many standard deviations the nurse is from the mean of the others. The score adds up the measures where the nurse is above peers, with each one capped at 4. The top reasons become plain-language codes, for example "cancel rate more than 10 SD above peers (0.13 vs peer median 0.03)".

Rules for what gets scored:
- A nurse-month needs at least 20 pulls.
- The waste measures count only when the nurse had at least 5 wastes that month.

A baseline score uses the same method but compares every nurse with all nurses, with no peer groups.

**Review list.** The top 10% of nurses each month, with their reason codes, are appended to `monthly_audit_snapshot`. Rows are never updated or deleted. Delta time travel (`VERSION AS OF`) shows exactly what the list said on any earlier run.

**Labels stay separate.** The injected labels are uploaded to their own volume in the `eval` schema (`generator/upload_labels.sh`). Only `src/eval/` reads them. `tests/test_label_isolation.py` fails if the pipeline, scoring, or snapshot code, or their job parameters, refer to labels.

**Results** (days 1 to 150; months with at least one injected nurse, April to July; counts pooled over those months):

| Method | Top 5%: precision | Top 5%: recall | Top 10%: precision | Top 10%: recall |
|---|---|---|---|---|
| Peer-grouped | 0.333 (8 of 24) | 0.615 (8 of 13) | 0.208 (10 of 48) | 0.769 (10 of 13) |
| Baseline (no peer groups) | 0.208 (5 of 24) | 0.385 (5 of 13) | 0.167 (8 of 48) | 0.615 (8 of 13) |

Only 2 to 4 nurses per month carry an injected pattern. So the highest possible precision at the top 5% (6 nurses) is 0.54 pooled, and at the top 10% it is 0.27. The baseline loses mostly to ED nurses: their override and waste rates are high for ordinary reasons, and they look normal next to other ED nurses.

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
