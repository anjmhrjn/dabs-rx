# Data dictionary

All data is synthetic. This lists the `reporting` schema, the only schema the dashboard reads. Every view except `quarantine_summary` shows only the facilities the reader has access to (see `user_facility_access`). Patient and admission IDs are hashed unless the reader is in the `rx_privileged` group.

Facilities are `MAIN` (Main Hospital) and `COMM` (Community Hospital). Units are `MAIN-MSA`, `MAIN-MSB`, `MAIN-ED`, `COMM-MSA`, and `COMM-BH` (Behavioral Health). DEA schedules run from `CII` to `CV`.

## daily_discrepancies
One row per day, cabinet, and drug with at least one count discrepancy. A discrepancy is a blind count that did not match the cabinet's expected quantity.

| Column | Type | Meaning |
|---|---|---|
| report_date | date | Day of the count |
| facility_id | string | Facility |
| cabinet_id | string | Cabinet, for example `MAIN-ED-ADC1` |
| cabinet_unit_id | string | Unit the cabinet is on |
| ndc | string | 11-digit NDC of the drug |
| drug_key | string | Formulary item key |
| display_name | string | Drug name, strength, and form |
| dea_schedule | string | DEA schedule |
| discrepancies | long | Counts that did not match that day |
| resolved | long | Discrepancies a pharmacist has resolved |
| unresolved | long | Discrepancies still open |
| net_variance_units | double | Counted minus expected, summed. Negative means fewer units than expected. |
| median_hours_to_resolve | double | Median hours from the count to its resolution |
| resolution_codes | array | Resolution codes used: `COUNT_ERROR`, `DOCUMENTATION_ERROR`, `RESTOCK_ERROR` |

## daily_compliance_events
One row per day, unit, and flag type with at least one flagged event.

| Column | Type | Meaning |
|---|---|---|
| report_date | date | Day of the events |
| facility_id | string | Facility |
| unit_id | string | Unit of the cabinet where the event happened |
| flag_type | string | One of the flags listed below |
| events | long | Flagged events |
| users | long | Distinct nurses with a flagged event |
| patients | long | Distinct patients involved |

Flag types:
- `override`: pulled before a pharmacist verified the order
- `unwitnessed_waste`: waste with no witness
- `delayed_waste`: waste more than 30 minutes after the pull
- `pull_without_active_order`: no active order on file for that drug at that time
- `pull_after_discharge`: pulled after the patient was discharged
- `pull_after_transfer`: pulled on a unit the patient had already left

## compliance_event_detail
One row per flagged cabinet event, for review. The flag columns are true or false.

| Column | Type | Meaning |
|---|---|---|
| event_id | string | Cabinet event ID |
| event_ts, event_date | timestamp, date | When it happened (local facility time) |
| event_type | string | `PULL`, `OVERRIDE`, or `WASTE` |
| facility_id, unit_id, cabinet_id | string | Where |
| drug_key, dea_schedule | string | What |
| user_id | string | Nurse who made the transaction |
| witness_id | string | Witness for a waste. Empty means unwitnessed. |
| patient_id, admission_id | string | Patient and stay. Hashed (`h_...`) unless the reader is in `rx_privileged`. |
| order_id | string | Order the pull was made against. Empty if none. |
| quantity | double | Units of issue (tablets, vials). Wastes can be part of a unit. |
| minutes_to_waste | double | For wastes: minutes after the matching pull |
| is_override, is_unwitnessed_waste, is_delayed_waste, is_pull_without_active_order, is_pull_after_discharge, is_pull_after_transfer | boolean | Compliance flags, the same meanings as the flag types above |

## inventory_daily
One row per day for every stocked cabinet pocket.

