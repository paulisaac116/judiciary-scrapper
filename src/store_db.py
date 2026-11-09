"""Carga los JSON generados por scrapper.py en la base PostgreSQL "judiciary".

Lee json/areas.json y, por cada juez de JUDGES, la carpeta json/<PROVINCE>/<CEDULA JUEZ>/
(judges.json, trials.json, incident_trials.json, updates_incidents.json).

Uso (también se pueden editar los valores por defecto en la sección CONFIGURACIÓN):
    uv run python src/store_db.py all
    uv run python src/store_db.py trials --province PICHINCHA
    uv run python src/store_db.py updates_incidents --file "json/PICHINCHA/<CEDULA>/updates_incidents.json" --batch-size 500

La carga es idempotente: INSERT ... ON CONFLICT (id) DO UPDATE.
Cada archivo se carga en una sola transacción (todo o nada).
Conexión: variable DATABASE_URL (entorno o .env).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

import orjson
from pydantic import ValidationError
from sqlalchemy import Connection, literal_column, select
from sqlalchemy.dialects.postgresql import insert

from db.config import get_engine
from db.models import Area, Base, IncidentTrial, Judge, Trial, UpdateIncident
from schemas import DATASETS, JSON_DIR

# =====================================================================
# CONFIGURACIÓN (valores por defecto; la CLI los sobrescribe)
# =====================================================================
# Carpeta de provincia (dentro de json/) de donde se lee la información.
PROVINCE = "PICHINCHA"
# Jueces cuya carpeta json/<PROVINCE>/<id>/ se carga en la base ("judge" solo se usa en los logs).
JUDGES: list[dict[str, str]] = [
    {"judge": "CHACON  ORTIZ FRANCISCO GABRIEL", "id": "1714913850"},
]
DEFAULT_TARGET = "all"
DEFAULT_BATCH_SIZE = 1000

# Dataset -> modelo ORM. El orden de DATASETS respeta las FKs.
ORM_MODELS: dict[str, type[Base]] = {
    "areas": Area,
    "judges": Judge,
    "trials": Trial,
    "incident_trials": IncidentTrial,
    "updates_incidents": UpdateIncident,
}

log = logging.getLogger("store_db")


def load_records(name: str, path: Path) -> list[dict[str, Any]]:
    """Lee el JSON y valida cada registro con el modelo Pydantic correspondiente."""
    model = DATASETS[name][2]
    if not path.exists() or path.stat().st_size == 0:
        log.warning("%s: archivo vacío o inexistente (%s)", name, path)
        return []
    raw = orjson.loads(path.read_bytes())
    records, invalid = [], 0
    for i, item in enumerate(raw):
        try:
            records.append(model.model_validate(item).model_dump())
        except ValidationError as exc:
            invalid += 1
            log.error("%s[%d] inválido, se omite: %s", name, i, exc.errors(include_url=False))
    if invalid:
        log.warning("%s: %d registros inválidos omitidos", name, invalid)
    return records


def _existing_ids(conn: Connection, model: type[Base]) -> set[Any]:
    return set(conn.scalars(select(model.id)))


def resolve_foreign_keys(conn: Connection, name: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Evita violaciones de FK.

    - trials: judge_id / area_id inexistentes -> NULL (columna opcional), con aviso.
    - incident_trials / updates_incidents: registros huérfanos -> se omiten, con aviso.
    """
    if name == "trials":
        judges, areas = _existing_ids(conn, Judge), _existing_ids(conn, Area)
        missing_judges, missing_areas = set(), set()
        for r in records:
            if r["judge_id"] is not None and r["judge_id"] not in judges:
                missing_judges.add(r["judge_id"])
                r["judge_id"] = None
            if r["area_id"] is not None and r["area_id"] not in areas:
                missing_areas.add(r["area_id"])
                r["area_id"] = None
        if missing_judges:
            log.warning("trials: %d jueces no existen en judges -> judge_id NULL: %s",
                        len(missing_judges), sorted(missing_judges)[:10])
        if missing_areas:
            log.warning("trials: materias no existen en areas -> area_id NULL: %s", sorted(missing_areas))
        return records

    parent = {"incident_trials": ("trial_id", Trial), "updates_incidents": ("incident_trial_id", IncidentTrial)}
    if name in parent:
        column, parent_model = parent[name]
        valid_ids = _existing_ids(conn, parent_model)
        kept = [r for r in records if r[column] in valid_ids]
        if len(kept) != len(records):
            log.warning("%s: %d registros huérfanos (%s inexistente) omitidos",
                        name, len(records) - len(kept), column)
        return kept
    return records


