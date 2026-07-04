"""Seed the Xero Demo Company with the 3PL's client accounts.

Run once after `claimback auth` (connect to the DEMO COMPANY, not a real org):

    python scripts/seed_xero.py

Creates each client brand as a Contact with one AUTHORISED monthly fulfilment
invoice — so when ClaimBack raises claim credit notes, there's a real client
account (and an invoice to allocate the credit against) in the org.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from claimback.xero.client import XeroClient  # noqa: E402

# Fictional demo brands (see data/demo_shipments.csv) and their June fulfilment bills.
CLIENTS = {
    "OatSnax": 480.00,
    "ChocoLoco": 1240.50,
    "Brew & Bean": 655.25,
}


def main() -> None:
    client = XeroClient()
    for name, monthly_total in CLIENTS.items():
        contact = client.get_or_create_contact(name)
        ref = f"FUL-2026-06-{name.replace(' ', '').replace('&', 'and')}"
        existing = client.find_invoice_by_reference(ref)
        if existing:
            print(f"{name}: fulfilment invoice already exists, skipping")
            continue
        invoice = {
            "Type": "ACCREC",
            "Contact": {"ContactID": contact["ContactID"]},
            "Reference": ref,
            "InvoiceNumber": ref,
            "LineAmountTypes": "Exclusive",
            "LineItems": [{
                "Description": "Fulfilment services — June 2026 (pick/pack, storage, despatch)",
                "Quantity": 1,
                "UnitAmount": monthly_total,
                "AccountCode": "200",
            }],
            "Status": "AUTHORISED",
        }
        created = client.create_invoice(invoice)
        print(f"{name}: contact + fulfilment invoice {created['InvoiceID']} £{monthly_total}")


if __name__ == "__main__":
    main()
