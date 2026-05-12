-- SPM Thermal Timeline — PostgreSQL schema
-- Run this after creating the database: createdb spm_thermal

-- Temperature readings (per-minute granularity)
CREATE TABLE IF NOT EXISTS temperature (
    id              SERIAL PRIMARY KEY,
    temperature     DOUBLE PRECISION NOT NULL,
    timestamp       TIMESTAMP NOT NULL,
    station_key     VARCHAR(50) NOT NULL
);

-- Cover open/close history
CREATE TABLE IF NOT EXISTS cover_history (
    id              SERIAL PRIMARY KEY,
    cover_status_id INTEGER NOT NULL CHECK (cover_status_id IN (0, 1)),
    timestamp       TIMESTAMP NOT NULL,
    station_key     VARCHAR(50) NOT NULL
);

-- Cover status reference table
CREATE TABLE IF NOT EXISTS cover_status (
    cover_status_id          INTEGER PRIMARY KEY,
    cover_status_description VARCHAR(50) NOT NULL
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_temperature_timestamp   ON temperature (timestamp);
CREATE INDEX IF NOT EXISTS idx_temperature_station     ON temperature (station_key);
CREATE INDEX IF NOT EXISTS idx_cover_history_timestamp ON cover_history (timestamp);
CREATE INDEX IF NOT EXISTS idx_cover_history_station   ON cover_history (station_key);
