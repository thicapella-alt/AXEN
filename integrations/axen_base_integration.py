"""
integrations/axen_base_integration.py — Abstract base class for all AXEN integrations.

Every platform integration (Mercado Livre, Nuvemshop, Shopee, …) must:
  1. Subclass BaseIntegration.
  2. Set `name` to the lowercase platform slug (e.g. "mercadolivre").
  3. Implement get_sales_report() and get_ad_metrics().

Feature-flag pattern
────────────────────
Each integration is guarded by an environment variable:
  mercadolivre  →  MERCADOLIVRE_ENABLED
  nuvemshop     →  NUVEMSHOP_ENABLED
  shopee        →  SHOPEE_ENABLED
  <other>       →  <NAME_UPPER>_ENABLED   (auto-generated)

Set the variable to "true" (case-insensitive) to enable.

Calling check_enabled() raises IntegrationDisabledError when the flag is off.
Subclasses should call check_enabled() in __init__() so the error surfaces early.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod


class IntegrationDisabledError(Exception):
    """Raised when an integration is called but its feature flag is off."""


# ── Feature-flag helper ───────────────────────────────────────────────────────

_ENV_VAR_MAP: dict[str, str] = {
    "mercadolivre": "MERCADOLIVRE_ENABLED",
    "nuvemshop":    "NUVEMSHOP_ENABLED",
    "shopee":       "SHOPEE_ENABLED",
}


def is_enabled(name: str) -> bool:
    """
    Return True if the integration identified by *name* is enabled.

    Looks up the canonical env-var name from the built-in mapping; falls back
    to ``<NAME_UPPER>_ENABLED`` for unknown platforms.

    Parameters
    ----------
    name:
        Lowercase platform slug, e.g. ``"mercadolivre"``.
    """
    var = _ENV_VAR_MAP.get(name.lower(), f"{name.upper()}_ENABLED")
    return os.getenv(var, "false").lower() == "true"


# ── Abstract base ─────────────────────────────────────────────────────────────

class BaseIntegration(ABC):
    """
    Contract that all platform integrations must satisfy.

    Attributes
    ----------
    name:
        Lowercase slug that identifies this integration (must be overridden).
    """

    #: Lowercase platform slug, e.g. "mercadolivre".
    name: str = ""

    # ── Guard ────────────────────────────────────────────────────────────────

    def check_enabled(self) -> None:
        """
        Raise IntegrationDisabledError if the integration's feature flag is off.

        Subclasses should call this in ``__init__()`` so the error surfaces at
        construction time rather than on the first API call.
        """
        if not is_enabled(self.name):
            raise IntegrationDisabledError(
                f"Integration '{self.name}' is disabled. "
                f"Set {_ENV_VAR_MAP.get(self.name.lower(), self.name.upper() + '_ENABLED')}=true to enable."
            )

    # ── Abstract interface ────────────────────────────────────────────────────

    @abstractmethod
    def get_sales_report(self, days: int = 30) -> list[dict]:
        """
        Fetch and return a normalised sales report for the past *days* days.

        Returns
        -------
        list[dict]:
            Each element represents one order with at least the keys:
            ``order_id``, ``buyer_id``, ``total_amount``, ``status``,
            ``date_created``, ``items`` (list of dicts with ``sku``,
            ``title``, ``qty``, ``unit_price``).
        """

    @abstractmethod
    def get_ad_metrics(self, days: int = 30) -> list[dict]:
        """
        Fetch and return advertising / ROAS metrics for the past *days* days.

        Returns
        -------
        list[dict]:
            Platform-specific ad metrics.  Returns ``[]`` on any non-critical
            error so callers need not handle partial failures.
        """
