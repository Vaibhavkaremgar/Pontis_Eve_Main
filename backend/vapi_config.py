"""Small, dependency-free helpers for Vapi configuration diagnostics."""

import os


VAPI_CONFIGURATION_NAMES = (
    "VAPI_PUBLIC_KEY",
    "EVE_VAPI_ASSISTANT_ID",
    "VAPI_WEBHOOK_SECRET",
)


def vapi_configuration_status(environ=None) -> dict[str, bool]:
    """Return presence-only Vapi configuration status; never return values."""
    values = os.environ if environ is None else environ
    return {
        name: bool(str(values.get(name, "")).strip())
        for name in VAPI_CONFIGURATION_NAMES
    }
