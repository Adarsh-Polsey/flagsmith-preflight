import json
import os
from pathlib import Path

DIR = ".preflight"


def find_root(start: Path) -> Path:
    for path in [start, *start.parents]:
        if (path / DIR).is_dir() or (path / "flagsmith.json").exists():
            return path
    return start


def binding(root: Path) -> tuple[str | None, str | None]:
    # The same sources the flagsmith CLI reads: env vars first, then flagsmith.json from `flagsmith init`.
    env, project = os.environ.get("FLAGSMITH_ENVIRONMENT"), os.environ.get("FLAGSMITH_PROJECT")
    config = root / "flagsmith.json"
    if config.exists():
        data = json.loads(config.read_text())
        env = env or data.get("environment")
        project = project or (str(data["project"]) if data.get("project") else None)
    return env, project


def load(root: Path) -> dict:
    path = root / DIR / "experiment.json"
    return json.loads(path.read_text()) if path.exists() else {}


def save(root: Path, data: dict) -> None:
    (root / DIR).mkdir(exist_ok=True)
    (root / DIR / "experiment.json").write_text(json.dumps(data, indent=2) + "\n")
