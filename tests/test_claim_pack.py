"""Golden-file snapshot testing for claim packs.

The generated file must match the fixture BYTE FOR BYTE. If a change is
intentional, regenerate the fixture deliberately — never loosen the test.
"""
from decimal import Decimal
from pathlib import Path

import pytest

from claimback.couriers import ClaimPackError, get_adapter
from claimback.models import Claim, ClaimStatus, ClaimType

FIXTURE = Path(__file__).parent / "fixtures" / "swiftship_golden.csv"


def make_claim(tracking: str, order: str, value: str, ctype=ClaimType.LOSS) -> Claim:
    return Claim(
        tracking_number=tracking, courier="swiftship", claim_type=ctype,
        status=ClaimStatus.READY, order_ref=order,
        invoice_total=Decimal(value), claim_value=Decimal(value),
    )


def batch() -> list[Claim]:
    return [
        make_claim("SW1000000003", "INV-1003", "18"),
        make_claim("SW1000000006", "INV-1006", "25", ClaimType.DAMAGE),
        make_claim("SW1000000007", "INV-1007", "25"),
    ]


def test_pack_matches_golden_file_byte_for_byte():
    pack = get_adapter("swiftship").generate_pack(batch())
    assert pack == FIXTURE.read_bytes()


def test_pack_structure():
    pack = get_adapter("swiftship").generate_pack(batch())
    assert not pack.startswith(b"\xef\xbb\xbf")      # no BOM
    assert pack.endswith(b"\r\n")                     # trailing CRLF
    assert b"\r\n" in pack and b"\n" in pack
    lines = pack.decode().split("\r\n")
    assert lines[0].startswith("CLAIM REF,")


def test_ceiling_violation_aborts_batch():
    bad = make_claim("SW9999999999", "INV-9999", "26")  # over the £25 ceiling
    with pytest.raises(ClaimPackError):
        get_adapter("swiftship").generate_pack([bad])


def test_value_is_capped_at_ceiling():
    adapter = get_adapter("swiftship")
    assert adapter.claim_value(Decimal("80")) == Decimal("25")
    assert adapter.claim_value(Decimal("18")) == Decimal("18")


def test_dirty_fields_are_sanitised():
    dirty = make_claim('SW1"00,003', "INV,1003", "18")
    pack = get_adapter("swiftship").generate_pack([dirty])
    assert b'"' not in pack.split(b"\r\n")[1]
