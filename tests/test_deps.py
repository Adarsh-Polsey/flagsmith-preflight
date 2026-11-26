import json

from conftest import only, status

CODE = {"app.py": """
from flagsmith import Flagsmith
flagsmith = Flagsmith(environment_key="x", enable_events=True)
def home(user_id):
    flagsmith.get_experiment_flag("signup_cta", identifier=user_id)
"""}


def with_files(**files):
    return {**CODE, **files}


def package(deps: dict) -> str:
    return json.dumps({"name": "app", "dependencies": deps}, indent=2)


def test_python_minimum_passes(run):
    assert status(run(with_files(**{"requirements.txt": "flagsmith>=6.3.0\n"})), "SDK version") == "pass"


def test_python_old_pin_fails(run):
    [r] = only(run(with_files(**{"requirements.txt": "flask\nflagsmith==6.2.1\n"})), "SDK version")
    assert r.status == "fail" and r.where == "requirements.txt:2"


def test_python_unpinned_warns(run):
    assert status(run(with_files(**{"requirements.txt": "flagsmith\n"})), "SDK version") == "warn"


def test_python_range_starting_too_low_fails(run):
    assert status(run(with_files(**{"requirements.txt": "flagsmith>=5,<7\n"})), "SDK version") == "fail"


def test_other_flagsmith_packages_are_ignored(run):
    results = run(with_files(**{"requirements.txt": "flagsmith-flag-engine==5.0\nflagsmith>=6.3.0\n"}))
    assert [r.status for r in only(results, "SDK version")] == ["pass"]


def test_pyproject_dependency(run):
    toml = '[project]\nname = "x"\ndependencies = ["flagsmith~=6.2"]\n'
    assert status(run(with_files(**{"pyproject.toml": toml})), "SDK version") == "fail"


def test_poetry_dependency(run):
    toml = '[tool.poetry.dependencies]\npython = "^3.11"\nflagsmith = "^6.4"\n'
    assert status(run(with_files(**{"pyproject.toml": toml})), "SDK version") == "pass"


def test_npm_client_minimum_passes(run):
    assert status(run(with_files(**{"package.json": package({"@flagsmith/flagsmith": "^12.4.0"})})), "SDK version") == "pass"


def test_npm_client_too_old_fails(run):
    assert status(run(with_files(**{"package.json": package({"@flagsmith/flagsmith": "^11.0.2"})})), "SDK version") == "fail"


def test_node_minimum_passes(run):
    assert status(run(with_files(**{"package.json": package({"@flagsmith/nodejs": "~9.1.0"})})), "SDK version") == "pass"


def test_old_flagsmith_nodejs_package_fails(run):
    [r] = only(run(with_files(**{"package.json": package({"flagsmith-nodejs": "^8.0.0"})})), "SDK version")
    assert r.status == "fail" and "@flagsmith/nodejs" in r.reason


def test_old_flagsmith_npm_package_fails(run):
    assert status(run(with_files(**{"package.json": package({"flagsmith": "^10.0.0"})})), "SDK version") == "fail"


def test_lockfile_version_wins_over_range(run):
    lock = json.dumps({"packages": {"node_modules/@flagsmith/nodejs": {"version": "9.0.1"}}})
    files = with_files(**{"package.json": package({"@flagsmith/nodejs": "*"}), "package-lock.json": lock})
    [r] = only(run(files), "SDK version")
    assert r.status == "fail" and "9.0.1" in r.reason


def test_no_sdk_dependency_warns(run):
    assert status(run(CODE), "SDK version") == "warn"
