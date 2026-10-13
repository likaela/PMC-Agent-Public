"""Portable, checksum-bound release inputs; never stores runtime state."""
import copy
import hashlib
import json
import os
from pathlib import Path

def public_root():
    value = os.getenv("PMC_PUBLIC_DATA_ROOT")
    return Path(value).expanduser().resolve() if value else None

def release_settings(root=None):
    root = Path(root or public_root()).resolve(strict=True)
    manifest = json.loads((root / "release.json").read_text(encoding="utf-8"))
    if manifest.get("format") != "pmc_public_runtime_v1":
        raise ValueError("Unsupported public data release")
    paths = {}
    for name, entry in manifest["files"].items():
        path = (root / entry["path"]).resolve(strict=True)
        if not path.is_relative_to(root):
            raise ValueError("Release path escapes data root")
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError("Public data checksum mismatch: " + name)
        paths[name] = path
    return paths

class PublicInventoryRepository:
    """Projection of existing Excel observations, not a new calculation."""
    def __init__(self, path):
        self.path = Path(path).resolve(strict=True)
        self.checksum = hashlib.sha256(self.path.read_bytes()).hexdigest()
    def load(self):
        raw = self.path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != self.checksum:
            raise ValueError("Released inventory changed")
        data = json.loads(raw)
        if data.get("format") != "pmc_excel_observations_v1":
            raise ValueError("Invalid released observations")
        return copy.deepcopy(data["observations"])
