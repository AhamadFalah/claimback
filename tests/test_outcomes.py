"""The courier fight: outcome ingestion drives only legal state transitions."""
import pytest

from claimback.models import Claim, ClaimStatus, ClaimType
from claimback.outcomes import MAX_RESUBMISSIONS, apply_outcome


def filed_claim(resubmissions: int = 0) -> Claim:
    return Claim(tracking_number="EV1", courier="evri", claim_type=ClaimType.LOSS,
                 status=ClaimStatus.FILED, resubmissions=resubmissions)


def test_paid_moves_to_paid():
    assert apply_outcome(filed_claim(), "paid").status == ClaimStatus.PAID


def test_declined_is_a_write_off():
    updated = apply_outcome(filed_claim(), "declined")
    assert updated.status == ClaimStatus.REJECTED
    assert "declined by courier" in updated.notes


def test_info_requested_resubmits_and_counts_rounds():
    updated = apply_outcome(filed_claim(), "info_requested")
    assert updated.status == ClaimStatus.FILED       # back in the fight
    assert updated.resubmissions == 1


def test_fight_gives_up_after_max_rounds():
    exhausted = filed_claim(resubmissions=MAX_RESUBMISSIONS)
    updated = apply_outcome(exhausted, "info_requested")
    assert updated.status == ClaimStatus.REJECTED
    assert "gave up" in updated.notes


def test_unknown_outcome_raises():
    with pytest.raises(ValueError):
        apply_outcome(filed_claim(), "maybe")


def test_outcome_on_terminal_claim_raises():
    rejected = filed_claim().transition(ClaimStatus.REJECTED)
    with pytest.raises(Exception):                    # InvalidTransition — law, not judgement
        apply_outcome(rejected, "paid")
