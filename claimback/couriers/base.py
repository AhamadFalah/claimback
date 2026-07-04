"""Courier adapter interface.

Each courier has its own compensation ceiling, claim window, eligible
claim types, and submission format. Formats are generated as BYTES and
validated against golden fixtures byte-for-byte — courier parsers reject
files over invisible differences (encoding, line endings, header quirks),
and a rejected batch is unrecovered money.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal

from ..models import Claim, ClaimType


class ClaimPackError(Exception):
    """Raised when a generated pack fails validation. The batch ABORTS —
    a partially-wrong money file must never leave the building."""


class CourierAdapter(ABC):
    name: str
    ceiling: Decimal                       # max compensation per parcel
    claim_window_days: int
    eligible_types: set[ClaimType]

    def claim_value(self, invoice_total: Decimal) -> Decimal:
        """Claim at min(invoice value, ceiling). Enforced, never silently clamped —
        the delta between invoice value and ceiling is reported, not hidden."""
        return min(invoice_total, self.ceiling)

    @abstractmethod
    def generate_pack(self, claims: list[Claim]) -> bytes:
        """Produce the courier's exact submission artefact (CSV/email body/API payload)."""

    @abstractmethod
    def validate_pack(self, pack: bytes) -> None:
        """Structural validation. Raise ClaimPackError on ANY deviation."""


_REGISTRY: dict[str, CourierAdapter] = {}


def register(adapter: CourierAdapter) -> CourierAdapter:
    _REGISTRY[adapter.name.lower()] = adapter
    return adapter


def get_adapter(name: str) -> CourierAdapter:
    try:
        return _REGISTRY[name.lower()]
    except KeyError:
        raise KeyError(f"No adapter for courier {name!r}. Registered: {sorted(_REGISTRY)}")
