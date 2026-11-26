import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .result import FAIL, PASS, WARN, Result

MINIMUM = {
    ("python", "flagsmith"): (6, 3, 0),
    ("npm", "@flagsmith/flagsmith"): (12, 4, 0),
    ("npm", "@flagsmith/nodejs"): (9, 1, 0),
}
RETIRED = {
    ("npm", "flagsmith-nodejs"): "flagsmith-nodejs can't send events: move to @flagsmith/nodejs 9.1.0+",
    ("npm", "flagsmith"): "the old 'flagsmith' npm package stops at 10.x: move to @flagsmith/flagsmith 12.4.0+",
    ("npm", "react-native-flagsmith"): "react-native-flagsmith predates experiments: move to @flagsmith/flagsmith 12.4.0+",
}
SKIP_DIRS = {"node_modules", ".venv", "venv", ".git", "dist", "build", "site-packages", "__pycache__", ".next"}


@dataclass
class Dep:
    ecosystem: str
    name: str
    spec: str
    file: str
    line: int
    locked: str | None = None


def version_tuple(text: str) -> tuple[int, ...] | None:
    m = re.search(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", text)
    if not m:
        return None
    return tuple(int(g or 0) for g in m.groups())


def lowest_allowed(spec: str) -> tuple[int, ...] | None:
    # The lowest version a spec lets an install resolve to; None when unbounded below.
    spec = spec.strip()
    if not spec or spec in ("*", "latest") or spec.startswith(("<", "!=")):
        return None
    for part in re.split(r"[,|]|\s+", spec):
        if re.match(r"^(==|===|>=|~=|\^|~|>|=)?\s*v?\d", part):
            return version_tuple(part)
    return None


def walk(root: Path, names: tuple[str, ...]):
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name in names and not SKIP_DIRS & set(path.relative_to(root).parts):
            yield path


def find_deps(root: Path) -> list[Dep]:
    deps: list[Dep] = []
    for path in walk(root, ("requirements.txt", "requirements.in", "requirements-prod.txt", "pyproject.toml", "package.json")):
        rel = str(path.relative_to(root))
        text = path.read_text(errors="ignore")
        if path.name == "package.json":
            deps += npm_deps(path, rel, text)
        elif path.name == "pyproject.toml":
            deps += pyproject_deps(rel, text)
        else:
            for i, line in enumerate(text.splitlines(), 1):
                m = re.match(r"^\s*flagsmith(?:\[[^\]]*\])?\s*([^#;]*)", line, re.I)
                if m and not re.match(r"^\s*flagsmith[-_]", line, re.I):
                    deps.append(Dep("python", "flagsmith", m.group(1).strip(), rel, i))
    return deps


def pyproject_deps(rel: str, text: str) -> list[Dep]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return []
    lines = text.splitlines()
    line = next((i for i, l in enumerate(lines, 1) if re.search(r"""['"]?flagsmith\b(?![-_])""", l)), 1)
    found = []
    for item in data.get("project", {}).get("dependencies", []):
        m = re.match(r"^flagsmith(?:\[[^\]]*\])?\s*([^;]*)", item, re.I)
        if m and not re.match(r"^flagsmith[-_]", item, re.I):
            found.append(Dep("python", "flagsmith", m.group(1).strip(), rel, line))
    poetry = data.get("tool", {}).get("poetry", {}).get("dependencies", {})
    if "flagsmith" in poetry:
        spec = poetry["flagsmith"]
        found.append(Dep("python", "flagsmith", spec if isinstance(spec, str) else spec.get("version", ""), rel, line))
    return found


def npm_deps(path: Path, rel: str, text: str) -> list[Dep]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    lock = {}
    lock_path = path.with_name("package-lock.json")
    if lock_path.exists():
        try:
            lock = json.loads(lock_path.read_text()).get("packages", {})
        except json.JSONDecodeError:
            pass
    found = []
    lines = text.splitlines()
    for section in ("dependencies", "devDependencies", "peerDependencies"):
        for name, spec in (data.get(section) or {}).items():
            if ("npm", name) in MINIMUM or ("npm", name) in RETIRED:
                line = next((i for i, l in enumerate(lines, 1) if f'"{name}"' in l), 1)
                locked = lock.get(f"node_modules/{name}", {}).get("version")
                found.append(Dep("npm", name, str(spec), rel, line, locked))
    return found


def check_deps(deps: list[Dep], langs: set[str]) -> list[Result]:
    results = []
    for d in deps:
        where = f"{d.file}:{d.line}"
        key = (d.ecosystem, d.name)
        if key in RETIRED:
            results.append(Result("SDK version", FAIL, RETIRED[key], where))
            continue
        need = MINIMUM[key]
        need_text = ".".join(map(str, need))
        have = version_tuple(d.locked) if d.locked else lowest_allowed(d.spec)
        label = f"{d.name} {d.locked or d.spec}".strip()
        if have is None:
            results.append(Result("SDK version", WARN, f"{d.name} is unpinned: make sure the installed version is {need_text}+", where))
        elif have < need:
            results.append(Result("SDK version", FAIL, f"{label} allows versions below {need_text}, which can't record experiment events", where))
        else:
            results.append(Result("SDK version", PASS, f"{label} meets {need_text}+", where))
    if not deps and langs:
        results.append(Result("SDK version", WARN, "no Flagsmith SDK found in requirements, pyproject.toml or package.json"))
    return results