def upsert(conn: Connection, name: str, records: list[dict[str, Any]], batch_size: int) -> tuple[int, int]:
    """INSERT ... ON CONFLICT (id) DO UPDATE por lotes. Devuelve (insertados, actualizados)."""
    model = ORM_MODELS[name]
    table = model.__table__
    inserted = updated = 0
    for start in range(0, len(records), batch_size):
        batch = records[start:start + batch_size]
        stmt = insert(table).values(batch)
        stmt = stmt.on_conflict_do_update(
            index_elements=[table.c.id],
            set_={c.name: stmt.excluded[c.name] for c in table.columns if not c.primary_key},
        ).returning(literal_column("(xmax = 0)").label("inserted"))
        flags = conn.scalars(stmt).all()
        batch_inserted = sum(flags)
        inserted += batch_inserted
        updated += len(flags) - batch_inserted
    return inserted, updated


def store(name: str, path: Path, batch_size: int) -> None:
    records = load_records(name, path)
    if not records:
        return
    engine = get_engine()
    with engine.begin() as conn:
        records = resolve_foreign_keys(conn, name, records)
        inserted, updated = upsert(conn, name, records, batch_size)
    log.info("%s -> tabla %s: %d insertados, %d actualizados (archivo: %s)",
             name, DATASETS[name][1], inserted, updated, path)


def judge_directory(province: str, judge: dict[str, str]) -> Path | None:
    """Carpeta del juez si existe y su judges.json corresponde al id configurado; None si no."""
    directory = JSON_DIR / province / judge["id"].strip()
    if not directory.is_dir():
        log.error("Juez %s: no existe la carpeta %s", judge["id"], directory)
        return None
    judges_path = directory / DATASETS["judges"][0]
    if not judges_path.exists() or judges_path.stat().st_size == 0:
        log.error("Juez %s: falta %s", judge["id"], judges_path)
        return None
    stored_ids = {str(j.get("id")) for j in orjson.loads(judges_path.read_bytes())}
    if stored_ids != {judge["id"]}:
        log.error("Juez %s: %s contiene ids %s; se omite", judge["id"], judges_path, sorted(stored_ids))
        return None
    return directory


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Carga los JSON del scraper en PostgreSQL (judiciary)")
    parser.add_argument(
        "target",
        nargs="?",
        default=DEFAULT_TARGET,
        choices=[*DATASETS, "all"],
        help="Archivo/tabla a cargar ('all' carga todos en orden de dependencias)",
    )
    parser.add_argument("--province", default=PROVINCE, help=f"Carpeta de provincia dentro de json/ (default: {PROVINCE})")
    parser.add_argument("--file", type=Path, help="Ruta del JSON (solo con un target concreto; ignora JUDGES)")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="Registros por INSERT")
    parser.add_argument("-v", "--verbose", action="store_true", help="Mostrar SQL")
    args = parser.parse_args(argv)
    if args.file and args.target == "all":
        parser.error("--file requiere un target concreto, no 'all'")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.verbose:
        logging.getLogger("sqlalchemy.engine").setLevel(logging.INFO)

    if args.file:
        store(args.target, args.file, args.batch_size)
        return 0

    targets = list(DATASETS) if args.target == "all" else [args.target]
    if "areas" in targets:
        store("areas", JSON_DIR / DATASETS["areas"][0], args.batch_size)

    per_judge = [name for name in targets if name != "areas"]
    status = 0
    for judge in JUDGES if per_judge else []:
        directory = judge_directory(args.province, judge)
        if directory is None:
            status = 1
            continue
        log.info("=== Juez %s (%s) <- %s", judge["id"], judge["judge"], directory)
        for name in per_judge:
            store(name, directory / DATASETS[name][0], args.batch_size)
    return status


if __name__ == "__main__":
    sys.exit(main())
