import json

import pytest

from app.seed import seed


@pytest.fixture
def seeded(conn):
    seed(conn)
    return conn


def test_2a_snapshot_complete(seeded):
    unit = seeded.execute("SELECT * FROM units WHERE id='2a'").fetchone()
    assert unit["rooms"] == 3 and unit["sqm"] == 64.0

    meters = seeded.execute("SELECT * FROM meters WHERE unit_id='2a' ORDER BY type").fetchall()
    assert [m["type"] for m in meters] == ["electricity", "heat_cost_allocator"]
    hca = meters[1]
    assert hca["provider"] == "ista" and hca["remote_read"] == 1
    assert meters[0]["number"].startswith("1ESY1160")

    tenant = seeded.execute(
        "SELECT * FROM people WHERE unit_id='2a' AND role='tenant'"
    ).fetchone()
    assert tenant["name"] == "Lena Hoffmann"
    assert tenant["move_in"] == "2026-03-01" and tenant["move_out"] is None

    lease = seeded.execute(
        "SELECT * FROM contracts WHERE unit_id='2a' AND kind='lease'"
    ).fetchone()
    assert lease["party_id"] == tenant["id"]
    assert lease["start"] == tenant["move_in"]
    assert json.loads(lease["terms"])["rent_cold_eur"] > 0

    annex = seeded.execute(
        "SELECT * FROM documents WHERE unit_id='2a' AND kind='lease_annex'"
    ).fetchone()
    payload = json.loads(annex["payload"])
    assert payload["monitoring_consent"] is True
    assert payload["access_windows"] == [{"days": "mon-fri", "from": "08:00", "to": "12:00"}]


def test_every_tenant_has_lease_meters_and_one_moves_out(seeded):
    tenants = seeded.execute("SELECT * FROM people WHERE role='tenant'").fetchall()
    assert len(tenants) == 6
    assert sum(t["move_out"] == "2026-12-31" for t in tenants) == 1
    for t in tenants:
        lease = seeded.execute(
            "SELECT start FROM contracts WHERE kind='lease' AND party_id=?", (t["id"],)
        ).fetchone()
        assert lease["start"] == t["move_in"]
        n = seeded.execute(
            "SELECT COUNT(*) FROM meters WHERE unit_id=?", (t["unit_id"],)
        ).fetchone()[0]
        assert n == 2


def test_heating_system_has_model(seeded):
    hs = seeded.execute("SELECT * FROM heating_systems WHERE building_id='B1'").fetchone()
    assert (hs["make"], hs["model"]) == ("Vaillant", "ecoTEC plus VC 206")
    assert hs["installed_year"] == 2009
    assert hs["last_service"] == "2025-09-18"
    boiler_service = seeded.execute(
        "SELECT DISTINCT last_service FROM boiler_metrics"
    ).fetchall()
    assert [r[0] for r in boiler_service] == [hs["last_service"]]
    mc = seeded.execute(
        "SELECT * FROM contracts WHERE id=?", (hs["maintenance_contract_id"],)
    ).fetchone()
    assert mc["kind"] == "maintenance" and mc["party_id"] == 2
    assert mc["partner"] == "Wärmetechnik Aachen"
    b = seeded.execute("SELECT energy_cert_valid_until FROM buildings").fetchone()
    assert b[0] == "2029-03-31"


def test_framework_contract_rates_match_craftsmen(seeded):
    rows = seeded.execute(
        "SELECT c.party_id, c.partner, c.terms, k.name, k.callout_eur, k.hourly_eur "
        "FROM contracts c JOIN craftsmen k ON k.id = c.party_id "
        "WHERE c.kind='framework' ORDER BY c.party_id"
    ).fetchall()
    assert [r["party_id"] for r in rows] == [1, 2, 3, 4]
    for r in rows:
        terms = json.loads(r["terms"])
        assert r["partner"] == r["name"]
        assert terms["callout_eur"] == r["callout_eur"]
        assert terms["hourly_eur"] == r["hourly_eur"]
    metering = seeded.execute("SELECT partner FROM contracts WHERE kind='metering'").fetchone()
    assert metering["partner"] == "ista"


def test_caretaker_exists(seeded):
    ct = seeded.execute("SELECT * FROM people WHERE role='caretaker'").fetchone()
    assert ct["name"] == "Jonas Klein"
    assert ct["building_id"] == "B1"


def test_voice_channel_allowed_with_transcript(seeded):
    seeded.execute(
        "INSERT INTO messages (unit_id, direction, channel, body, payload) VALUES (?,?,?,?,?)",
        ("2a", "in", "voice", "call", json.dumps({"transcript": "Heizung kalt"})),
    )
    with pytest.raises(Exception):
        seeded.execute("INSERT INTO messages (channel) VALUES ('fax')")
