# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup & Development

```bash
# Create virtual environment and install dependencies
python -m venv .venv && source .venv/bin/activate  # or .venv\Scripts\Activate on Windows
pip install -e ".[dev,api]"

# Run all tests (offline, no Xero credentials needed)
pytest

# Run tests with coverage
pytest --cov=claimback

# Run a specific test file
pytest tests/test_claim_pack.py

# Run tests matching a pattern
pytest -k "test_state_machine"

# Start the FastAPI dev server (if working on the API)
uvicorn claimback.api:app --reload --port 8000
```

## Commands for Testing the Full Pipeline

From the README demo script — these show the complete claim recovery flow:

```bash
# One-time setup: authorize with Xero and seed demo data
cp .env.example .env                  # Fill in XERO_CLIENT_ID, XERO_CLIENT_SECRET
claimback auth                         # Browser OAuth consent → token cached
python scripts/seed_xero.py            # Create demo clients in Xero

# Full demo (dry-run by default)
claimback detect data/demo_shipments.csv   # See what's flagged and why
claimback run data/demo_shipments.csv      # Detect → value → pack (shows counts & totals)
DRY_RUN=false claimback file evri          # File claims: post receivables to Xero, attach packs
claimback outcomes data/demo_outcomes.csv  # Ingest courier responses (paid/declined/info_requested)
python scripts/seed_payouts.py             # Simulate payouts landing in bank feed
DRY_RUN=false claimback reconcile          # Match payouts → apply to claims → raise client credit notes
claimback dashboard                        # View recovered/pending/written-off, split by client
```

## Architecture Overview

### Claim Lifecycle (Strict State Machine)

Every claim follows a deterministic path that is enforced at the model level:

```
DETECTED → MATCHED → READY → FILED → (PAID | REJECTED | EVIDENCE_REQUESTED) → RECONCILED
```

Key points:
- **DETECTED**: Detection rules flagged a shipment as potentially claimable.
- **MATCHED**: Claim matched to a Xero invoice; value determined (min of declared value and channel ceiling).
- **READY**: Claim pack generated and validated (byte-exact, golden-file tested).
- **FILED**: Submitted to courier; receivable posted to Xero with claim pack attached.
- **EVIDENCE_REQUESTED**: Courier requested more info; resubmission allowed (≤2 rounds).
- **PAID/REJECTED**: Final outcome from courier.
- **RECONCILED**: Payout matched to bank transaction; client credit note raised in Xero.

Invalid transitions raise `InvalidTransition` immediately. See `models.py:TRANSITIONS` for the legal state graph.

### Design Principle: Deterministic Where Money Moves, AI at the Edges

- **Money-touching code is deterministic**: Claim values (min of declared vs ceiling), claim packs (byte-exact), state transitions, Xero postings. All tested, no discretion.
- **AI/agent handles messy data**: Column mapping from WMS exports (via embeddings), triage decisions, dispute responses, next-action planning. Lives in `agent/orchestrator.py`.
- This separation ensures financial trustworthiness: if the agent proposes something invalid, the tools reject it; if tests pass, money is safe.

### Channel-Aware Courier Rules

Compensation rules differ per (courier, channel) pair, not just courier:
- **Evri standard**: £25 loss-only claims.
- **Evri×Amazon**: £20 loss+damage claims.

Unwinnable claims are refused up-front and surfaced as write-off exposure — never filed to fail. See `couriers/` for adapter pattern implementation.

### Data Layers

1. **WMS export (facts ledger)**: Raw shipment CSV from the 3PL's Mintsoft-style export.
2. **Claims register (SQLite, `db.py`)**: All detected/valued/filed/reconciled claims. Enforces dedupe (tracking number claimed only once).
3. **Xero (money ledger)**: Filed claims appear as receivables (ACCREC named CLAIM-<tracking>); payouts reconcile against them; recovered money flows to client via credit note.

The WMS is transient; the register is the source of truth for claim state; Xero is where money is authoritative.

## Key Files & Modules

| File | Purpose |
|------|---------|
| `models.py` | Domain models (Shipment, Claim, ClaimStatus, ClaimType) and the claim state machine. **Every claim lifecycle change goes through the state machine.** |
| `detect.py` | Rule engine: scans shipments for loss/damage/no-scan. Returns `DetectionResult` with rule name. |
| `ingest.py` | Messy WMS CSV ingest with AI-powered column mapping. |
| `valuation.py` | Apply channel-specific courier rules; enforce ceilings; refuse unwinnable claims. |
| `couriers/base.py` | Abstract courier adapter pattern. |
| `couriers/evri.py` | Evri standard & Evri×Amazon rules; pack generation. Byte-exact, golden-file tested. |
| `db.py` | SQLite claims register. Enforces dedupe and state transitions. |
| `xero/auth.py` | OAuth token management (browser-based consent, cached tokens). |
| `xero/client.py` | Thin API client for only what ClaimBack needs: Invoices, Contacts, Payments, CreditNotes, BankTransactions, Attachments. Respects Xero rate limits (60/min, 5000/day); backs off on 429 using Retry-After header. |
| `xero/matching.py` | Payout reconciliation: match bank payouts to open claims, flag ambiguous matches for human review. |
| `outcomes.py` | Ingest courier response files (paid/declined/info_requested). Drives resubmission loop on evidence_requested. |
| `dashboard.py` | Money view: recovered/pending/written-off, split by client. |
| `agent/orchestrator.py` | Tool definitions + system prompt for the agent orchestration loop (Xero Agent Toolkit / Claude tool-use integration). |
| `cli.py` | Typer CLI driver (auth, ingest, detect, run, file, outcomes, reconcile, dashboard). |
| `api.py` | FastAPI endpoints for headless integration. |

