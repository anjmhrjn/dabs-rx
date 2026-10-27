# CLAUDE.md

Project: **Controlled Substance Surveillance Lakehouse** (`dabs-rx`)

A Databricks Asset Bundle that ingests simulated automated dispensing cabinet (ADC) activity for a two-hospital system, flags controlled substance compliance events, scores users against peer groups for diversion risk, tracks inventory and shortage exposure, and serves daily and monthly reports on an AI/BI dashboard. Built as a portfolio project for hospital pharmacy analyst and healthcare data engineering roles.

## Ground rules (read first)

1. **All patient, staff, and cabinet data is synthetic.** Never imply otherwise in code comments, README, dashboard titles, or docs. Every README section and the dashboard header must say "synthetic data".
2. **No PHI and no real people.** Generated names come from a fixed fake-name list or IDs only.
3. **Work checkpoint by checkpoint, one PR per checkpoint.** Follow "Git and review workflow" below. Do not start the next checkpoint until the current PR is merged.
4. **Never push to `main`.** Never merge a PR. Never force-push. The user merges.
5. **Ask before**: deploying to the `prod` target, dropping or recreating any table, schema, or volume, deleting files in a volume, or adding a new dependency.
6. **Keep data small.** Free Edition has a daily compute quota. If exceeded, compute shuts off for the rest of the day. Stay within the sizes in "Data scale" unless told otherwise.
7. **Don't guess Databricks APIs.** Lakeflow and Unity Catalog APIs have been renamed recently. When unsure of a function name or SQL clause, check current docs at docs.databricks.com before writing code, and state which version you used.
8. **Labels are off limits to scoring.** Nothing under `src/scoring/` or the pipeline may read the `_eval` schema or `labels/` files. Only `src/eval/` may. A test enforces this.
9. Plain language in docs and README. No marketing words ("cutting-edge", "robust", "seamless", "leverage", "synergy").

## Databricks Free Edition constraints

Design around these. Do not write code that needs anything outside them.

| Constraint | Consequence for this repo |
|---|---|
| Serverless compute only, Python and SQL only | No cluster specs in bundle resources. No Scala, no R. Jobs use serverless `environments`. |
| No RDD API or `sparkContext` on serverless | Use DataFrame API and Spark SQL only. Avoid `.cache()` / `.persist()`. Avoid `spark.conf.set` except documented serverless-safe configs. |
| Outbound internet restricted to trusted domains | **All external fetches (openFDA, NADAC) run locally** in `generator/` and get uploaded to a UC volume. Never call external APIs from a notebook or pipeline. |
| One SQL warehouse, 2X-Small | Dashboard queries hit pre-aggregated gold or reporting views only. No heavy ad hoc joins in dashboard datasets. |
| Max 5 concurrent job tasks | Job DAG stays at 5 tasks or fewer and runs mostly sequentially. |
| One active Lakeflow pipeline per pipeline type | One pipeline covers bronze, silver, and gold. Scoring, snapshot, and eval run as job tasks, not as a second pipeline. |
| One workspace, one metastore | `dev` and `prod` targets live in the same workspace and are separated by the development-mode schema name prefix. |

Default catalog in Free Edition is typically `workspace`. The catalog name comes from the `catalog` bundle variable, set in CLAUDE.local.md. Never hardcode it.

## Repository layout

```
dabs-rx/
  CLAUDE.md
  CLAUDE.local.md            # gitignored, machine and user specific
  databricks.yml             # bundle root, variables, dev/prod targets
  resources/
    pipeline.yml             # single Lakeflow Declarative Pipeline (serverless)
    job.yml                  # orchestration job (<= 5 tasks)
    dashboard.yml            # AI/BI dashboard resource
  generator/                 # runs LOCALLY, never on Databricks
    fetch_reference.py       # openFDA NDC, openFDA shortages, NADAC -> data/reference/
    build_formulary.py       # curated controlled substance formulary with NDCs
    simulate.py              # patients, admissions, orders, ADC events, labels
    config.yaml              # scale, seed, diversion injection rates
    upload.sh                # databricks fs cp into the landing volume
  src/
    pipeline/
      bronze.py
      silver.py
      gold.py
    scoring/
      score_users.py         # peer-group z-scores -> gold.user_risk_monthly
    snapshot/
      snapshot_audit.py      # append monthly audit list to an immutable snapshot table
    eval/
      evaluate.py            # precision@k / recall@k vs labels, baseline vs peer-grouped
  sql/
    governance/
      01_access_tables.sql
      02_column_masks.sql
      03_row_filters.sql
      04_reporting_views.sql
  dashboards/
    rx_surveillance.lvdash.json
  docs/
    user_guide.md            # one page for pharmacy staff reading the dashboard
    data_dictionary.md
    design_notes.md
  tests/
    test_simulator.py
    test_features.py
    test_label_isolation.py
  data/                      # gitignored, local generator output
  README.md
```

