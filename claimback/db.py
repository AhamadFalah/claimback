"""SQLite persistence — the claims register.

The register is the single source of truth for claim state. Dedupe on
tracking_number is enforced at the DB level: a courier can only ever be
claimed once per parcel (double-claiming is how you lose a courier account).
"""
from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Iterable, Optional

from sqlalchemy import String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from .models import Claim, ClaimStatus


class Base(DeclarativeBase):
    pass


class ClaimRow(Base):
    __tablename__ = "claims"

    tracking_number: Mapped[str] = mapped_column(String(64), primary_key=True)  # dedupe guardrail
    courier: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), index=True)
    payload: Mapped[str] = mapped_column(Text)  # full Claim as JSON


def _default(o):
    if isinstance(o, (Decimal,)):
        return str(o)
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    raise TypeError(type(o))


class Register:
    def __init__(self, db_path: str = "claimback.db"):
        self.engine = create_engine(f"sqlite:///{db_path}")
        Base.metadata.create_all(self.engine)

    def upsert(self, claim: Claim) -> None:
        with Session(self.engine) as s:
            row = s.get(ClaimRow, claim.tracking_number)
            payload = json.dumps(claim.model_dump(mode="json"), default=_default)
            if row is None:
                s.add(ClaimRow(
                    tracking_number=claim.tracking_number,
                    courier=claim.courier,
                    status=claim.status.value,
                    payload=payload,
                ))
            else:
                row.status = claim.status.value
                row.payload = payload
            s.commit()

    def exists(self, tracking_number: str) -> bool:
        with Session(self.engine) as s:
            return s.get(ClaimRow, tracking_number) is not None

    def get(self, tracking_number: str) -> Optional[Claim]:
        with Session(self.engine) as s:
            row = s.get(ClaimRow, tracking_number)
            return Claim.model_validate_json(row.payload) if row else None

    def by_status(self, *statuses: ClaimStatus) -> list[Claim]:
        vals = [st.value for st in statuses]
        with Session(self.engine) as s:
            rows = s.scalars(select(ClaimRow).where(ClaimRow.status.in_(vals))).all()
            return [Claim.model_validate_json(r.payload) for r in rows]

    def all(self) -> list[Claim]:
        with Session(self.engine) as s:
            rows = s.scalars(select(ClaimRow)).all()
            return [Claim.model_validate_json(r.payload) for r in rows]
