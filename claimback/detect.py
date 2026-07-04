"""Claimable-shipment detection.

Every rule is named and every detection carries the rule that fired —
when money is involved, "the AI thought so" is not an audit trail.
"""
from __future__ import annotations

from datetime import date, timedelta

from .config import settings
from .models import ClaimType, DetectionResult, Shipment, ShipmentStatus


def detect(shipments: list[Shipment], today: date | None = None) -> list[DetectionResult]:
    today = today or date.today()
    results: list[DetectionResult] = []

    for s in shipments:
        # Rule 1: explicit damage flag
        if s.status == ShipmentStatus.DAMAGED:
            results.append(DetectionResult(shipment=s, claim_type=ClaimType.DAMAGE, rule="explicit_damage_flag"))
            continue

        if s.status == ShipmentStatus.DELIVERED:
            continue

        # Rule 2: label created but never scanned into the network
        if s.status == ShipmentStatus.NO_SCAN and s.last_scan_at is None:
            if (today - s.shipped_at).days >= settings.no_scan_days:
                results.append(DetectionResult(shipment=s, claim_type=ClaimType.LOSS, rule="never_scanned"))
            continue

        # Rule 3: tracking went dark mid-journey
        if s.last_scan_at and (today - s.last_scan_at).days >= settings.no_scan_days:
            results.append(DetectionResult(shipment=s, claim_type=ClaimType.LOSS, rule="tracking_dead_end"))
            continue

    return results


def claim_deadline(shipped_at: date) -> date:
    """Couriers time-bar claims. Expiring claims are surfaced on the dashboard."""
    return shipped_at + timedelta(days=settings.claim_window_days)