## Bundle conventions

- The only bundle variable for naming is `catalog`. There is no `prefix` variable.
- Schemas are defined once in `resources/schemas.yml` with keys `bronze`, `silver`, `gold`, `eval`, `reporting` and base names `rx_bronze`, `rx_silver`, `rx_gold`, `rx_eval`, `rx_reporting`.
  - dev (`mode: development`) adds the `dev_${workspace.current_user.short_name}_` prefix automatically, e.g. `dev_anjmhrjn1_rx_bronze`.
  - prod uses the base names, e.g. `rx_bronze`.
- Everything else references the resources, never builds names from strings: `${resources.schemas.<key>.name}` for schemas, `${resources.volumes.landing.volume_path}` for the landing volume.
- Landing volume: `${resources.volumes.landing.volume_path}/<source>/` (resolves to `/Volumes/<catalog>/<bronze schema>/landing/<source>/`).
- The catalog name `dabs-rx` contains a hyphen, so SQL and Spark identifiers must backtick-quote it: `` `dabs-rx`.<schema>.<table> ``. Volume paths need no quoting.
- Pipeline: `serverless: true`, Python source files, one pipeline only.
- Job tasks (in order):
  1. `refresh_pipeline` (pipeline task)
  2. `score_users`
  3. `snapshot_audit`
  4. `evaluate`
- Commands:
  - `databricks bundle validate -t dev`
  - `databricks bundle deploy -t dev`
  - `databricks bundle run rx_job -t dev`
  - Use the CLI profile named in CLAUDE.local.md: `--profile <name>`.

## Data model

### Simulated hospital system
- Two facilities: `MAIN` (larger, med/surg and ED heavy) and `COMM` (community hospital with a behavioral health unit, psychiatric pharmacotherapy emphasis).
- Units: Med/Surg A, Med/Surg B, ED, Behavioral Health. One to two ADCs per unit.
- Staff: nurses with a home unit and shift (day/night). Some float or transfer units mid-period, which is why unit assignment is SCD Type 2.

### Formulary
About 20 controlled substances across Schedules II to V. Include psych-relevant agents for the behavioral health unit: lorazepam, clonazepam, diazepam, and others. Also include opioids (morphine, hydromorphone, oxycodone, fentanyl, hydrocodone/APAP, tramadol) plus zolpidem, ketamine, phenobarbital, methylphenidate, and pregabalin.
- NDCs and DEA schedule come from the openFDA NDC directory (`dea_schedule` field).
- Unit cost comes from NADAC where a match exists. Otherwise use a seeded fallback cost, flagged `cost_source = 'fallback'`.

### ADC event types
`PULL`, `WASTE` (requires witness), `RETURN`, `CANCEL`, `OVERRIDE` (pull without pharmacist-verified order), `RESTOCK`, `COUNT` (blind count, may show discrepancy), `DISCREPANCY_RESOLVED`.

### Injected diversion-like patterns
Assign to about 3% of nurses, each getting 1 or 2 patterns:
- Elevated waste ratio relative to peers
- Unwitnessed waste, or the same witness on most wastes (collusion signal)
- Delayed waste (long gap between pull and waste)
- Pulls with no active order, or pulls after the patient was discharged or transferred
- High pull-then-cancel rate

Add benign confounders so the problem is not trivial:
- ED and night shift have naturally higher pull rates
- A few legitimate high-acuity nurses have high volume but normal ratios

Labels (`user_id`, `pattern`, `start_date`) go to `data/labels/` locally and to `injected_labels` in the `eval` schema. Nowhere else.

### Data scale (Free Edition safe)
- About 120 nurses, 8 cabinets, about 3,000 admissions over 180 days. Target 250k to 400k ADC events total.
- The simulator writes **one file per day per source** so Auto Loader ingestion is incremental. Upload days 1 to 150 first, then 151 to 180 to demonstrate incremental loads.
- Deterministic: a fixed seed in `generator/config.yaml` reproduces identical output.

## Layer responsibilities

**Bronze.** Auto Loader (`cloudFiles`) from the landing volume, raw columns plus `_ingested_at` and `_source_file`. Reference data (formulary, shortages, NADAC) loads as bronze batch tables.

