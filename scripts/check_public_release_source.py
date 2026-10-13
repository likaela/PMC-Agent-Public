"""Validate release source files without printing secret values."""
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOME_PATH = re.compile(r"(?:[A-Za-z]:[\\/]Users[\\/][A-Za-z0-9_.-]+[\\/]|/Users/[A-Za-z0-9_.-]+/|[A-Za-z]:[\\/]I Wanna go to)", re.I)
SECRET = re.compile(r"(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)")
TEXT = {".py", ".md", ".json", ".ts", ".vue", ".js", ".yml", ".yaml", ".sh", ".ps1", ".conf", ".service", ".example", ".txt", ".sql"}
PUBLIC_DB = "data/public/bom/published.db"


def audit(root=ROOT):
    root = Path(root)
    names = subprocess.check_output(["git", "-c", "core.quotePath=false", "ls-files", "-z", "-co", "--exclude-standard"], cwd=root).decode("utf-8").split("\0")
    issues, checked = [], 0
    for name in sorted(set(names) - {""}):
        path = root / name
        if not path.is_file():
            continue  # Tracked removals do not belong to the candidate tree.
        checked += 1
        if name.startswith(("outputs/", "pmc-agent-frontend-main/outputs/", "data/private/", "data/runtime/", "data/raw/", "data/facts/", "data/normalized/", ".runtime/")):
            issues.append({"file": name, "reason": "non-release source or runtime artifact"})
        if path.suffix in {".zip", ".db", ".sqlite", ".sqlite3"} and name != PUBLIC_DB:
            issues.append({"file": name, "reason": "non-runtime archive/database"})
        if path.name == ".env" or (path.name.startswith(".env.") and not path.name.endswith(".example")) or ".local." in path.name:
            issues.append({"file": name, "reason": "private configuration"})
        if path.suffix not in TEXT and path.name not in {"README", "Dockerfile", ".gitignore", ".dockerignore", ".gitattributes"}:
            continue
        text = path.read_text(encoding="utf-8-sig")
        for line_number, line in enumerate(text.splitlines(), 1):
            if HOME_PATH.search(line):
                issues.append({"file": name, "line": line_number, "reason": "actual machine/user path"})
            if SECRET.search(line):
                issues.append({"file": name, "line": line_number, "reason": "credential-like value; manual review required"})
    return {"status": "failed" if issues else "passed", "files": checked, "issues": issues,
            "scope": "release source files; ignored runtime state excluded"}


if __name__ == "__main__":
    result = audit()
    print(json.dumps(result, ensure_ascii=True))
    raise SystemExit(bool(result["issues"]))
