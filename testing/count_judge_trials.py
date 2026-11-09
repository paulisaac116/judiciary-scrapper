"""Cuenta los juicios de cada juez de Pichincha con una sola llamada a la API por juez.

Lee docs/judges_pichincha.csv, consulta la API "juicios por materias y juez" con size=1
(solo interesa "totalRegistros") y muestra una tabla: id del juez, nombre y número de juicios.

Uso: uv run python testing/count_judge_trials.py
"""

from __future__ import annotations

import asyncio
import csv
import sys
from pathlib import Path

import httpx
import orjson
from aiolimiter import AsyncLimiter

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "src"))

from scrapper import HEADERS, URL_TRIALS_SEARCH  # noqa: E402

CSV_PATH = ROOT_DIR / "docs" / "judges_pichincha.csv"
AREAS_PATH = ROOT_DIR / "json" / "areas.json"
OUTPUT_PATH = ROOT_DIR / "testing" / "judge_trials_count.csv"
RATE = 5.0  # peticiones por segundo
CONCURRENCY = 5
TIMEOUT = 60.0
MAX_ATTEMPTS = 4


def read_judges() -> list[tuple[str, str]]:
    with CSV_PATH.open(newline="", encoding="utf-8-sig") as f:
        return [(row["id"].strip(), row["name"].strip()) for row in csv.DictReader(f)]


def read_area_ids() -> list[int]:
    return [area["id"] for area in orjson.loads(AREAS_PATH.read_bytes())]


async def count_trials(
    client: httpx.AsyncClient,
    limiter: AsyncLimiter,
    semaphore: asyncio.Semaphore,
    judge_id: str,
    area_ids: list[int],
) -> int | None:
    body = {
        "resultadoSearch": {
            "idMateria": area_ids,
            "identificacionJuez": judge_id,
            "fechaInicio": "",
            "fechaFin": "",
            "idProvincia": "",
        }
    }
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            async with semaphore, limiter:
                response = await client.post(URL_TRIALS_SEARCH, params={"page": 1, "size": 100}, json=body)
            response.raise_for_status()
            return int(response.json().get("totalRegistros") or 0)
        except (httpx.HTTPError, ValueError) as exc:
            if attempt == MAX_ATTEMPTS:
                print(f"Error con el juez {judge_id}: {exc!r}", file=sys.stderr)
                return None
            await asyncio.sleep(2**attempt)
    return None


async def main() -> None:
    judges = read_judges()
    area_ids = read_area_ids()
    limiter = AsyncLimiter(max_rate=RATE, time_period=1)
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async with httpx.AsyncClient(headers=HEADERS, timeout=TIMEOUT) as client:
        totals = await asyncio.gather(
            *(count_trials(client, limiter, semaphore, judge_id, area_ids) for judge_id, _ in judges)
        )

    rows = [(judge_id, name, total) for (judge_id, name), total in zip(judges, totals)]
    with OUTPUT_PATH.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_NONNUMERIC)
        writer.writerow(["id", "name", "total_trials"])
        writer.writerows((judge_id, name, "" if total is None else total) for judge_id, name, total in rows)

    name_width = max(len(name) for _, name, _ in rows)
    print(f"{'ID JUEZ':<12} {'NOMBRE':<{name_width}} {'JUICIOS':>8}")
    print(f"{'-' * 12} {'-' * name_width} {'-' * 8}")
    for judge_id, name, total in rows:
        print(f"{judge_id:<12} {name:<{name_width}} {'ERROR' if total is None else total:>8}")
    print(f"\nJueces: {len(rows)} | Juicios: {sum(t or 0 for _, _, t in rows)} | Errores: {sum(t is None for _, _, t in rows)}")


if __name__ == "__main__":
    asyncio.run(main())
