"""Modelos SQLAlchemy 2.0 de la base "judiciary" (equivalentes a docs/db_structure.sql)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, MetaData, SmallInteger, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Mismos nombres que genera PostgreSQL por defecto en db_structure.sql.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "pk": "%(table_name)s_pkey",
    "fk": "%(table_name)s_%(column_0_name)s_fkey",
}

EMPTY_OBJECT = text("'{}'::jsonb")
EMPTY_ARRAY = text("'[]'::jsonb")


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Area(Base):
    __tablename__ = "areas"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(Text)


class Judge(Base):
    __tablename__ = "judges"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    total_trials: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))


class Trial(Base):
    __tablename__ = "trials"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    crime: Mapped[str] = mapped_column(Text)
    judge_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("judges.id", onupdate="CASCADE", ondelete="SET NULL"), index=True
    )
    area_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("areas.id", onupdate="CASCADE", ondelete="SET NULL"), index=True
    )
    date_admission: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    action_type: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, server_default=EMPTY_OBJECT)


class IncidentTrial(Base):
    __tablename__ = "incidents_trials"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    trial_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("trials.id", onupdate="CASCADE", ondelete="CASCADE"), index=True
    )
    city: Mapped[str] = mapped_column(Text)
    jurisdictional_dependence: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actor: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=EMPTY_ARRAY)
    defendant: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=EMPTY_ARRAY)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, server_default=EMPTY_OBJECT)


class UpdateIncident(Base):
    __tablename__ = "updates_incidents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    incident_trial_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("incidents_trials.id", onupdate="CASCADE", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    visible: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, server_default=EMPTY_OBJECT)
