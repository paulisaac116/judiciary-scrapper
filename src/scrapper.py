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
from typing import Any, Callable, Iterable, NamedTuple
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

from schemas import (
    DATASETS,
    JSON_DIR,
    Area,
    IncidentTrial,
    Judge,
    JudgeScrapeStatus,
    Trial,
    UpdateIncident,
    clean_name,
)

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
# "page" es el desplazamiento del primer registro del lote: 1, 101, 201... (tope del lote - 99).
DEFAULT_PAGE = 1
DEFAULT_PAGE_SIZE = 100
DEFAULT_MAX_PAGES = 1  # 0 = todas las páginas
DEFAULT_RATE = 5.0  # peticiones por segundo
DEFAULT_CONCURRENCY = 5
DEFAULT_TIMEOUT = 60.0
MAX_ATTEMPTS = 5
MAX_WAIT = 30.0  # espera máxima entre reintentos (s)
# La búsqueda de juicios devuelve resultados parciales con mucha frecuencia (hasta ~75 % de las respuestas en jueces
# grandes): se reintenta más veces y con esperas cortas, porque el fallo no se debe a saturación del cliente.
SEARCH_MAX_ATTEMPTS = 15
SEARCH_MAX_WAIT = 5.0
# Antes de dar por terminada la paginación de un juez se consulta la página siguiente estas veces: la API a veces
# informa un totalRegistros menor al real (resultado parcial) y sin esta confirmación el juez quedaría truncado.
END_CONFIRMATIONS = 3
# La API no ordena el listado de forma estable entre peticiones: un juicio puede salir en dos páginas y otro en
# ninguna. Si se detecta un duplicado ENTRE páginas, se repite el listado completo (hasta este número de pasadas en
# total) y se unen los idJuicio, hasta que una pasada no aporte juicios nuevos.
LISTING_PASSES = 3
# Si una extracción completa trae menos de esta fracción de los juicios ya guardados, se fusiona en vez de
# sobrescribir trials.json (protege contra resultados parciales que pasen la validación).
SHRINK_GUARD = 0.5

# Las fechas sin zona horaria que devuelve la API están en hora local de Ecuador.
LOCAL_TZ = ZoneInfo("America/Guayaquil")
# Solo se extraen los juicios con fechaIngreso igual o posterior a esta fecha.
MIN_ADMISSION_DATE = datetime(2021, 1, 1, tzinfo=LOCAL_TZ)
# La API filtra por fechaIngreso solo si recibe fechaInicio Y fechaFin (dd/MM/yyyy); con fechaFin vacío ignora el filtro.
# fechaFin vacío se completa con la fecha actual en build_search_body.
DEFAULT_SEARCH_BODY: dict[str, Any] = {
    "resultadoSearch": {
        "idMateria": DEFAULT_AREAS,
        "identificacionJuez": "",
        "fechaInicio": MIN_ADMISSION_DATE.strftime("%d/%m/%Y"),
        "fechaFin": "",
        "idProvincia": "",
    }
}
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


def write_json(name: str, records: Iterable[BaseModel], merge: bool, directory: Path = JSON_DIR) -> int:
    """Escribe el JSON y devuelve el número de registros guardados en el archivo (tras el merge, si aplica)."""
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
    return len(data)


def read_json(name: str, directory: Path = JSON_DIR) -> list[dict[str, Any]]:
    path = directory / DATASETS[name][0]
    if not path.exists() or path.stat().st_size == 0:
        return []
    return orjson.loads(path.read_bytes())


