# Design notes

Decisions specific to this project or to Databricks Free Edition. All data in this project is synthetic.

## Governance: patient ID mask and facility filter

### What is required
- `patient_id` is visible only to members of the `rx_privileged` group. Everyone else sees a hash.
- Rows are limited to the facilities a user is allowed to see, using a mapping table `reporting.user_facility_access`.

### Approach used: dynamic views in the reporting schema
The mask and the filter are applied in views in the `reporting` schema (`sql/governance/`), not as row filters and column masks on the silver and gold tables. The dashboard reads only these views.

| Object | File | What it does |
|---|---|---|
| `user_facility_access` (table) | `01_access_tables.sql` | One row per user and facility the user may see. The setup adds the user who runs it for both facilities, so no user names are stored in the repo. |
| `mask_id(id)` (function) | `02_column_masks.sql` | Returns the ID for members of `rx_privileged`, otherwise `h_` plus the first 12 hex characters of its SHA-256 hash. The hash lets readers count and group by patient without seeing the ID. |
| `my_facilities` (view) | `03_row_filters.sql` | The facilities the current user may see. |
| Reporting views | `04_reporting_views.sql` | Each facility-level view keeps only `facility_id IN (SELECT facility_id FROM my_facilities)`. `compliance_event_detail` passes `patient_id` and `admission_id` through `mask_id`. |

The job `rx_governance_job` (`src/governance/apply_governance.py`) runs the four files in order. Run it after the first pipeline refresh, and again when the SQL changes.

### Why views and not table-level masks and filters
Checked on docs.databricks.com on 2026-10-27:
- **Declared in the pipeline.** On materialized views and streaming tables built by a pipeline, row filters and column masks must be declared in the pipeline definition itself. They can't be added afterwards with `ALTER TABLE`.
- **Run as the owner during refresh.** Their functions run as the pipeline owner during refresh, and as the reader at query time.
- **No time travel.** Time travel isn't supported on tables with row filters or column masks.
- **Free Edition support isn't stated.**

Several of the protected tables aren't pipeline tables at all. `user_risk_monthly` and `monthly_audit_snapshot` are plain Delta tables written by job tasks. The audit snapshot depends on time travel to answer "what did the report show on date X", so it can't carry a table-level filter.

Views give one mechanism that works the same over materialized views, streaming tables, and Delta tables. They don't touch the pipeline, and they keep time travel on the snapshot.

### Groups: `is_member`, not `is_account_group_member`
The workspace group `rx_privileged` was created with `databricks groups create`. In this Free Edition workspace:
- `is_member('rx_privileged')` returns true for a member.
- `is_account_group_member('rx_privileged')` returns false, and it also returns false for the built-in `admins` group, because those are workspace groups and not account groups.

Databricks recommends `is_account_group_member` in Unity Catalog. It can't be used here, so the mask uses `is_member`.

### Limitations
- **Base tables are not locked down.** The silver and gold tables stay readable by their owner, and by anyone else granted access to them. In a real deployment, readers would get `USE SCHEMA` and `SELECT` on `reporting` only, and no access to silver or gold. This workspace has one user, who owns everything, so those grants are described here but not applied.
- **The hash can be reversed.** `sha2` of a low-entropy ID such as `P000123` can be reversed by trying every possible ID. That's acceptable for synthetic data. Production would add a secret salt kept in a secret scope.
- **Group changes take time.** A membership change is not visible to SQL immediately. The delay measured during verification is recorded below.
- **`quarantine_summary` is not filtered by facility.** It holds only counts by day and rule, no patient data, and many quarantined rows have no usable facility.

### Verification
There is one identity in this workspace (no second user or service principal). Privileged and non-privileged access was shown by changing that user's own group membership and facility rows, then running the same queries against the reporting views. The results are below.

Verified on 2026-10-28 in the dev target, as the workspace's only user (an admin).

| State | `is_member('rx_privileged')` | `my_facilities` | `compliance_event_detail` |
|---|---|---|---|
| A. In the group, both facilities | true | COMM, MAIN | COMM 505 events and 315 patients, MAIN 2,197 and 1,269. IDs shown as `P000006`, `A000006`. |
| B. Removed from the group | false | COMM, MAIN | Same counts and same distinct patients. IDs shown as `h_00ab325d8e51`, `h_00577847ff32`. |
| C. Back in the group, MAIN only | true | MAIN | MAIN only: 2,197 events and 1,269 patients, IDs shown. |
| Restored (setup rerun) | true | COMM, MAIN | Same as A. The `MERGE` re-added only the missing COMM row. |

Row counts in the facility-level views:

| View | A and restored | C (MAIN only) | Gold, MAIN only |
|---|---|---|---|
| daily_discrepancies | 287 | 188 | 188 |
| daily_compliance_events | 1,315 | 886 | 886 |
| inventory_daily | 14,760 | 9,180 | 9,180 |
| shortage_exposure | 9 | 5 | 5 |
| procurement_monthly | 187 | 94 | 94 |
| user_risk_monthly | 720 | 420 | 420 |
| monthly_review_list | 72 | 43 | n/a |

In state A, every count equals the matching gold or silver table. In state C, every count equals the gold table filtered to MAIN.

Group change delay. A removal reached SQL in about 23 seconds (02:36:05 to 02:36:28 UTC). Adding the user back took 4 minutes 20 seconds (02:37:18 to 02:41:38 UTC). Access changes are not immediate, so a revocation should be checked rather than assumed.
