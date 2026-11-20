"""Fault detectors over centrix data. Each returns a list of Alerts."""
import sqlite3
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

DRIFT_WINDOW_DAYS = 10
DRIFT_LOW = 0.75
DRIFT_HIGH = 1.3
FROST_SOON_DAYS = 7
CYCLING_RECENT_DAYS = 7
CYCLING_BASELINE_DAYS = 60
CYCLING_RATIO = 2.0
SERVICE_INTERVAL_MONTHS = 12

LOW_DRIFT_CAUSES = [
    {"cause": "seized_thermostatic_valve", "p": 0.5},
    {"cause": "air_in_system", "p": 0.3},
    {"cause": "low_use_or_vacancy", "p": 0.2},
]
HIGH_DRIFT_CAUSES = [
    {"cause": "window_left_open", "p": 0.4},
    {"cause": "stuck_open_valve", "p": 0.35},
    {"cause": "poor_insulation", "p": 0.25},
]
CYCLING_CAUSES = [
    {"cause": "oversized_or_misconfigured_burner", "p": 0.4},
    {"cause": "faulty_boiler_thermostat", "p": 0.35},
    {"cause": "low_system_water_flow", "p": 0.25},
]
SERVICE_CAUSES = [{"cause": "overdue_annual_service", "p": 1.0}]


@dataclass
class Alert:
    unit_id: str | None
    building_id: str
    signal: str
    risk: float
    evidence: dict = field(default_factory=dict)
    likely_causes: list[dict] = field(default_factory=list)
    urgency: str = "low"


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _urgency(risk: float) -> str:
    return "high" if risk >= 0.7 else "medium" if risk >= 0.4 else "low"


def frost_in_days(conn: sqlite3.Connection, now: datetime) -> int | None:
    """Days until the first forecast frost day strictly after today."""
    today = now.date()
    row = conn.execute(
        "SELECT MIN(date) FROM weather WHERE frost_forecast = 1 AND date > ?",
        (today.isoformat(),),
    ).fetchone()
    if not row or row[0] is None:
        return None
    return (date.fromisoformat(row[0]) - today).days


def _frost_bonus(frost: int | None) -> float:
    return 0.05 if frost is not None and frost <= FROST_SOON_DAYS else 0.0


def detect_neighbour_drift(conn: sqlite3.Connection, now: datetime) -> list[Alert]:
    today = now.date().isoformat()
    start = (now.date() - timedelta(days=DRIFT_WINDOW_DAYS)).isoformat()
    rows = conn.execute(
        """SELECT u.id, u.building_id, SUM(h.kwh) / u.sqm AS kwh_sqm,
                  SUM(MAX(0, 18 - w.temp_c)) AS dd
           FROM heat_readings h
           JOIN units u ON u.id = h.unit_id
           JOIN weather w ON w.date = h.date
           WHERE h.date >= ? AND h.date < ?
           GROUP BY u.id""",
        (start, today),
    ).fetchall()
    intensity: dict[str, dict[str, float]] = {}
    for r in rows:
        if r["dd"]:
            intensity.setdefault(r["building_id"], {})[r["id"]] = r["kwh_sqm"] / r["dd"]

    frost = frost_in_days(conn, now)
    alerts = []
    for building_id, units in intensity.items():
        for unit_id, value in units.items():
            others = [v for u, v in units.items() if u != unit_id]
            if not others:
                continue
            median = statistics.median(others)
            if median <= 0:
                continue
            ratio = value / median
            if DRIFT_LOW <= ratio <= DRIFT_HIGH:
                continue
            if ratio < DRIFT_LOW:
                base = 0.4 + (DRIFT_LOW - ratio) * 1.5
                causes = LOW_DRIFT_CAUSES
            else:
                base = 0.4 + (ratio - DRIFT_HIGH) * 1.5
                causes = HIGH_DRIFT_CAUSES
            risk = round(_clamp(base + _frost_bonus(frost)), 3)
            alerts.append(Alert(
                unit_id=unit_id, building_id=building_id, signal="neighbour_drift",
                risk=risk,
                evidence={
                    "window_days": DRIFT_WINDOW_DAYS,
                    "kwh_per_sqm_degree_day": round(value, 5),
                    "neighbour_median": round(median, 5),
                    "ratio_vs_neighbours": round(ratio, 3),
                    "frost_in_days": frost,
                },
                likely_causes=[dict(c) for c in causes],
                urgency=_urgency(risk),
            ))
    return alerts


def detect_short_cycling(conn: sqlite3.Connection, now: datetime) -> list[Alert]:
    today = now.date()
    recent_start = (today - timedelta(days=CYCLING_RECENT_DAYS)).isoformat()
    base_start = (today - timedelta(days=CYCLING_RECENT_DAYS + CYCLING_BASELINE_DAYS)).isoformat()
    frost = frost_in_days(conn, now)
    alerts = []
    for (building_id,) in conn.execute("SELECT id FROM buildings").fetchall():
        def avg(lo: str, hi: str) -> float | None:
            return conn.execute(
                "SELECT AVG(burner_starts) FROM boiler_metrics "
                "WHERE building_id = ? AND date >= ? AND date < ?",
                (building_id, lo, hi),
            ).fetchone()[0]

        recent = avg(recent_start, today.isoformat())
        baseline = avg(base_start, recent_start)
        if not recent or not baseline or recent / baseline <= CYCLING_RATIO:
            continue
        ratio = recent / baseline
        risk = round(_clamp(0.4 + (ratio - CYCLING_RATIO) * 0.2 + _frost_bonus(frost)), 3)
        alerts.append(Alert(
            unit_id=None, building_id=building_id, signal="short_cycling", risk=risk,
            evidence={
                "recent_avg_starts": round(recent, 1),
                "baseline_avg_starts": round(baseline, 1),
                "ratio": round(ratio, 2),
                "frost_in_days": frost,
            },
            likely_causes=[dict(c) for c in CYCLING_CAUSES],
            urgency=_urgency(risk),
        ))
    return alerts


def detect_service_due(conn: sqlite3.Connection, now: datetime) -> list[Alert]:
    today = now.date()
    frost = frost_in_days(conn, now)
    alerts = []
    rows = conn.execute(
        """SELECT building_id, last_service FROM boiler_metrics b
           WHERE date = (SELECT MAX(date) FROM boiler_metrics
                         WHERE building_id = b.building_id AND date < ?)""",
        (today.isoformat(),),
    ).fetchall()
    for r in rows:
        if not r["last_service"]:
            continue
        last = date.fromisoformat(r["last_service"])
        months = (today.year - last.year) * 12 + today.month - last.month - (today.day < last.day)
        if months < SERVICE_INTERVAL_MONTHS:
            continue
        risk = 0.3 + 0.03 * (months - SERVICE_INTERVAL_MONTHS)
        if frost is not None and frost <= FROST_SOON_DAYS:
            risk += 0.2
        risk = round(_clamp(risk), 3)
        alerts.append(Alert(
            unit_id=None, building_id=r["building_id"], signal="service_due", risk=risk,
            evidence={
                "last_service": r["last_service"],
                "months_since_service": months,
                "frost_in_days": frost,
            },
            likely_causes=[dict(c) for c in SERVICE_CAUSES],
            urgency=_urgency(risk),
        ))
    return alerts


def run_all(conn: sqlite3.Connection, now: datetime) -> list[Alert]:
    return (
        detect_neighbour_drift(conn, now)
        + detect_short_cycling(conn, now)
        + detect_service_due(conn, now)
    )