| Column | Type | Meaning |
|---|---|---|
| report_date | date | Day |
| facility_id, unit_id, cabinet_id | string | Where |
| ndc, drug_key | string | Drug |
| on_hand | double | Units at the end of the day, from the cabinet's last count plus later movements |
| daily_usage | double | Units dispensed that day, net of returns and cancels |
| avg_daily_usage | double | Average daily usage over the last 28 days |
| days_on_hand | double | on_hand divided by avg_daily_usage |
| par_level | int | Restock-to level |
| restock_below | int | Restock when on hand falls below this |
| below_par | boolean | on_hand is below restock_below |

## shortage_exposure
Formulary items on the current FDA shortage list, one row per drug and facility, as of the latest day.

| Column | Type | Meaning |
|---|---|---|
| as_of_date | date | Inventory day used |
| facility_id | string | Facility |
| drug_key, display_name, ndc, dea_schedule | string | Drug |
| shortage_records | long | Current FDA shortage records matched to this item |
| availability | array | FDA availability values: `Available`, `Limited Availability`, `Unavailable` |
| latest_update_date | date | Latest FDA update for those records |
| on_hand_units | double | Cabinet stock across the facility |
| avg_daily_usage | double | Average daily usage across the facility (last 28 days) |
| days_on_hand | double | Cabinet days on hand. Central pharmacy stock is not modeled. |

## procurement_monthly
Units restocked into cabinets and estimated spend, one row per month, facility, and drug.

| Column | Type | Meaning |
|---|---|---|
| report_month | date | First day of the month |
| facility_id | string | Facility |
| drug_key, display_name, ndc, dea_schedule | string | Drug |
| unit_of_issue | string | `TAB`, `CAP`, `VIAL`, or `CARTRIDGE` |
| unit_cost_per_issue_unit | double | Cost of one unit of issue (USD) |
| cost_source | string | `nadac` (CMS NADAC price) or `fallback` (seeded estimate) |
| restocked_units | double | Units restocked |
| restock_events | long | Restock transactions |
| est_spend | double | restocked_units times unit_cost_per_issue_unit (USD) |

## user_risk_monthly
One row per nurse and month with the screening score. A screening aid, not a determination.

| Column | Type | Meaning |
|---|---|---|
| report_month | date | First day of the month |
| user_id | string | Nurse |
| facility_id, unit_id, shift | string | Peer group used for the score |
| pulls | long | Pulls that month in that peer group |
| scored | boolean | False if the nurse had fewer than 20 pulls that month |
| peer_n | long | Other nurses in the peer group |
| peer_score | double | Sum of positive z-scores against peers, each capped at 4 |
| peer_rank | long | Rank within the month (1 = highest score) |
| peer_reason_codes | array | Up to 3 plain-language reasons |
| baseline_score, baseline_rank | double, long | Same method comparing with all nurses, no peer groups |
| scored_at | timestamp | When the score was computed |

## monthly_review_list
The monthly list for pharmacy review: the top 10% of scored nurses each month, from the latest saved snapshot.

| Column | Type | Meaning |
|---|---|---|
| report_month | date | First day of the month |
| rank | long | Position on the list |
| user_id | string | Nurse |
| facility_id, unit_id, shift | string | Peer group |
| pulls | long | Pulls that month |
| score | double | Peer score |
| reasons | string | Reason codes, separated by "; " |
| run_id | string | Job run that saved this list |
| snapshot_ts | timestamp | When it was saved |

## quarantine_summary
Rows held back by the data quality rules, by file day and rule. Counts only.

| Column | Type | Meaning |
|---|---|---|
| file_date | date | Day of the source file |
| rule | string | `user_id_not_null`, `cabinet_id_not_null`, `quantity_not_negative`, `event_ts_in_window`, `ndc_known`, `cabinet_known`, or `event_id_not_null` |
| rows | long | Rows that failed the rule |

## user_facility_access and my_facilities
- `user_facility_access`: one row per user and facility the user may see. The columns are user_name, facility_id, granted_by, and granted_at.
- `my_facilities`: the facilities the current reader may see.
