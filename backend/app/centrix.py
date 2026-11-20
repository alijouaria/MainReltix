"""Integration boundary to centrix. Real centrix is unreachable; MockCentrix uses SQLite."""
import json
import sqlite3
from typing import Protocol

from app.clock import clock


class CentrixClient(Protocol):
    def get_unit_snapshot(self, unit_id: str) -> dict: ...
    def write_result(self, unit_id: str, result: dict) -> None: ...
    def post_case_event(self, case_id: int | None, event: dict) -> int: ...


class MockCentrix:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def _rows(self, sql: str, *args) -> list[dict]:
        return [dict(r) for r in self.conn.execute(sql, args)]

    def get_unit_snapshot(self, unit_id: str) -> dict:
        unit = dict(self.conn.execute("SELECT * FROM units WHERE id=?", (unit_id,)).fetchone())
        b = unit["building_id"]
        prop = dict(self.conn.execute("SELECT * FROM buildings WHERE id=?", (b,)).fetchone())
        heating = self.conn.execute("SELECT * FROM heating_systems WHERE building_id=?", (b,)).fetchone()
        prop["heating_system"] = dict(heating) if heating else None
        unit["meters"] = self._rows("SELECT * FROM meters WHERE unit_id=?", unit_id)
        owner = self.conn.execute("SELECT * FROM people WHERE role='owner' AND building_id=?", (b,)).fetchone()
        tenant = self.conn.execute("SELECT * FROM people WHERE role='tenant' AND unit_id=?", (unit_id,)).fetchone()
        prefs = self.conn.execute("SELECT * FROM tenant_prefs WHERE unit_id=?", (unit_id,)).fetchone()
        tenant = dict(tenant) if tenant else None
        if tenant and prefs:
            tenant["access_windows"] = json.loads(prefs["access_windows"] or "[]")
            tenant["channel"] = prefs["channel"]
            tenant["monitoring_consent"] = bool(prefs["monitoring_consent"])
        contracts = self._rows(
            "SELECT * FROM contracts WHERE unit_id=? OR (unit_id IS NULL AND building_id=?)", unit_id, b)
        return {"unit_id": unit_id, "property": prop, "unit": unit,
                "owner": dict(owner) if owner else None, "tenant": tenant, "contracts": contracts}

    def write_result(self, unit_id: str, result: dict) -> None:
        case_id = result.get("service_job", {}).get("reference")
        self.post_case_event(case_id, {"actor": "centrix", "kind": "centrix_write_result",
                                       "payload": {"unit_id": unit_id, **result}})

    def post_case_event(self, case_id: int | None, event: dict) -> int:
        cur = self.conn.execute(
            "INSERT INTO case_events (case_id, at, actor, kind, payload) VALUES (?,?,?,?,?)",
            (case_id, event.get("at") or clock.now().isoformat(), event.get("actor", "guardian"),
             event["kind"], json.dumps(event.get("payload", {}), default=str)),
        )
        self.conn.commit()
        return cur.lastrowid
