from app.job_ingestion.collect_jobs import JobCollector


collector = JobCollector()

jobs = collector.collect_company_jobs(
    ats_type="greenhouse",
    identifier="jumio",
    company_name="Jumio",
)

print(f"\nTotal normalized jobs: {len(jobs)}")

for job in jobs:
    print(
        job["ats_job_id"],
        "|",
        job["title"],
    )