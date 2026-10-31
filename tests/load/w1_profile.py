"""In-process timing of every W1 page, per persona: wall time, CPU time, SQL statements and database time.

Wall time on a shared machine moves with whatever else is running; CPU time, the statement count and the
database time per request move much less, so this is the run that says which page is heavy and why. It
complements the locust scenario (``locustfile.py``), which measures what a person waiting would see.

    ARCHIE_W1_PASSWORD=... ARCHIE_W1_<PERSONA>_EMAIL=... ARCHIE_W1_SAMPLES="user_id=20,application_id=27,..." \\
    FLASK_CONFIG=smoke \\
    python tests/load/w1_profile.py --repeat 7 --out profile.json

Reads only. Results hold routes and timings, no names.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import w1_routes  # noqa: E402


def measure(client, path, engine):
    """One GET: (status, wall_ms, cpu_ms, statements, db_ms)."""
    from sqlalchemy import event

    stats = {"n": 0, "db": 0.0}
    started = {}

    def before(conn, cursor, statement, parameters, context, executemany):
        started[id(cursor)] = time.perf_counter()

    def after(conn, cursor, statement, parameters, context, executemany):
        t0 = started.pop(id(cursor), None)
        stats["n"] += 1
        if t0 is not None:
            stats["db"] += time.perf_counter() - t0

    event.listen(engine, "before_cursor_execute", before)
    event.listen(engine, "after_cursor_execute", after)
    try:
        w0, c0 = time.perf_counter(), time.process_time()
        response = client.get(path, follow_redirects=False)
        wall = (time.perf_counter() - w0) * 1000.0
        cpu = (time.process_time() - c0) * 1000.0
    finally:
        event.remove(engine, "before_cursor_execute", before)
        event.remove(engine, "after_cursor_execute", after)
    return response.status_code, wall, cpu, stats["n"], stats["db"] * 1000.0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=7)
    parser.add_argument("--out", default="w1-profile.json")
    args = parser.parse_args()

    from app import create_app
    from app.extensions import db

    app = create_app(os.environ.get("FLASK_CONFIG", "smoke"))
    password = os.environ["ARCHIE_W1_PASSWORD"]
    samples = w1_routes.Samples(dict(
        pair.split("=", 1) for pair in os.environ.get("ARCHIE_W1_SAMPLES", "").split(",") if "=" in pair
    ))
    results = []
    with app.app_context():
        engine = db.engine
    people = [("anonymous", None)] + [(p.key, os.environ.get("ARCHIE_W1_%s_EMAIL" % p.key.upper()))
                                      for p in w1_routes.PERSONAS]
    for persona, email in people:
        client = app.test_client()
        if email:
            client.post("/account/login", data={"email": email, "password": password})
        for page_id, pattern in w1_routes.W1_ROUTES:
            path, reason = w1_routes.resolve_route(pattern, samples)
            if path is None:
                results.append({"persona": persona, "page": page_id, "route": pattern, "status": reason})
                continue
            status, _w, _c, _n, _d = measure(client, path, engine)  # warm-up, not counted
            if not 200 <= status < 300:
                results.append({"persona": persona, "page": page_id, "route": pattern, "status": status})
                continue
            walls, cpus, counts, dbs = [], [], [], []
            for _ in range(args.repeat):
                status, wall, cpu, count, dbms = measure(client, path, engine)
                walls.append(wall)
                cpus.append(cpu)
                counts.append(count)
                dbs.append(dbms)
            results.append({
                "persona": persona, "page": page_id, "route": pattern, "status": status,
                "wall_ms_median": statistics.median(walls), "wall_ms_max": max(walls),
                "cpu_ms_median": statistics.median(cpus), "statements": int(statistics.median(counts)),
                "db_ms_median": statistics.median(dbs),
            })
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"load_average": os.getloadavg(), "rows": results}, fh, indent=1)


if __name__ == "__main__":
    main()
