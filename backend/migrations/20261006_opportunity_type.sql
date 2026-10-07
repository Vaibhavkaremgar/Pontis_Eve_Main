-- Canonical opportunity classification. Existing rows are ordinary jobs.
ALTER TABLE job_descriptions
    ADD COLUMN IF NOT EXISTS opportunity_type VARCHAR(32) NOT NULL DEFAULT 'jobs';
UPDATE job_descriptions SET opportunity_type = CASE WHEN opportunity_type IN ('intern', 'internship') THEN 'intern' ELSE 'jobs' END;
ALTER TABLE job_descriptions DROP CONSTRAINT IF EXISTS ck_job_descriptions_opportunity_type;
ALTER TABLE job_descriptions DROP CONSTRAINT IF EXISTS job_descriptions_opportunity_type_check;
ALTER TABLE job_descriptions
    ADD CONSTRAINT ck_job_descriptions_opportunity_type CHECK (opportunity_type IN ('jobs', 'intern'));

ALTER TABLE candidate_preferences
    ADD COLUMN IF NOT EXISTS opportunity_type VARCHAR(32) NOT NULL DEFAULT 'jobs';
UPDATE candidate_preferences SET opportunity_type = CASE WHEN opportunity_type IN ('intern', 'internship') THEN 'intern' ELSE 'jobs' END;
ALTER TABLE candidate_preferences
    DROP CONSTRAINT IF EXISTS candidate_preferences_opportunity_type_check;
ALTER TABLE candidate_preferences
    ADD CONSTRAINT candidate_preferences_opportunity_type_check
    CHECK (opportunity_type IN ('jobs', 'intern'));
