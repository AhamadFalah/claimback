"""Thin Xero Accounting API client (httpx + tenacity retries).

Only the endpoints ClaimBack needs. Rate limits: 60 calls/min, 5000/day
per tenant — the retry policy backs off on 429 using Retry-After.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Optional

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt

from ..config import settings
from .auth import get_access

BASE = "https://api.xero.com/api.xro/2.0"


def _retryable(exc: BaseException) -> bool:
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (429, 500, 502, 503)


def _retry_wait(retry_state) -> float:
    """Honour Xero's Retry-After header on 429; exponential backoff otherwise."""
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    if isinstance(exc, httpx.HTTPStatusError):
        retry_after = exc.response.headers.get("Retry-After", "")
        if retry_after.isdigit():
            return float(retry_after)
    return float(min(2 ** retry_state.attempt_number, 30))


class XeroClient:
    def __init__(self):
        self._client = httpx.Client(timeout=30)

    def _headers(self) -> dict:
        token, tenant = get_access()
        return {
            "Authorization": f"Bearer {token}",
            "Xero-tenant-id": tenant,
            "Accept": "application/json",
        }

    @retry(retry=retry_if_exception(_retryable), wait=_retry_wait, stop=stop_after_attempt(5))
    def _request(self, method: str, path: str, extra_headers: dict | None = None, **kwargs) -> dict:
        headers = {**self._headers(), **(extra_headers or {})}
        resp = self._client.request(method, f"{BASE}{path}", headers=headers, **kwargs)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # Xero puts the validation detail in the body — surface it or debug blind.
            exc.args = (f"{exc.args[0]}\nXero response: {resp.text[:4000]}",)
            raise
        return resp.json()

    # ---- Invoices ----
    def find_invoice_by_reference(self, reference: str) -> Optional[dict]:
        """Match a shipment's order ref to an ACCREC invoice (Reference or InvoiceNumber)."""
        where = f'Reference=="{reference}" OR InvoiceNumber=="{reference}"'
        data = self._request("GET", "/Invoices", params={"where": where})
        invoices = data.get("Invoices", [])
        return invoices[0] if invoices else None

    def list_invoices(self, page: int = 1) -> list[dict]:
        data = self._request("GET", "/Invoices", params={"page": page, "where": 'Type=="ACCREC"'})
        return data.get("Invoices", [])

    def create_invoice(self, invoice: dict) -> dict:
        data = self._request("POST", "/Invoices", json={"Invoices": [invoice]})
        return data["Invoices"][0]

    # ---- Contacts ----
    def get_or_create_contact(self, name: str) -> dict:
        data = self._request("GET", "/Contacts", params={"where": f'Name=="{name}"'})
        contacts = data.get("Contacts", [])
        if contacts:
            return contacts[0]
        data = self._request("POST", "/Contacts", json={"Contacts": [{"Name": name}]})
        return data["Contacts"][0]

    # ---- Claim receivable tracking ----
    def create_claim_receivable(self, courier_name: str, tracking_number: str, value: Decimal) -> dict:
        """Post the filed claim as an ACCREC invoice against the courier contact.

        The claim becomes a visible, reportable receivable in Xero the moment
        it's filed — the business can see money-in-flight, and payouts get
        applied against it like any other invoice.
        """
        contact = self.get_or_create_contact(f"{courier_name} (Claims)")
        invoice = {
            "Type": "ACCREC",
            "Contact": {"ContactID": contact["ContactID"]},
            "Reference": f"CLAIM-{tracking_number}",
            "Date": date.today().isoformat(),
            "DueDate": (date.today() + timedelta(days=30)).isoformat(),
            "LineAmountTypes": "NoTax",  # courier compensation is outside the scope of VAT (HMRC VATSC06190)
            "LineItems": [{
                "Description": f"Courier compensation claim — parcel {tracking_number}",
                "Quantity": 1,
                "UnitAmount": float(value),
                "AccountCode": settings.xero_recoveries_account,
            }],
            # FRS 102 s21: a filed-but-unaccepted claim is a contingent asset, so it
            # must not hit the ledgers yet. DRAFT tracks it in Xero without recognising
            # income; it is authorised when the courier accepts / the payout lands.
            "Status": "DRAFT",
        }
        return self.create_invoice(invoice)

    def authorise_invoice(self, invoice_id: str) -> dict:
        """Recognise the receivable — called once the claim is accepted (virtually
        certain per FRS 102 s21), in the demo when the payout is reconciled."""
        data = self._request("POST", "/Invoices", json={"Invoices": [{
            "InvoiceID": invoice_id, "Status": "AUTHORISED",
        }]})
        return data["Invoices"][0]

    def find_open_invoice_for_contact(self, contact_id: str) -> Optional[dict]:
        """The client's outstanding fulfilment invoice — allocation target for claim credit notes."""
        where = f'Contact.ContactID==Guid("{contact_id}") AND Status=="AUTHORISED" AND Type=="ACCREC"'
        data = self._request("GET", "/Invoices", params={"where": where})
        invoices = [i for i in data.get("Invoices", []) if i.get("AmountDue", 0) > 0]
        return invoices[0] if invoices else None

    def allocate_credit_note(self, credit_note_id: str, invoice_id: str, amount: Decimal) -> dict:
        """Allocate the claim credit note against the client's fulfilment invoice —
        the recovered money visibly reduces what the client owes."""
        data = self._request("PUT", f"/CreditNotes/{credit_note_id}/Allocations", json={
            "Allocations": [{
                "Invoice": {"InvoiceID": invoice_id},
                "Amount": float(amount),
                "Date": date.today().isoformat(),
            }],
        })
        return data

    def create_claim_credit_note(self, client_name: str, tracking_number: str, amount: Decimal) -> dict:
        """Pass the recovered payout through to the 3PL client as an ACCREC credit note.

        Referenced CLAIM-<tracking>; the client allocates it against their next
        fulfilment invoice. Raised only after the courier payout has reconciled —
        money is distributed when it exists, never before.
        """
        contact = self.get_or_create_contact(client_name)
        credit_note = {
            "Type": "ACCRECCREDIT",
            "Contact": {"ContactID": contact["ContactID"]},
            "Reference": f"CLAIM-{tracking_number}",
            "Date": date.today().isoformat(),
            "LineAmountTypes": "NoTax",  # compensation pass-through, outside the scope of VAT
            "LineItems": [{
                # Explicit narration matters: this is compensation, not a price
                # reduction — outside the scope of VAT per HMRC VATSC06190.
                "Description": (f"Compensation for goods lost/damaged in transit — parcel "
                                f"{tracking_number} — outside the scope of VAT"),
                "Quantity": 1,
                "UnitAmount": float(amount),
                "AccountCode": settings.xero_recoveries_account,
            }],
            "Status": "AUTHORISED",
        }
        data = self._request("PUT", "/CreditNotes", json={"CreditNotes": [credit_note]})
        return data["CreditNotes"][0]

    def attach_file_to_invoice(self, invoice_id: str, filename: str, content: bytes) -> dict:
        """Attach evidence (e.g. the submitted claim pack) to the claim receivable."""
        return self._request(
            "PUT", f"/Invoices/{invoice_id}/Attachments/{filename}",
            extra_headers={"Content-Type": "application/octet-stream"},
            content=content,
        )

    def apply_payment(self, invoice_id: str, amount: Decimal, account_code: str | None = None) -> dict:
        data = self._request("PUT", "/Payments", json={"Payments": [{
            "Invoice": {"InvoiceID": invoice_id},
            "Account": {"Code": account_code or settings.xero_payment_account},
            "Amount": float(amount),
        }]})
        return data["Payments"][0]

    # ---- Accounts (verify chart-of-accounts codes on day one) ----
    def list_accounts(self) -> list[dict]:
        data = self._request("GET", "/Accounts")
        return data.get("Accounts", [])

    def find_bank_account(self) -> Optional[dict]:
        return next((a for a in self.list_accounts() if a.get("Type") == "BANK"), None)

    # ---- Bank transactions (payout reconciliation) ----
    def list_bank_transactions(self, page: int = 1) -> list[dict]:
        data = self._request("GET", "/BankTransactions", params={"page": page})
        return data.get("BankTransactions", [])

    def create_bank_transaction(self, reference: str, amount: Decimal,
                                contact_name: str, bank_account_id: str) -> dict:
        """Simulate a courier payout landing in the bank feed (demo seeding)."""
        contact = self.get_or_create_contact(contact_name)
        data = self._request("PUT", "/BankTransactions", json={"BankTransactions": [{
            "Type": "RECEIVE",
            "Reference": reference,
            "Contact": {"ContactID": contact["ContactID"]},
            "BankAccount": {"AccountID": bank_account_id},
            "LineAmountTypes": "NoTax",
            "LineItems": [{
                "Description": f"Courier compensation payout {reference}",
                "Quantity": 1,
                "UnitAmount": float(amount),
                "AccountCode": settings.xero_recoveries_account,
            }],
        }]})
        return data["BankTransactions"][0]
