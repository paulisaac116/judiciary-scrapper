# Judicatura

Scraping de las APIs públicas de la Función Judicial de Ecuador → archivos JSON → PostgreSQL (`judiciary`).

## Requisitos
- Python 3.13 + [uv](https://docs.astral.sh/uv/)
- PostgreSQL (probado con 18.6)
- `.env` en la raíz (no se versiona):
  ```
  DATABASE_URL=postgresql://<usuario>:<contraseña>@localhost:5432/judiciary
  ```

```
uv sync
```

## 1. Crear la base de datos
Opción A — SQL:
```
psql -U postgres -c "CREATE DATABASE judiciary"
psql -U postgres -d judiciary -f docs/db_structure.sql
uv run alembic stamp head
```
Opción B — Alembic (BD vacía):
```
psql -U postgres -c "CREATE DATABASE judiciary"
uv run alembic upgrade head
```
BD existente creada antes de `total_trials`: `uv run alembic upgrade head` aplica `0002_judges_total_trials`
(columna `judges.total_trials SMALLINT NOT NULL DEFAULT 0`).

## 2. Extraer datos (`src/scrapper.py`)
Estructura generada:
```
json/
  areas.json                          catálogo de materias
  judges.json                         catálogo global de jueces (cédula -> nombre)
  PICHINCHA/                          carpeta de provincia (variable PROVINCE / --province)
    1714155148/                       carpeta del juez (cédula: identificador único)
      judges.json                     [{"id", "name", "total_trials"}]  (total_trials = totalRegistros de la API)
      trials.json
      incident_trials.json
      updates_incidents.json
```
Paginación de la búsqueda de juicios: `page` es el desplazamiento del lote, `page = tope del lote - 99`
con `size=100` → `page=1` (1-100), `page=101` (101-200), `page=201` (201-300)...

```
uv run python src/scrapper.py all --max-pages 1                     # cadena completa
uv run python src/scrapper.py areas | judges                         # catálogos
uv run python src/scrapper.py trials --judges 1714155148 --areas 19 32 --max-pages 0
uv run python src/scrapper.py trials --fecha-inicio 2023-10-01 --fecha-fin 2023-10-31
uv run python src/scrapper.py trials --body-file body.json --merge
uv run python src/scrapper.py incidents [--trial-ids ID ...]         # lee trials.json de la carpeta del juez
uv run python src/scrapper.py updates   [--trial-ids ID ...]         # lee incident_trials.json de la carpeta del juez
```
| Opción | Descripción |
|---|---|
| `--judges` / `--all-judges` | Jueces a consultar (default `1714155148`) / todos |
| `--areas` / `--all-areas` | `idMateria` del body (default `19 32 37 8 36`) / todas |
| `--province` | Carpeta de provincia dentro de `json/` (default `PICHINCHA`) |
| `--trial-ids` | Juicios concretos de un único juez (omite la búsqueda; consulta solo `totalRegistros`) |
| `--body-file` | JSON con `resultadoSearch` personalizado |
| `--fecha-inicio`, `--fecha-fin`, `--provincia` | Campos del body (fechas `dd/MM/yyyy` o `YYYY-MM-DD`) |
| `--page`, `--size`, `--max-pages` | Desplazamiento inicial (1, 101, 201…), tamaño (100) y máx. lotes por juez (`--max-pages 0` = todos) |
| `--rate`, `--concurrency`, `--timeout` | Peticiones/s (5), simultáneas (5), timeout (60 s) |
| `--merge` | Fusiona por `id` con el JSON existente en vez de sobrescribir |

Los valores por defecto también se pueden editar en la sección `CONFIGURACIÓN` del script.

## 3. Cargar en PostgreSQL (`src/store_db.py`)
Configurar en la sección `CONFIGURACIÓN` del script:
```python
PROVINCE = "PICHINCHA"
JUDGES = [
    {"judge": "VALDIVIESO CEVALLOS PAOLA", "id": "1714155148"},
]
```
Carga `json/areas.json` y, por cada juez de `JUDGES`, su carpeta `json/<PROVINCE>/<id>/` (`judge` solo se usa en logs)
(se omite, con error y código de salida 1, si falta la carpeta/`judges.json` o el id no coincide).
```
uv run python src/store_db.py all                                    # todos, en orden de FKs
uv run python src/store_db.py trials --province PICHINCHA
uv run python src/store_db.py trials --file "json/PICHINCHA/1714155148/trials.json" --batch-size 500
```
Carga idempotente (`INSERT ... ON CONFLICT (id) DO UPDATE`), una transacción por archivo.
FKs inexistentes: en `trials` se ponen a `NULL`; incidentes/actuaciones huérfanos se omiten (con aviso).

## Estructura
```
docs/db_structure.sql        DDL de la base judiciary
src/schemas.py               Modelos Pydantic (forma de cada JSON/tabla)
src/scrapper.py              Script 1: APIs -> JSON
src/store_db.py              Script 2: JSON -> PostgreSQL
src/db/models.py             Modelos SQLAlchemy 2.x
src/db/config.py             DATABASE_URL (.env) y engine
alembic/versions/            Migraciones
```
