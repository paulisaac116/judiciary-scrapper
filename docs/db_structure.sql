-- =====================================================================
-- Base de datos: judiciary
-- Estructura según docs/database_structure.png
--
-- Uso:
--   1) Crear la base (fuera de transacción):
--        psql -U postgres -c "CREATE DATABASE judiciary"
--   2) Crear las tablas:
--        psql -U postgres -d judiciary -f docs/db_structure.sql
--
-- Nota: judges.id y trials.id son TEXT (no NUMBER) para preservar
-- ceros iniciales en cédulas (ej. '0101567741') e ids de juicio.
-- =====================================================================

BEGIN;

-- ---------------------------------------------------------------------
-- areas (materias)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS areas (
    id   BIGINT PRIMARY KEY,
    name TEXT   NOT NULL
);

-- ---------------------------------------------------------------------
-- judges (jueces)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS judges (
    id           TEXT     PRIMARY KEY,
    name         TEXT     NOT NULL,
    total_trials SMALLINT NOT NULL DEFAULT 0  -- totalRegistros de la API de juicios
);

-- ---------------------------------------------------------------------
-- trials (juicios)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trials (
    id             TEXT        PRIMARY KEY,
    crime          TEXT        NOT NULL,
    judge_id       TEXT        REFERENCES judges (id) ON UPDATE CASCADE ON DELETE SET NULL,
    area_id        BIGINT      REFERENCES areas (id)  ON UPDATE CASCADE ON DELETE SET NULL,
    date_admission TIMESTAMPTZ,
    action_type    TEXT,
    metadata       JSONB       NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS ix_trials_judge_id ON trials (judge_id);
CREATE INDEX IF NOT EXISTS ix_trials_area_id  ON trials (area_id);

-- ---------------------------------------------------------------------
-- incidents_trials (incidentes por juicio)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS incidents_trials (
    id                        BIGINT      PRIMARY KEY,
    trial_id                  TEXT        REFERENCES trials (id) ON UPDATE CASCADE ON DELETE CASCADE,
    city                      TEXT        NOT NULL,
    jurisdictional_dependence TEXT        NOT NULL,
    created_at                TIMESTAMPTZ,
    actor                     JSONB       NOT NULL DEFAULT '[]'::jsonb,
    defendant                 JSONB       NOT NULL DEFAULT '[]'::jsonb,
    metadata                  JSONB       NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS ix_incidents_trials_trial_id ON incidents_trials (trial_id);

-- ---------------------------------------------------------------------
-- updates_incidents (actuaciones / avances de cada incidente)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS updates_incidents (
    id                UUID        PRIMARY KEY,
    incident_trial_id BIGINT      REFERENCES incidents_trials (id) ON UPDATE CASCADE ON DELETE CASCADE,
    type              TEXT        NOT NULL,
    content           TEXT        NOT NULL,
    visible           TEXT        NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL,
    metadata          JSONB       NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS ix_updates_incidents_incident_trial_id ON updates_incidents (incident_trial_id);

COMMIT;
