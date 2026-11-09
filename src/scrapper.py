"""Scraper de las APIs públicas del Consejo de la Judicatura (Ecuador).

Genera:
    json/areas.json, json/judges.json                      catálogos globales
    json/<PROVINCIA>/<CEDULA JUEZ>/                        un directorio por juez con:
        judges.json (id, name, total_trials), trials.json, incident_trials.json, updates_incidents.json

Uso (también se pueden editar los valores por defecto en la sección CONFIGURACIÓN):
    uv run python src/scrapper.py areas
    uv run python src/scrapper.py judges
    uv run python src/scrapper.py trials --judges 1714155148 --areas 19 32 37 8 36 --max-pages 2
    uv run python src/scrapper.py trials --judges 1714155148 --trial-ids 17510202300650 17510202300589
    uv run python src/scrapper.py incidents            # lee trials.json de la carpeta del juez
    uv run python src/scrapper.py updates              # lee incident_trials.json de la carpeta del juez
    uv run python src/scrapper.py all --max-pages 1 --province PICHINCHA
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import re
import sys
import uuid
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import httpx
import orjson
from aiolimiter import AsyncLimiter
from pydantic import BaseModel
from tenacity import (
    AsyncRetrying,
    before_sleep_log,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from schemas import DATASETS, JSON_DIR, Area, IncidentTrial, Judge, Trial, UpdateIncident, clean_name

# =====================================================================
# CONFIGURACIÓN (valores por defecto; la CLI los sobrescribe)
# =====================================================================
BASE_URL = "https://api.funcionjudicial.gob.ec"
URL_JUDGES = f"{BASE_URL}/MANTICORE-SERVICE/api/manticore/consulta/jueces"
URL_AREAS = f"{BASE_URL}/EXPEL-CATALOGO-JURIDICO-SERVICE/api/catalogo/materia/getAllNemonico"
URL_TRIALS_SEARCH = f"{BASE_URL}/MANTICORE-SERVICE/api/manticore/consulta/procesos-judiciales-resueltos"
URL_TRIAL_DETAIL = f"{BASE_URL}/EXPEL-CONSULTA-CAUSAS-SERVICE/api/consulta-causas/informacion/getInformacionJuicio/{{trial_id}}"
URL_INCIDENTS = f"{BASE_URL}/EXPEL-CONSULTA-CAUSAS-CLEX-SERVICE/api/consulta-causas-clex/informacion/getIncidenteJudicatura/{{trial_id}}"
URL_UPDATES = f"{BASE_URL}/EXPEL-CONSULTA-CAUSAS-SERVICE/api/consulta-causas/informacion/actuacionesJudiciales"

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-419,es;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Content-Type": "application/json",
    "Origin": "https://procesosjudiciales.funcionjudicial.gob.ec",
    "Pragma": "no-cache",
    "Referer": "https://procesosjudiciales.funcionjudicial.gob.ec/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"
    ),
    "sec-ch-ua": '"Google Chrome";v="153", "Not_A Brand";v="8", "Chromium";v="153"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

# Carpeta de provincia (dentro de json/) donde se guarda la información de cada juez.
PROVINCE = "PICHINCHA"
DEFAULT_JUDGES: list[str] = ["1714155148"]
DEFAULT_AREAS: list[int] = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 19, 22, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37]
DEFAULT_SEARCH_BODY: dict[str, Any] = {
    "resultadoSearch": {
        "idMateria": DEFAULT_AREAS,
        "identificacionJuez": "",
        "fechaInicio": "",
        "fechaFin": "",
        "idProvincia": "",
    }
}
# "page" es el desplazamiento del primer registro del lote: 1, 101, 201... (tope del lote - 99).
DEFAULT_PAGE = 1
DEFAULT_PAGE_SIZE = 100
DEFAULT_MAX_PAGES = 1  # 0 = todas las páginas
DEFAULT_RATE = 5.0  # peticiones por segundo
DEFAULT_CONCURRENCY = 5
DEFAULT_TIMEOUT = 60.0
MAX_ATTEMPTS = 5

# Las fechas sin zona horaria que devuelve la API están en hora local de Ecuador.
LOCAL_TZ = ZoneInfo("America/Guayaquil")
# Solo se extraen los juicios con fechaIngreso igual o posterior a esta fecha.
MIN_ADMISSION_DATE = datetime(2021, 1, 1, tzinfo=LOCAL_TZ)
UPDATES_NAMESPACE = uuid.UUID("6f1c2a52-5d2b-4f0e-9c1a-3b7e0d9a4c11")

log = logging.getLogger("scrapper")


# =====================================================================
# Utilidades
# =====================================================================
def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value.strip())
    return dt.replace(tzinfo=LOCAL_TZ) if dt.tzinfo is None else dt


def to_api_date(value: str | None) -> str:
    """La búsqueda de juicios solo acepta dd/MM/yyyy (con YYYY-MM-DD responde HTTP 500)."""
    if not value:
        return ""
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt).strftime("%d/%m/%Y")
        except ValueError:
            continue
    raise ValueError(f"Fecha inválida {value!r}: use dd/MM/yyyy o YYYY-MM-DD")


class _HTMLText(HTMLParser):
    """Extrae solo el texto; <br> y cierres de bloque se convierten en saltos de línea."""

    BLOCK_TAGS = {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag == "br":
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: Any) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        # Los saltos de línea crudos dentro del HTML no son significativos.
        self.parts.append(re.sub(r"\s+", " ", data))


def html_to_text(value: str | None) -> str:
    if not value:
        return ""
    parser = _HTMLText()
    parser.feed(value)
    parser.close()
    lines = [line.strip() for line in "".join(parser.parts).split("\n")]
    return "\n".join(lines).strip()


def judge_dir(province: str, judge_id: str) -> Path:
    """json/<PROVINCIA>/<CEDULA JUEZ>: la cédula es el identificador único y estable del juez."""
    return JSON_DIR / province / judge_id.strip()


def next_page(page: int, size: int) -> int:
    """El parámetro "page" de la API es el desplazamiento del lote: 1 -> 101 -> 201 (con size=100)."""
    return page + size


def write_json(name: str, records: Iterable[BaseModel], merge: bool, directory: Path = JSON_DIR) -> Path:
    filename, _, _ = DATASETS[name]
    path = directory / filename
    data = [r.model_dump(mode="json") for r in records]
    if merge:
        by_id = {str(r["id"]): r for r in read_json(name, directory)}
        by_id.update({str(r["id"]): r for r in data})
        data = list(by_id.values())
    directory.mkdir(parents=True, exist_ok=True)
    path.write_bytes(orjson.dumps(data, option=orjson.OPT_INDENT_2))
    log.info("%s: %d registros escritos en %s", name, len(data), path)
    return path


def read_json(name: str, directory: Path = JSON_DIR) -> list[dict[str, Any]]:
    path = directory / DATASETS[name][0]
    if not path.exists() or path.stat().st_size == 0:
        return []
    return orjson.loads(path.read_bytes())


# =====================================================================
# Cliente HTTP con rate limit + reintentos
# =====================================================================
def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return False


class ApiClient:
    def __init__(self, rate: float, concurrency: int, timeout: float) -> None:
        self._client = httpx.AsyncClient(headers=HEADERS, timeout=timeout)
        self._limiter = AsyncLimiter(max_rate=rate, time_period=1)
        self._semaphore = asyncio.Semaphore(concurrency)
        self.requests = 0
        self.failures = 0

    async def __aenter__(self) -> ApiClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self._client.aclose()

    async def request(self, method: str, url: str, **kwargs: Any) -> Any:
        async for attempt in AsyncRetrying(
            retry=retry_if_exception(_is_retryable),
            stop=stop_after_attempt(MAX_ATTEMPTS),
            wait=wait_exponential_jitter(initial=1, max=30),
            before_sleep=before_sleep_log(log, logging.WARNING),
            reraise=True,
        ):
            with attempt:
                async with self._semaphore, self._limiter:
                    self.requests += 1
                    response = await self._client.request(method, url, **kwargs)
                    response.raise_for_status()
                    if not response.content:
                        return None
                    return response.json()

    async def safe_request(self, method: str, url: str, **kwargs: Any) -> Any:
        """Como request(), pero registra el error y devuelve None en vez de abortar todo el proceso."""
        try:
            return await self.request(method, url, **kwargs)
        except (httpx.HTTPError, ValueError) as exc:
            self.failures += 1
            log.error("Fallo definitivo %s %s: %r", method, url, exc)
            return None


# =====================================================================
# Extractores por API
# =====================================================================
async def scrape_areas(api: ApiClient) -> list[Area]:
    data = await api.request("GET", URL_AREAS) or []
    return [Area(id=item["idMateria"], name=item["nombreMateria"]) for item in data]


async def scrape_judges(api: ApiClient) -> list[Judge]:
    data = await api.request("GET", URL_JUDGES) or []
    judges: dict[str, Judge] = {}
    for item in data:
        judge_id = (item.get("identificacionjuez") or "").strip()
        if judge_id:
            judges[judge_id] = Judge(id=judge_id, name=item.get("nombrejuez") or "")
    return list(judges.values())


def filter_trials_by_admission_date(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Conserva los juicios del listado con fechaIngreso >= MIN_ADMISSION_DATE.

    Los juicios sin fechaIngreso o con una fecha inválida se descartan, porque no se puede
    comprobar que cumplan el filtro.
    """
    selected: list[dict[str, Any]] = []
    for item in items:
        try:
            admission = parse_datetime(item.get("fechaIngreso"))
        except ValueError:
            admission = None
        if admission is None:
            log.warning("Juicio %s sin fechaIngreso válida; se omite: %r", item.get("idJuicio"), item.get("fechaIngreso"))
        elif admission >= MIN_ADMISSION_DATE:
            selected.append(item)
    return selected


