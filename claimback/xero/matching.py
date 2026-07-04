"""Shipment ↔ Xero invoice matching, and payout ↔ claim reconciliation.

Conflict rule: the ACCOUNTING SYSTEM WINS. If the shipment file and the
Xero invoice disagree (value, customer), the submission uses Xero data
and the conflict is logged — never silently merged.
"""
from __future__ import annotations

from decimal import Decimal

from ..couriers import get_adapter
from ..detect import claim_deadline
from ..models import Claim, ClaimStatus, DetectionResult
from .client import XeroClient


def match_claims(client: XeroClient, detections: list[DetectionResult]) -> tuple[list[Claim], list[str]]:
    """Turn detections into MATCHED claims with values from Xero."""
    claims: list[Claim] = []
    unmatched: list[str] = []
    for d in detections:
        s = d.shipment
        invoice = client.find_invoice_by_reference(s.order_ref)
        if invoice is None:
            unmatched.append(f"{s.tracking_number}: no Xero invoice for ref {s.order_ref!r}")
            continue
        adapter = get_adapter(s.courier)
        if d.claim_type not in adapter.eligible_types:
            unmatched.append(f"{s.tracking_number}: {d.claim_type.value} not covered by {adapter.name}")
            continue
        # Xero JSON gives floats; quantise to pence at the money boundary.
        total = Decimal(str(invoice.get("Total", 0))).quantize(Decimal("0.01"))
        claim = Claim(
            tracking_number=s.tracking_number,
            courier=s.courier,
            claim_type=d.claim_type,
            order_ref=s.order_ref,
            xero_invoice_id=invoice["InvoiceID"],
            invoice_total=total,
            claim_value=adapter.claim_value(total),
            deadline=claim_deadline(s.shipped_at),
            notes=f"rule={d.rule}",
        ).transition(ClaimStatus.MATCHED)
        claims.append(claim)
    return claims, unmatched


def reconcile_payouts(
    client: XeroClient, filed: list[Claim]
) -> tuple[list[tuple[Claim, Decimal]], list[dict]]:
    """Match courier payouts in the bank feed to open claims.

    Heuristics: reference contains the tracking number (covers CLAIM-<tracking>);
    else courier-name payer + exact claim value match, but only when exactly one
    open claim fits. Ambiguous matches (several candidates) are returned in the
    second element for a human to resolve — never auto-applied.
    """
    def tx_amount(tx: dict) -> Decimal:
        # Xero JSON gives floats; quantise to pence at the money boundary.
        return Decimal(str(tx.get("Total", 0))).quantize(Decimal("0.01"))

    matches: list[tuple[Claim, Decimal]] = []
    ambiguous: list[dict] = []
    transactions = client.list_bank_transactions()
    open_by_tracking = {c.tracking_number: c for c in filed}
    unmatched_txs: list[dict] = []
    for tx in transactions:
        if tx.get("Type") != "RECEIVE":
            continue
        ref = (tx.get("Reference") or "") + " " + (tx.get("Contact", {}).get("Name") or "")
        for tracking, claim in list(open_by_tracking.items()):
            if tracking in ref:
                matches.append((claim, tx_amount(tx)))
                del open_by_tracking[tracking]
                break
        else:
            unmatched_txs.append(tx)

    # Second pass: payer looks like the courier + exact claim-value match.
    for tx in unmatched_txs:
        amount = tx_amount(tx)
        payer = ((tx.get("Reference") or "") + " " + (tx.get("Contact", {}).get("Name") or "")).lower()
        candidates = [c for c in open_by_tracking.values()
                      if c.claim_value == amount and c.courier.lower() in payer]
        if len(candidates) == 1:
            claim = candidates[0]
            matches.append((claim, amount))
            del open_by_tracking[claim.tracking_number]
        elif candidates:
            ambiguous.append({
                "reference": tx.get("Reference") or "",
                "amount": str(amount),
                "candidates": [c.tracking_number for c in candidates],
            })
    return matches, ambiguous
