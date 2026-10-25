"""Natural-language E2E test server (loopback only, project-external web build).

Two modes, same SPA bundle:

* ``public``      — the real public entry over ``data/public`` (enterprise read-only);
* ``simulation``  — the isolated synthetic ledger used only for execution cases.

The web build is passed in with ``--web`` and must live outside the project so
that no build artefact is written into the released tree. Nothing in this file
can enable enterprise inventory writes: the public app always answers 403 for
``/api/v1/inventory/execute`` because no request principal is ever injected.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src" / "backend"))


def build_public(web: Path, runtime_root: Path):
    os.environ["PMC_PUBLIC_DATA_ROOT"] = str(PROJECT_ROOT / "data" / "public")
    os.environ["PMC_RUNTIME_ROOT"] = str(runtime_root)
    os.environ.pop("PMC_SIMULATION_MODE", None)
    os.environ.pop("PMC_ALLOW_DEMO_SCENARIO", None)
    os.environ.pop("PMC_DEMO_INVENTORY_WRITE_ENABLED", None)
    os.environ.setdefault("LANGSMITH_TRACING", "false")
    from scripts.run_public import initialize

    initialize()
    from api.public_app import app
    return app


def build_simulation(web: Path, runtime_root: Path):
    sys.path.insert(0, str(PROJECT_ROOT / "tests" / "support"))
    import simulation_server as sim

    return sim.create_simulation_app(runtime_root)


def mount_spa(app, web: Path):
    from fastapi.staticfiles import StaticFiles

    if not (web / "index.html").is_file():
        raise SystemExit(f"Web build is missing: {web}")
    app.mount("/", StaticFiles(directory=str(web), html=True), name="web")


def main() -> None:
    parser = argparse.ArgumentParser(description="Loopback E2E server")
    parser.add_argument("--mode", choices=("public", "simulation"), required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--web", required=True)
    parser.add_argument("--root", required=True)
    args = parser.parse_args()
    web, root = Path(args.web).resolve(), Path(args.root).resolve()
    app = build_public(web, root) if args.mode == "public" else build_simulation(web, root)
    mount_spa(app, web)
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