# =====================================================================
# Cliente HTTP con rate limit + reintentos
# =====================================================================
class IncompletePageError(Exception):
    """La API respondió 200 pero con una página incompleta o inconsistente (resultado parcial del backend)."""


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.TransportError, IncompletePageError)):
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

    async def request(
        self,
        method: str,
        url: str,
        validate: Callable[[Any], None] | None = None,
        attempts: int = MAX_ATTEMPTS,
        max_wait: float = MAX_WAIT,
        **kwargs: Any,
    ) -> Any:
        """validate(data) puede lanzar IncompletePageError para reintentar una respuesta 200 incorrecta."""
        async for attempt in AsyncRetrying(
            retry=retry_if_exception(_is_retryable),
            stop=stop_after_attempt(attempts),
            wait=wait_exponential_jitter(initial=1, max=max_wait),
            before_sleep=before_sleep_log(log, logging.WARNING),
            reraise=True,
        ):
            with attempt:
                async with self._semaphore, self._limiter:
                    self.requests += 1
                    response = await self._client.request(method, url, **kwargs)
                    response.raise_for_status()
                    data = response.json() if response.content else None
                if validate is not None:
                    validate(data)
                return data

    async def safe_request(
        self, method: str, url: str, validate: Callable[[Any], None] | None = None, **kwargs: Any
    ) -> Any:
        """Como request(), pero registra el error y devuelve None en vez de abortar todo el proceso."""
        try:
            return await self.request(method, url, validate=validate, **kwargs)
        except (httpx.HTTPError, ValueError, IncompletePageError) as exc:
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


class SearchResult(NamedTuple):
    listings: list[dict[str, Any]]  # juicios listados con fechaIngreso >= MIN_ADMISSION_DATE
    total: int  # total de referencia
    failed_page: int  # offset donde se cortó la paginación (0 = sin fallo)
    cross_page_dups: int  # juicios repetidos entre páginas distintas (señal de orden inestable)


