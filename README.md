# ClaimBack

**Autonomous courier-claims recovery for 3PLs, powered by Xero.**

Built from real fulfilment-ops experience: a UK 3PL ships thousands of parcels a week for dozens of client brands across Evri, DPD, Royal Mail and more. When parcels are lost or damaged, the courier owes compensation — but claiming is per-courier, per-channel, deadline-bound and formatting-fussy, and couriers fight claims hard. Most recoverable money is never recovered, and what is recovered rarely finds its way back to the right client cleanly. ClaimBack finds claimable shipments, files byte-perfect claims, fights the disputes, and distributes every recovered pound to the right client through Xero.

**Target bounty:** 💸 Cash Flow Accelerator (analyse Xero data → surface actionable insight → take autonomous action → measurable cash outcome). The messy-WMS-export ingest also speaks to 🔗 The Vibe Integrator.

## How it works

```
WMS shipment export (Mintsoft-style CSV)        Xero (Accounting API)
        │                                              │
        ▼                                              │
   INGEST  ──column-alias + AI mapping──               │
        │                                              │
        ▼                                              │
   DETECT ──named rules──► VALUE ── min(declared value, channel ceiling)
        │                    │       Evri std: £25 loss-only · Evri×Amazon: £20 loss+damage
        │                    │       unwinnable claims REFUSED and surfaced as write-off exposure
        │                    ▼
        │             CLAIM PACK (byte-exact per rule set, golden-file tested)
        │                    │
        │                    ▼
        │              FILE ──► claim receivable posted to Xero ──────────────┐
        │                    │   (money-in-flight visible, pack attached)     │
        │                    ▼                                                ▼
        │             OUTCOMES ──courier fight: paid / declined /       RECONCILE ── payout in bank
        │                    │    info_requested (auto-resubmit ≤2)          │       feed matched to claim
        │                    │                                               ▼
        │                    ▼                                        CREDIT NOTE to the client
        │              write-off ledger (per client & courier)        (pass-through, CLAIM-<tracking>)
```

Claim lifecycle is a strict state machine: `DETECTED → MATCHED → READY → FILED → (PAID | REJECTED | EVIDENCE_REQUESTED) → RECONCILED`. Illegal transitions raise — money never moves through an undefined path.

## Design principles

1. **Deterministic where money moves, AI where data is messy.** Claim values, submission files, state transitions and Xero postings are plain tested code. The AI layer handles column mapping, triage, and next-action decisions via tools (`claimback/agent/orchestrator.py`).
2. **Channel-aware courier rules.** Compensation rules differ per sales channel, not just per courier (standard Evri: £25 loss-only; Evri via Amazon: £20 loss+damage). Unwinnable claims are refused up-front and surfaced as write-off exposure — never filed to fail.
3. **Byte-exact claim packs.** Courier parsers reject files over invisible differences. Packs are generated as bytes and snapshot-tested against golden fixtures (`tests/test_claim_pack.py`). A validation failure aborts the whole batch.
4. **Guardrails.** Dry-run by default; DB-enforced dedupe (a parcel can never be claimed twice); ceilings enforced, never silently clamped; ambiguous payout matches surfaced to a human, never auto-applied.
5. **Xero is the money ledger.** Filed claims are receivables (money-in-flight is visible); payouts reconcile against them; recovered money passes through to the right client as a tracking-referenced credit note. The WMS export is the facts ledger; Xero is where the money is true.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,api]"
pytest                                   # everything passes offline (22 tests incl. full e2e sim)

cp .env.example .env                     # add your Xero app credentials
claimback auth                           # browser consent -> connect the DEMO COMPANY
python scripts/seed_xero.py              # seed the 3PL's client accounts

claimback detect data/demo_shipments.csv # see what's flagged and why
claimback run data/demo_shipments.csv    # value + pack (dry-run): 7 claims £148.59, 1 refused
DRY_RUN=false claimback file evri        # receivables posted, packs attached
claimback outcomes data/demo_outcomes.csv# the courier fight: 1 declined, 1 evidence loop
python scripts/seed_payouts.py           # simulate payouts landing in the bank feed
DRY_RUN=false claimback reconcile        # payments applied + client credit notes raised
claimback dashboard                      # recovered / pending / written-off, split by client
```

## Repo map

```
claimback/
├── models.py            # domain + claim state machine (client, channel, declared value)
├── ingest.py            # messy WMS-CSV ingest, alias mapping + AI hook
├── detect.py            # named detection rules
├── valuation.py         # min(declared value, channel ceiling); refusals for unwinnable claims
├── outcomes.py          # courier response ingestion — the dispute loop
├── db.py                # claims register (SQLite), dedupe guardrail
├── couriers/            # adapter per (courier, channel): ceiling, window, eligible types, pack
│   └── evri.py          # Evri standard (£25 loss-only) + Evri×Amazon (£20 loss+damage)
├── xero/                # OAuth, API client, payout reconciliation, credit notes
├── agent/               # tool definitions + system prompt for the agent loop
├── dashboard.py         # the money view, split by client
└── cli.py               # demo driver
```

## Demo script

Import WMS export → "8 flagged, 7 claimable (£148.59), 1 unwinnable damage refused (£30.50 exposure surfaced)" → file → receivables + attached packs appear in Xero → courier fights: one declined (write-off), one evidence request auto-resubmitted → payouts land → reconciled → **credit notes appear on each client's Xero account** → dashboard: £82.60 recovered, £50 in flight, £46.49 written off, split by client.

## Judging criteria mapping

- **Xero Connection (50%)** — a real, quantifiable 3PL problem lived weekly; Xero is the system of record for money-in-flight, recovery, and client distribution, not an add-on.
- **API Integration (30%)** — Invoices, Contacts, Payments, CreditNotes, BankTransactions, Attachments + the Xero MCP server for agent actions.
- **Architecture (20%)** — state machine, channel-aware adapter pattern, snapshot tests, rate-limit-aware client (Retry-After honoured), guardrailed agent. Built to be financially trustworthy.
