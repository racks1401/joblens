"""Record small real API responses for offline tests: tests/fixtures/{source}_board.json.

Picks the first verified company per source in config/companies.yaml, fetches its full board,
keeps up to 3 India/remote jobs + 1 other job, and saves the raw payload shape.

    python scripts/record_fixtures.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import ROOT, get_settings  # noqa: E402
from app.ingest import ashby, greenhouse, lever  # noqa: E402
from app.ingest.http import PoliteClient  # noqa: E402
from app.ingest.normalize import location_matches  # noqa: E402
from app.ingest.run import load_companies  # noqa: E402

MODS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby}
PARAMS = {"greenhouse": {"content": "true"}, "lever": {"mode": "json"}, "ashby": {"includeCompensation": "true"}}


def main() -> None:
    s = get_settings()
    cl = PoliteClient(s.cfg.ingest.user_agent, rps=2)
    done: set[str] = set()
    for c in load_companies(s.env.companies_file):
        if c.source in done:
            continue
        mod = MODS[c.source]
        status, data = cl.get_json(mod.URL.format(token=c.token), PARAMS[c.source])
        if status != 200:
            continue
        jobs = data if c.source == "lever" else data["jobs"]
        good = [j for j in jobs if location_matches(mod.location_of(j), s.cfg.ingest)][:3]
        other = [j for j in jobs if not location_matches(mod.location_of(j), s.cfg.ingest)][:1]
        sample = good + other
        payload = sample if c.source == "lever" else {**{k: v for k, v in data.items() if k != "jobs"}, "jobs": sample}
        out = ROOT / "tests" / "fixtures" / f"{c.source}_board.json"
        out.write_text(json.dumps({"company": c.name, "token": c.token, "payload": payload}, indent=1), encoding="utf-8")
        print(f"recorded {c.source} {c.token}: {len(good)} matching + {len(other)} other -> {out.name}")
        done.add(c.source)
    cl.close()


if __name__ == "__main__":
    main()
