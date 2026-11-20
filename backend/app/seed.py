"""Deterministic mocked centrix data for the Musterstraße 12 demo building."""
import json
import math
import random
import sqlite3
from datetime import date, timedelta

BUILDING_ID = "B1"
LAST_READING = date(2026, 11, 19)
READING_DAYS = 365
FROST_DATE = date(2026, 11, 25)
FORECAST_END = date(2026, 11, 27)
FAULT_UNIT = "2a"
FAULT_DAYS = 10
FAULT_FACTOR = 0.6
KWH_PER_SQM_DEGREE = 0.012  # base_by_size = sqm * this

UNITS = [  # id, floor, sqm
    ("1a", 1, 62.0), ("1b", 1, 78.0),
    ("2a", 2, 64.0), ("2b", 2, 80.0),
    ("3a", 3, 60.0), ("3b", 3, 75.0),
]
TENANTS = {
    "1a": "Jonas Weber", "1b": "Leonie Schmitz", "2a": "Ahmet Yilmaz",
    "2b": "Sophie Hoffmann", "3a": "Lukas Fischer", "3b": "Emma Wagner",
}
# Ranked best-first: #1 is closest/highest rated but declines; #2 accepts.
CRAFTSMEN = [  # name, distance_km, callout_eur, hourly_eur, rating, will_accept
    ("Heizung Krämer GmbH", 2.1, 65.0, 70.0, 4.8, False),
    ("Wärmetechnik Aachen", 3.4, 65.0, 70.0, 4.6, True),
    ("Sanitär Schulte", 7.8, 85.0, 80.0, 4.4, True),
    ("ThermoService Eifel", 18.5, 55.0, 62.0, 4.1, True),
]


def _temp(d: date, rng: random.Random) -> float:
    # Seasonal curve: coldest mid-January (~1 C), warmest mid-July (~19 C).
    day = d.timetuple().tm_yday
    return round(10.0 - 9.0 * math.cos(2 * math.pi * (day - 15) / 365) + rng.gauss(0, 2.0), 1)


def seed(conn: sqlite3.Connection, rng_seed: int = 42) -> None:
    rng = random.Random(rng_seed)
    cur = conn.cursor()

    cur.execute(
        "INSERT INTO buildings VALUES (?,?,?,?,?,?)",
        (BUILDING_ID, "Musterstraße 12", "Aachen", 1972, "gas central", "E"),
    )
    cur.executemany(
        "INSERT INTO units VALUES (?,?,?,?)",
        [(uid, BUILDING_ID, floor, sqm) for uid, floor, sqm in UNITS],
    )
    cur.execute(
        "INSERT INTO people (name, role, unit_id, building_id, email, phone) VALUES (?,?,?,?,?,?)",
        ("Maria Becker", "owner", None, BUILDING_ID, "maria.becker@example.com", "+49 241 555 0100"),
    )
    for i, (uid, name) in enumerate(TENANTS.items(), start=1):
        cur.execute(
            "INSERT INTO people (name, role, unit_id, building_id, email, phone) VALUES (?,?,?,?,?,?)",
            (name, "tenant", uid, BUILDING_ID,
             name.lower().replace(" ", ".") + "@example.com", f"+49 241 555 01{i:02d}"),
        )
        if uid == FAULT_UNIT:
            windows = [{"days": "mon-fri", "from": "08:00", "to": "12:00"}]
            channel = "portal"
        else:
            windows = [{"days": "mon-fri", "from": "16:00", "to": "19:00"}]
            channel = rng.choice(["portal", "sms", "email"])
        cur.execute(
            "INSERT INTO tenant_prefs VALUES (?,?,?,?)",
            (uid, json.dumps(windows), channel, 1),
        )
    cur.executemany(
        "INSERT INTO craftsmen (name, trades, distance_km, callout_eur, hourly_eur, rating, will_accept) "
        "VALUES (?,?,?,?,?,?,?)",
        [(n, json.dumps(["heating"]), d, c, h, r, int(a)) for n, d, c, h, r, a in CRAFTSMEN],
    )

    first = LAST_READING - timedelta(days=READING_DAYS - 1)
    fault_start = LAST_READING - timedelta(days=FAULT_DAYS - 1)
    d = first
    while d <= FORECAST_END:
        temp = -4.0 if d == FROST_DATE else _temp(d, rng)
        cur.execute(
            "INSERT INTO weather VALUES (?,?,?)",
            (d.isoformat(), temp, int(d == FROST_DATE or temp <= 0)),
        )
        if d <= LAST_READING:
            for uid, _, sqm in UNITS:
                kwh = sqm * KWH_PER_SQM_DEGREE * max(0.0, 18.0 - temp) * rng.uniform(0.92, 1.08)
                if uid == FAULT_UNIT and d >= fault_start:
                    kwh *= FAULT_FACTOR
                cur.execute(
                    "INSERT INTO heat_readings VALUES (?,?,?)",
                    (uid, d.isoformat(), round(kwh, 3)),
                )
        d += timedelta(days=1)

    # Boiler: ~30 burner starts/day, rising linearly to ~120 over the last 3 weeks.
    last_service = date(2025, 9, 18).isoformat()  # 14 months before demo
    ramp_start = LAST_READING - timedelta(days=20)
    d = first
    while d <= LAST_READING:
        if d < ramp_start:
            starts = 30 + rng.randint(-3, 3)
        else:
            starts = round(30 + 90 * (d - ramp_start).days / 20) + rng.randint(-2, 2)
        pressure = round(1.6 + rng.uniform(-0.05, 0.05), 2)
        cur.execute(
            "INSERT INTO boiler_metrics VALUES (?,?,?,?,?)",
            (BUILDING_ID, d.isoformat(), starts, pressure, last_service),
        )
        d += timedelta(days=1)

    conn.commit()
