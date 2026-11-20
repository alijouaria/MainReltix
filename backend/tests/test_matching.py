from app.matching import estimate, rank_craftsmen
from app.seed import seed


def test_estimate_golden_is_380(conn):
    seed(conn)
    row = conn.execute("SELECT * FROM craftsmen WHERE id = 1").fetchone()
    assert (row["callout_eur"], row["hourly_eur"]) == (65.0, 70.0)
    e = estimate("valve_replacement_balancing", row)
    assert e == {"callout": 65.0, "labour_hours": 3.5, "labour": 245.0, "parts": 70.0, "total": 380.0}


def test_rank_order_and_reasons(conn):
    seed(conn)
    ranked = rank_craftsmen(conn, "heating", "valve_replacement_balancing")
    assert [c["id"] for c in ranked] == [1, 2, 4, 3]
    assert ranked[0]["score"] > ranked[1]["score"]
    assert ranked[0]["estimate"]["total"] == 380.0
    for c in ranked:
        assert c["reasons"] and all(isinstance(r, str) for r in c["reasons"])
    assert rank_craftsmen(conn, "plumbing", "valve_replacement_balancing") == []
