"""Cross-platform release bootstrap. No local/private config is required."""
import argparse
import hashlib
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src/backend"))

def initialize(data_root=None):
    base=Path(data_root or os.getenv("DATA_ROOT", str(ROOT/"data"))).expanduser()
    if not base.is_absolute():base=ROOT/base
    public=Path(os.getenv("PMC_PUBLIC_DATA_ROOT", str(base/"public")))
    runtime=Path(os.getenv("PMC_RUNTIME_ROOT", str(base/"runtime")))
    public=(public if public.is_absolute() else ROOT/public).resolve()
    runtime=(runtime if runtime.is_absolute() else ROOT/runtime).resolve()
    if runtime==public or runtime.is_relative_to(public):raise ValueError("Runtime cannot reside in public release")
    for forbidden in (ROOT/"data/raw", ROOT/"data/private", ROOT/"data/normalized"):
        if public.is_relative_to(forbidden.resolve()) or runtime.is_relative_to(forbidden.resolve()):
            raise ValueError("Production inputs/runtime cannot use private or archive directories")
    from repositories.public_data_repository import release_settings
    settings=release_settings(public)
    if "demo_manifest" not in settings:
        raise ValueError("Public release must include its Demo source package")
    runtime.mkdir(parents=True,exist_ok=True)
    # Bind the runtime seed to the verified business package's source identity.
    # Existing session files are never overwritten during initialization.
    source_hash=hashlib.sha256(settings["facts_path"].read_bytes()).hexdigest()
    db=runtime/("agent-"+source_hash+".db")
    if not db.exists():
        with sqlite3.connect(settings["facts_path"].as_uri()+"?mode=ro",uri=True) as src, sqlite3.connect(db) as dst:src.backup(dst)
    os.environ.update(PMC_PUBLIC_DATA_ROOT=str(public),PMC_RUNTIME_ROOT=str(runtime),PMC_LOCAL_REVIEW="1",
        PMC_FACT_DATABASE_URL="sqlite:///"+db.as_posix(),PMC_REVIEW_MODE="1",PMC_TRIAL_MODE="0",
        LANGSMITH_TRACING="false",LANGCHAIN_TRACING_V2="false",LANGCHAIN_TRACING="false")
    # ReviewBinding/ResponseGenerator provide evidence-grounded replies without
    # external model calls. Public distribution does not grant model egress.
    from repositories.fact_store import SQLiteFactStore
    SQLiteFactStore(db).migrate()
    return settings

if __name__=="__main__":
    from dotenv import load_dotenv
    load_dotenv(ROOT/".env",override=False)  # Optional, never required/copied.
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--init-only",action="store_true")
    p.add_argument("--port",type=int,default=int(os.getenv("PORT", "8000")))
    p.add_argument("--host",default=os.getenv("HOST", "127.0.0.1"))
    args=p.parse_args();initialize()
    if args.init_only:print("Public data verified; runtime initialized")
    else:
        import uvicorn
        uvicorn.run("api.public_app:app",host=args.host,port=args.port,workers=1)
