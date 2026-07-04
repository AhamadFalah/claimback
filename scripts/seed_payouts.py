"""Simulate courier compensation landing in the demo org's bank feed.

Run AFTER `claimback file swiftship` has posted the claim receivables:

    python scripts/seed_payouts.py

Creates RECEIVE bank transactions referencing CLAIM-<tracking> so that
`claimback reconcile` / POST /reconcile has real payouts to match.
Pays 4 of the 7 demo claims (leaves 3 in flight — better demo story),
plus one deliberately ambiguous payout to show the human-review path.
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

PAY_FIRST_N = 4


def main() -> None:
    client = XeroClient()
    bank = client.find_bank_account()
    if bank is None:
        sys.exit("No BANK account in this org — check GET /Accounts")
    print(f"Using bank account: {bank['Name']} ({bank['Code']})")

    filed = Register(settings.db_path).by_status(ClaimStatus.FILED)
    if not filed:
        sys.exit("No FILED claims — run the pipeline and `claimback file swiftship` first (DRY_RUN=false)")

    for claim in filed[:PAY_FIRST_N]:
        ref = f"CLAIM-{claim.tracking_number}"
        tx = client.create_bank_transaction(
            reference=f"SWIFTSHIP COMP {ref}",
            amount=claim.claim_value or Decimal("0"),
            contact_name="SwiftShip (Claims)",
            bank_account_id=bank["AccountID"],
        )
        print(f"payout £{claim.claim_value} -> {ref} (tx {tx['BankTransactionID']})")

    # One ambiguous payout: no tracking ref, ceiling-value amount.
    tx = client.create_bank_transaction(
        reference="SWIFTSHIP COMP REF-UNKNOWN-77",
        amount=Decimal("25.00"),
        contact_name="SwiftShip (Claims)",
        bank_account_id=bank["AccountID"],
    )
    print(f"ambiguous payout £25.00 (tx {tx['BankTransactionID']}) — should surface for human review")


if __name__ == "__main__":
    main()
