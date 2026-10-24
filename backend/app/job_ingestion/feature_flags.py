"""Provider feature flags for job ingestion.

The ATS flags default to enabled so an absent variable preserves the legacy
ingestion behavior. Values are intentionally parsed conservatively: only the
standard true spellings enable a provider; invalid values use the documented
default rather than accidentally disabling an existing integration.
"""

import os


_TRUE_VALUES = {"1", "true", "yes", "on"}
_DEFAULTS = {
    "greenhouse": True,
    "lever": True,
    "ashby": True,
    "fantastic": False,
    "theirstack": False,
}
_ENV_NAMES = {
    "greenhouse": "GREENHOUSE_ENABLED",
    "lever": "LEVER_ENABLED",
    "ashby": "ASHBY_ENABLED",
    "fantastic": "FANTASTIC_ENABLED",
    "theirstack": "THEIRSTACK_ENABLED",
}


def provider_enabled(provider: str) -> bool:
    """Return a provider flag using its safe, documented default."""
    key = provider.strip().casefold()
    default = _DEFAULTS.get(key, False)
    raw = os.getenv(_ENV_NAMES.get(key, f"{key.upper()}_ENABLED"))
    if raw is None:
        return default
    value = raw.strip().casefold()
    if value in _TRUE_VALUES:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def provider_flag_name(provider: str) -> str:
    return _ENV_NAMES[provider.strip().casefold()]


class ProviderDisabledError(RuntimeError):
    """Raised when a direct collector call targets a disabled provider."""
