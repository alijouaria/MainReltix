from app import detectors, seed
from app.clock import clock


def test_drift_flags_only_2a(conn):
    seed.seed(conn)
    alerts = detectors.detect_neighbour_drift(conn, clock.now())
    assert len(alerts) == 1
    a = alerts[0]
    assert a.unit_id == "2a"
    assert a.building_id == "B1"
    assert a.signal == "neighbour_drift"
    assert 0.65 <= a.risk <= 0.8
    assert [(c["cause"], c["p"]) for c in a.likely_causes] == [
        ("seized_thermostatic_valve", 0.5),
        ("air_in_system", 0.3),
        ("low_use_or_vacancy", 0.2),
    ]
    assert abs(a.evidence["ratio_vs_neighbours"] - 0.6) <= 0.1
    assert a.evidence["frost_in_days"] == 5


def test_short_cycling_flags_building(conn):
    seed.seed(conn)
    alerts = detectors.detect_short_cycling(conn, clock.now())
    assert len(alerts) == 1
    a = alerts[0]
    assert a.unit_id is None
    assert a.building_id == "B1"
    assert a.signal == "short_cycling"
    assert a.evidence["recent_avg_starts"] > 2 * a.evidence["baseline_avg_starts"]
    assert 0 < a.risk <= 1


def test_service_due_with_frost(conn):
    seed.seed(conn)
    alerts = detectors.detect_service_due(conn, clock.now())
    assert len(alerts) == 1
    a = alerts[0]
    assert a.unit_id is None
    assert a.signal == "service_due"
    assert a.evidence["months_since_service"] >= 12
    assert a.evidence["frost_in_days"] == 5


def test_no_drift_on_flat_data(conn):
    seed.seed(conn)
    conn.execute(
        """UPDATE heat_readings SET kwh = (
               SELECT AVG(h2.kwh / u2.sqm) FROM heat_readings h2
               JOIN units u2 ON u2.id = h2.unit_id
               WHERE h2.date = heat_readings.date AND h2.unit_id != '2a'
           ) * (SELECT sqm FROM units WHERE id = '2a')
           WHERE unit_id = '2a'"""
    )
    conn.commit()
    assert detectors.detect_neighbour_drift(conn, clock.now()) == []


def test_run_all_combines(conn):
    seed.seed(conn)
    signals = sorted(a.signal for a in detectors.run_all(conn, clock.now()))
    assert signals == ["neighbour_drift", "service_due", "short_cycling"]
