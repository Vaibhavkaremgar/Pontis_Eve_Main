"""Provider feature flags for job ingestion."""

import os


_SUPPORTED_PROVIDERS = frozenset(
    {"greenhouse", "lever", "ashby", "workable", "fantastic", "theirstack"}
)


def provider_enabled(provider: str) -> bool:
    """Return whether a supported provider is explicitly enabled."""
    key = provider.strip().casefold()
    if key not in _SUPPORTED_PROVIDERS:
        return False
    return os.getenv(f"{key.upper()}_ENABLED", False) == "true"
