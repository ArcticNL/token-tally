"""Read build-time identity, never caller-supplied runtime environment values."""
import json
from pathlib import Path


def current_build():
    path = Path(__file__).resolve().parents[1] / "build-info.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {"schema": 1, "version": data["version"], "revision": data["revision"]}
    except FileNotFoundError:
        return {"schema": 1, "version": "dev", "revision": None}