**Silver.** Two kinds of rules. Keep them separate.

1. *Data quality expectations* (bad data):
   - null keys
   - negative quantities
   - timestamps outside the simulation window
   - unknown NDC or cabinet

   Use pipeline expectations. Rows that fail go to `silver.adc_events_quarantine` with the failed rule name. Never silently drop.
2. *Compliance flags* (valid data that is suspicious): unwitnessed waste, waste delay over threshold, pull without active order, pull after discharge, override. These are boolean columns on `silver.adc_events`. They are **not** expectations, because the events really happened and must stay in the record.

Also in silver:
- `silver.staff_unit_scd2`, built with the current Lakeflow auto CDC API with SCD Type 2. Verify the current function name in the docs (older name: `apply_changes`).
- Event-to-assignment join uses the assignment that was valid at event time.

**Gold** (materialized views in the pipeline):
- `gold.daily_discrepancies`: unresolved count discrepancies by facility, cabinet, drug, day
- `gold.daily_compliance_events`: flagged events by facility, unit, type, day
- `gold.user_activity_monthly`: per user per month features
  - pulls per patient-day
  - waste ratio
  - unwitnessed waste count
  - median minutes to waste
  - override rate
  - cancel rate
  - post-discharge pulls
  - witness concentration
- `gold.inventory_daily`: on hand, average daily usage, days on hand, below-par flag by cabinet and drug
- `gold.shortage_exposure`: formulary items currently in FDA shortage with days on hand
- `gold.procurement_monthly`: restocked units and estimated spend by drug and schedule

**Scoring** (`src/scoring/score_users.py`, job task):
- Peer group = facility + unit + shift, taken from the SCD2 assignment.
- For each feature, compute a z-score within the peer group per month. The composite score is the sum of positive z-scores, each capped at 4.
- Also compute a **baseline** score using global z-scores with no peer grouping.
- Write both to `gold.user_risk_monthly`, along with the top contributing features per user (plain-language reason codes like "waste ratio 3.1 SD above peers").

**Snapshot** (`src/snapshot/snapshot_audit.py`): append each month's top-ranked users and reason codes to `gold.monthly_audit_snapshot` (regular Delta table, append only, keyed by `report_month` and `run_id`). This is what pharmacy leadership would review. Delta time travel on this table answers "what did the report show on date X". Do not rely on time travel of materialized views.

**Eval** (`src/eval/evaluate.py`): read labels and compute precision@k and recall@k at k = top 5% and top 10% of users per month, for peer-grouped vs baseline. Write to `metrics` in the `eval` schema. Print a small comparison table. These numbers feed the README and the resume bullet, so they must be exact and reproducible.

**Governance** (`sql/governance/`):
- Column mask on `patient_id` in silver and gold tables. Show it only to members of an `rx_privileged` group, otherwise a hash.
- Row filter by facility, driven by a `reporting.user_facility_access` mapping table.
- **Verify first** that Free Edition supports row filters and column masks on the object types involved (streaming tables and materialized views can differ from managed tables). If a combination is unsupported, fall back to dynamic views in the `reporting` schema that implement the same logic. Record which approach was used in `docs/design_notes.md`.
- If workspace groups can't be created, use the mapping table with `current_user()` and note it.

**Dashboard.** AI/BI dashboard with the following pages:
- **Daily:** discrepancies, compliance events, below-par and shortage items
- **Monthly:** user risk ranking with reason codes, unit trends, procurement spend
- **Data quality:** quarantine counts by rule

Requirements:
- Datasets query the `reporting` schema views only.
- Header text states the data is synthetic.
- The monthly review list must be exportable to Excel/CSV, because the target audience works in Excel.

## Code standards
- Python 3.11. Use `uv` for local env. Type hints on public functions.
- Pipeline code: use the current Lakeflow Declarative Pipelines Python API. Check whether `from pyspark import pipelines as dp` is the recommended import, otherwise use `import dlt`, and stay consistent across files.
- No hardcoded catalog, schema, or paths in `src/`. Read them from pipeline configuration or job parameters.
- Thresholds (waste delay minutes, top-k fractions, z cap) live in one config, passed as parameters. No magic numbers in transforms.
- SQL keywords uppercase, snake_case identifiers.
- Tests: `uv run pytest`. The simulator and pure feature/score functions must be testable without Databricks.

## Git and review workflow

GitHub repo, `main` is protected (PR required, no direct pushes). Uses the `gh` CLI.

### Per checkpoint

