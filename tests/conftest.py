"""Shared test doubles. No test in this suite may touch the real Xero API."""
from __future__ import annotations

import pytest

# Bank feed: payouts for 4 of the open claims, one deliberately ambiguous payout
# (no tracking ref, ceiling-value amount matching several open claims), and a
# SPEND that reconciliation must ignore.
BANK_TRANSACTIONS: list[dict] = [
    {"Type": "RECEIVE", "Reference": "EVRI COMP CLAIM-EV1000000003",
     "Total": 18.40, "Contact": {"Name": "Evri (Claims)"}},
    {"Type": "RECEIVE", "Reference": "EVRI COMP CLAIM-EV1000000006",
     "Total": 20.00, "Contact": {"Name": "Evri (Claims)"}},
    {"Type": "RECEIVE", "Reference": "EVRI COMP CLAIM-EV1000000007",
     "Total": 20.00, "Contact": {"Name": "Evri (Claims)"}},
    {"Type": "RECEIVE", "Reference": "EVRI COMP CLAIM-EV1000000012",
     "Total": 24.20, "Contact": {"Name": "Evri (Claims)"}},
    {"Type": "RECEIVE", "Reference": "EVRI COMP REF-UNKNOWN-77",
     "Total": 25.00, "Contact": {"Name": "Evri (Claims)"}},
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
    credit_notes: list[tuple[str, str, object]] = []
    authorised: list[str] = []
    allocations: list[tuple[str, str, object]] = []

    @classmethod
    def reset(cls):
        cls.receivables, cls.payments, cls.attachments = [], [], []
        cls.credit_notes, cls.authorised, cls.allocations = [], [], []

    def list_bank_transactions(self, page: int = 1) -> list[dict]:
        return [dict(tx) for tx in BANK_TRANSACTIONS]

    def create_claim_receivable(self, courier_name: str, tracking_number: str, value):
        FakeXero.receivables.append((tracking_number, value))
        return {"InvoiceID": f"rcv-{tracking_number}", "Status": "DRAFT"}

    def authorise_invoice(self, invoice_id: str):
        FakeXero.authorised.append(invoice_id)
        return {"InvoiceID": invoice_id, "Status": "AUTHORISED"}

    def apply_payment(self, invoice_id: str, amount, account_code=None):
        assert invoice_id in FakeXero.authorised, "payment applied to unauthorised (DRAFT) receivable"
        FakeXero.payments.append((invoice_id, amount))
        return {"PaymentID": f"pay-{len(FakeXero.payments)}"}

    def create_claim_credit_note(self, client_name: str, tracking_number: str, amount):
        FakeXero.credit_notes.append((client_name, tracking_number, amount))
        return {"CreditNoteID": f"cn-{tracking_number}",
                "Contact": {"ContactID": f"contact-{client_name}"}}

    def find_open_invoice_for_contact(self, contact_id: str):
        return {"InvoiceID": f"ful-{contact_id}", "AmountDue": 9999.0}

    def allocate_credit_note(self, credit_note_id: str, invoice_id: str, amount):
        FakeXero.allocations.append((credit_note_id, invoice_id, amount))
        return {}

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