async def _search_trials_for_judge(
    api: ApiClient, judge_id: str, base_body: dict[str, Any], page: int, size: int, max_pages: int
) -> tuple[list[dict[str, Any]], int]:
    """Devuelve (juicios listados con fechaIngreso >= MIN_ADMISSION_DATE, totalRegistros)."""
    body = orjson.loads(orjson.dumps(base_body))
    body["resultadoSearch"]["identificacionJuez"] = judge_id
    results: list[dict[str, Any]] = []
    total = 0
    pages_read = 0
    while True:
        data = await api.safe_request("POST", URL_TRIALS_SEARCH, params={"page": page, "size": size}, json=body)
        if not data:
            break
        items = data.get("resultadoProcesosResueltos") or []
        total = data.get("totalRegistros") or total
        # El listado no viene ordenado por fecha: se filtra cada lote sin cortar la paginación.
        selected = filter_trials_by_admission_date(items)
        for item in selected:
            item["_judge_id"] = judge_id
        results.extend(selected)
        pages_read += 1
        log.info(
            "Juez %s: page=%d -> %d juicios, %d desde %s (total API: %d)",
            judge_id, page, len(items), len(selected), MIN_ADMISSION_DATE.date(), total,
        )
        if not items or page + size - 1 >= total or (max_pages and pages_read >= max_pages):
            break
        page = next_page(page, size)
    return results, total


