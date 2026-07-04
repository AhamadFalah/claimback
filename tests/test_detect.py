from datetime import date
from decimal import Decimal

from claimback.detect import detect
from claimback.ingest import ingest_csv
from claimback.models import ClaimType

TODAY = date(2026, 7, 2)


def test_demo_data_detections():
    shipments = ingest_csv("data/demo_shipments.csv")
    assert len(shipments) == 12

    detections = detect(shipments, today=TODAY)
    by_tracking = {d.shipment.tracking_number: d for d in detections}

    # Damage flags (claimability is decided later, per channel rules)
    assert by_tracking["EV1000000006"].claim_type == ClaimType.DAMAGE
    assert by_tracking["EV1000000011"].claim_type == ClaimType.DAMAGE

    # Never scanned (label created >= 10 days ago, no scan)
    assert by_tracking["EV1000000003"].rule == "never_scanned"
    assert by_tracking["EV1000000007"].rule == "never_scanned"
    assert by_tracking["EV1000000012"].rule == "never_scanned"

    # Tracking dead-ends (last scan >= 10 days ago)
    assert by_tracking["EV1000000005"].rule == "tracking_dead_end"
    assert by_tracking["EV1000000008"].rule == "tracking_dead_end"
    assert by_tracking["EV1000000009"].rule == "tracking_dead_end"

    # Delivered and recent in-transit are NOT flagged
    for ok in ("EV1000000001", "EV1000000002", "EV1000000004", "EV1000000010"):
        assert ok not in by_tracking

    assert len(detections) == 8


def test_3pl_columns_are_mapped():
    shipments = ingest_csv("data/demo_shipments.csv")
    s = shipments[0]
    assert s.tracking_number == "EV1000000001"
    assert s.order_ref == "INV-1001"
    assert s.courier == "evri"
    assert s.client == "OatSnax"
    assert s.channel == "standard"          # Shopify -> standard rules
    assert s.declared_value == Decimal("21.50")

    amazon = shipments[3]
    assert amazon.client == "ChocoLoco"
    assert amazon.channel == "amazon"
