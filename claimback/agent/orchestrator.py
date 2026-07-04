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
    from ..outcomes import ingest_outcomes
    from ..valuation import value_claims
    from ..xero.matching import reconcile_payouts
    from ..couriers import adapter_for

    return [
        Tool("ingest_shipments", "Load a WMS shipment CSV (messy columns handled)", ingest_csv),
        Tool("detect_claimables", "Run detection rules over shipments", detect),
        Tool("value_claims",
             "Value detections against channel-specific courier rules; returns "
             "(claims, refusals) — refusals are unwinnable, never file them",
             value_claims),
        Tool("generate_claim_pack", "Generate the courier's byte-exact submission file",
             lambda courier, channel, claims: adapter_for(courier, channel).generate_pack(claims)),
        Tool("ingest_courier_outcomes",
             "Apply a courier response file (paid/declined/info_requested) to the register",
             lambda path: ingest_outcomes(path, register)),
        Tool("reconcile_payouts",
             "Match bank payouts to open claims; returns (matches, ambiguous) — "
             "ambiguous payouts must go to the operator, never be applied",
             lambda open_claims: reconcile_payouts(xero_client, open_claims)),
    ]


SYSTEM_PROMPT = """\
You are ClaimBack, an autonomous claims-recovery agent for a UK 3PL.
You raise courier compensation claims on behalf of the 3PL's client brands,
fight rejections, and pass recovered money to the right client.
Your job: make sure no recoverable courier compensation is ever left behind.
You may use the provided tools. You must never invent claim values, never
resubmit an already-filed tracking number, and always surface ambiguous
matches to the operator instead of guessing. Prioritise claims nearest
their filing deadline.
"""
