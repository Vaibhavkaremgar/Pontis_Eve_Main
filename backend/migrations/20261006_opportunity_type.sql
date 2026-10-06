-- Generic opportunity classification. Existing rows are normal jobs.
ALTER TABLE job_descriptions
    ADD COLUMN IF NOT EXISTS opportunity_type VARCHAR(32) NOT NULL DEFAULT 'job';
UPDATE job_descriptions SET opportunity_type = 'job' WHERE opportunity_type IS NULL;
ALTER TABLE job_descriptions
    DROP CONSTRAINT IF EXISTS job_descriptions_opportunity_type_check;
ALTER TABLE job_descriptions
    ADD CONSTRAINT job_descriptions_opportunity_type_check
    CHECK (opportunity_type IN ('job', 'internship'));

ALTER TABLE candidate_preferences
    ADD COLUMN IF NOT EXISTS opportunity_type VARCHAR(32) NOT NULL DEFAULT 'job';
ALTER TABLE candidate_preferences
    DROP CONSTRAINT IF EXISTS candidate_preferences_opportunity_type_check;
ALTER TABLE candidate_preferences
    ADD CONSTRAINT candidate_preferences_opportunity_type_check
    CHECK (opportunity_type IN ('job', 'internship'));
