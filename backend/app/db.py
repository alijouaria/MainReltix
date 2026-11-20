import sqlite3

from app import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS buildings (
    id TEXT PRIMARY KEY, address TEXT, city TEXT, year_built INTEGER,
    heating_type TEXT, energy_class TEXT
);
CREATE TABLE IF NOT EXISTS units (
    id TEXT PRIMARY KEY, building_id TEXT REFERENCES buildings(id),
    floor INTEGER, sqm REAL
);
CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY, name TEXT, role TEXT CHECK (role IN ('tenant', 'owner')),
    unit_id TEXT, building_id TEXT, email TEXT, phone TEXT
);
CREATE TABLE IF NOT EXISTS tenant_prefs (
    unit_id TEXT PRIMARY KEY REFERENCES units(id), access_windows TEXT,
    channel TEXT, monitoring_consent INTEGER
);
CREATE TABLE IF NOT EXISTS craftsmen (
    id INTEGER PRIMARY KEY, name TEXT, trades TEXT, distance_km REAL,
    callout_eur REAL, hourly_eur REAL, rating REAL, will_accept INTEGER
);
CREATE TABLE IF NOT EXISTS heat_readings (
    unit_id TEXT, date TEXT, kwh REAL, PRIMARY KEY (unit_id, date)
);
CREATE TABLE IF NOT EXISTS weather (
    date TEXT PRIMARY KEY, temp_c REAL, frost_forecast INTEGER
);
CREATE TABLE IF NOT EXISTS boiler_metrics (
    building_id TEXT, date TEXT, burner_starts INTEGER, pressure_bar REAL,
    last_service TEXT, PRIMARY KEY (building_id, date)
);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY, unit_id TEXT, building_id TEXT, kind TEXT,
    status TEXT, risk REAL, created_at TEXT, payload TEXT
);
CREATE TABLE IF NOT EXISTS cases (
    id INTEGER PRIMARY KEY, unit_id TEXT, building_id TEXT, alert_id INTEGER,
    status TEXT, rung TEXT, confidence REAL, created_at TEXT, updated_at TEXT,
    payload TEXT
);
CREATE TABLE IF NOT EXISTS case_events (
    id INTEGER PRIMARY KEY, case_id INTEGER, at TEXT, actor TEXT, kind TEXT,
    payload TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY, case_id INTEGER, unit_id TEXT, craftsman_id INTEGER,
    status TEXT, scheduled_at TEXT, est_cost_eur REAL, created_at TEXT,
    updated_at TEXT, payload TEXT
);
CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY, case_id INTEGER, job_id INTEGER, unit_id TEXT,
    amount_eur REAL, status TEXT, requested_at TEXT, decided_at TEXT, payload TEXT
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY, case_id INTEGER, unit_id TEXT, person_id INTEGER,
    direction TEXT, channel TEXT, body TEXT, status TEXT, created_at TEXT,
    payload TEXT
);
"""


def connect(path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or config.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