async def _search_trials_for_judge(
    api: ApiClient,
    judge_id: str,
    base_body: dict[str, Any],
    page: int,
    size: int,
    max_pages: int,
    known_total: int = 0,
) -> SearchResult:
    """Pagina el listado de juicios del juez (una pasada).

    failed_page: offset "page" donde se cortó la paginación por un fallo definitivo (0 = sin fallo); permite
    reanudar desde ahí. known_total: total ya conocido (p. ej. al reanudar) para validar desde la primera página.
    """
    body = orjson.loads(orjson.dumps(base_body))
    body["resultadoSearch"]["identificacionJuez"] = judge_id
    results: list[dict[str, Any]] = []
    # Total de referencia: el mayor totalRegistros visto, o la última posición realmente recibida si es mayor
    # (los totales falsos de la API siempre son menores al real).
    total = known_total
    pages_read = 0
    failed_page = 0
    seen_totals: set[int] = set()
    first_offset: dict[str, int] = {}  # idJuicio -> offset de la primera página (cruda) donde apareció
    cross_page_dups = 0
    retry: dict[str, Any] = {"attempts": SEARCH_MAX_ATTEMPTS, "max_wait": SEARCH_MAX_WAIT}

    def check_has_total(data: Any) -> None:
        nonlocal total
        if not isinstance(data, dict) or "totalRegistros" not in data:
            raise IncompletePageError(f"juez {judge_id} page={page}: respuesta sin totalRegistros")
        page_total = int(data.get("totalRegistros") or 0)
        seen_totals.add(page_total)
        # Los totales falsos siempre son menores al real: un total mayor se adopta como referencia aunque la
        # página se rechace después (p. ej. página vacía con el total real).
        if page_total > total and total:
            log.warning("Juez %s: page=%d informa total %d > %d; se adopta como referencia", judge_id, page, page_total, total)
        total = max(total, page_total)

    def check_page(data: Any) -> None:
        """Valida la página CRUDA (antes del filtro local de fecha) contra el total de referencia.

        La API a veces responde 200 con resultados parciales: 1 registro, página vacía o sin la clave
        "resultadoProcesosResueltos", totalRegistros=0 con registros, o un totalRegistros menor al real.
        """
        check_has_total(data)
        page_total = int(data.get("totalRegistros") or 0)
        items = data.get("resultadoProcesosResueltos") or []
        if page_total <= 0 and items:
            raise IncompletePageError(f"juez {judge_id} page={page}: totalRegistros=0 con {len(items)} registros")
        if page_total < total:
            raise IncompletePageError(f"juez {judge_id} page={page}: totalRegistros={page_total} < {total} ya visto")
        expected = max(0, min(size, max(total, page_total) - page + 1))
        if len(items) < expected:
            raise IncompletePageError(
                f"juez {judge_id} page={page}: {len(items)} registros, se esperaban {expected} (total {page_total})"
            )

    async def _confirm_end() -> bool | None:
        """True: hay más registros (se actualiza el total). False: fin confirmado. None: no se pudo confirmar."""
        nonlocal total
        before = total  # check_has_total puede subir `total` durante la confirmación: se compara con el previo
        # Con total 0 (juez aparentemente sin juicios) se confirma la MISMA página: un "0 registros / total 0"
        # puede ser un resultado parcial y solo se acepta si las END_CONFIRMATIONS respuestas coinciden.
        offset = page if before == 0 else next_page(page, size)
        new_total = await _confirm_end_of_listing(api, body, judge_id, offset, size, before, check_has_total)
        if new_total is None:
            return None
        if new_total > before:
            total = max(total, new_total)
            return True
        return False

    while True:
        data = await api.safe_request(
            "POST", URL_TRIALS_SEARCH, validate=check_page, params={"page": page, "size": size}, json=body, **retry
        )
        if not data:
            failed_page = page
            log.error("Juez %s: paginación interrumpida en page=%d; los juicios quedan incompletos", judge_id, page)
            break
        items = data.get("resultadoProcesosResueltos") or []
        page_total = int(data.get("totalRegistros") or 0)
        if items and page + len(items) - 1 > page_total:
            # Más registros de los que permite el total informado: ese total es falso (menor al real).
            log.warning(
                "Juez %s: page=%d trae %d registros pero totalRegistros=%d; el total real es mayor",
                judge_id, page, len(items), page_total,
            )
        total = max(total, page_total, page + len(items) - 1 if items else 0)
        # El listado no viene ordenado por fecha: se filtra cada lote sin cortar la paginación.
        for item in items:
            # Duplicado dentro de la misma página (o al repedir la misma página): inofensivo, la API repite filas.
            # Duplicado entre páginas distintas: el orden cambió entre peticiones y pudo saltarse un juicio.
            first = first_offset.setdefault(str(item.get("idJuicio") or "").strip(), page)
            if first != page:
                cross_page_dups += 1
        selected = filter_trials_by_admission_date(items)
        for item in selected:
            item["_judge_id"] = judge_id
        results.extend(selected)
        pages_read += 1
        log.info(
            "Juez %s: page=%d -> %d juicios, %d desde %s (total API: %d)",
            judge_id, page, len(items), len(selected), MIN_ADMISSION_DATE.date(), total,
        )
        if max_pages and pages_read >= max_pages:
            break
        if not items or page + size - 1 >= total:
            confirm_offset = page if total == 0 else next_page(page, size)
            more = await _confirm_end()
            if more is None:
                failed_page = confirm_offset
                log.error("Juez %s: no se pudo confirmar el fin de la paginación (page=%d)", judge_id, failed_page)
                break
            if not more:
                break
            if len(items) < size:
                # La página se aceptó como "última" con un total falso: se vuelve a pedir con el total corregido.
                # Los registros ya guardados de esta página se deduplican por idJuicio en scrape_trials.
                continue
        page = next_page(page, size)
    if len(seen_totals) > 1:
        log.warning("Juez %s: totales inconsistentes de la API %s; se usó %d", judge_id, sorted(seen_totals), total)
    if cross_page_dups:
        log.warning("Juez %s: %d juicios repetidos entre páginas distintas (orden inestable)", judge_id, cross_page_dups)
    return SearchResult(results, total, failed_page, cross_page_dups)


