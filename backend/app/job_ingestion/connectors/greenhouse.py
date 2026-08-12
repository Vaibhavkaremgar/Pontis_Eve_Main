import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

GREENHOUSE_BASE_URL = "https://boards-api.greenhouse.io/v1/boards"


def collect_jobs(board_token: str) -> list[dict[str, Any]]:
    """
    Fetch all active jobs from a Greenhouse board.

    Args:
        board_token: Greenhouse board token
                     Example: bettertaxrelief

    Returns:
        List of jobs from Greenhouse.
    """

    url = f"{GREENHOUSE_BASE_URL}/{board_token}/jobs?content=true"

    logger.info("Fetching Greenhouse jobs for board: %s", board_token)

    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.get(url)

        response.raise_for_status()

        data = response.json()

        jobs = data.get("jobs", [])

        logger.info(
            "Successfully fetched %d jobs from Greenhouse board '%s'",
            len(jobs),
            board_token,
        )

        return jobs

    except httpx.HTTPStatusError as exc:
        logger.error(
            "Greenhouse returned HTTP %s for board '%s'",
            exc.response.status_code,
            board_token,
        )
        raise

    except httpx.RequestError as exc:
        logger.error(
            "Unable to connect to Greenhouse for board '%s': %s",
            board_token,
            exc,
        )
        raise

    except Exception:
        logger.exception(
            "Unexpected error while fetching Greenhouse jobs for '%s'",
            board_token,
        )
        raise