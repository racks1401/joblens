"""Local dev/test Postgres 16 + pgvector without Docker.

Uses the binaries bundled in the `pgserver` pip package (dev dependency only).
Docker Compose remains the supported way to run JobLens; this helper exists so
tests and ingestion can run on a machine without Docker.

    python scripts/devdb.py start   # starts on 127.0.0.1:54329, db "joblens"
    python scripts/devdb.py stop
    python scripts/devdb.py url
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / ".pgdata16"
PORT = 54329
URL = f"postgresql+psycopg://postgres@127.0.0.1:{PORT}/joblens"


def _bin(name: str) -> str:
    import pgserver  # type: ignore[import-untyped]

    return str(Path(pgserver.__file__).parent / "pginstall" / "bin" / name)


def start() -> None:
    """Init (first time) and start the server, then create db + extension."""
    if not (DATA / "PG_VERSION").exists():
        subprocess.run([_bin("initdb"), "-D", str(DATA), "-U", "postgres", "-A", "trust", "-E", "UTF8"], check=True)
    status = subprocess.run([_bin("pg_ctl"), "-D", str(DATA), "status"], capture_output=True)
    if status.returncode != 0:
        subprocess.run(
            [_bin("pg_ctl"), "-D", str(DATA), "-l", str(DATA / "server.log"), "-o", f"-p {PORT} -h 127.0.0.1", "-w", "start"],
            check=True,
        )
    import psycopg

    with psycopg.connect(f"postgresql://postgres@127.0.0.1:{PORT}/postgres", autocommit=True) as c:
        for db in ("joblens", "joblens_test"):
            if not c.execute("SELECT 1 FROM pg_database WHERE datname=%s", (db,)).fetchone():
                c.execute(f'CREATE DATABASE "{db}"')
    for db in ("joblens", "joblens_test"):
        with psycopg.connect(f"postgresql://postgres@127.0.0.1:{PORT}/{db}", autocommit=True) as c:
            c.execute("CREATE EXTENSION IF NOT EXISTS vector")
            v = c.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()
            print(f"{db}: pgvector {v[0] if v else '?'}")
    print(URL)


def stop() -> None:
    """Stop the server."""
    subprocess.run([_bin("pg_ctl"), "-D", str(DATA), "-m", "fast", "stop"], check=False)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "start"
    {"start": start, "stop": stop, "url": lambda: print(URL)}[cmd]()
