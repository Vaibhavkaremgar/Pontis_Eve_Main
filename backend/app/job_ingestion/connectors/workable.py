import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

WORKABLE_BASE_URL = "https://apply.workable.com/api/v3/accounts"


def collect_jobs(account_slug: str) -> list[dict[str, Any]]:
    """
    Fetch all active jobs from a Workable account.

    Args:
        account_slug: Workable account slug
                      Example: supersummary

    Returns:
        List of Workable jobs.
    """

    url = f"{WORKABLE_BASE_URL}/{account_slug}/jobs"

    logger.info("Fetching Workable jobs for account: %s", account_slug)

    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.get(url)

        response.raise_for_status()

        data = response.json()

        jobs = data.get("results", [])

        logger.info(
            "Successfully fetched %d jobs from Workable account '%s'",
            len(jobs),
            account_slug,
        )

        return jobs

    except httpx.HTTPStatusError as exc:
        logger.error(
            "Workable returned HTTP %s for account '%s'",
            exc.response.status_code,
            account_slug,
        )
        raise

    except httpx.RequestError as exc:
        logger.error(
            "Unable to connect to Workable for account '%s': %s",
            account_slug,
            exc,
        )
        raise

    except Exception:
        logger.exception(
            "Unexpected error while fetching Workable jobs for '%s'",
            account_slug,
        )
        raise