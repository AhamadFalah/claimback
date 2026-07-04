"""FastAPI surface — the contract the dashboard frontend builds against.

XeroClient is swapped for FakeXero and the DB lives in tmp_path; nothing
here can reach the real Xero API.
"""
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from claimback.api import app
from claimback.config import settings

ROOT = Path(__file__).parent.parent
CSV = ROOT / "data" / "demo_shipments.csv"
OUTCOMES_CSV = ROOT / "data" / "demo_outcomes.csv"


class FrozenDate(date):
    """detect() defaults to date.today(); pin it so the demo counts hold."""
    @classmethod
    def today(cls):
        return date(2026, 7, 2)


@pytest.fixture
def client(tmp_path, monkeypatch, fake_xero_class):
    monkeypatch.setattr("claimback.xero.XeroClient", fake_xero_class)
    monkeypatch.setattr("claimback.detect.date", FrozenDate)
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "api.db"))
    monkeypatch.chdir(tmp_path)  # /run writes packs to ./out
    return TestClient(app)


def test_dashboard_empty(client):
    data = client.get("/dashboard").json()
    assert data["recovered"] == 0.0
    assert data["pending"] == 0.0
    assert data["written_off"] == 0.0
    assert data["by_client"] == {}
    assert data["total_claims"] == 0
    assert data["dry_run"] is True


def test_run_claims_dashboard_flow(client):
    # POST /run (dry-run): full detect -> value -> pack
    run = client.post("/run", params={"csv_path": str(CSV)})
    assert run.status_code == 200
    data = run.json()
    assert data["detected"] == 8
    assert data["claimable"] == 7
    assert data["recoverable"] == pytest.approx(148.59)
    assert data["packs"] == {"evri": 5, "evri:amazon": 2}
    assert data["dry_run"] is True
    assert (Path("out") / "evri_claims.csv").exists()
    assert (Path("out") / "evri_amazon_claims.csv").exists()

    # the unwinnable damage claim is refused with visible exposure, not filed
    assert len(data["refused"]) == 1
    refused = data["refused"][0]
    assert refused["tracking_number"] == "EV1000000011"
    assert refused["client"] == "ChocoLoco"
    assert refused["exposure"] == pytest.approx(30.50)

    # GET /claims: register reflects the run (7 ready + 1 refused)
    claims = client.get("/claims").json()
    assert len(claims) == 8
    assert sum(c["status"] == "ready" for c in claims) == 7
    assert sum(c["status"] == "rejected" for c in claims) == 1

    # GET /dashboard: money in flight + write-off exposure, split by client
    dash = client.get("/dashboard").json()
    assert dash["pending"] == pytest.approx(148.59)
    assert dash["written_off"] == pytest.approx(30.50)
    assert dash["by_client"]["ChocoLoco"]["written_off"] == pytest.approx(30.50)

    # Second POST /run: dedupe means nothing new is detected or packed
    rerun = client.post("/run", params={"csv_path": str(CSV)}).json()
    assert rerun["detected"] == 0
    assert rerun["packs"] == {}


def test_run_missing_csv_is_404(client):
    assert client.post("/run", params={"csv_path": "nope.csv"}).status_code == 404


def test_file_fight_reconcile_live_flow(client, monkeypatch, fake_xero_class):
    """DRY_RUN off (against FakeXero): file posts receivables + attaches packs;
    outcomes drive the courier fight; reconcile applies payments and passes
    recovered money to clients as credit notes."""
    monkeypatch.setattr(settings, "dry_run", False)
    client.post("/run", params={"csv_path": str(CSV)})

    filed = client.post("/file/evri").json()
    assert filed["filed"] == 7
    assert len(fake_xero_class.receivables) == 7
    assert len(fake_xero_class.attachments) == 7  # claim pack attached as evidence

    # The courier fight: one declined (write-off), one evidence loop (resubmitted)
    fight = client.post("/outcomes", params={"csv_path": str(OUTCOMES_CSV)}).json()
    by_tracking = {r["tracking_number"]: r["status"] for r in fight["applied"]}
    assert by_tracking == {"EV1000000009": "rejected", "EV1000000005": "filed"}

    rec = client.post("/reconcile").json()
    assert rec["reconciled"] == 4
    assert len(rec["ambiguous"]) == 1
    assert "REF-UNKNOWN-77" in rec["ambiguous"][0]["reference"]
    assert sorted(rec["ambiguous"][0]["candidates"]) == ["EV1000000005", "EV1000000008"]

    assert len(fake_xero_class.payments) == 4      # applied against receivables
    posted = {f"rcv-{tracking}" for tracking, _ in fake_xero_class.receivables}
    assert all(invoice_id in posted for invoice_id, _ in fake_xero_class.payments)

    # pass-through: credit notes to the right clients
    credited = {}
    for client_name, _, amount in fake_xero_class.credit_notes:
        credited[client_name] = credited.get(client_name, 0) + float(amount)
    assert credited == {"OatSnax": pytest.approx(42.60), "ChocoLoco": pytest.approx(20.0),
                        "Brew & Bean": pytest.approx(20.0)}

    dash = client.get("/dashboard").json()
    assert dash["recovered"] == pytest.approx(82.60)
    assert dash["pending"] == pytest.approx(50.00)
    assert dash["written_off"] == pytest.approx(46.49)
