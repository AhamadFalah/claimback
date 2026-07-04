"""Seed the Xero Demo Company with invoices matching data/demo_shipments.csv.

Run once after `claimback auth` (connect to the DEMO COMPANY, not a real org):

    python scripts/seed_xero.py

Creates one ACCREC invoice per demo order ref so shipment↔invoice matching
works end-to-end in the demo.
"""
from __future__ import annotations

import csv
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from claimback.xero.client import XeroClient  # noqa: E402

random.seed(42)

def main() -> None:
    client = XeroClient()
    rows = list(csv.DictReader(open(Path(__file__).parent.parent / "data" / "demo_shipments.csv")))
    for row in rows:
        ref = row["Order Number"]
        existing = client.find_invoice_by_reference(ref)
        if existing:
            print(f"{ref}: already exists, skipping")
            continue
        contact = client.get_or_create_contact(row["Customer"])
        value = round(random.uniform(12, 85), 2)  # mix of below/above the £25 ceiling
        invoice = {
            "Type": "ACCREC",
            "Contact": {"ContactID": contact["ContactID"]},
            "Reference": ref,
            "InvoiceNumber": ref,
            "LineAmountTypes": "Inclusive",  # UK demo org applies 20% VAT — keep totals = the seeded value
            "LineItems": [{
                "Description": "Online order",
                "Quantity": 1,
                "UnitAmount": value,
                "AccountCode": "200",
            }],
            "Status": "AUTHORISED",
        }
        created = client.create_invoice(invoice)
        print(f"{ref}: created invoice {created['InvoiceID']} £{value}")


if __name__ == "__main__":
    main()
