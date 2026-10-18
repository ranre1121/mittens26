ALTER TABLE jobs ADD COLUMN remote_job_id TEXT;
CREATE INDEX jobs_remote_id ON jobs(remote_job_id);
