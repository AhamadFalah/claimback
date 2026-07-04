"""Domain models and the claim state machine.

Design principle (learned the hard way in production claims work):
anything that touches money is DETERMINISTIC code with tests.
AI/agents sit around the edges — interpreting messy data, deciding
what to do next — never generating the financial artefacts themselves.
"""
from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field


class ShipmentStatus(str, enum.Enum):
    IN_TRANSIT = "in_transit"
    DELIVERED = "delivered"
    DAMAGED = "damaged"
    NO_SCAN = "no_scan"          # tracking dead-end
    RETURNED = "returned"


class ClaimStatus(str, enum.Enum):
    """State machine: DETECTED -> MATCHED -> READY -> FILED -> (PAID | REJECTED | EVIDENCE_REQUESTED) -> RECONCILED"""
    DETECTED = "detected"                  # rule engine flagged shipment as claimable
    MATCHED = "matched"                    # matched to a Xero invoice, value established
    READY = "ready"                        # claim pack generated & validated
    FILED = "filed"                        # submitted to the courier
    EVIDENCE_REQUESTED = "evidence_requested"  # courier wants more info (resubmission loop)
    REJECTED = "rejected"
    PAID = "paid"                          # payout received
    RECONCILED = "reconciled"              # payout matched to Xero bank transaction


# Allowed transitions — anything else is a bug, not a judgement call.
TRANSITIONS: dict[ClaimStatus, set[ClaimStatus]] = {
    ClaimStatus.DETECTED: {ClaimStatus.MATCHED, ClaimStatus.REJECTED},
    ClaimStatus.MATCHED: {ClaimStatus.READY, ClaimStatus.REJECTED},
    ClaimStatus.READY: {ClaimStatus.FILED},
    ClaimStatus.FILED: {ClaimStatus.PAID, ClaimStatus.REJECTED, ClaimStatus.EVIDENCE_REQUESTED},
    ClaimStatus.EVIDENCE_REQUESTED: {ClaimStatus.FILED, ClaimStatus.REJECTED},
    ClaimStatus.PAID: {ClaimStatus.RECONCILED},
    ClaimStatus.REJECTED: set(),
    ClaimStatus.RECONCILED: set(),
}


class InvalidTransition(Exception):
    pass


def assert_transition(current: ClaimStatus, new: ClaimStatus) -> None:
    if new not in TRANSITIONS[current]:
        raise InvalidTransition(f"{current.value} -> {new.value} is not a legal claim transition")


class Shipment(BaseModel):
    tracking_number: str
    courier: str
    order_ref: str                       # links to the Xero invoice reference
    shipped_at: date
    last_scan_at: Optional[date] = None
    status: ShipmentStatus = ShipmentStatus.IN_TRANSIT
    postcode: str = ""
    recipient: str = ""


class ClaimType(str, enum.Enum):
    LOSS = "loss"
    DAMAGE = "damage"


class Claim(BaseModel):
    tracking_number: str
    courier: str
    claim_type: ClaimType
    status: ClaimStatus = ClaimStatus.DETECTED
    order_ref: str = ""
    xero_invoice_id: Optional[str] = None
    invoice_total: Optional[Decimal] = None
    claim_value: Optional[Decimal] = None    # min(invoice_total, courier ceiling) — enforced, never clamped silently
    deadline: Optional[date] = None          # couriers time-bar claims; expiring money is lost money
    filed_at: Optional[datetime] = None
    payout_value: Optional[Decimal] = None
    xero_receivable_id: Optional[str] = None  # the CLAIM-<tracking> ACCREC posted on filing
    xero_payment_id: Optional[str] = None
    notes: str = ""

    def transition(self, new: ClaimStatus) -> "Claim":
        assert_transition(self.status, new)
        return self.model_copy(update={"status": new})


class DetectionResult(BaseModel):
    shipment: Shipment
    claim_type: ClaimType
    rule: str = Field(description="Which detection rule fired — every claim is explainable")
