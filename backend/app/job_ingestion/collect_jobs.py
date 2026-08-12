import logging
from typing import Any

from app.job_ingestion.connectors import ashby, greenhouse, lever, workable
from app.job_ingestion.normalize import (
    normalize_ashby,
    normalize_greenhouse,
    normalize_lever,
    normalize_workable,
)

logger = logging.getLogger(__name__)


class JobCollector:
    """
    Collect and normalize jobs from supported ATS providers.

    This class does NOT write to the database.
    It only fetches and normalizes jobs.
    """

    def collect_company_jobs(
        self,
        ats_type: str,
        identifier: str,
        company_name: str,
    ) -> list[dict[str, Any]]:
        """
        Fetch and normalize jobs for one company.

        Args:
            ats_type:
                greenhouse / lever / ashby / workable

            identifier:
                Greenhouse -> board_token
                Lever -> company_slug
                Ashby -> job_board_name
                Workable -> account_slug

            company_name:
                Company name to attach to normalized jobs.

        Returns:
            List of normalized job dictionaries.
        """

        ats_type = ats_type.lower().strip()

        logger.info(
            "Collecting jobs: company=%s ats=%s identifier=%s",
            company_name,
            ats_type,
            identifier,
        )

        if ats_type == "greenhouse":
            raw_jobs = greenhouse.collect_jobs(identifier)

            normalized_jobs = [
                normalize_greenhouse(job, company_name)
                for job in raw_jobs
            ]

        elif ats_type == "lever":
            raw_jobs = lever.collect_jobs(identifier)

            normalized_jobs = [
                normalize_lever(job, company_name)
                for job in raw_jobs
            ]

        elif ats_type == "ashby":
            raw_jobs = ashby.collect_jobs(identifier)

            normalized_jobs = [
                normalize_ashby(job, company_name)
                for job in raw_jobs
            ]

        elif ats_type == "workable":
            raw_jobs = workable.collect_jobs(identifier)

            normalized_jobs = [
                normalize_workable(job, company_name)
                for job in raw_jobs
            ]

        else:
            raise ValueError(
                f"Unsupported ATS type: {ats_type}"
            )

        logger.info(
            "Normalized %d jobs for %s",
            len(normalized_jobs),
            company_name,
        )

        return normalized_jobs