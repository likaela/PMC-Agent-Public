"""Verify SHA-bound business inputs and an empty conversation seed."""
import json
import re
import sqlite3
from pathlib import Path
from repositories.public_data_repository import release_settings

ROOT=Path(__file__).resolve().parents[1]
FORBIDDEN=re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z]:[\\/]|(?:sk-[A-Za-z0-9_-]{16,})|-----BEGIN .*PRIVATE KEY|(?:/Users/|/home/)[^\s]+",re.I)
CONTACT=re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|(?<!\d)1[3-9]\d{9}(?!\d)")

def audit(root=ROOT/"data/public"):
    root=Path(root);paths=release_settings(root);issues=[]
    declared = {p.resolve() for p in paths.values()} | {(root / "release.json").resolve()}
    def text_check(value,label):
        if isinstance(value,str) and FORBIDDEN.search(value):issues.append(label+": secret/machine path")
        if isinstance(value,str) and CONTACT.search(value):issues.append(label+": email/phone requires review")
    def visit(v,label):
        if isinstance(v,dict):
            for k,x in v.items():
                if k in {"password","api_key","token","proxy_secret","users"}:issues.append(label+": forbidden key "+k)
                if k=="confirmed_by" and x!="release-authorized":issues.append(label+": private confirmer")
                visit(x,label)
        elif isinstance(v,list):
            for x in v:visit(x,label)
        else:text_check(v,label)
    for p in root.rglob("*"):
        if not p.is_file():continue
        if p.resolve() not in declared:issues.append(p.relative_to(root).as_posix()+": file not declared in release")
        if p.suffix not in {".json",".db"}:issues.append(p.name+": unapproved file type")
        if p.suffix==".json":visit(json.loads(p.read_text(encoding="utf-8")),p.relative_to(root).as_posix())
    with sqlite3.connect(paths["facts_path"].as_uri()+"?mode=ro",uri=True) as c:
        for table in ("agent_sessions","agent_conversations","agent_turns","agent_fact_references"):
            if c.execute("SELECT COUNT(*) FROM "+table).fetchone()[0]:issues.append(table+": runtime history present")
        for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            for col in c.execute('PRAGMA table_info("'+t+'")').fetchall():
                if "TEXT" not in col[2].upper():continue
                for (v,) in c.execute('SELECT "'+col[1]+'" FROM "'+t+'"'):
                    text_check(v,t+"."+col[1])
                    if col[1].endswith("_json") and v:
                        try:visit(json.loads(v),t+"."+col[1])
                        except ValueError:issues.append(t+"."+col[1]+": invalid JSON")
    return dict(status="passed" if not issues else "failed",issues=sorted(set(issues)),
        files=sum(p.is_file() for p in root.rglob('*')),bytes=sum(p.stat().st_size for p in root.rglob('*') if p.is_file()))

if __name__=="__main__":
    result=audit();print(json.dumps(result));raise SystemExit(bool(result["issues"]))
