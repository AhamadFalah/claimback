"""The money view. Swap for a Streamlit/web dashboard at the hackathon —
this gives you the numbers to put on the pitch slide."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from rich.console import Console
from rich.table import Table

from .db import Register
from .models import ClaimStatus


def compute(register: Register, today: date | None = None) -> dict:
    """Single source for the recovery numbers — CLI view and API both use this."""
    claims = register.all()
    today = today or date.today()
    recovered = sum((c.payout_value or Decimal(0)) for c in claims if c.status == ClaimStatus.RECONCILED)
    pending = sum((c.claim_value or Decimal(0)) for c in claims
                  if c.status in (ClaimStatus.READY, ClaimStatus.FILED, ClaimStatus.EVIDENCE_REQUESTED))
    soon = today + timedelta(days=3)
    expiring = [c for c in claims
                if c.status in (ClaimStatus.DETECTED, ClaimStatus.MATCHED, ClaimStatus.READY)
                and c.deadline and c.deadline <= soon]
    return {"claims": claims, "recovered": recovered, "pending": pending, "expiring": expiring}


def summarise(register: Register, console: Console) -> None:
    numbers = compute(register)
    claims, recovered, pending, expiring = (
        numbers["claims"], numbers["recovered"], numbers["pending"], numbers["expiring"])

    table = Table(title="ClaimBack — recovery position")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Recovered (reconciled in Xero)", f"£{recovered}")
    table.add_row("Pending (filed / in flight)", f"£{pending}")
    table.add_row("Claims expiring within 3 days", str(len(expiring)))
    table.add_row("Total claims tracked", str(len(claims)))
    console.print(table)

    for c in expiring:
        console.print(f"[red]EXPIRING {c.deadline}[/red] {c.tracking_number} £{c.claim_value} ({c.courier})")
