"""Agent orchestration layer (hackathon build target).

The deterministic pipeline (ingest -> detect -> match -> pack -> file ->
reconcile) is exposed here as TOOLS for an LLM agent. The agent decides
WHAT to do (triage unmatched shipments, resolve messy column mappings,
draft dispute responses, chase expiring claims); the tools guarantee that
whatever it does to money is valid.

Wiring options at the hackathon:
  * Xero Agent Toolkit / MCP server (github.com/XeroAPI/xero-agent-toolkit)
    for the Xero-side tools — judges will want to see this.
  * Anthropic/OpenAI tool-use loop for orchestration.

Guardrails (non-negotiable, mirror production automation practice):
  * dry_run defaults ON — the agent proposes, a human approves the first N runs
  * dedupe: a tracking number can never be claimed twice (DB-enforced)
  * pack validation failure aborts the whole batch
  * the agent NEVER edits claim values — it can only call claim_value()
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class Tool:
    name: str
    description: str
    fn: Callable[..., Any]


def build_tools(register, xero_client) -> list[Tool]:
    from ..detect import detect
    from ..ingest import ingest_csv
    from ..xero.matching import match_claims, reconcile_payouts
    from ..couriers import get_adapter

    return [
        Tool("ingest_shipments", "Load a shipment CSV (messy columns handled)", ingest_csv),
        Tool("detect_claimables", "Run detection rules over shipments", detect),
        Tool("match_to_invoices", "Match detections to Xero invoices and set claim values",
             lambda detections: match_claims(xero_client, detections)),
        Tool("generate_claim_pack", "Generate the courier's byte-exact submission file",
             lambda courier, claims: get_adapter(courier).generate_pack(claims)),
        Tool("reconcile_payouts",
             "Match bank payouts to filed claims; returns (matches, ambiguous) — "
             "ambiguous payouts must go to the operator, never be applied",
             lambda filed: reconcile_payouts(xero_client, filed)),
    ]


SYSTEM_PROMPT = """\
You are ClaimBack, an autonomous claims-recovery agent for a small business.
Your job: make sure no recoverable courier compensation is ever left behind.
You may use the provided tools. You must never invent claim values, never
resubmit an already-filed tracking number, and always surface ambiguous
matches to the operator instead of guessing. Prioritise claims nearest
their filing deadline.
"""