## Testing

- **`tests/test_claim_pack.py`**: Snapshot tests for byte-exact claim packs. A pack generation failure aborts the whole batch.
- **`tests/test_state_machine.py`**: Validates the claim state machine transitions.
- **`tests/test_detect.py`**: Detection rule correctness.
- **`tests/test_outcomes.py`**: Outcome ingestion (courier responses).
- **`tests/test_e2e_sim.py`**: Full end-to-end simulation: ingest → detect → value → pack → file → outcomes → reconcile. **Run this to verify the whole pipeline works.**
- **`tests/test_api.py`**: FastAPI endpoint tests.
- **`tests/conftest.py`**: Fixtures and test database setup.

All tests pass offline (no Xero credentials required). Run `pytest` frequently.

## Guardrails (Non-Negotiable)

1. **Dry-run defaults ON** (`DRY_RUN=true` in `.env`). Nothing touches Xero until explicitly `DRY_RUN=false`.
2. **DB-enforced dedupe**: A tracking number can never be claimed twice.
3. **Pack validation failure aborts the batch**: A single malformed pack halts filing.
4. **Ceilings enforced, never silently clamped**: If a claim exceeds the channel ceiling, it's refused with explanation, not reduced.
5. **Ambiguous payout matches surfaced to humans**: The reconciliation loop flags matches it can't confidently make; operator decides.
6. **Agent never edits claim values**: The agent can only *propose* claims; `value_claims()` tool enforces the rule and either accepts or refuses. See `agent/orchestrator.py`.

## Xero Integration Notes

- **OAuth scopes**: After 2 Mar 2026, apps must use granular scopes. ClaimBack uses: `accounting.transactions`, `accounting.attachments`.
- **API endpoints used**: Invoices, Contacts, Payments, CreditNotes, BankTransactions, Attachments.
- **Rate limits**: 60 calls/min, 5000/day per tenant. The client in `xero/client.py` respects Retry-After.
- **Receivables naming**: Filed claims post as ACCREC named `CLAIM-<tracking_number>` for easy reconciliation.
- **Credit notes**: On reconciliation, payouts flow to the client as credit notes tagged with the claim tracking number.
- **Validation errors**: Xero may reject documents (e.g., missing required fields on receivables). These surface as exceptions in the filequeue, halting the batch.

## Common Development Tasks

### Adding a New Courier or Channel

1. Create a new adapter in `couriers/` (inherit from `base.ChallengeAdapter`).
2. Define the rule set (loss/damage ceiling, eligible statuses, claim window).
3. Implement `generate_pack()` to produce the courier's byte-exact submission file.
4. Add golden-file fixtures in `tests/fixtures/` and snapshot tests in `tests/test_claim_pack.py`.
5. Update detection rules in `detect.py` if needed (loss vs. damage flags).
6. Test end-to-end with `claimback run` and `claimback file`.

### Handling a Courier Dispute

1. Courier sends an outcome file (paid/declined/info_requested).
2. `claimback outcomes <file>` ingests it; updates claim status and resubmission counter.
3. If `info_requested`, the claim loops back to READY for resubmission (≤2 times).
4. Once PAID or REJECTED, the claim waits for reconciliation.

### Reconciling a Payout

1. Bank feed lands payout in Xero.
2. `claimback reconcile` matches payouts to open (PAID) claims using amount + fuzzy date window.
3. Ambiguous matches are flagged for operator review.
4. Confirmed matches post a credit note to the client account (tracking reference included).

### Debugging a Claim

Use the dashboard and register:
```bash
claimback dashboard                         # Top-line view
sqlite3 claims.db "SELECT * FROM claims WHERE tracking_number = '...';"  # Deep dive
```

## Environment Variables

See `.env.example`:
- `XERO_CLIENT_ID`, `XERO_CLIENT_SECRET`: OAuth credentials (from developer.xero.com).
- `DRY_RUN`: Set to `false` to actually post to Xero. Default: `true`.
- `NO_SCAN_DAYS`, `CLAIM_WINDOW_DAYS`: Detection thresholds (configurable per rule).

## Performance & Scale

- **SQLite claims register**: Works fine for SME 3PLs (thousands of claims). For 100k+, consider Postgres.
- **Xero API rate limits**: 60 calls/min, 5000/day per tenant. The client batches reads and respects Retry-After. For high-volume ops, batch filing in chunks or queue async.
- **Pack generation**: Deterministic, no I/O. Snapshots validate byte-exactness every test run.
- **Payout matching**: O(n²) fuzzy search in the worst case; typically O(n) with date windowing. Operator review gates ambiguous matches.