async def _build_trial(api: ApiClient, listing: dict[str, Any]) -> Trial | None:
    trial_id = str(listing["idJuicio"]).strip()
    detail_list = await api.safe_request("GET", URL_TRIAL_DETAIL.format(trial_id=trial_id))
    detail: dict[str, Any] = (detail_list or [{}])[0] if isinstance(detail_list, list) else {}
    crime = detail.get("nombreDelito") or listing.get("nombreDelito")
    if not crime:
        log.warning("Juicio %s sin nombreDelito; se omite", trial_id)
        return None
    mapped = {"idJuicio", "nombreDelito", "fechaIngreso", "nombreTipoAccion", "idMateria"}
    metadata = {k: v for k, v in detail.items() if k not in mapped}
    if "estadoActual" in listing:
        metadata["estadoActualListado"] = listing["estadoActual"]
    return Trial(
        id=trial_id,
        crime=crime,
        judge_id=listing.get("_judge_id"),
        area_id=detail.get("idMateria"),
        date_admission=parse_datetime(detail.get("fechaIngreso") or listing.get("fechaIngreso")),
        action_type=detail.get("nombreTipoAccion"),
        metadata=metadata,
    )


async def scrape_trials(
    api: ApiClient,
    judge_id: str,
    search_body: dict[str, Any],
    page: int,
    size: int,
    max_pages: int,
    trial_ids: list[str] | None,
) -> tuple[list[Trial], int]:
    """Juicios de un juez y su totalRegistros. Con trial_ids solo se consulta el total (size=1)."""
    if trial_ids:
        _, total = await _search_trials_for_judge(api, judge_id, search_body, 1, 1, 1)
        listings = [{"idJuicio": t, "_judge_id": judge_id} for t in trial_ids]
    else:
        listings, total = await _search_trials_for_judge(api, judge_id, search_body, page, size, max_pages)
    unique: dict[str, dict[str, Any]] = {}
    for item in listings:
        unique.setdefault(str(item["idJuicio"]).strip(), item)
    log.info("Juez %s: obteniendo detalle de %d juicios", judge_id, len(unique))
    trials = await asyncio.gather(*(_build_trial(api, item) for item in unique.values()))
    return [t for t in trials if t is not None], total


