"""In-process timing of every W1 page, per persona: wall time, CPU time, SQL statements and database time.

Wall time on a shared machine moves with whatever else is running; CPU time, the statement count and the
database time per request move much less, so this is the run that says which page is heavy and why. It
complements the locust scenario (``locustfile.py``), which measures what a person waiting would see.

    ARCHIE_LOAD_PASSWORD=... ARCHIE_W1_IDS=ids.json FLASK_CONFIG=smoke \\
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

import w1_scenario  # noqa: E402

EMAILS = {
    "cto": "perf.cto@lantern-quay.example.com",
    "viewer": "perf.viewer@lantern-quay.example.com",
    "admin": "perf.admin@lantern-quay.example.com",
}


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
    password = os.environ["ARCHIE_LOAD_PASSWORD"]
    ids = w1_scenario.load_ids()
    routes = w1_scenario.resolve(ids)
    results = []
    with app.app_context():
        engine = db.engine
    for persona in w1_scenario.PERSONAS:
        client = app.test_client()
        if persona != "anonymous":
            client.post("/account/login", data={"email": EMAILS[persona], "password": password})
        for page_id, path, access, template in routes:
            if not w1_scenario.can_open(persona, access):
                continue
            status, _w, _c, _n, _d = measure(client, path, engine)  # warm-up, not counted
            if not 200 <= status < 300:
                results.append({"persona": persona, "page": page_id, "route": template, "status": status})
                continue
            walls, cpus, counts, dbs = [], [], [], []
            for _ in range(args.repeat):
                status, wall, cpu, count, dbms = measure(client, path, engine)
                walls.append(wall)
                cpus.append(cpu)
                counts.append(count)
                dbs.append(dbms)
            results.append({
                "persona": persona, "page": page_id, "route": template, "status": status,
                "wall_ms_median": statistics.median(walls), "wall_ms_max": max(walls),
                "cpu_ms_median": statistics.median(cpus), "statements": int(statistics.median(counts)),
                "db_ms_median": statistics.median(dbs),
            })
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"load_average": os.getloadavg(), "rows": results}, fh, indent=1)


if __name__ == "__main__":
    main()
