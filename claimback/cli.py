"""ClaimBack CLI — the demo driver (3PL mode).

    claimback auth                      # one-time Xero OAuth
    claimback ingest data/demo_shipments.csv
    claimback detect data/demo_shipments.csv
    claimback run data/demo_shipments.csv       # detect -> value -> pack (dry-run by default)
    claimback file <courier>                     # FILED + receivables posted to Xero (DRY_RUN=false)
    claimback outcomes data/demo_outcomes.csv    # ingest the courier's claim responses (the fight)
    claimback reconcile                          # payouts -> payments -> client credit notes
    claimback dashboard                          # money view, split by client
"""
from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .config import settings
from .db import Register
from .detect import detect as run_detect
from .ingest import ingest_csv
from .models import ClaimStatus

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()


@app.command()
def auth():
    """One-time Xero OAuth consent + token cache."""
    from .xero.auth import authorize
    tokens = authorize()
    console.print(f"[green]Connected to Xero tenant {tokens['tenant_id']}[/green]")


@app.command()
def ingest(csv_path: Path):
    """Parse a shipment export and show what we understood."""
    shipments = ingest_csv(csv_path)
    console.print(f"Ingested [bold]{len(shipments)}[/bold] shipments from {csv_path}")
    for s in shipments[:5]:
        console.print(f"  {s.tracking_number}  {s.courier:<8} {s.channel:<9} "
                      f"{s.status.value:<12} {s.client:<12} ref={s.order_ref}")


@app.command()
def detect(csv_path: Path):
    """Run detection rules and show flagged shipments."""
    detections = run_detect(ingest_csv(csv_path))
    table = Table(title=f"{len(detections)} shipments flagged")
    for col in ("Tracking", "Client", "Channel", "Order ref", "Type", "Rule"):
        table.add_column(col)
    for d in detections:
        table.add_row(d.shipment.tracking_number, d.shipment.client, d.shipment.channel,
                      d.shipment.order_ref, d.claim_type.value, d.rule)
    console.print(table)


@app.command()
def run(csv_path: Path, out_dir: Path = Path("out")):
    """Full pipeline: detect -> value against channel rules -> generate claim packs."""
    from .couriers import adapter_for
    from .valuation import value_claims

    register = Register(settings.db_path)
    detections = run_detect(ingest_csv(csv_path))
    detections = [d for d in detections if not register.exists(d.shipment.tracking_number)]
    console.print(f"{len(detections)} new flagged shipments (already-processed deduped)")

    claims, refusals = value_claims(detections)
    for r in refusals:
        register.upsert(r)  # visible write-off exposure, and dedupe stops re-flagging
        console.print(f"[yellow]not claimable:[/yellow] {r.tracking_number} ({r.client}) "
                      f"£{r.declared_value} — {r.notes.split('; ', 1)[-1]}")

    by_adapter: dict[str, list] = {}
    for c in claims:
        by_adapter.setdefault(adapter_for(c.courier, c.channel).name, []).append(c)

    out_dir.mkdir(exist_ok=True)
    for adapter_name, batch in sorted(by_adapter.items()):
        from .couriers import get_adapter
        adapter = get_adapter(adapter_name)
        ready = [c.transition(ClaimStatus.READY) for c in batch]
        pack = adapter.generate_pack(ready)          # validates or aborts
        pack_path = out_dir / f"{adapter_name.replace(':', '_')}_claims.csv"
        pack_path.write_bytes(pack)
        for c in ready:
            register.upsert(c)
        total = sum(c.claim_value for c in ready)
        console.print(f"[green]{adapter_name}[/green]: {len(ready)} claims, £{total} -> {pack_path}")

    if settings.dry_run:
        console.print("[cyan]DRY RUN[/cyan] — packs generated locally; nothing filed, nothing posted to Xero. "
                      "Set DRY_RUN=false to go live.")