async def _incidents_for_trial(api: ApiClient, trial_id: str) -> list[IncidentTrial]:
    data = await api.safe_request("GET", URL_INCIDENTS.format(trial_id=trial_id)) or []
    incidents: list[IncidentTrial] = []
    for judicatura in data:
        for inc in judicatura.get("lstIncidenteJudicatura") or []:
            mapped = {"idIncidenteJudicatura", "fechaCrea", "lstLitiganteActor", "lstLitiganteDemandado"}
            metadata = {k: v for k, v in inc.items() if k not in mapped}
            metadata["idJudicatura"] = judicatura.get("idJudicatura")
            incidents.append(
                IncidentTrial(
                    id=inc["idIncidenteJudicatura"],
                    trial_id=trial_id,
                    city=judicatura.get("ciudad") or "",
                    jurisdictional_dependence=judicatura.get("nombreJudicatura") or "",
                    created_at=parse_datetime(inc.get("fechaCrea")),
                    actor=inc.get("lstLitiganteActor") or [],
                    defendant=inc.get("lstLitiganteDemandado") or [],
                    metadata=metadata,
                )
            )
    return incidents


async def scrape_incidents(api: ApiClient, trial_ids: list[str]) -> list[IncidentTrial]:
    log.info("Obteniendo incidentes de %d juicios", len(trial_ids))
    chunks = await asyncio.gather(*(_incidents_for_trial(api, t) for t in trial_ids))
    return [i for chunk in chunks for i in chunk]


async def _updates_for_incident(api: ApiClient, incident: IncidentTrial) -> list[UpdateIncident]:
    md = incident.metadata
    body = {
        "idMovimientoJuicioIncidente": md.get("idMovimientoJuicioIncidente"),
        "idJuicio": incident.trial_id,
        "idJudicatura": md.get("idJudicatura"),
        "idIncidenteJudicatura": incident.id,
        "aplicativo": "web",
        "nombreJudicatura": incident.jurisdictional_dependence,
        "incidente": md.get("incidente"),
    }
    data = await api.safe_request("POST", URL_UPDATES, json=body) or []
    updates: list[UpdateIncident] = []
    for item in data:
        created_at = parse_datetime(item.get("fecha"))
        if created_at is None:
            log.warning("Actuación sin fecha en incidente %s; se omite: %s", incident.id, item.get("codigo"))
            continue
        # Ni "codigo" ni "uuid" de la API son únicos: la identidad es el registro completo.
        key = hashlib.sha256(orjson.dumps(item, option=orjson.OPT_SORT_KEYS)).hexdigest()
        metadata = {k: v for k, v in item.items() if k not in {"tipo", "actividad", "fecha", "visible"}}
        if isinstance(metadata.get("idJuicio"), str):
            metadata["idJuicio"] = metadata["idJuicio"].strip()
        updates.append(
            UpdateIncident(
                id=uuid.uuid5(UPDATES_NAMESPACE, f"{incident.id}:{key}"),
                incident_trial_id=incident.id,
                type=(item.get("tipo") or "").strip(),
                content=html_to_text(item.get("actividad")),
                visible=item.get("visible") or "",
                created_at=created_at,
                metadata=metadata,
            )
        )
    return updates


