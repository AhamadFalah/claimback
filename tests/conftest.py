"""Shared test doubles. No test in this suite may touch the real Xero API."""
from __future__ import annotations

import pytest

# Invoice totals for the demo order refs (what the seeded demo org would hold).
INVOICES: dict[str, float] = {
    "INV-1003": 18.40,
    "INV-1005": 42.00,
    "INV-1006": 23.75,
    "INV-1007": 61.20,
    "INV-1009": 15.99,
    "INV-1011": 30.50,
    "INV-1012": 24.20,
}

# Bank feed: payouts for 4 of the 7 claims, one deliberately ambiguous payout
# (no tracking ref, ceiling-value amount matching several open claims), and a
# SPEND that reconciliation must ignore.
BANK_TRANSACTIONS: list[dict] = [
    {"Type": "RECEIVE", "Reference": "SWIFTSHIP COMP CLAIM-SW1000000003",
     "Total": 18.40, "Contact": {"Name": "SwiftShip (Claims)"}},
    {"Type": "RECEIVE", "Reference": "SWIFTSHIP COMP CLAIM-SW1000000006",
     "Total": 23.75, "Contact": {"Name": "SwiftShip (Claims)"}},
    {"Type": "RECEIVE", "Reference": "SWIFTSHIP COMP CLAIM-SW1000000007",
     "Total": 25.00, "Contact": {"Name": "SwiftShip (Claims)"}},
    {"Type": "RECEIVE", "Reference": "SWIFTSHIP COMP CLAIM-SW1000000012",
     "Total": 24.20, "Contact": {"Name": "SwiftShip (Claims)"}},
    {"Type": "RECEIVE", "Reference": "SWIFTSHIP COMP REF-UNKNOWN-77",
     "Total": 25.00, "Contact": {"Name": "SwiftShip (Claims)"}},
    {"Type": "SPEND", "Reference": "OFFICE SUPPLIES",
     "Total": 12.00, "Contact": {"Name": "Staples"}},
]


class FakeXero:
    """Stands in for XeroClient — only the methods the pipeline calls.

    Writes are recorded at class level so tests can inspect them even when
    the endpoint under test constructs its own instance.
    """

    receivables: list[tuple[str, object]] = []
    payments: list[tuple[str, object]] = []
    attachments: list[tuple[str, str, int]] = []

    @classmethod
    def reset(cls):
        cls.receivables, cls.payments, cls.attachments = [], [], []

    def find_invoice_by_reference(self, reference: str):
        if reference in INVOICES:
            return {"InvoiceID": f"xero-{reference}", "Total": INVOICES[reference]}
        return None

    def list_bank_transactions(self, page: int = 1) -> list[dict]:
        return [dict(tx) for tx in BANK_TRANSACTIONS]

    def create_claim_receivable(self, courier_name: str, tracking_number: str, value):
        FakeXero.receivables.append((tracking_number, value))
        return {"InvoiceID": f"rcv-{tracking_number}"}

    def apply_payment(self, invoice_id: str, amount, account_code=None):
        FakeXero.payments.append((invoice_id, amount))
        return {"PaymentID": f"pay-{len(FakeXero.payments)}"}

    def attach_file_to_invoice(self, invoice_id: str, filename: str, content: bytes):
        FakeXero.attachments.append((invoice_id, filename, len(content)))
        return {}


@pytest.fixture
def fake_xero() -> FakeXero:
    FakeXero.reset()
    return FakeXero()


@pytest.fixture
def fake_xero_class():
    FakeXero.reset()
    return FakeXero
