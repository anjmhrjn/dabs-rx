-- Facility access mapping that drives the reporting views' row filter. Synthetic setup.
-- Placeholders (${reporting} etc.) are filled in by src/governance/apply_governance.py.

CREATE TABLE IF NOT EXISTS ${reporting}.user_facility_access (
  user_name STRING NOT NULL COMMENT 'Workspace user name, as returned by current_user()',
  facility_id STRING NOT NULL COMMENT 'Facility the user may see: MAIN or COMM',
  granted_by STRING COMMENT 'Who added the row',
  granted_at TIMESTAMP COMMENT 'When the row was added'
)
COMMENT 'Which facilities each user may see in the reporting views. Edit rows here to change access.';

-- Seed: whoever runs the setup gets both facilities. No user names are stored in the repo.
MERGE INTO ${reporting}.user_facility_access AS t
USING (
  SELECT current_user() AS user_name, facility_id
  FROM VALUES ('MAIN'), ('COMM') AS f(facility_id)
) AS s
ON t.user_name = s.user_name AND t.facility_id = s.facility_id
WHEN NOT MATCHED THEN
  INSERT (user_name, facility_id, granted_by, granted_at)
  VALUES (s.user_name, s.facility_id, current_user(), current_timestamp());