@app.command()
def file(courier: str):
    """Mark READY claims as FILED and post claim receivables to Xero."""
    from .couriers import adapter_for
    from .xero import XeroClient

    register = Register(settings.db_path)
    ready = [c for c in register.by_status(ClaimStatus.READY) if c.courier == courier]
    if not ready:
        console.print("Nothing READY to file.")
        raise typer.Exit()
    if settings.dry_run:
        console.print(f"[cyan]DRY RUN[/cyan] — would file {len(ready)} claims and post receivables.")
        raise typer.Exit()
    client = XeroClient()
    for c in ready:
        inv = client.create_claim_receivable(c.courier, c.tracking_number, c.claim_value)
        adapter_name = adapter_for(c.courier, c.channel).name
        pack_path = Path("out") / f"{adapter_name.replace(':', '_')}_claims.csv"
        if pack_path.exists():
            try:
                client.attach_file_to_invoice(inv["InvoiceID"], pack_path.name, pack_path.read_bytes())
            except Exception as exc:  # evidence attachment must never block the filing itself
                console.print(f"[yellow]warning:[/yellow] could not attach pack to {inv['InvoiceID']}: {exc}")
        register.upsert(c.transition(ClaimStatus.FILED).model_copy(
            update={"xero_receivable_id": inv["InvoiceID"]}))
    console.print(f"[green]Filed {len(ready)} claims; receivables posted to Xero.[/green]")


@app.command()
def outcomes(csv_path: Path):
    """Ingest the courier's claim responses — paid / declined / info_requested."""
    from .outcomes import ingest_outcomes

    register = Register(settings.db_path)
    results = ingest_outcomes(csv_path, register)
    for tracking, outcome, status in results:
        colour = {"rejected": "red", "filed": "yellow", "paid": "green"}.get(status, "white")
        console.print(f"[{colour}]{tracking}[/{colour}]: courier said {outcome!r} -> {status}")
    if not results:
        console.print("No outcomes in file.")


@app.command()
def reconcile():
    """Match payouts to claims, apply payments, pass recovery to clients as credit notes."""
    from .xero import XeroClient
    from .xero.matching import reconcile_payouts

    register = Register(settings.db_path)
    open_claims = register.by_status(ClaimStatus.FILED, ClaimStatus.PAID)
    client = XeroClient()
    matches, ambiguous = reconcile_payouts(client, open_claims)
    for claim, amount in matches:
        paid = claim if claim.status == ClaimStatus.PAID else claim.transition(ClaimStatus.PAID)
        paid = paid.model_copy(update={"payout_value": amount})
        if not settings.dry_run and claim.xero_receivable_id:
            # Apply the payout against the claim receivable — Xero shows it as a
            # suggested match on the bank line; the human clicks OK (honest boundary).
            payment = client.apply_payment(claim.xero_receivable_id, amount)
            paid = paid.model_copy(update={"xero_payment_id": payment.get("PaymentID")})
        done = paid.transition(ClaimStatus.RECONCILED)
        if not settings.dry_run and claim.client:
            # Pass-through: the client gets the recovered amount as a credit note.
            note = client.create_claim_credit_note(claim.client, claim.tracking_number, amount)
            done = done.model_copy(update={"xero_credit_note_id": note.get("CreditNoteID")})
        register.upsert(done)
        console.print(f"[green]£{amount}[/green] reconciled against {claim.tracking_number} "
                      f"-> credit note to {claim.client or 'n/a'}")
    for amb in ambiguous:
        console.print(f"[yellow]AMBIGUOUS[/yellow] payout £{amb['amount']} ref {amb['reference']!r} "
                      f"could be any of {', '.join(amb['candidates'])} — resolve manually")
    if not matches:
        console.print("No payouts matched.")


@app.command()
def dashboard():
    """Recovered / pending / written-off — the money view, split by client."""
    from .dashboard import summarise
    summarise(Register(settings.db_path), console)


if __name__ == "__main__":
    app()
