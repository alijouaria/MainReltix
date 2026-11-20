from datetime import date, datetime, timedelta

from app import config, db
from app.clock import clock
from app.seed import seed


def _per_sqm(conn, start, end):
    rows = conn.execute(
        "SELECT u.id, AVG(h.kwh) / u.sqm AS v FROM heat_readings h "
        "JOIN units u ON u.id = h.unit_id WHERE h.date BETWEEN ? AND ? GROUP BY u.id",
        (start, end),
    ).fetchall()
    return {r["id"]: r["v"] for r in rows}


def _ratio(conn, start, end):
    v = _per_sqm(conn, start, end)
    others = [x for k, x in v.items() if k != "2a"]
    return v["2a"] / (sum(others) / len(others))


def test_seed_creates_six_units(conn):
    seed(conn)
    ids = [r["id"] for r in conn.execute("SELECT id FROM units ORDER BY id")]
    assert ids == ["1a", "1b", "2a", "2b", "3a", "3b"]


def test_unit_2a_last10_ratio_below_0_7(conn):
    seed(conn)
    last = date(2026, 11, 19)
    assert _ratio(conn, str(last - timedelta(days=9)), str(last)) < 0.7
    early_end = last - timedelta(days=10)
    r = _ratio(conn, str(early_end - timedelta(days=59)), str(early_end))
    assert 0.9 < r < 1.1


def test_seed_deterministic(tmp_path):
    sums = []
    for name in ("a.db", "b.db"):
        c = db.connect(str(tmp_path / name))
        db.init_schema(c)
        seed(c)
        sums.append(
            (
                c.execute("SELECT SUM(kwh) FROM heat_readings").fetchone()[0],
                c.execute("SELECT SUM(temp_c) FROM weather").fetchone()[0],
                c.execute("SELECT SUM(burner_starts) FROM boiler_metrics").fetchone()[0],
            )
        )
        c.close()
    assert sums[0] == sums[1]


def test_clock_advance_and_reset():
    start = datetime.fromisoformat(config.DEMO_START)
    assert clock.now() == start
    assert clock.advance(timedelta(hours=5)) == start + timedelta(hours=5)
    assert clock.now() == start + timedelta(hours=5)
    clock.reset()
    assert clock.now() == start
