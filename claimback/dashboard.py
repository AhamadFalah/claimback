"""The money view (3PL mode). Swap for a web dashboard at the hackathon —
this gives you the numbers to put on the pitch slide."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from rich.console import Console
from rich.table import Table

from .db import Register
from .models import ClaimStatus

PENDING = (ClaimStatus.READY, ClaimStatus.FILED, ClaimStatus.EVIDENCE_REQUESTED, ClaimStatus.PAID)


def compute(register: Register, today: date | None = None) -> dict:
    """Single source for the recovery numbers — CLI view and API both use this."""
    claims = register.all()
    today = today or date.today()
    recovered = sum((c.payout_value or Decimal(0)) for c in claims if c.status == ClaimStatus.RECONCILED)
    pending = sum((c.claim_value or Decimal(0)) for c in claims if c.status in PENDING)
    # Write-offs: declined/abandoned claims at claim value, plus never-claimable
    # detections (no claim value set) at their declared value — real exposure either way.
    written_off = sum(((c.claim_value or c.declared_value) or Decimal(0)) for c in claims
                      if c.status == ClaimStatus.REJECTED)
    soon = today + timedelta(days=3)
    expiring = [c for c in claims
                if c.status in (ClaimStatus.DETECTED, ClaimStatus.MATCHED, ClaimStatus.READY)
                and c.deadline and c.deadline <= soon]

    by_client: dict[str, dict[str, Decimal]] = {}
    for c in claims:
        row = by_client.setdefault(c.client or "(unassigned)", {
            "recovered": Decimal(0), "pending": Decimal(0), "written_off": Decimal(0)})
        if c.status == ClaimStatus.RECONCILED:
            row["recovered"] += c.payout_value or Decimal(0)
        elif c.status in PENDING:
            row["pending"] += c.claim_value or Decimal(0)
        elif c.status == ClaimStatus.REJECTED:
            row["written_off"] += (c.claim_value or c.declared_value) or Decimal(0)

    return {"claims": claims, "recovered": recovered, "pending": pending,
            "written_off": written_off, "expiring": expiring, "by_client": by_client}


def summarise(register: Register, console: Console) -> None:
    numbers = compute(register)

    table = Table(title="ClaimBack — recovery position")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Recovered (reconciled in Xero)", f"£{numbers['recovered']}")
    table.add_row("Pending (filed / in flight)", f"£{numbers['pending']}")
    table.add_row("Written off (declined / not claimable)", f"£{numbers['written_off']}")
    table.add_row("Claims expiring within 3 days", str(len(numbers["expiring"])))
    table.add_row("Total claims tracked", str(len(numbers["claims"])))
    console.print(table)

    client_table = Table(title="By client (credit notes follow recovered £)")
    client_table.add_column("Client")
    for col in ("Recovered", "Pending", "Written off"):
        client_table.add_column(col, justify="right")
    for name, row in sorted(numbers["by_client"].items()):
        client_table.add_row(name, f"£{row['recovered']}", f"£{row['pending']}", f"£{row['written_off']}")
    console.print(client_table)

    for c in numbers["expiring"]:
        console.print(f"[red]EXPIRING {c.deadline}[/red] {c.tracking_number} £{c.claim_value} ({c.courier})")
