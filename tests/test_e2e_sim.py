"""Simulated end-to-end run: ingest -> detect -> match -> pack -> file ->
reconcile -> dashboard, against a FakeXero. No network, tmp_path DB.

The numbers are the demo story: 7 claimable shipments, £157.34 recoverable,
4 payouts land (£91.35), one ambiguous payout is surfaced for a human.
"""
from datetime import date
from decimal import Decimal
from pathlib import Path

from claimback.couriers import get_adapter
from claimback.dashboard import compute
from claimback.db import Register
from claimback.detect import detect
from claimback.ingest import ingest_csv
from claimback.models import ClaimStatus
from claimback.xero.matching import match_claims, reconcile_payouts

ROOT = Path(__file__).parent.parent
CSV = ROOT / "data" / "demo_shipments.csv"
TODAY = date(2026, 7, 2)  # the date the demo data was authored for
CEILING = Decimal("25")


def new_detections(register: Register) -> list:
    detections = detect(ingest_csv(CSV), today=TODAY)
    return [d for d in detections if not register.exists(d.shipment.tracking_number)]


def test_end_to_end_simulation(tmp_path, fake_xero):
    register = Register(str(tmp_path / "e2e.db"))

    # --- detect + match: 7 claims, values from Xero, capped at the ceiling ---
    claims, unmatched = match_claims(fake_xero, new_detections(register))
    assert len(claims) == 7
    assert unmatched == []
    assert all(c.status == ClaimStatus.MATCHED for c in claims)
    assert sum(c.claim_value for c in claims) == Decimal("157.34")

    capped = [c for c in claims if c.invoice_total > CEILING]
    assert len(capped) == 3
    assert all(c.claim_value == CEILING for c in capped)
    uncapped = [c for c in claims if c.invoice_total <= CEILING]
    assert all(c.claim_value == c.invoice_total for c in uncapped)

    # --- pack: byte discipline, pence intact ---
    adapter = get_adapter("swiftship")
    ready = [c.transition(ClaimStatus.READY) for c in claims]
    pack = adapter.generate_pack(ready)
    assert not pack.startswith(b"\xef\xbb\xbf")  # no BOM
    assert pack.endswith(b"\r\n")                # trailing CRLF
    assert b'"' not in pack
    assert b"18.40" in pack                      # claim value never truncated
    for c in ready:
        register.upsert(c)

    # --- dedupe: a second run finds nothing new ---
    assert new_detections(register) == []

    # --- file: READY -> FILED, receivable posted (to the fake) ---
    for c in register.by_status(ClaimStatus.READY):
        fake_xero.create_claim_receivable(c.courier, c.tracking_number, c.claim_value)
        register.upsert(c.transition(ClaimStatus.FILED))
    filed = register.by_status(ClaimStatus.FILED)
    assert len(filed) == 7
    assert len(fake_xero.receivables) == 7

    # --- reconcile: 4 payouts matched, the ambiguous one surfaced, not applied ---
    matches, ambiguous = reconcile_payouts(fake_xero, filed)
    assert len(matches) == 4
    assert sum(amount for _, amount in matches) == Decimal("91.35")

    assert len(ambiguous) == 1
    amb = ambiguous[0]
    assert "REF-UNKNOWN-77" in amb["reference"]
    assert Decimal(amb["amount"]) == Decimal("25")
    assert len(amb["candidates"]) == 2
    matched_tracking = {c.tracking_number for c, _ in matches}
    assert not set(amb["candidates"]) & matched_tracking  # never auto-applied

    for claim, amount in matches:
        paid = claim.transition(ClaimStatus.PAID).model_copy(update={"payout_value": amount})
        register.upsert(paid.transition(ClaimStatus.RECONCILED))

    # --- dashboard: the money view ---
    numbers = compute(register, today=TODAY)
    assert numbers["recovered"] == Decimal("91.35")
    assert numbers["pending"] == Decimal("65.99")
    assert len(numbers["claims"]) == 7
