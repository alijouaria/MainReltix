import json
import sqlite3

JOB_TYPES = {
    "valve_replacement_balancing": {"labour_hours": 3.5, "parts": 70.0},
}


def estimate(job_type: str, craftsman: sqlite3.Row) -> dict:
    job = JOB_TYPES[job_type]
    callout = float(craftsman["callout_eur"])
    labour = job["labour_hours"] * float(craftsman["hourly_eur"])
    total = callout + labour + job["parts"]
    return {
        "callout": callout,
        "labour_hours": job["labour_hours"],
        "labour": round(labour, 2),
        "parts": job["parts"],
        "total": round(total, 2),
    }


def _norm(values: list[float], higher_is_better: bool) -> list[float]:
    lo, hi = min(values), max(values)
    if hi == lo:
        return [1.0] * len(values)
    if higher_is_better:
        return [(v - lo) / (hi - lo) for v in values]
    return [(hi - v) / (hi - lo) for v in values]


def rank_craftsmen(conn: sqlite3.Connection, trade: str, job_type: str) -> list[dict]:
    rows = [
        r for r in conn.execute("SELECT * FROM craftsmen ORDER BY id").fetchall()
        if trade in json.loads(r["trades"] or "[]")
    ]
    if not rows:
        return []
    ests = [estimate(job_type, r) for r in rows]
    price_n = _norm([e["total"] for e in ests], higher_is_better=False)
    dist_n = _norm([r["distance_km"] for r in rows], higher_is_better=False)
    rating_n = _norm([r["rating"] for r in rows], higher_is_better=True)
    min_price = min(e["total"] for e in ests)
    min_dist = min(r["distance_km"] for r in rows)
    max_rating = max(r["rating"] for r in rows)

    out = []
    for r, e, p, d, q in zip(rows, ests, price_n, dist_n, rating_n):
        reasons = [
            f"Estimate EUR {e['total']:.0f}" + (" (cheapest)" if e["total"] == min_price else ""),
            f"{r['distance_km']} km away" + (" (closest)" if r["distance_km"] == min_dist else ""),
            f"Rating {r['rating']}" + (" (top rated)" if r["rating"] == max_rating else ""),
        ]
        out.append({
            "id": r["id"],
            "name": r["name"],
            "distance_km": r["distance_km"],
            "rating": r["rating"],
            "will_accept": bool(r["will_accept"]),
            "estimate": e,
            "score": round(0.4 * p + 0.3 * d + 0.3 * q, 4),
            "reasons": reasons,
        })
    out.sort(key=lambda c: (-c["score"], c["id"]))
    return out
