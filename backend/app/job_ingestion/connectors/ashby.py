import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

ASHBY_BASE_URL = "https://api.ashbyhq.com/posting-api/job-board"


def collect_jobs(job_board_name: str) -> list[dict[str, Any]]:
    """
    Fetch all active jobs from an Ashby job board.

    Args:
        job_board_name: Ashby job board name
                        Example: OpenAI

    Returns:
        List of Ashby jobs.
    """

    url = (
        f"{ASHBY_BASE_URL}/{job_board_name}"
        "?includeCompensation=true"
    )

    logger.info("Fetching Ashby jobs for board: %s", job_board_name)

    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.get(url)

        response.raise_for_status()

        data = response.json()

        jobs = data.get("jobs", [])

        logger.info(
            "Successfully fetched %d jobs from Ashby board '%s'",
            len(jobs),
            job_board_name,
        )

        return jobs

    except httpx.HTTPStatusError as exc:
        logger.error(
            "Ashby returned HTTP %s for board '%s'",
            exc.response.status_code,
            job_board_name,
        )
        raise

    except httpx.RequestError as exc:
        logger.error(
            "Unable to connect to Ashby for board '%s': %s",
            job_board_name,
            exc,
        )
        raise

    except Exception:
        logger.exception(
            "Unexpected error while fetching Ashby jobs for '%s'",
            job_board_name,
        )
        raise