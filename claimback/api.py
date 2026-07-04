"""FastAPI wrapper — the contract the Lovable dashboard builds against.

Run:
    pip install -e ".[api]"
    uvicorn claimback.api:app --reload --port 8000

Expose to Lovable (hosted apps can't reach plain localhost — HTTPS needed):
    ngrok http 8000        # or: cloudflared tunnel --url http://localhost:8000

Endpoints:
    GET  /dashboard          recovered / pending / expiring summary
    GET  /claims             full claims register with states
    POST /run?csv_path=...   ingest -> detect -> match -> pack (respects DRY_RUN)
    POST /file/{courier}     file READY claims, post receivables to Xero
    POST /reconcile          match bank payouts to filed claims
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

app = FastAPI(title="ClaimBack API", version="0.1.0")

# Hackathon-permissive CORS so the Lovable app can call this from anywhere.
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
    from .couriers import get_adapter
    from .xero import XeroClient
    from .xero.matching import match_claims

    if not Path(csv_path).exists():
        raise HTTPException(404, f"CSV not found: {csv_path}")
    register = _register()
    detections = run_detect(ingest_csv(csv_path))
    detections = [d for d in detections if not register.exists(d.shipment.tracking_number)]
    matched, unmatched = match_claims(XeroClient(), detections)

    packs: dict[str, int] = {}
    by_courier: dict[str, list] = {}
    for c in matched:
        by_courier.setdefault(c.courier, []).append(c)
    out = Path("out"); out.mkdir(exist_ok=True)
    for courier, batch in by_courier.items():
        ready = [c.transition(ClaimStatus.READY) for c in batch]
        pack = get_adapter(courier).generate_pack(ready)
        (out / f"{courier}_claims.csv").write_bytes(pack)
        for c in ready:
            register.upsert(c)
        packs[courier] = len(ready)

    return {
        "detected": len(detections),
        "matched": len(matched),
        "unmatched": unmatched,
        "recoverable": float(sum(c.claim_value for c in matched)),
        "packs": packs,
        "dry_run": settings.dry_run,
    }


@app.post("/file/{courier}")
def file_claims(courier: str) -> dict:
    from .xero import XeroClient

    register = _register()
    ready = [c for c in register.by_status(ClaimStatus.READY) if c.courier == courier]
    if settings.dry_run:
        return {"filed": 0, "would_file": len(ready), "dry_run": True}
    client = XeroClient()
    filed = []
    pack_path = Path("out") / f"{courier}_claims.csv"
    for c in ready:
        inv = client.create_claim_receivable(c.courier, c.tracking_number, c.claim_value)
        if pack_path.exists():
            try:
                client.attach_file_to_invoice(inv["InvoiceID"], pack_path.name, pack_path.read_bytes())
            except Exception:
                pass  # evidence attachment must never block the filing itself
        register.upsert(c.transition(ClaimStatus.FILED).model_copy(
            update={"xero_receivable_id": inv["InvoiceID"]}))
        filed.append(c.tracking_number)
    return {"filed": len(filed), "tracking_numbers": filed, "dry_run": False}


@app.post("/reconcile")
def reconcile() -> dict:
    from .xero import XeroClient
    from .xero.matching import reconcile_payouts

    register = _register()
    filed = register.by_status(ClaimStatus.FILED)
    client = XeroClient()
    matches, ambiguous = reconcile_payouts(client, filed)
    results = []
    for claim, amount in matches:
        paid = claim.transition(ClaimStatus.PAID).model_copy(update={"payout_value": amount})
        if not settings.dry_run and claim.xero_receivable_id:
            payment = client.apply_payment(claim.xero_receivable_id, amount)
            paid = paid.model_copy(update={"xero_payment_id": payment.get("PaymentID")})
        register.upsert(paid.transition(ClaimStatus.RECONCILED))
        results.append({"tracking_number": claim.tracking_number, "payout": float(amount)})
    return {"reconciled": len(results), "matches": results, "ambiguous": ambiguous}
