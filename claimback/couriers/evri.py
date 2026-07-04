"""Evri adapters — the 3PL's workhorse courier, with channel-specific rules.

Compensation rules differ by SALES CHANNEL, not just courier:
  * standard Evri:      ceiling £25, LOSS claims only — damage is not claimable,
                        so damaged standard-channel parcels are surfaced as
                        write-off exposure instead of doomed claims.
  * Evri via Amazon:    ceiling £20, LOSS and DAMAGE claimable.

Pack format is deliberately strict (fixed header, CRLF, no quoting, UTF-8
no BOM) and snapshot-tested byte-for-byte — courier portals reject bulk
claim files over invisible differences.
"""
from __future__ import annotations

from decimal import Decimal

from ..models import Claim, ClaimType
from .base import ClaimPackError, CourierAdapter, register

HEADER = "CLAIM REF,TRACKING NO,ORDER REF,POSTCODE,CLAIM TYPE,PARCEL VALUE,COMMENTS"
FORBIDDEN = (",", '"', "\n", "\r")


def _sanitise(value: str) -> str:
    out = value
    for ch in FORBIDDEN:
        out = out.replace(ch, " ")
    return out.strip()


class _EvriPackFormat(CourierAdapter):
    """Shared bulk-claim CSV format; ceiling/eligibility set per subclass."""

    COMMENTS = {
        ClaimType.LOSS: "Parcel lost in network - no tracking movement",
        ClaimType.DAMAGE: "Item arrived damaged",
    }

    def generate_pack(self, claims: list[Claim]) -> bytes:
        lines = [HEADER]
        for i, c in enumerate(claims, start=1):
            if c.claim_type not in self.eligible_types:
                raise ClaimPackError(f"{c.tracking_number}: {c.claim_type.value} not eligible for {self.name}")
            if c.claim_value is None:
                raise ClaimPackError(f"{c.tracking_number}: claim has no value set")
            if c.claim_value > self.ceiling:
                raise ClaimPackError(
                    f"{c.tracking_number}: value {c.claim_value} exceeds ceiling {self.ceiling}"
                )
            row = [
                f"CB{i:04d}",
                _sanitise(c.tracking_number),
                _sanitise(c.order_ref),
                "",  # postcode joined during matching if needed
                c.claim_type.value.upper(),
                str(c.claim_value),  # exact Decimal — truncating pence understates the claim
                self.COMMENTS[c.claim_type],
            ]
            lines.append(",".join(row))
        # CRLF on every line including the last; UTF-8, no BOM; binary output.
        pack = ("\r\n".join(lines) + "\r\n").encode("utf-8")
        self.validate_pack(pack)
        return pack

    def validate_pack(self, pack: bytes) -> None:
        if pack.startswith(b"\xef\xbb\xbf"):
            raise ClaimPackError("BOM detected — Evri portal reads it as part of column 1")
        if not pack.endswith(b"\r\n"):
            raise ClaimPackError("Missing trailing CRLF")
        text = pack.decode("utf-8")
        lines = text.split("\r\n")
        if lines[0] != HEADER:
            raise ClaimPackError(f"Header mismatch: {lines[0]!r}")
        for n, line in enumerate(lines[1:], start=2):
            if not line:
                continue
            if line.count(",") != HEADER.count(","):
                raise ClaimPackError(f"Line {n}: wrong column count")
            if '"' in line:
                raise ClaimPackError(f"Line {n}: quoting is forbidden — sanitise at source")


class EvriAdapter(_EvriPackFormat):
    name = "evri"
    ceiling = Decimal("25")
    claim_window_days = 28
    eligible_types = {ClaimType.LOSS}          # damage is NOT claimable on standard Evri


class EvriAmazonAdapter(_EvriPackFormat):
    name = "evri:amazon"
    ceiling = Decimal("20")
    claim_window_days = 28
    eligible_types = {ClaimType.LOSS, ClaimType.DAMAGE}


register(EvriAdapter())
register(EvriAmazonAdapter())
