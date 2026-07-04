"""The courier fight — ingest a claim-outcomes export and drive the state machine.

Couriers respond to bulk claims via portal exports / response files, one row
per claim: paid, declined, or "more information required". Statuses map to
state transitions; nothing moves through an undefined path:

    paid            FILED -> PAID          (bank payout still reconciles it)
    declined        FILED -> REJECTED      (write-off, visible per client)
    info_requested  FILED -> EVIDENCE_REQUESTED -> FILED   (auto-resubmit,
                    up to MAX_RESUBMISSIONS rounds, then REJECTED)
"""
from __future__ import annotations

import csv
from pathlib import Path

from .db import Register
from .models import Claim, ClaimStatus

MAX_RESUBMISSIONS = 2

OUTCOMES = {"paid", "declined", "info_requested"}


def apply_outcome(claim: Claim, outcome: str) -> Claim:
    """Pure transition logic — returns the updated claim, raises on bad input."""
    if outcome not in OUTCOMES:
        raise ValueError(f"Unknown outcome {outcome!r} (expected one of {sorted(OUTCOMES)})")
    if outcome == "paid":
        return claim.transition(ClaimStatus.PAID)
    if outcome == "declined":
        return claim.transition(ClaimStatus.REJECTED).model_copy(update={
            "notes": claim.notes + "; declined by courier",
        })
    # info_requested — the fight loop
    evidence = claim.transition(ClaimStatus.EVIDENCE_REQUESTED)
    if claim.resubmissions >= MAX_RESUBMISSIONS:
        return evidence.transition(ClaimStatus.REJECTED).model_copy(update={
            "notes": claim.notes + f"; gave up after {claim.resubmissions} resubmissions",
        })
    return evidence.transition(ClaimStatus.FILED).model_copy(update={
        "resubmissions": claim.resubmissions + 1,
        "notes": claim.notes + f"; evidence resubmitted (round {claim.resubmissions + 1})",
    })


def ingest_outcomes(path: str | Path, register: Register) -> list[tuple[str, str, str]]:
    """Apply an outcomes CSV (tracking_number,outcome) to the register.

    Returns [(tracking, outcome, resulting_status)] for reporting; rows for
    unknown tracking numbers are reported as 'unknown_claim' and skipped.
    """
    results: list[tuple[str, str, str]] = []
    with Path(path).open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            tracking = (row.get("tracking_number") or row.get("tracking") or "").strip()
            outcome = (row.get("outcome") or "").strip().lower()
            claim = register.get(tracking) if tracking else None
            if claim is None:
                results.append((tracking, outcome, "unknown_claim"))
                continue
            updated = apply_outcome(claim, outcome)
            register.upsert(updated)
            results.append((tracking, outcome, updated.status.value))
    return results
