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
    assert data == {
        "recovered": 0.0, "pending": 0.0, "expiring_count": 0,
        "expiring": [], "total_claims": 0, "dry_run": True,
    }


def test_run_claims_dashboard_flow(client):
    # POST /run (dry-run): full detect -> match -> pack
    run = client.post("/run", params={"csv_path": str(CSV)})
    assert run.status_code == 200
    data = run.json()
    assert data["detected"] == 7
    assert data["matched"] == 7
    assert data["unmatched"] == []
    assert data["recoverable"] == pytest.approx(157.34)
    assert data["packs"] == {"swiftship": 7}
    assert data["dry_run"] is True
    assert (Path("out") / "swiftship_claims.csv").exists()

    # GET /claims: register reflects the run
    claims = client.get("/claims").json()
    assert len(claims) == 7
    assert all(c["status"] == "ready" for c in claims)
    assert all(c["claim_value"] is not None for c in claims)

    # GET /dashboard: money in flight, nothing recovered yet
    dash = client.get("/dashboard").json()
    assert dash["pending"] == pytest.approx(157.34)
    assert dash["recovered"] == 0.0
    assert dash["total_claims"] == 7
    assert dash["dry_run"] is True

    # Second POST /run: dedupe means nothing new is detected or packed
    rerun = client.post("/run", params={"csv_path": str(CSV)}).json()
    assert rerun["detected"] == 0
    assert rerun["matched"] == 0
    assert rerun["packs"] == {}


def test_run_missing_csv_is_404(client):
    assert client.post("/run", params={"csv_path": "nope.csv"}).status_code == 404


def test_file_and_reconcile_live_flow(client, monkeypatch, fake_xero_class):
    """DRY_RUN off (against FakeXero): file posts receivables + attaches the pack;
    reconcile applies payments against them and surfaces the ambiguous payout."""
    monkeypatch.setattr(settings, "dry_run", False)
    client.post("/run", params={"csv_path": str(CSV)})

    filed = client.post("/file/swiftship").json()
    assert filed["filed"] == 7
    assert len(fake_xero_class.receivables) == 7
    assert len(fake_xero_class.attachments) == 7  # claim pack attached as evidence

    rec = client.post("/reconcile").json()
    assert rec["reconciled"] == 4
    assert len(rec["ambiguous"]) == 1
    assert "REF-UNKNOWN-77" in rec["ambiguous"][0]["reference"]
    assert len(fake_xero_class.payments) == 4  # applied against receivables, ambiguous excluded
    posted = {f"rcv-{tracking}" for tracking, _ in fake_xero_class.receivables}
    assert all(invoice_id in posted for invoice_id, _ in fake_xero_class.payments)

    dash = client.get("/dashboard").json()
    assert dash["recovered"] == pytest.approx(91.35)
    assert dash["pending"] == pytest.approx(65.99)
