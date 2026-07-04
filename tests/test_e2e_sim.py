"""Simulated end-to-end 3PL run: ingest -> detect -> value -> pack -> file ->
outcomes (the courier fight) -> reconcile -> credit notes -> dashboard,
against a FakeXero. No network, tmp_path DB.

The demo story in numbers: 12 shipments, 8 flagged, 7 claims worth £148.59
(1 unwinnable damage refused = £30.50 exposure), courier declines one
(£15.99 write-off) and demands evidence on another (auto-resubmitted),
4 payouts land (£82.60) and pass through to clients as credit notes.
"""
from datetime import date
from decimal import Decimal
from pathlib import Path

from claimback.couriers import adapter_for
from claimback.dashboard import compute
from claimback.db import Register
from claimback.detect import detect
from claimback.ingest import ingest_csv
from claimback.models import ClaimStatus
from claimback.outcomes import ingest_outcomes
from claimback.valuation import value_claims
from claimback.xero.matching import reconcile_payouts

ROOT = Path(__file__).parent.parent
CSV = ROOT / "data" / "demo_shipments.csv"
OUTCOMES_CSV = ROOT / "data" / "demo_outcomes.csv"
TODAY = date(2026, 7, 2)  # the date the demo data was authored for


def new_detections(register: Register) -> list:
    detections = detect(ingest_csv(CSV), today=TODAY)
    return [d for d in detections if not register.exists(d.shipment.tracking_number)]


def test_end_to_end_simulation(tmp_path, fake_xero):
    register = Register(str(tmp_path / "e2e.db"))

    # --- detect + value: 8 flagged, 7 claims, 1 refusal (unwinnable damage) ---
    detections = new_detections(register)
    assert len(detections) == 8
    claims, refusals = value_claims(detections)
    assert len(claims) == 7
    assert sum(c.claim_value for c in claims) == Decimal("148.59")

    assert len(refusals) == 1
    refused = refusals[0]
    assert refused.tracking_number == "EV1000000011"   # damage on standard Evri
    assert refused.status == ClaimStatus.REJECTED
    assert refused.declared_value == Decimal("30.50")  # visible exposure, not a doomed claim
    register.upsert(refused)

    # channel rules applied: amazon capped at £20, standard at £25
    by_tracking = {c.tracking_number: c for c in claims}
    assert by_tracking["EV1000000006"].claim_value == Decimal("20")   # amazon damage
    assert by_tracking["EV1000000007"].claim_value == Decimal("20")   # amazon loss, £61.20 declared
    assert by_tracking["EV1000000005"].claim_value == Decimal("25")   # standard, £42 declared
    assert by_tracking["EV1000000003"].claim_value == Decimal("18.40")  # under ceiling: exact pence

    # --- packs: one per (courier, channel) rule set, byte discipline held ---
    by_adapter: dict[str, list] = {}
    for c in claims:
        by_adapter.setdefault(adapter_for(c.courier, c.channel).name, []).append(c)
    assert {k: len(v) for k, v in by_adapter.items()} == {"evri": 5, "evri:amazon": 2}
    for adapter_name, batch in by_adapter.items():
        ready = [c.transition(ClaimStatus.READY) for c in batch]
        pack = adapter_for(batch[0].courier, batch[0].channel).generate_pack(ready)
        assert not pack.startswith(b"\xef\xbb\xbf")
        assert pack.endswith(b"\r\n")
        for c in ready:
            register.upsert(c)

    # --- dedupe: a second run finds nothing new (refusal included) ---
    assert new_detections(register) == []

    # --- file: READY -> FILED, receivable posted (to the fake) ---
    for c in register.by_status(ClaimStatus.READY):
        inv = fake_xero.create_claim_receivable(c.courier, c.tracking_number, c.claim_value)
        register.upsert(c.transition(ClaimStatus.FILED).model_copy(
            update={"xero_receivable_id": inv["InvoiceID"]}))
    assert len(register.by_status(ClaimStatus.FILED)) == 7

    # --- the courier fight: one declined, one evidence loop ---
    results = dict((t, s) for t, _, s in ingest_outcomes(OUTCOMES_CSV, register))
    assert results["EV1000000009"] == "rejected"       # write-off, visible per client
    assert results["EV1000000005"] == "filed"          # auto-resubmitted, round 1
    assert register.get("EV1000000005").resubmissions == 1
    assert len(register.by_status(ClaimStatus.FILED)) == 6

    # --- reconcile: 4 payouts, ambiguous one surfaced, never applied ---
    open_claims = register.by_status(ClaimStatus.FILED, ClaimStatus.PAID)
    matches, ambiguous = reconcile_payouts(fake_xero, open_claims)
    assert len(matches) == 4
    assert sum(amount for _, amount in matches) == Decimal("82.60")

    assert len(ambiguous) == 1
    amb = ambiguous[0]
    assert "REF-UNKNOWN-77" in amb["reference"]
    assert sorted(amb["candidates"]) == ["EV1000000005", "EV1000000008"]  # both £25, both open

    # --- pass-through: each reconciled claim raises a client credit note ---
    for claim, amount in matches:
        paid = claim.transition(ClaimStatus.PAID).model_copy(update={"payout_value": amount})
        note = fake_xero.create_claim_credit_note(claim.client, claim.tracking_number, amount)
        register.upsert(paid.transition(ClaimStatus.RECONCILED).model_copy(
            update={"xero_credit_note_id": note["CreditNoteID"]}))

    credited: dict[str, Decimal] = {}
    for client_name, _, amount in fake_xero.credit_notes:
        credited[client_name] = credited.get(client_name, Decimal(0)) + amount
    assert credited == {
        "OatSnax": Decimal("42.60"),        # 18.40 + 24.20
        "ChocoLoco": Decimal("20.00"),
        "Brew & Bean": Decimal("20.00"),
    }

    # --- dashboard: the money view, split by client ---
    numbers = compute(register, today=TODAY)
    assert numbers["recovered"] == Decimal("82.60")
    assert numbers["pending"] == Decimal("50.00")       # the two £25 claims still in flight
    assert numbers["written_off"] == Decimal("46.49")   # declined 15.99 + unclaimable 30.50
    assert numbers["by_client"]["OatSnax"]["recovered"] == Decimal("42.60")
    assert numbers["by_client"]["ChocoLoco"]["written_off"] == Decimal("30.50")
    assert numbers["by_client"]["OatSnax"]["written_off"] == Decimal("15.99")