async def _confirm_end_of_listing(
    api: ApiClient, body: dict[str, Any], judge_id: str, next_offset: int, size: int, total: int,
    validate: Callable[[Any], None],
) -> int | None:
    """Consulta END_CONFIRMATIONS veces la página siguiente al supuesto final.

    Devuelve un total mayor que `total` si alguna respuesta revela más registros (la paginación debe seguir),
    `total` si las respuestas confirman el final, o None si ninguna petición tuvo éxito.
    """
    answered = False
    for attempt in range(1, END_CONFIRMATIONS + 1):
        data = await api.safe_request(
            "POST", URL_TRIALS_SEARCH, validate=validate, params={"page": next_offset, "size": size}, json=body,
            attempts=SEARCH_MAX_ATTEMPTS, max_wait=SEARCH_MAX_WAIT,
        )
        if not data:
            continue
        answered = True
        items = data.get("resultadoProcesosResueltos") or []
        new_total = max(int(data.get("totalRegistros") or 0), next_offset + len(items) - 1 if items else 0)
        if new_total > total:
            log.warning(
                "Juez %s: la confirmación %d de fin (page=%d) revela total %d > %d; se continúa la paginación",
                judge_id, attempt, next_offset, new_total, total,
            )
            return new_total
    return total if answered else None


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


async def _search_with_extra_passes(
    api: ApiClient,
    judge_id: str,
    search_body: dict[str, Any],
    page: int,
    size: int,
    max_pages: int,
    known_total: int,
) -> tuple[list[dict[str, Any]], int, int]:
    """Lista los juicios del juez; si hubo duplicados entre páginas, repite el listado y une los idJuicio.

    Las pasadas extra solo se hacen tras una pasada completa (sin fallo y sin límite --max-pages) y terminan cuando
    una pasada no aporta juicios nuevos o se alcanza LISTING_PASSES. Devuelve (juicios, total, página fallida).
    """
    first = await _search_trials_for_judge(api, judge_id, search_body, page, size, max_pages, known_total)
    listings, total = list(first.listings), first.total
    if not first.cross_page_dups or first.failed_page or max_pages:
        return listings, total, first.failed_page

    known = {str(item["idJuicio"]).strip() for item in listings}
    for n in range(2, LISTING_PASSES + 1):
        log.warning("Juez %s: pasada %d del listado para recuperar juicios saltados por el orden inestable", judge_id, n)
        again = await _search_trials_for_judge(api, judge_id, search_body, page, size, 0, total)
        total = max(total, again.total)
        new = [item for item in again.listings if str(item["idJuicio"]).strip() not in known]
        known.update(str(item["idJuicio"]).strip() for item in new)
        listings.extend(new)
        log.info("Juez %s: pasada %d -> %d juicios nuevos (%d únicos en total)", judge_id, n, len(new), len(known))
        if again.failed_page:
            # La primera pasada sí fue completa: no hay página que reanudar, pero el fallo queda en api.failures.
            log.warning("Juez %s: la pasada %d se interrumpió en page=%d", judge_id, n, again.failed_page)
            break
        if not new or not again.cross_page_dups:
            break
    return listings, total, 0


async def scrape_trials(
    api: ApiClient,
    judge_id: str,
    search_body: dict[str, Any],
    page: int,
    size: int,
    max_pages: int,
    trial_ids: list[str] | None,
    known_total: int = 0,
) -> tuple[list[Trial], int, int]:
    """(juicios del juez, total de referencia, página fallida o 0). Con trial_ids solo se consulta el total."""
    if trial_ids:
        _, total, failed_page, _ = await _search_trials_for_judge(api, judge_id, search_body, 1, 1, 1)
        listings = [{"idJuicio": t, "_judge_id": judge_id} for t in trial_ids]
    else:
        listings, total, failed_page = await _search_with_extra_passes(
            api, judge_id, search_body, page, size, max_pages, known_total
        )
    unique: dict[str, dict[str, Any]] = {}
    for item in listings:
        unique.setdefault(str(item["idJuicio"]).strip(), item)
    log.info("Juez %s: obteniendo detalle de %d juicios", judge_id, len(unique))
    trials = await asyncio.gather(*(_build_trial(api, item) for item in unique.values()))
    return [t for t in trials if t is not None], total, failed_page


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
    if search["fechaInicio"] and not search["fechaFin"]:
        search["fechaFin"] = datetime.now(LOCAL_TZ).strftime("%d/%m/%Y")
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


