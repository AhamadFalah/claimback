"""Shipment ingest with flexible column mapping.

Real-world shipment exports are messy: every platform names columns
differently, dates come in three formats, statuses are free text.
Deterministic aliases handle the common cases; the `ai_map_columns`
hook is where an LLM call maps anything unrecognised (Bounty 2 angle).
"""
from __future__ import annotations

import csv
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Optional

from .models import Shipment, ShipmentStatus

# Known aliases -> canonical field. Extend freely during the hack.
COLUMN_ALIASES: dict[str, str] = {
    "tracking_number": "tracking_number", "tracking": "tracking_number",
    "tracking no": "tracking_number", "barcode": "tracking_number",
    "consignment": "tracking_number", "parcel id": "tracking_number",
    "courier": "courier", "carrier": "courier", "shipping method": "courier",
    "courier service": "courier",
    "order_ref": "order_ref", "order no": "order_ref", "order number": "order_ref",
    "order id": "order_ref", "reference": "order_ref", "invoice ref": "order_ref",
    "shipped_at": "shipped_at", "ship date": "shipped_at", "despatch date": "shipped_at",
    "dispatched": "shipped_at",
    "last_scan_at": "last_scan_at", "last scan": "last_scan_at", "last update": "last_scan_at",
    "status": "status", "delivery status": "status", "tracking status": "status",
    "postcode": "postcode", "zip": "postcode", "post code": "postcode",
    "recipient": "recipient", "customer": "recipient", "name": "recipient",
    # 3PL columns (Mintsoft-style exports)
    "client": "client", "brand": "client", "client name": "client", "account": "client",
    "channel": "channel", "sales channel": "channel", "platform": "channel",
    "source": "channel", "marketplace": "channel",
    "declared value": "declared_value", "order value": "declared_value",
    "goods value": "declared_value", "value": "declared_value", "parcel value": "declared_value",
    # Mintsoft header spellings (no spaces/underscores after normalisation)
    "trackingnumber": "tracking_number", "consignmentnumber": "tracking_number",
    "ordernumber": "order_ref", "courierservice": "courier", "despatchdate": "shipped_at",
}

STATUS_ALIASES: dict[str, ShipmentStatus] = {
    "delivered": ShipmentStatus.DELIVERED,
    "in transit": ShipmentStatus.IN_TRANSIT, "in_transit": ShipmentStatus.IN_TRANSIT,
    "out for delivery": ShipmentStatus.IN_TRANSIT,
    "damaged": ShipmentStatus.DAMAGED, "arrived damaged": ShipmentStatus.DAMAGED,
    "no scan": ShipmentStatus.NO_SCAN, "no_scan": ShipmentStatus.NO_SCAN,
    "no tracking events": ShipmentStatus.NO_SCAN, "label created": ShipmentStatus.NO_SCAN,
    "returned": ShipmentStatus.RETURNED, "returned to sender": ShipmentStatus.RETURNED,
}

DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y")


def parse_date(value: str) -> Optional[date]:
    value = (value or "").strip()
    if not value:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unrecognised date: {value!r}")


def map_header(header: list[str]) -> dict[int, str]:
    """Map CSV column indices to canonical fields via aliases."""
    mapping: dict[int, str] = {}
    for i, col in enumerate(header):
        key = col.strip().lower().replace("_", " ")
        canonical = COLUMN_ALIASES.get(key) or COLUMN_ALIASES.get(key.replace(" ", "_"))
        if canonical:
            mapping[i] = canonical
    return mapping


def ai_map_columns(unmapped: list[str]) -> dict[str, str]:
    """HOOK: call an LLM to map unrecognised columns to canonical fields.

    Hackathon plan: send the header + 3 sample rows to Claude/GPT with the
    canonical schema, get back {column_name: canonical_field}. Cache the
    result per source so repeated imports are deterministic.
    """
    return {}


def ingest_csv(path: str | Path) -> list[Shipment]:
    path = Path(path)
    shipments: list[Shipment] = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = next(reader)
        mapping = map_header(header)
        if "tracking_number" not in mapping.values():
            raise ValueError(
                f"Could not find a tracking-number column in {header}. "
                "Wire ai_map_columns() to resolve this automatically."
            )
        for row in reader:
            if not any(cell.strip() for cell in row):
                continue
            data: dict = {}
            for i, field in mapping.items():
                if i >= len(row):
                    continue
                raw = row[i].strip()
                if field in ("shipped_at", "last_scan_at"):
                    data[field] = parse_date(raw)
                elif field == "status":
                    data[field] = STATUS_ALIASES.get(raw.lower(), ShipmentStatus.IN_TRANSIT)
                elif field == "declared_value":
                    data[field] = Decimal(raw.replace("£", "").replace(",", "")) if raw else None
                elif field == "channel":
                    data[field] = "amazon" if "amazon" in raw.lower() else "standard"
                elif field == "courier":
                    data[field] = raw.lower()  # adapter keys are lower-case
                else:
                    data[field] = raw
            shipments.append(Shipment(**data))
    return shipments