async def scrape_updates(api: ApiClient, incidents: list[IncidentTrial]) -> list[UpdateIncident]:
    log.info("Obteniendo actuaciones de %d incidentes", len(incidents))
    chunks = await asyncio.gather(*(_updates_for_incident(api, i) for i in incidents))
    unique = {u.id: u for chunk in chunks for u in chunk}
    return list(unique.values())


# =====================================================================
# Orquestación / CLI
# =====================================================================
def build_search_body(args: argparse.Namespace, all_area_ids: list[int] | None) -> dict[str, Any]:
    body = orjson.loads(orjson.dumps(DEFAULT_SEARCH_BODY))
    if args.body_file:
        custom = orjson.loads(Path(args.body_file).read_bytes())
        body["resultadoSearch"].update(custom.get("resultadoSearch", custom))
    search = body["resultadoSearch"]
    if args.areas:
        search["idMateria"] = args.areas
    elif all_area_ids is not None:
        search["idMateria"] = all_area_ids
    if args.fecha_inicio is not None:
        search["fechaInicio"] = args.fecha_inicio
    if args.fecha_fin is not None:
        search["fechaFin"] = args.fecha_fin
    if args.provincia is not None:
        search["idProvincia"] = args.provincia
    search["fechaInicio"] = to_api_date(search.get("fechaInicio"))
    search["fechaFin"] = to_api_date(search.get("fechaFin"))
    return body


async def resolve_judges(api: ApiClient, args: argparse.Namespace) -> list[str]:
    if not args.all_judges:
        return args.judges or DEFAULT_JUDGES
    stored = read_json("judges")
    if stored:
        return [j["id"] for j in stored]
    return [j.id for j in await scrape_judges(api)]


async def resolve_all_area_ids(api: ApiClient, args: argparse.Namespace) -> list[int] | None:
    if not args.all_areas:
        return None
    stored = read_json("areas")
    if stored:
        return [a["id"] for a in stored]
    return [a.id for a in await scrape_areas(api)]


async def resolve_judge_names(api: ApiClient, judge_ids: list[str]) -> dict[str, str]:
    """Cédula -> nombre limpio, desde el catálogo json/judges.json (o la API si no existe)."""
    catalog = {j["id"]: j["name"] for j in read_json("judges")}
    if not catalog:
        catalog = {j.id: j.name for j in await scrape_judges(api)}
    names: dict[str, str] = {}
    for judge_id in judge_ids:
        if judge_id in catalog:
            names[judge_id] = clean_name(catalog[judge_id])
        else:
            log.error("Juez %s no está en el catálogo de jueces; se omite", judge_id)
    return names


async def process_judge(
    api: ApiClient,
    args: argparse.Namespace,
    judge_id: str,
    name: str,
    body: dict[str, Any] | None,
    stages: list[str],
) -> int:
    """Ejecuta trials -> incidents -> updates de un juez y escribe en json/<PROVINCIA>/<CEDULA JUEZ>/."""
    directory = judge_dir(args.province, judge_id)
    log.info("=== Juez %s (%s) -> %s", judge_id, name, directory)
    trials: list[Trial] | None = None
    incidents: list[IncidentTrial] | None = None

    if "trials" in stages:
        trials, total = await scrape_trials(
            api, judge_id, body or {}, args.page, args.size, args.max_pages, args.trial_ids
        )
        write_json("judges", [Judge(id=judge_id, name=name, total_trials=total)], False, directory)
        write_json("trials", trials, args.merge, directory)

    if "incidents" in stages:
        if trials is not None:
            trial_ids = [t.id for t in trials]
        else:
            trial_ids = args.trial_ids or [t["id"] for t in read_json("trials", directory)]
        if not trial_ids:
            log.error("Juez %s: no hay juicios; ejecute antes 'trials' o use --trial-ids", judge_id)
            return 1
        incidents = await scrape_incidents(api, trial_ids)
        write_json("incident_trials", incidents, args.merge, directory)

    if "updates" in stages:
        if incidents is None:
            incidents = [IncidentTrial.model_validate(i) for i in read_json("incident_trials", directory)]
            if args.trial_ids:
                incidents = [i for i in incidents if i.trial_id in set(args.trial_ids)]
        if not incidents:
            log.error("Juez %s: no hay incidentes; ejecute antes 'incidents'", judge_id)
            return 1
        write_json("updates_incidents", await scrape_updates(api, incidents), args.merge, directory)
    return 0


