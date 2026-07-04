# ClaimBack

**Autonomous courier-claims recovery for small businesses, powered by Xero.**

UK small businesses shipping D2C lose money to lost and damaged parcels every week — and most never claim. The process is tedious, per-courier, deadline-bound, and easy to get wrong: couriers reject claims over formatting errors, and time-barred claims are money gone forever. ClaimBack finds claimable shipments, files the claims, and tracks every pound into Xero until it's reconciled.

**Target bounty:** 💸 Cash Flow Accelerator (analyse Xero data → surface actionable insight → take autonomous action → measurable cash outcome). The messy-data ingest also speaks to 🔗 The Vibe Integrator.

## How it works

```
shipment export (messy CSV)          Xero (Accounting API)
        │                                   │
        ▼                                   │
   INGEST  ──column-alias + AI mapping──    │
        │                                   │
        ▼                                   ▼
   DETECT ──named rules──►  MATCH ──shipment ↔ invoice, value = min(invoice, ceiling)
                              │
                              ▼
                       CLAIM PACK (byte-exact, golden-file tested)
                              │
                              ▼
                        FILE ──► claim receivable posted to Xero (money-in-flight is visible)
                              │
                              ▼
                       RECONCILE ──► courier payout in bank feed matched to claim
```

Claim lifecycle is a strict state machine: `DETECTED → MATCHED → READY → FILED → (PAID | REJECTED | EVIDENCE_REQUESTED) → RECONCILED`. Illegal transitions raise — money never moves through an undefined path.

## Design principles

1. **Deterministic where money moves, AI where data is messy.** Claim values, submission files, and Xero postings are plain tested code. The AI layer handles column mapping, triage, and next-action decisions via tools (`claimback/agent/orchestrator.py`).
2. **Byte-exact claim packs.** Courier parsers reject files over invisible differences. Packs are generated as bytes and snapshot-tested against golden fixtures (`tests/test_claim_pack.py`). A validation failure aborts the whole batch.
3. **Guardrails.** Dry-run by default; DB-enforced dedupe (a parcel can never be claimed twice); ceilings enforced, never silently clamped; ambiguous payout matches surfaced to a human.
4. **Xero is the source of truth.** Claim values come from the matched invoice, not the shipment file. Conflicts are logged, never merged.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest                                   # everything should pass offline

cp .env.example .env                     # add your Xero app credentials
claimback auth                           # browser consent -> connect the DEMO COMPANY
python scripts/seed_xero.py              # seed demo invoices matching data/demo_shipments.csv

claimback detect data/demo_shipments.csv # see what's claimable and why
claimback run data/demo_shipments.csv    # full pipeline (dry-run)
claimback dashboard                      # recovered / pending / expiring
```

## Repo map

```
claimback/
├── models.py            # domain + claim state machine
├── ingest.py            # messy-CSV ingest, alias mapping + AI hook
├── detect.py            # named detection rules
├── db.py                # claims register (SQLite), dedupe guardrail
├── couriers/            # adapter per courier: ceiling, window, pack generator
│   └── demo.py          # "SwiftShip" synthetic courier (the pattern to copy)
├── xero/                # OAuth, API client, invoice matching, payout reconciliation
├── agent/               # tool definitions + system prompt for the agent loop
├── dashboard.py         # the money view
└── cli.py               # demo driver
```

## Hackathon build plan (what's deliberately NOT built yet)

| Slot | Build |
| --- | --- |
| Sat AM | Xero app + OAuth wired to the demo org; seed data in; end-to-end dry run |
| Sat PM | Real courier adapter(s) from publicly documented claim processes; agent loop on the Xero MCP server / Agent Toolkit |
| Sat eve | AI column-mapping (`ingest.ai_map_columns`) + damage-evidence handling |
| Sun AM | Payout reconciliation against the demo bank feed; `EVIDENCE_REQUESTED` resubmission flow |
| Sun PM | Web dashboard (recovered £ / pending £ / expiring claims), pitch + demo script |

**Demo script:** import messy CSV → "7 claimable shipments found, £158 recoverable, 2 expiring in 3 days" → file → receivables appear in Xero → payout lands → reconciled → dashboard shows £ recovered.

## Judging criteria mapping

- **Xero Connection (50%)** — real, quantifiable SMB problem; Xero is the system of record for claim value, receivables, and reconciliation, not an add-on.
- **API Integration (30%)** — Invoices, Contacts, Payments, BankTransactions + the Xero MCP/Agent Toolkit for agent actions.
- **Architecture (20%)** — state machine, adapter pattern, snapshot tests, rate-limit-aware client, guardrailed agent. Built to be financially trustworthy.
