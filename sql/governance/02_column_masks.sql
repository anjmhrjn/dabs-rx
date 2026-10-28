-- Mask for patient and admission IDs in the reporting views.
-- Members of the workspace group rx_privileged see the ID; everyone else sees a short hash, which still lets
-- them count and group by patient without seeing the ID.
-- is_member() checks workspace groups. is_account_group_member() returns false for workspace groups in this
-- Free Edition workspace (checked 2026-10-27), so it can't be used here.

CREATE OR REPLACE FUNCTION ${reporting}.mask_id(id STRING)
RETURNS STRING
COMMENT 'Returns the ID for members of rx_privileged, otherwise h_ plus the first 12 hex characters of its SHA-256.'
RETURN CASE
  WHEN id IS NULL THEN NULL
  WHEN is_member('rx_privileged') THEN id
  ELSE concat('h_', left(sha2(id, 256), 12))
END;
