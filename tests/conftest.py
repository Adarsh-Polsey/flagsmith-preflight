import textwrap
from pathlib import Path

import pytest

from preflight.code_checks import check_code


@pytest.fixture
def run(tmp_path: Path):
    def _run(files: dict[str, str], flag="signup_cta", events=("signup",)):
        for name, body in files.items():
            path = tmp_path / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(body).lstrip())
        return check_code(tmp_path, flag, list(events) if events is not None else None)

    return _run


def only(results, check):
    hits = [r for r in results if r.check == check]
    assert hits, f"no result for {check}: {results}"
    return hits


def status(results, check):
    statuses = {r.status for r in only(results, check)}
    for s in ("fail", "warn", "pass"):
        if s in statuses:
            return s
