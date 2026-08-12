import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

LEVER_BASE_URL = "https://api.lever.co/v0/postings"


def collect_jobs(company_slug: str) -> list[dict[str, Any]]:
    """
    Fetch all active jobs from a Lever company.

    Args:
        company_slug: Lever company slug
                      Example: palantir

    Returns:
        List of Lever jobs.
    """

    url = f"{LEVER_BASE_URL}/{company_slug}?mode=json"

    logger.info("Fetching Lever jobs for company: %s", company_slug)

    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.get(url)

        response.raise_for_status()

        jobs = response.json()

        logger.info(
            "Successfully fetched %d jobs from Lever company '%s'",
            len(jobs),
            company_slug,
        )

        return jobs

    except httpx.HTTPStatusError as exc:
        logger.error(
            "Lever returned HTTP %s for company '%s'",
            exc.response.status_code,
            company_slug,
        )
        raise

    except httpx.RequestError as exc:
        logger.error(
            "Unable to connect to Lever for company '%s': %s",
            company_slug,
            exc,
        )
        raise

    except Exception:
        logger.exception(
            "Unexpected error while fetching Lever jobs for '%s'",
            company_slug,
        )
        raise