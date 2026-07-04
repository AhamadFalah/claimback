"""Golden-file snapshot testing for claim packs (Evri, channel-aware rules).

The generated file must match the fixture BYTE FOR BYTE. If a change is
intentional, regenerate the fixture deliberately — never loosen the test.
"""
from decimal import Decimal
from pathlib import Path

import pytest

from claimback.couriers import ClaimPackError, adapter_for, get_adapter
from claimback.models import Claim, ClaimStatus, ClaimType

FIXTURE = Path(__file__).parent / "fixtures" / "evri_golden.csv"


def make_claim(tracking: str, order: str, value: str, ctype=ClaimType.LOSS) -> Claim:
    return Claim(
        tracking_number=tracking, courier="evri", claim_type=ctype,
        status=ClaimStatus.READY, order_ref=order,
        declared_value=Decimal(value), claim_value=Decimal(value),
    )


def batch() -> list[Claim]:
    return [
        make_claim("EV1000000003", "INV-1003", "18.40"),
        make_claim("EV1000000005", "INV-1005", "25"),
        make_claim("EV1000000009", "INV-1009", "15.99"),
    ]


def test_pack_matches_golden_file_byte_for_byte():
    pack = get_adapter("evri").generate_pack(batch())
    assert pack == FIXTURE.read_bytes()


def test_pack_structure():
    pack = get_adapter("evri").generate_pack(batch())
    assert not pack.startswith(b"\xef\xbb\xbf")      # no BOM
    assert pack.endswith(b"\r\n")                     # trailing CRLF
    assert b"18.40" in pack                           # pence never truncated
    lines = pack.decode().split("\r\n")
    assert lines[0].startswith("CLAIM REF,")


def test_channel_rules():
    # standard Evri: £25 ceiling, loss only
    std = adapter_for("evri", "standard")
    assert std.ceiling == Decimal("25")
    assert std.claim_value(Decimal("80")) == Decimal("25")
    # Amazon channel: £20 ceiling, loss AND damage
    amz = adapter_for("evri", "amazon")
    assert amz.name == "evri:amazon"
    assert amz.ceiling == Decimal("20")
    assert amz.claim_value(Decimal("23.75")) == Decimal("20")
    assert ClaimType.DAMAGE in amz.eligible_types
    assert ClaimType.DAMAGE not in std.eligible_types


def test_damage_not_packable_on_standard_evri():
    bad = make_claim("EV9999999999", "INV-9999", "18", ClaimType.DAMAGE)
    with pytest.raises(ClaimPackError):
        get_adapter("evri").generate_pack([bad])


def test_ceiling_violation_aborts_batch():
    bad = make_claim("EV9999999999", "INV-9999", "26")  # over the £25 ceiling
    with pytest.raises(ClaimPackError):
        get_adapter("evri").generate_pack([bad])


def test_dirty_fields_are_sanitised():
    dirty = make_claim('EV1"00,003', "INV,1003", "18")
    pack = get_adapter("evri").generate_pack([dirty])
    assert b'"' not in pack.split(b"\r\n")[1]
