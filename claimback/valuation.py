"""Turn detections into valued claims — or explicit refusals.

3PL model: the parcel's worth comes from the client's DECLARED VALUE in the
WMS export; the claimable amount is min(declared value, the courier's
channel-specific ceiling). Xero is the money ledger (receivable, payout,
client credit note) — it is not the price list.

A detection whose claim type isn't covered by the courier's channel rules
(e.g. damage on standard Evri) becomes a REJECTED claim immediately: visible
write-off exposure, never a doomed submission.
"""
from __future__ import annotations

from .couriers import adapter_for
from .detect import claim_deadline
from .models import Claim, ClaimStatus, DetectionResult


def value_claims(detections: list[DetectionResult]) -> tuple[list[Claim], list[Claim]]:
    """Returns (claims valued and MATCHED, refusals recorded as REJECTED)."""
    claims: list[Claim] = []
    refusals: list[Claim] = []
    for d in detections:
        s = d.shipment
        adapter = adapter_for(s.courier, s.channel)
        base = Claim(
            tracking_number=s.tracking_number,
            courier=s.courier,
            claim_type=d.claim_type,
            order_ref=s.order_ref,
            client=s.client,
            channel=s.channel,
            declared_value=s.declared_value,
            deadline=claim_deadline(s.shipped_at),
            notes=f"rule={d.rule}",
        )
        if d.claim_type not in adapter.eligible_types:
            refusals.append(base.transition(ClaimStatus.REJECTED).model_copy(update={
                "notes": base.notes + f"; not claimable: {d.claim_type.value} not covered by {adapter.name}",
            }))
            continue
        if s.declared_value is None:
            refusals.append(base.transition(ClaimStatus.REJECTED).model_copy(update={
                "notes": base.notes + "; no declared value on the export — cannot value the claim",
            }))
            continue
        claims.append(base.model_copy(update={
            "claim_value": adapter.claim_value(s.declared_value),
        }).transition(ClaimStatus.MATCHED))
    return claims, refusals
