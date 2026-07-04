from datetime import date

from claimback.detect import detect
from claimback.ingest import ingest_csv
from claimback.models import ClaimType

TODAY = date(2026, 7, 2)


def test_demo_data_detections():
    shipments = ingest_csv("data/demo_shipments.csv")
    assert len(shipments) == 12

    detections = detect(shipments, today=TODAY)
    by_tracking = {d.shipment.tracking_number: d for d in detections}

    # Damage flags
    assert by_tracking["SW1000000006"].claim_type == ClaimType.DAMAGE
    assert by_tracking["SW1000000011"].claim_type == ClaimType.DAMAGE

    # Never scanned (label created >= 10 days ago, no scan)
    assert by_tracking["SW1000000003"].rule == "never_scanned"
    assert by_tracking["SW1000000007"].rule == "never_scanned"
    assert by_tracking["SW1000000012"].rule == "never_scanned"

    # Tracking dead-ends (last scan >= 10 days ago)
    assert by_tracking["SW1000000005"].rule == "tracking_dead_end"
    assert by_tracking["SW1000000009"].rule == "tracking_dead_end"

    # Delivered and recent in-transit are NOT claims
    for ok in ("SW1000000001", "SW1000000002", "SW1000000004", "SW1000000008", "SW1000000010"):
        assert ok not in by_tracking

    assert len(detections) == 7


def test_messy_column_names_are_mapped():
    shipments = ingest_csv("data/demo_shipments.csv")
    s = shipments[0]
    assert s.tracking_number == "SW1000000001"
    assert s.order_ref == "INV-1001"
    assert s.courier == "swiftship"
