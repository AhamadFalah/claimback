"""FastAPI wrapper — the contract the web dashboard builds against (3PL mode).

Run:
    pip install -e ".[api]"
    uvicorn claimback.api:app --reload --port 8000

Endpoints:
    GET  /dashboard          recovered / pending / written-off, split by client
    GET  /claims             full claims register with states
    POST /run?csv_path=...   ingest -> detect -> value -> pack (respects DRY_RUN)
    POST /file/{courier}     file READY claims, post receivables to Xero
    POST /outcomes?csv_path= ingest courier claim responses (the fight)
    POST /reconcile          payouts -> payments -> client credit notes
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .dashboard import compute
from .db import Register
from .detect import detect as run_detect
from .ingest import ingest_csv
from .models import ClaimStatus

app = FastAPI(title="ClaimBack API", version="0.2.0")

# Hackathon-permissive CORS so the dashboard app can call this from anywhere.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


def _register() -> Register:
    return Register(settings.db_path)


@app.get("/dashboard")
def dashboard() -> dict:
    numbers = compute(_register())
    return {
        "recovered": float(numbers["recovered"]),
        "pending": float(numbers["pending"]),
        "written_off": float(numbers["written_off"]),
        "by_client": {name: {k: float(v) for k, v in row.items()}
                      for name, row in numbers["by_client"].items()},
        "expiring_count": len(numbers["expiring"]),
        "expiring": [c.tracking_number for c in numbers["expiring"]],
        "total_claims": len(numbers["claims"]),
        "dry_run": settings.dry_run,
    }


@app.get("/claims")
def claims() -> list[dict]:
    return [c.model_dump(mode="json") for c in _register().all()]


@app.post("/run")
def run(csv_path: str = "data/demo_shipments.csv") -> dict:
    from .couriers import adapter_for, get_adapter
    from .valuation import value_claims

    if not Path(csv_path).exists():
        raise HTTPException(404, f"CSV not found: {csv_path}")
    register = _register()
    detections = run_detect(ingest_csv(csv_path))
    detections = [d for d in detections if not register.exists(d.shipment.tracking_number)]
    valued, refusals = value_claims(detections)
    for r in refusals:
        register.upsert(r)  # visible write-off exposure; dedupe stops re-flagging

    packs: dict[str, int] = {}
    by_adapter: dict[str, list] = {}
    for c in valued:
        by_adapter.setdefault(adapter_for(c.courier, c.channel).name, []).append(c)
    out = Path("out"); out.mkdir(exist_ok=True)
    for adapter_name, batch in sorted(by_adapter.items()):
        ready = [c.transition(ClaimStatus.READY) for c in batch]
        pack = get_adapter(adapter_name).generate_pack(ready)
        (out / f"{adapter_name.replace(':', '_')}_claims.csv").write_bytes(pack)
        for c in ready:
            register.upsert(c)
        packs[adapter_name] = len(ready)

    return {
        "detected": len(detections),
        "claimable": len(valued),
        "refused": [{"tracking_number": r.tracking_number, "client": r.client,
                     "exposure": float(r.declared_value or 0), "reason": r.notes.split("; ", 1)[-1]}
                    for r in refusals],
        "recoverable": float(sum(c.claim_value for c in valued)),
        "packs": packs,
        "dry_run": settings.dry_run,
    }


@app.post("/file/{courier}")
def file_claims(courier: str) -> dict:
    from .couriers import adapter_for
    from .xero import XeroClient

    register = _register()
    ready = [c for c in register.by_status(ClaimStatus.READY) if c.courier == courier]
    if settings.dry_run:
        return {"filed": 0, "would_file": len(ready), "dry_run": True}
    client = XeroClient()
    filed = []
    for c in ready:
        inv = client.create_claim_receivable(c.courier, c.tracking_number, c.claim_value)
        adapter_name = adapter_for(c.courier, c.channel).name
        pack_path = Path("out") / f"{adapter_name.replace(':', '_')}_claims.csv"
        if pack_path.exists():
            try:
                client.attach_file_to_invoice(inv["InvoiceID"], pack_path.name, pack_path.read_bytes())
            except Exception:
                pass  # evidence attachment must never block the filing itself
        register.upsert(c.transition(ClaimStatus.FILED).model_copy(
            update={"xero_receivable_id": inv["InvoiceID"]}))
        filed.append(c.tracking_number)
    return {"filed": len(filed), "tracking_numbers": filed, "dry_run": False}


@app.post("/outcomes")
def outcomes(csv_path: str) -> dict:
    from .outcomes import ingest_outcomes

    if not Path(csv_path).exists():
        raise HTTPException(404, f"CSV not found: {csv_path}")
    results = ingest_outcomes(csv_path, _register())
    return {"applied": [{"tracking_number": t, "outcome": o, "status": s} for t, o, s in results]}


@app.post("/reconcile")
def reconcile() -> dict:
    from .xero import XeroClient
    from .xero.matching import reconcile_payouts

    register = _register()
    open_claims = register.by_status(ClaimStatus.FILED, ClaimStatus.PAID)
    client = XeroClient()
    matches, ambiguous = reconcile_payouts(client, open_claims)
    results = []
    for claim, amount in matches:
        paid = claim if claim.status == ClaimStatus.PAID else claim.transition(ClaimStatus.PAID)
        paid = paid.model_copy(update={"payout_value": amount})
        if not settings.dry_run and claim.xero_receivable_id:
            payment = client.apply_payment(claim.xero_receivable_id, amount)
            paid = paid.model_copy(update={"xero_payment_id": payment.get("PaymentID")})
        done = paid.transition(ClaimStatus.RECONCILED)
        if not settings.dry_run and claim.client:
            note = client.create_claim_credit_note(claim.client, claim.tracking_number, amount)
            done = done.model_copy(update={"xero_credit_note_id": note.get("CreditNoteID")})
        register.upsert(done)
        results.append({"tracking_number": claim.tracking_number, "client": claim.client,
                        "payout": float(amount)})
    return {"reconciled": len(results), "matches": results, "ambiguous": ambiguous}