1. Sync: `git checkout main && git pull`.
2. Branch: `git checkout -b cp-<N>-<short-name>` (example: `cp-2-simulator`).
3. Build the checkpoint. Commit in small logical steps, imperative mood messages.
4. Verify:
   - Run `uv run pytest`.
   - Deploy and run the **dev** target from the branch: `databricks bundle deploy -t dev`, then the relevant `bundle run`.
   - Capture the output.
5. Push the branch and open **one** PR for the checkpoint: `gh pr create --base main --title "CP<N>: <name>" --body-file <tmpfile>`. Use the PR template below.
6. Stop and tell the user the PR number. Do not start other work.

### Handling review comments

When the user says the review is done (or asks you to address comments):

1. Read both kinds of comments. Missing inline comments is the common failure.
   - Conversation comments: `gh pr view <n> --comments`
   - Inline review comments: `gh api repos/{owner}/{repo}/pulls/<n>/comments`
2. Push fixes as new commits **to the same branch**. Never open a new PR for review fixes. Never force-push or rewrite history on a branch under review.
3. Reply to each comment with what changed and the commit SHA, or with a reason if you disagree. Disagreeing is fine; say why.
4. Re-run verification if the fix touched code or bundle config. Update the "Verification" section of the PR description.
5. Stop and tell the user the PR is ready for re-review.

### After merge

The user merges (squash merge). When the user says it's merged:

1. Run `git checkout main && git pull`.
2. Delete the local branch.
3. Wait for the go-ahead to start the next checkpoint.

### Deploy rules

- `dev` target: deploy from feature branches freely. That is how results get reviewed alongside code.
- `prod` target: deploy only from an up-to-date `main`, only after merge, and only when the user asks in chat.

### PR description template

```markdown
## Checkpoint
CP<N>: <name>

## What changed
- <files or components, one line each>

## Verification
<pytest summary, bundle deploy/run output, row counts, sample query results>

## Deviations from CLAUDE.md
<none, or what and why>

## Open questions
<none, or decisions the user should make>
```

### Review notes for the user
- `dashboards/*.lvdash.json` diffs are noisy. Say so in the PR and point the user to the deployed dev dashboard instead.
- Keep `data/` and `.databricks/` out of commits. Check `git status` before every commit.

## Checkpoints

Each checkpoint is one PR (see "Git and review workflow"). The "Done when" column is what goes in the PR's Verification section.

| # | Checkpoint | Done when |
|---|---|---|
| 0 | Scaffold: repo layout, `databricks.yml` with dev/prod targets and variables, `.gitignore` (includes `CLAUDE.local.md`, `data/`, `.databricks/`), `pyproject.toml` | `databricks bundle validate -t dev` passes |
| 1 | `fetch_reference.py` + `build_formulary.py` (local) | Formulary CSV has ~20 rows, each with NDC, DEA schedule, unit cost, cost_source. Shortage file present. |
| 2 | `simulate.py` + `config.yaml` + tests | `pytest` passes. Same seed gives identical output. Injected user rate is ~3%. Event counts are within the target range. Summary stats printed. |
| 3 | Upload days 1 to 150, bronze tables via pipeline | Deploy + run succeeds. Bronze row counts match local file counts. |
| 4 | Silver: expectations + quarantine, compliance flags, SCD2 | Quarantine has rows only for deliberately corrupted records (simulator injects ~0.5% bad rows). Flag counts are reasonable. SCD2 shows transfers. |
| 5 | Gold marts | Each gold table queryable, row counts and a sample shown. |
| 6 | Scoring, snapshot, eval tasks wired into the job | Metrics table populated. Peer-grouped vs baseline comparison printed. |
| 7 | Upload days 151 to 180 and rerun | Only new files are ingested (show Auto Loader incremental counts). Metrics update. |
| 8 | Governance | Masks and filters (or fallback views) verified with a query as a privileged and a non-privileged identity, or documented simulation of that. |
| 9 | Dashboard + `docs/user_guide.md` + `docs/data_dictionary.md` + README | Dashboard deploys via bundle. README has an architecture diagram (Mermaid), a synthetic-data disclaimer, how to reproduce, and the eval results table. |

## README must include
- What problem this models: controlled substance monitoring, diversion surveillance, inventory and shortage exposure for an inpatient pharmacy
- Synthetic data disclaimer near the top
- Architecture diagram (Mermaid)
- Free Edition design decisions (why external fetches are local, why one pipeline)
- Eval results table (peer-grouped vs baseline), with exact numbers from `metrics` in the `eval` schema
- Limitations: simulated patterns are simpler than real diversion, and rules and z-scores are a screening aid, not a determination
- Reproduce steps