"""Configuración de la conexión a PostgreSQL (lee DATABASE_URL del entorno o de .env)."""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import Engine, create_engine

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE_URL = "postgresql+psycopg://postgres:postgres@localhost:5432/judiciary"


def load_dotenv(path: Path = ROOT_DIR / ".env") -> None:
    """Carga KEY=VALUE de .env sin sobrescribir variables ya definidas en el entorno."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def get_database_url() -> str:
    load_dotenv()
    url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    # Forzar el driver psycopg3 (postgresql:// usaría psycopg2 por defecto).
    for prefix in ("postgresql://", "postgres://", "postgresql+psycopg2://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def get_engine(echo: bool = False) -> Engine:
    return create_engine(get_database_url(), echo=echo, pool_pre_ping=True)
