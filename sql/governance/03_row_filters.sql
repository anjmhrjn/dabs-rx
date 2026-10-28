-- Row filter for the reporting views: the facilities the current user may see.
-- Every facility-level reporting view keeps only rows WHERE facility_id IN (SELECT facility_id FROM my_facilities).

CREATE OR REPLACE VIEW ${reporting}.my_facilities
COMMENT 'Facilities the current user may see, from user_facility_access.'
AS SELECT facility_id
FROM ${reporting}.user_facility_access
WHERE user_name = current_user();
