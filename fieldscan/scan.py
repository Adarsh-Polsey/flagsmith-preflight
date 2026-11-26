"""Run Preflight's code checks over public repos that call Flagsmith's experiment SDK.

Read-only and aggregate-only: repos are shallow-cloned into a temp dir, parsed
(never run), counted, and deleted. The output holds counts, never repo names.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import time
import warnings
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from preflight.code_checks import check_code  # noqa: E402

warnings.simplefilter("ignore", SyntaxWarning)

QUERIES = [
    "get_experiment_flag language:python",
    "getExperimentFlag @flagsmith language:typescript",
    "getExperimentFlag @flagsmith language:javascript",
    "useExperiment @flagsmith/flagsmith",
    "getExperimentFlag flagsmith",
]
SKIP_OWNERS = {"flagsmith"}
SELF = "adarsh-polsey/flagsmith-preflight"


def search(query: str) -> set[str]:
    repos = set()
    for page in range(1, 11):
        out = subprocess.run(
            ["gh", "api", "-X", "GET", "search/code", "-f", f"q={query}", "-f", "per_page=100", "-f", f"page={page}"],
            capture_output=True, text=True,
        )
        if out.returncode != 0:
            if "rate limit" in out.stderr.lower() or "403" in out.stderr:
                time.sleep(65)
                continue
            break
        items = json.loads(out.stdout).get("items", [])
        repos |= {i["repository"]["full_name"] for i in items if not i["repository"].get("fork")}
        if len(items) < 100:
            break
        time.sleep(7)  # code search allows about 10 requests a minute
    time.sleep(7)
    return repos


def candidates() -> list[str]:
    repos = set()
    for q in QUERIES:
        repos |= search(q)
    return sorted(r for r in repos if r.split("/")[0].lower() not in SKIP_OWNERS and r.lower() != SELF)


def scan_repo(full_name: str, workdir: Path) -> dict | None:
    dest = workdir / "repo"
    try:
        clone = subprocess.run(
            ["git", "clone", "--quiet", "--depth", "1", "--single-branch", "--filter=blob:limit=500k", f"https://github.com/{full_name}.git", str(dest)],
            capture_output=True, text=True, timeout=180, env={"GIT_TERMINAL_PROMPT": "0", "GIT_LFS_SKIP_SMUDGE": "1", "PATH": "/usr/bin:/bin:/opt/homebrew/bin"},
        )
    except subprocess.TimeoutExpired:
        shutil.rmtree(dest, ignore_errors=True)
        return None
    try:
        if clone.returncode != 0:
            return None
        results = check_code(dest)
        if not any(r.check == "exposure recorded" and r.status == "pass" for r in results):
            return None
        # A repo counts once per check, at its worst result.
        rank = {"pass": 0, "warn": 1, "fail": 2}
        per_check = {}
        for r in results:
            if rank[r.status] >= rank[per_check.get(r.check, "pass")]:
                per_check[r.check] = r.status
        return per_check
    finally:
        shutil.rmtree(dest, ignore_errors=True)


def main():
    repos = candidates()
    print(f"{len(repos)} candidate repos from code search", file=sys.stderr)
    tally: dict[str, Counter] = {}
    qualified = any_failure = all_clear = 0
    with tempfile.TemporaryDirectory(prefix="preflight-fieldscan-") as tmp:
        for i, name in enumerate(repos, 1):
            per_check = scan_repo(name, Path(tmp))
            print(f"[{i}/{len(repos)}] {'qualifies' if per_check else 'skipped'}", file=sys.stderr)
            if not per_check:
                continue
            qualified += 1
            any_failure += "fail" in per_check.values()
            all_clear += set(per_check.values()) == {"pass"}
            for check, status in per_check.items():
                tally.setdefault(check, Counter())[status] += 1
    report = {
        "scanned_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "candidates": len(repos),
        "qualified": qualified,
        "repos_with_any_failure": any_failure,
        "repos_all_clear": all_clear,
        "checks": {c: dict(t) for c, t in sorted(tally.items())},
    }
    text = json.dumps(report, indent=2)
    print(text)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(text + "\n")


if __name__ == "__main__":
    main()
