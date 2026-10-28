-- Reporting views. The dashboard reads only these. All data is synthetic.
-- Facility-level views keep only the current user's facilities. Patient and admission IDs go through mask_id.

CREATE OR REPLACE VIEW ${reporting}.daily_discrepancies
COMMENT 'Cabinet count discrepancies by day, cabinet, and drug (synthetic).'
AS SELECT *
FROM ${gold}.daily_discrepancies
WHERE facility_id IN (SELECT facility_id FROM ${reporting}.my_facilities);

CREATE OR REPLACE VIEW ${reporting}.daily_compliance_events
COMMENT 'Flagged cabinet events by day, unit, and flag type (synthetic).'
AS SELECT *
FROM ${gold}.daily_compliance_events
WHERE facility_id IN (SELECT facility_id FROM ${reporting}.my_facilities);

CREATE OR REPLACE VIEW ${reporting}.compliance_event_detail
COMMENT 'One row per flagged cabinet event, for review. Patient and admission IDs are masked unless the reader is in rx_privileged (synthetic).'
AS SELECT
  event_id,
  event_ts,
  event_date,
  event_type,
  facility_id,
  cabinet_unit_id AS unit_id,
  cabinet_id,
  drug_key,
  dea_schedule,
  user_id,
  witness_id,
  ${reporting}.mask_id(patient_id) AS patient_id,
  ${reporting}.mask_id(admission_id) AS admission_id,
  order_id,
  quantity,
  minutes_to_waste,
  is_override,
  is_unwitnessed_waste,
  is_delayed_waste,
  is_pull_without_active_order,
  is_pull_after_discharge,
  is_pull_after_transfer
FROM ${silver}.adc_events
WHERE (is_override OR is_unwitnessed_waste OR is_delayed_waste OR is_pull_without_active_order
       OR is_pull_after_discharge OR is_pull_after_transfer)
  AND facility_id IN (SELECT facility_id FROM ${reporting}.my_facilities);

CREATE OR REPLACE VIEW ${reporting}.inventory_daily
COMMENT 'End-of-day cabinet stock, usage, days on hand, and below-par flag (synthetic).'
AS SELECT *
FROM ${gold}.inventory_daily
WHERE facility_id IN (SELECT facility_id FROM ${reporting}.my_facilities);

CREATE OR REPLACE VIEW ${reporting}.shortage_exposure
COMMENT 'Formulary items on the current FDA shortage list with cabinet days on hand.'
AS SELECT *
FROM ${gold}.shortage_exposure
WHERE facility_id IN (SELECT facility_id FROM ${reporting}.my_facilities);

CREATE OR REPLACE VIEW ${reporting}.procurement_monthly
COMMENT 'Restocked units and estimated spend by month, facility, and drug (synthetic volumes, NADAC costs).'
AS SELECT *
FROM ${gold}.procurement_monthly
WHERE facility_id IN (SELECT facility_id FROM ${reporting}.my_facilities);

CREATE OR REPLACE VIEW ${reporting}.user_risk_monthly
COMMENT 'Monthly screening scores and reason codes per nurse. A screening aid, not a determination (synthetic).'
AS SELECT
  report_month,
  user_id,
  facility_id,
  unit_id,
  shift,
  pulls,
  scored,
  peer_n,
  peer_score,
  peer_rank,
  peer_reason_codes,
  baseline_score,
  baseline_rank,
  scored_at
FROM ${gold}.user_risk_monthly
WHERE facility_id IN (SELECT facility_id FROM ${reporting}.my_facilities);

CREATE OR REPLACE VIEW ${reporting}.monthly_review_list
COMMENT 'The monthly review list from the latest audit snapshot run, one row per nurse. Export to Excel from the dashboard (synthetic).'
AS WITH latest AS (
  SELECT report_month, max_by(run_id, snapshot_ts) AS run_id
  FROM ${gold}.monthly_audit_snapshot
  GROUP BY report_month
)
SELECT
  s.report_month,
  s.rank,
  s.user_id,
  s.facility_id,
  s.unit_id,
  s.shift,
  s.pulls,
  round(s.peer_score, 1) AS score,
  array_join(s.reason_codes, '; ') AS reasons,
  s.run_id,
  s.snapshot_ts
FROM ${gold}.monthly_audit_snapshot AS s
JOIN latest AS l ON s.report_month = l.report_month AND s.run_id = l.run_id
WHERE s.facility_id IN (SELECT facility_id FROM ${reporting}.my_facilities);

-- Data quality counts. No patient data, and many quarantined rows have no usable facility, so not filtered.
CREATE OR REPLACE VIEW ${reporting}.quarantine_summary
COMMENT 'Quarantined cabinet rows by file day and failed rule (synthetic). Counts only.'
AS SELECT
  to_date(regexp_extract(_source_file, '(\\d{4}-\\d{2}-\\d{2})', 1)) AS file_date,
  rule,
  count(*) AS rows
FROM ${silver}.adc_events_quarantine
LATERAL VIEW explode(failed_rules) AS rule
GROUP BY ALL;
