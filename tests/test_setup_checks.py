import copy

from fake import EXPERIMENT, FEATURE, FakeAdmin

from preflight.setup_checks import check_setup


def statuses(admin, flag="signup_cta"):
    results, info = check_setup(admin, flag)
    return {r.check: r.status for r in results}, results, info


def test_healthy_setup_passes_and_returns_metric_events():
    s, results, info = statuses(FakeAdmin())
    assert set(s.values()) == {"pass"}
    assert list(s) == ["warehouse connected", "flag is multivariate", "every variation keyed", "metric names an event", "experiment ready"]
    assert info["metric_events"] == ["signup"]


def test_pending_warehouse_fails():
    s, results, _ = statuses(FakeAdmin(warehouse="pending_connection"))
    assert s["warehouse connected"] == "fail"
    assert "pending connection" in results[0].reason


def test_missing_warehouse_fails():
    s, _, _ = statuses(FakeAdmin(warehouse=None))
    assert s["warehouse connected"] == "fail"


def test_missing_flag_fails():
    s, _, _ = statuses(FakeAdmin(), flag="other_flag")
    assert s["flag is multivariate"] == "fail"


def test_boolean_flag_fails():
    feature = {**FEATURE, "type": "standard", "variants": []}
    s, _, _ = statuses(FakeAdmin(feature=feature))
    assert s["flag is multivariate"] == "fail"


def test_variation_without_key_fails():
    feature = copy.deepcopy(FEATURE)
    feature["variants"][0]["key"] = None
    s, results, _ = statuses(FakeAdmin(feature=feature))
    assert s["every variation keyed"] == "fail"
    assert "'Get early access' (id 11)" in next(r.reason for r in results if r.check == "every variation keyed")


def test_metric_without_event_fails():
    s, _, _ = statuses(FakeAdmin(event=""))
    assert s["metric names an event"] == "fail"


def test_experiment_without_metric_fails():
    s, _, _ = statuses(FakeAdmin(experiment={**EXPERIMENT, "metrics": []}))
    assert s["metric names an event"] == "fail"


def test_missing_experiment_fails():
    s, _, _ = statuses(FakeAdmin(experiment=None))
    assert s["experiment ready"] == "fail"


def test_running_experiment_fails():
    s, _, _ = statuses(FakeAdmin(experiment={**EXPERIMENT, "status": "running"}))
    assert s["experiment ready"] == "fail"


def test_completed_experiment_fails():
    s, _, _ = statuses(FakeAdmin(experiment={**EXPERIMENT, "status": "completed"}))
    assert s["experiment ready"] == "fail"