async def _trials_stage(
    api: ApiClient,
    args: argparse.Namespace,
    judge_id: str,
    name: str,
    body: dict[str, Any] | None,
    directory: Path,
) -> None:
    """Extrae los juicios del juez y escribe trials.json y judges.json (con el estado de la extracción).

    Con --resume: un juez ya completo (scrapping_failed=false) se omite; uno con last_page_failed > 0 continúa
    desde esa página, conserva el total conocido y fusiona con el trials.json existente.
    Protección: un resultado fallido, o mucho menor que lo guardado (SHRINK_GUARD), se fusiona con trials.json en
    vez de sobrescribirlo; un 0 tras un total histórico > 0 se marca como fallo.
    """
    previous =(read_json("judges", directory) or [{}])[0]
    start_page, known_total, merge = args.page, 0, args.merge
    if args.resume and not args.trial_ids:
        if previous.get("scrapping_failed") is False:
            log.info("Juez %s: extracción previa completa; se omite 'trials' (--resume)", judge_id)
            return
        if previous.get("last_page_failed"):
            start_page = int(previous["last_page_failed"])
            known_total = int(previous.get("total_trials") or 0)
            merge = True
            log.info("Juez %s: se reanuda desde page=%d (total conocido %d)", judge_id, start_page, known_total)

    failures_before = api.failures
    trials, total, failed_page = await scrape_trials(
        api, judge_id, body or {}, start_page, args.size, args.max_pages, args.trial_ids, known_total
    )
    failed = bool(failed_page) or api.failures > failures_before

    previous_total = int(previous.get("total_trials") or 0)
    if total == 0 and previous_total > 0 and not args.trial_ids:
        # Un juez que antes tenía juicios no pasa a tener 0: se trata como fallo para reintentarlo con --resume.
        failed, failed_page = True, failed_page or start_page
        total = previous_total  # se conserva el total conocido para validar al reanudar
        log.error("Juez %s: la API informa 0 juicios pero antes informó %d; se marca como fallido", judge_id, previous_total)

    # Protección: nunca reemplazar un trials.json con datos por un resultado fallido o mucho menor (resultado
    # parcial de la API); en esos casos se fusiona con lo existente en vez de sobrescribir.
    existing = len(read_json("trials", directory))
    if not merge and existing:
        if failed:
            merge = True
            log.warning("Juez %s: extracción fallida; se fusiona con los %d juicios existentes", judge_id, existing)
        elif len(trials) < existing * SHRINK_GUARD and not args.max_pages and not args.trial_ids:
            merge = True
            log.warning(
                "Juez %s: %d juicios nuevos frente a %d existentes (< %d %%); se fusiona en vez de sobrescribir",
                judge_id, len(trials), existing, int(SHRINK_GUARD * 100),
            )
    stored = write_json("trials", trials, merge, directory)
    status = JudgeScrapeStatus(
        id=judge_id,
        name=name,
        total_trials=total,
        total_trials_stored=stored,
        scrapping_failed=failed,
        last_page_failed=failed_page,
    )
    write_json("judges", [status], False, directory)
    if failed:
        log.error(
            "Juez %s: extracción INCOMPLETA (%d de %d juicios, last_page_failed=%d); reanude con --resume",
            judge_id, stored, total, failed_page,
        )


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
        await _trials_stage(api, args, judge_id, name, body, directory)
        # Se releen de disco: tras un --resume o --merge, trials.json contiene más que lo extraído ahora.
        trials = None

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
    parser.add_argument(
        "--resume", action="store_true",
        help="Reanudar 'trials': omite jueces completos y continúa desde last_page_failed los que fallaron",
    )
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