async def run(args: argparse.Namespace) -> int:
    stages = ["areas", "judges", "trials", "incidents", "updates"] if args.target == "all" else [args.target]
    status = 0

    async with ApiClient(args.rate, args.concurrency, args.timeout) as api:
        if "areas" in stages:
            write_json("areas", await scrape_areas(api), args.merge)

        if "judges" in stages:
            write_json("judges", await scrape_judges(api), args.merge)

        if {"trials", "incidents", "updates"} & set(stages):
            judge_ids = await resolve_judges(api, args)
            if args.trial_ids and len(judge_ids) != 1:
                log.error("--trial-ids requiere un único juez (--judges CEDULA)")
                return 1
            body = None
            if "trials" in stages:
                body = build_search_body(args, await resolve_all_area_ids(api, args))
                log.info("Body de búsqueda: %s", orjson.dumps(body).decode())
            names = await resolve_judge_names(api, judge_ids)
            if len(names) != len(judge_ids):
                status = 1
            for judge_id, name in names.items():
                status = max(status, await process_judge(api, args, judge_id, name, body, stages))

        log.info("Peticiones HTTP: %d | fallos definitivos: %d", api.requests, api.failures)
        return 2 if api.failures else status


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scraper de APIs públicas de la Función Judicial (Ecuador)")
    parser.add_argument(
        "target",
        choices=["areas", "judges", "trials", "incidents", "updates", "all"],
        help="API a extraer ('all' ejecuta todas en cadena)",
    )
    parser.add_argument("--judges", nargs="+", metavar="CEDULA", help=f"Jueces a consultar (default: {DEFAULT_JUDGES})")
    parser.add_argument("--all-judges", action="store_true", help="Usar todos los jueces (json/judges.json o la API)")
    parser.add_argument("--areas", nargs="+", type=int, metavar="ID", help=f"idMateria del body (default: {DEFAULT_AREAS})")
    parser.add_argument("--all-areas", action="store_true", help="Usar todas las materias (json/areas.json o la API)")
    parser.add_argument("--trial-ids", nargs="+", metavar="ID_JUICIO", help="Juicios concretos (omite la búsqueda por juez)")
    parser.add_argument("--body-file", help="JSON con el body de búsqueda de juicios (resultadoSearch)")
    parser.add_argument("--fecha-inicio", help="fechaInicio del body (dd/MM/yyyy o YYYY-MM-DD)")
    parser.add_argument("--fecha-fin", help="fechaFin del body (dd/MM/yyyy o YYYY-MM-DD)")
    parser.add_argument("--provincia", help="idProvincia del body")
    parser.add_argument("--province", default=PROVINCE, help=f"Carpeta de provincia dentro de json/ (default: {PROVINCE})")
    parser.add_argument(
        "--page", type=int, default=DEFAULT_PAGE,
        help="Desplazamiento inicial (param 'page'): 1, 101, 201... = tope del lote - 99 con size=100",
    )
    parser.add_argument("--size", type=int, default=DEFAULT_PAGE_SIZE, help="Tamaño de página (param 'size')")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES, help="Máx. páginas por juez (0 = todas)")
    parser.add_argument("--rate", type=float, default=DEFAULT_RATE, help="Peticiones por segundo")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY, help="Peticiones simultáneas")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="Timeout por petición (s)")
    parser.add_argument("--merge", action="store_true", help="Fusionar con el JSON existente (por id) en vez de sobrescribir")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.INFO if args.verbose else logging.WARNING)
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
