"""Payout ↔ claim reconciliation against the Xero bank feed.

Valuation moved to claimback.valuation — in the 3PL model Xero is the money
ledger (receivables, payouts, client credit notes), not the price list.
"""
from __future__ import annotations

from decimal import Decimal

from ..models import Claim
from .client import XeroClient


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
