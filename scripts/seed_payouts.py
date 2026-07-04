"""Simulate Evri compensation landing in the demo org's bank feed.

Run AFTER `claimback file evri` has posted the claim receivables:

    python scripts/seed_payouts.py

Creates RECEIVE bank transactions referencing CLAIM-<tracking> so that
`claimback reconcile` / POST /reconcile has real payouts to match.
Pays 4 specific demo claims (leaves the two £25 claims in flight so the
ambiguous-payout beat works), plus one deliberately ambiguous payout to
show the human-review path.
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from claimback.config import settings          # noqa: E402
from claimback.db import Register              # noqa: E402
from claimback.models import ClaimStatus       # noqa: E402
from claimback.xero.client import XeroClient   # noqa: E402

# The demo story pays these four; EV...005 and EV...008 (both £25) stay open
# so the ambiguous £25 payout below has two candidates and goes to a human.
PAID_TRACKING = {"EV1000000003", "EV1000000006", "EV1000000007", "EV1000000012"}


def main() -> None:
    client = XeroClient()
    bank = client.find_bank_account()
    if bank is None:
        sys.exit("No BANK account in this org — check GET /Accounts")
    print(f"Using bank account: {bank['Name']} ({bank.get('Code', '?')})")

    open_claims = Register(settings.db_path).by_status(ClaimStatus.FILED, ClaimStatus.PAID)
    payable = [c for c in open_claims if c.tracking_number in PAID_TRACKING]
    if not payable:
        sys.exit("No matching FILED claims — run the pipeline and `claimback file evri` first (DRY_RUN=false)")

    for claim in payable:
        ref = f"CLAIM-{claim.tracking_number}"
        tx = client.create_bank_transaction(
            reference=f"EVRI COMP {ref}",
            amount=claim.claim_value or Decimal("0"),
            contact_name="Evri (Claims)",
            bank_account_id=bank["AccountID"],
        )
        print(f"payout £{claim.claim_value} -> {ref} (tx {tx['BankTransactionID']})")

    # One ambiguous payout: no tracking ref, ceiling-value amount.
    tx = client.create_bank_transaction(
        reference="EVRI COMP REF-UNKNOWN-77",
        amount=Decimal("25.00"),
        contact_name="Evri (Claims)",
        bank_account_id=bank["AccountID"],
    )
    print(f"ambiguous payout £25.00 (tx {tx['BankTransactionID']}) — should surface for human review")


if __name__ == "__main__":
    main()
