"""Modelos Pydantic v2 con la estructura de cada archivo JSON / tabla.

Compartidos por scrapper.py (genera los JSON) y store_db.py (valida antes de insertar).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

ROOT_DIR = Path(__file__).resolve().parent.parent
JSON_DIR = ROOT_DIR / "json"
SMALLINT_MAX = 32767


def clean_name(value: str) -> str:
    """Deja las palabras separadas por un solo espacio ("BORJA  SOLANO" -> "BORJA SOLANO")."""
    return " ".join(value.split())


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Area(_Record):
    id: int
    name: str


class Judge(_Record):
    id: str  # cédula como texto: conserva ceros iniciales
    name: str
    total_trials: int = Field(default=0, ge=0, le=SMALLINT_MAX)  # totalRegistros de la API

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str) -> str:
        return clean_name(value)


class Trial(_Record):
    id: str
    crime: str
    judge_id: str | None = None
    area_id: int | None = None
    date_admission: datetime | None = None
    action_type: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class IncidentTrial(_Record):
    id: int
    trial_id: str
    city: str
    jurisdictional_dependence: str
    created_at: datetime | None = None
    actor: list[dict[str, Any]] = Field(default_factory=list)
    defendant: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class UpdateIncident(_Record):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    id: UUID
    incident_trial_id: int
    type: str
    content: str
    visible: str
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


# Nombre lógico -> (archivo JSON, tabla, modelo). El orden respeta las FKs.
DATASETS: dict[str, tuple[str, str, type[_Record]]] = {
    "areas": ("areas.json", "areas", Area),
    "judges": ("judges.json", "judges", Judge),
    "trials": ("trials.json", "trials", Trial),
    "incident_trials": ("incident_trials.json", "incidents_trials", IncidentTrial),
    "updates_incidents": ("updates_incidents.json", "updates_incidents", UpdateIncident),
}
