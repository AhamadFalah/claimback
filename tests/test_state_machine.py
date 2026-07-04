import pytest

from claimback.models import Claim, ClaimStatus, ClaimType, InvalidTransition


def claim() -> Claim:
    return Claim(tracking_number="SW1", courier="swiftship", claim_type=ClaimType.LOSS)


def test_happy_path():
    c = claim()
    for status in (ClaimStatus.MATCHED, ClaimStatus.READY, ClaimStatus.FILED,
                   ClaimStatus.PAID, ClaimStatus.RECONCILED):
        c = c.transition(status)
    assert c.status == ClaimStatus.RECONCILED


def test_evidence_loop():
    c = claim().transition(ClaimStatus.MATCHED).transition(ClaimStatus.READY).transition(ClaimStatus.FILED)
    c = c.transition(ClaimStatus.EVIDENCE_REQUESTED).transition(ClaimStatus.FILED)
    assert c.status == ClaimStatus.FILED


def test_illegal_transitions_raise():
    with pytest.raises(InvalidTransition):
        claim().transition(ClaimStatus.PAID)          # can't be paid before filing
    with pytest.raises(InvalidTransition):
        claim().transition(ClaimStatus.MATCHED).transition(ClaimStatus.FILED)  # must be READY first
