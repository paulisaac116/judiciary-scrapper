"""Pruebas sin red de la paginación de juicios ante respuestas defectuosas de la API.

Simula la API con httpx.MockTransport: cada caso define, por offset de página, la secuencia de respuestas
(la última se repite). Un offset no definido responde una página vacía con el total real del caso.
Las esperas entre reintentos se reducen para que las pruebas sean rápidas.

Uso: uv run python testing/test_search_validation.py
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

import httpx
import orjson

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "src"))

import scrapper as s  # noqa: E402
from schemas import DATASETS  # noqa: E402

logging.basicConfig(level=logging.CRITICAL)
s.SEARCH_MAX_WAIT = 0.01  # tenacity añade además hasta 1 s de jitter por reintento


def page(n: int, total: int, start: int = 0, key: bool = True) -> dict[str, Any]:
    """Respuesta con n registros (ids J<start>..), totalRegistros=total; key=False omite la lista."""
    data: dict[str, Any] = {"totalRegistros": total}
    if key:
        data["resultadoProcesosResueltos"] = [
            {"idJuicio": f"J{start + i}", "nombreDelito": "DELITO", "fechaIngreso": "2022-01-01 10:00:00.000"}
            for i in range(n)
        ]
    return data


def page_ids(ids: list[int], total: int) -> dict[str, Any]:
    """Respuesta con los ids J<n> indicados (permite simular repetidos y juicios saltados)."""
    return {"totalRegistros": total, "resultadoProcesosResueltos": [
        {"idJuicio": f"J{i}", "nombreDelito": "DELITO", "fechaIngreso": "2022-01-01 10:00:00.000"} for i in ids
    ]}


BROKEN = {"error": "sin totalRegistros"}


def mock_api(script: dict[int, list[Any]], real_total: int, calls: dict[int, int]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if "getInformacionJuicio" in request.url.path:  # detalle de juicio (etapa trials completa)
            return httpx.Response(200, json=[{"nombreDelito": "DELITO", "idMateria": 1}])
        offset = int(request.url.params["page"])
        calls[offset] = calls.get(offset, 0) + 1
        seq = script.get(offset) or [page(0, real_total)]
        return httpx.Response(200, json=seq[min(calls[offset], len(seq)) - 1])

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def run_search(script: dict[int, list[Any]], real_total: int, max_pages: int = 0):
    calls: dict[int, int] = {}
    async with s.ApiClient(1000, 5, 10) as api:
        await api._client.aclose()
        api._client = mock_api(script, real_total, calls)
        results, total, failed_page, _ = await s._search_trials_for_judge(
            api, "X", {"resultadoSearch": {}}, 1, 100, max_pages
        )
        return len({r["idJuicio"] for r in results}), total, api.failures, failed_page, calls


async def run_passes(script: dict[int, list[Any]], real_total: int):
    calls: dict[int, int] = {}
    async with s.ApiClient(1000, 5, 10) as api:
        await api._client.aclose()
        api._client = mock_api(script, real_total, calls)
        listings, total, failed_page = await s._search_with_extra_passes(
            api, "X", {"resultadoSearch": {}}, 1, 100, 0, 0
        )
        return len({r["idJuicio"] for r in listings}), total, failed_page, calls


# Riesgo 1. Total 250 filas = J0..J249. "Saltado": J99 repetido en page 101 y J199 ausente.
SKIPPED_101 = page_ids(list(range(99, 199)), 250)
GOOD = {1: page_ids(list(range(0, 100)), 250), 101: page_ids(list(range(100, 200)), 250),
        201: page_ids(list(range(200, 250)), 250)}
PASS_CASES: dict[str, tuple[dict[int, list[Any]], int, tuple[int, int, int], Callable[[dict], bool]]] = {
    "R1 sin duplicados -> una sola pasada": (
        {k: [v] for k, v in GOOD.items()}, 250, (250, 250, 0), lambda c: c[1] == 1),
    "R1 duplicado DENTRO de una página -> sin pasada extra": (
        {1: [page_ids([0] + list(range(0, 99)), 250)], 101: [GOOD[101]], 201: [GOOD[201]]},
        250, (249, 250, 0), lambda c: c[1] == 1),
    "R1 duplicado ENTRE páginas -> 2.ª pasada recupera el juicio saltado": (
        {1: [GOOD[1]], 101: [SKIPPED_101, GOOD[101]], 201: [GOOD[201]]},
        250, (250, 250, 0), lambda c: c[1] == 2 and c[101] == 2),
    "R1 duplicado entre páginas estable (dato repetido) -> se detiene en la 2.ª pasada": (
        {1: [GOOD[1]], 101: [SKIPPED_101], 201: [GOOD[201]]},
        250, (249, 250, 0), lambda c: c[1] == 2),
    "R1 orden inestable en varias pasadas -> máximo LISTING_PASSES": (
        {1: [GOOD[1]], 101: [SKIPPED_101, page_ids(list(range(99, 198)) + [199], 250), SKIPPED_101],
         201: [GOOD[201]]},
        250, (250, 250, 0), lambda c: c[1] == s.LISTING_PASSES),
}


# nombre -> (script, total real, (únicos, total, fallos, página fallida) esperados, comprobación sobre calls)
CASES: dict[str, tuple[dict[int, list[Any]], int, tuple[int, int, int, int], Callable[[dict], bool] | None]] = {
    # --- Respuestas incompletas (fase anterior) ---
    "1 registro y luego OK": (
        {1: [page(1, 250), page(100, 250)], 101: [page(100, 250, 100)], 201: [page(50, 250, 200)]},
        250, (250, 250, 0, 0), None),
    "página vacía con total>0": (
        {1: [page(0, 250), page(100, 250)], 101: [page(100, 250, 100)], 201: [page(50, 250, 200)]},
        250, (250, 250, 0, 0), None),
    "sin clave resultadoProcesosResueltos": (
        {1: [page(0, 250, key=False), page(100, 250)], 101: [page(100, 250, 100)], 201: [page(50, 250, 200)]},
        250, (250, 250, 0, 0), None),
    "total=0 con registros": (
        {1: [page(1, 0), page(100, 250)], 101: [page(100, 250, 100)], 201: [page(50, 250, 200)]},
        250, (250, 250, 0, 0), None),
    "total menor que el ya visto": (
        {1: [page(100, 250)], 101: [page(100, 132, 100), page(100, 250, 100)], 201: [page(50, 250, 200)]},
        250, (250, 250, 0, 0), None),
    "falla en página intermedia": (
        {1: [page(100, 250)], 101: [page(0, 250), page(1, 250), page(100, 250, 100)], 201: [page(50, 250, 200)]},
        250, (250, 250, 0, 0), None),
    "R3 juez sin juicios (0/0) se confirma END_CONFIRMATIONS veces en la misma página": (
        {1: [page(0, 0)]}, 0, (0, 0, 0, 0), lambda calls: calls == {1: 1 + s.END_CONFIRMATIONS}),
    "R3 0/0 falso: la confirmación revela juicios y se recupera la página": (
        {1: [page(0, 0), page(0, 0), page(100, 250)], 101: [page(100, 250, 100)], 201: [page(50, 250, 200)]},
        250, (250, 250, 0, 0), None),
    "R3 confirmación de 0/0 imposible -> last_page_failed=1": (
        {1: [page(0, 0), BROKEN]}, 0, (0, 0, s.END_CONFIRMATIONS, 1), None),
    "última página de 1 registro legítima": (
        {1: [page(100, 101)], 101: [page(1, 101, 100)]}, 101, (101, 101, 0, 0), None),
    # --- Riesgo 2: total falso (menor al real) ---
    "R2 página llena con total falso 290 (real 350)": (
        {1: [page(100, 290)], 101: [page(100, 290, 100)], 201: [page(100, 290, 200)],
         301: [page(0, 290), page(50, 350, 300)]},
        350, (350, 350, 0, 0), None),
    "R2 última página corta aceptada con total falso (real 250)": (
        {1: [page(100, 150)], 101: [page(50, 150, 100), page(100, 250, 100)], 201: [page(50, 250, 200)]},
        250, (250, 250, 0, 0), None),
    "R2 confirmación solo con total mayor (sin registros)": (
        {1: [page(100, 200)], 101: [page(100, 200, 100)], 201: [page(0, 290), page(0, 200), page(90, 290, 200)]},
        290, (290, 290, 0, 0), None),
    "R2 fin real: confirma END_CONFIRMATIONS veces y termina": (
        {1: [page(100, 250)], 101: [page(100, 250, 100)], 201: [page(50, 250, 200)]},
        250, (250, 250, 0, 0), lambda calls: calls.get(301) == s.END_CONFIRMATIONS),
    "R2 max-pages no dispara confirmación": (
        {1: [page(100, 250)]}, 250, (100, 250, 0, 0), lambda calls: set(calls) == {1}),
    "R2 total mayor en respuesta RECHAZADA se adopta (real 350)": (
        {1: [page(100, 290)], 101: [page(0, 350), page(100, 350, 100)], 201: [page(100, 350, 200)],
         301: [page(50, 350, 300)]},
        350, (350, 350, 0, 0), lambda calls: calls[101] == 2),
    "R2 total real conservado aunque la paginación falle": (
        {1: [page(100, 290)], 101: [page(0, 3173)]},
        3173, (100, 3173, 1, 101), None),
    # --- Opción (a): más intentos en la búsqueda; opción (b): página fallida para reanudar ---
    "(a) 10 respuestas malas seguidas y luego OK": (
        {1: [page(100, 250)], 101: [page(1, 250)] * 10 + [page(100, 250, 100)], 201: [page(50, 250, 200)]},
        250, (250, 250, 0, 0), lambda calls: calls[101] == 11),
    "(b) falla persistente -> last_page_failed=101": (
        {1: [page(100, 250)], 101: [page(1, 250)]},
        250, (100, 250, 1, 101), lambda calls: calls[101] == s.SEARCH_MAX_ATTEMPTS),
    "(b) confirmación imposible -> last_page_failed=301": (
        {1: [page(100, 250)], 101: [page(100, 250, 100)], 201: [page(50, 250, 200)], 301: [BROKEN]},
        250, (250, 250, s.END_CONFIRMATIONS, 301), None),
}


def stage_args(province: str, resume: bool) -> argparse.Namespace:
    return argparse.Namespace(
        page=1, size=100, max_pages=0, trial_ids=None, merge=False, resume=resume, province=province,
    )


async def run_stage(directory: Path, script: dict[int, list[Any]], real_total: int, resume: bool):
    calls: dict[int, int] = {}
    async with s.ApiClient(1000, 5, 10) as api:
        await api._client.aclose()
        api._client = mock_api(script, real_total, calls)
        await s._trials_stage(api, stage_args(str(directory.parent), resume), "X", "JUEZ X", {"resultadoSearch": {}},
                              directory)
    judge = s.read_json("judges", directory)
    return (judge[0] if judge else None), len(s.read_json("trials", directory)), calls


async def resume_tests() -> list[tuple[str, bool, str]]:
    """Ejecución con fallo -> --resume completa desde last_page_failed -> --resume omite al juez completo."""
    out: list[tuple[str, bool, str]] = []
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp) / "X"
        failing = {1: [page(100, 250)], 101: [page(1, 250)]}
        judge, stored, calls = await run_stage(directory, failing, 250, resume=False)
        expected = {"id": "X", "name": "JUEZ X", "total_trials": 250, "total_trials_stored": 100,
                    "scrapping_failed": True, "last_page_failed": 101}
        out.append(("judges.json tras fallo: stored=100, failed=true, last_page_failed=101",
                    judge == expected and stored == 100, f"{judge} trials={stored}"))

        healthy = {1: [page(100, 250)], 101: [page(100, 250, 100)], 201: [page(50, 250, 200)]}
        judge, stored, calls = await run_stage(directory, healthy, 250, resume=True)
        ok = (judge["total_trials_stored"] == 250 and judge["scrapping_failed"] is False
              and judge["last_page_failed"] == 0 and stored == 250 and 1 not in calls and calls.get(101) == 1)
        out.append(("--resume continúa desde page=101 y fusiona: stored=250, failed=false, last=0",
                    ok, f"{judge} trials={stored} llamadas={calls}"))

        judge, stored, calls = await run_stage(directory, healthy, 250, resume=True)
        out.append(("--resume omite al juez ya completo (0 llamadas)", calls == {} and stored == 250,
                    f"llamadas={calls}"))

        judge, stored, calls = await run_stage(directory, healthy, 250, resume=False)
        out.append(("sin --resume vuelve a extraer completo", judge["scrapping_failed"] is False and calls.get(1) == 1,
                    f"llamadas={calls}"))

        # --- Riesgo 3: protección de trials.json existente (hay 250 juicios guardados) ---
        broken_p1 = {1: [page(1, 250)]}
        judge, stored, calls = await run_stage(directory, broken_p1, 250, resume=False)
        out.append(("R3 fallo en page=1 NO borra los 250 juicios existentes",
                    stored == 250 and judge["scrapping_failed"] is True and judge["last_page_failed"] == 1
                    and judge["total_trials_stored"] == 250, f"{judge} trials={stored}"))

        judge, stored, calls = await run_stage(directory, healthy, 250, resume=True)
        out.append(("R3 --resume tras el fallo deja el juez completo",
                    stored == 250 and judge["scrapping_failed"] is False, f"{judge} trials={stored}"))

        shrunk = {1: [page(100, 100)]}
        judge, stored, calls = await run_stage(directory, shrunk, 100, resume=False)
        out.append(("R3 resultado completo pero < 50 % de lo guardado -> se fusiona (250 se conservan)",
                    stored == 250 and judge["scrapping_failed"] is False, f"{judge} trials={stored}"))

        zero = {1: [page(0, 0)]}
        judge, stored, calls = await run_stage(directory, zero, 0, resume=False)
        out.append(("R3 0 juicios tras un total histórico > 0 -> fallido, last_page_failed=1, datos conservados",
                    stored == 250 and judge["scrapping_failed"] is True and judge["last_page_failed"] == 1
                    and judge["total_trials"] == 100, f"{judge} trials={stored}"))

    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp) / "Y"
        judge, stored, calls = await run_stage(directory, {1: [page(0, 0)]}, 0, resume=False)
        out.append(("R3 juez nuevo sin juicios (sin histórico) -> 0 válido, no fallido",
                    stored == 0 and judge["scrapping_failed"] is False and judge["last_page_failed"] == 0,
                    f"{judge} trials={stored}"))
    return out


def schema_tests() -> list[tuple[str, bool, str]]:
    model = DATASETS["judges"][2]
    old = model.model_validate({"id": "1", "name": "A", "total_trials": 5})
    new = model.model_validate({"id": "1", "name": "A", "total_trials": 5, "total_trials_stored": 4,
                                "scrapping_failed": True, "last_page_failed": 301})
    return [
        ("judges.json antiguo (sin campos de estado) sigue siendo válido",
         old.scrapping_failed is False and old.last_page_failed == 0 and old.total_trials_stored == 0, repr(old)),
        ("judges.json nuevo se valida con los 3 campos", new.last_page_failed == 301, repr(new)),
    ]


async def main() -> int:
    all_ok = True

    def report(ok: bool, name: str, detail: str) -> None:
        nonlocal all_ok
        all_ok &= ok
        print(f"{'PASA ' if ok else 'FALLA'} | {name}: {detail}")

    for name, (script, real_total, expected, extra) in CASES.items():
        max_pages = 1 if "max-pages" in name else 0
        n, total, failures, failed_page, calls = await run_search(script, real_total, max_pages)
        ok = (n, total, failures, failed_page) == expected and (extra is None or extra(calls))
        report(ok, name, f"juicios={n} total={total} fallos={failures} last_page_failed={failed_page} llamadas={calls}")
    for name, (script, real_total, expected, extra) in PASS_CASES.items():
        n, total, failed_page, calls = await run_passes(script, real_total)
        ok = (n, total, failed_page) == expected and extra(calls)
        report(ok, name, f"juicios={n} total={total} last_page_failed={failed_page} llamadas={calls}")
    for name, ok, detail in await resume_tests() + schema_tests():
        report(ok, name, detail)
    print("\nTODOS PASAN" if all_ok else "\nHAY FALLOS")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
