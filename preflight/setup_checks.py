from .admin import Admin
from .result import FAIL, PASS, WARN, Result


def setup(check: str, status: str, reason: str) -> Result:
    return Result(check, status, reason, group="setup")


def check_warehouse(admin: Admin) -> Result:
    connections = admin.warehouses()
    connected = [w for w in connections if w["status"] == "connected"]
    if connected:
        w = connected[0]
        return setup("warehouse connected", PASS, f"{w['name']} warehouse is connected ({w.get('total_events_received', 0):,} events received)")
    if connections:
        status = connections[0]["status"].replace("_", " ")
        return setup("warehouse connected", FAIL, f"warehouse is {status}: events are rejected until it's connected (Environment Settings › Warehouse › Send your first event)")
    return setup("warehouse connected", FAIL, "no warehouse: events have nowhere to go (Environment Settings › Warehouse › Flagsmith › Enable)")


def check_flag(feature: dict | None, flag: str) -> list[Result]:
    if feature is None:
        return [setup("flag is multivariate", FAIL, f"flag '{flag}' doesn't exist in this project")]
    variants = feature.get("variants") or []
    if feature["type"] != "multivariate" or not variants:
        return [setup("flag is multivariate", FAIL, f"'{flag}' is {feature['type']}: make it multivariate before any data comes in; a late switch throws away what was collected")]
    results = [setup("flag is multivariate", PASS, f"'{flag}' serves control plus {len(variants)} variation(s)")]
    unkeyed = [v for v in variants if not v.get("key")]
    for v in unkeyed:
        results.append(setup("every variation keyed", FAIL, f"variation {v['value']!r} (id {v['id']}) has no key: Flagsmith never counts its exposures"))
    if not unkeyed:
        keys = ", ".join(["control", *(v["key"] for v in variants)])
        results.append(setup("every variation keyed", PASS, f"keys: {keys}"))
    return results


def check_experiment(admin: Admin, experiment: dict | None, flag: str) -> tuple[list[Result], list[str]]:
    if experiment is None:
        return [setup("experiment ready", FAIL, f"no experiment uses '{flag}': create one first")], []
    results, events = [], []
    for m in experiment["metrics"]:
        event = (admin.metric(m["metric"]).get("definition") or {}).get("event")
        if event:
            events.append(event)
            results.append(setup("metric names an event", PASS, f"metric {m['metric_name']!r} counts event {event!r}"))
        else:
            results.append(setup("metric names an event", FAIL, f"metric {m['metric_name']!r} has no event name"))
    if not experiment["metrics"]:
        results.append(setup("metric names an event", FAIL, "the experiment has no metric: nothing decides a winner"))
    name, status = experiment["name"], experiment["status"]
    if status == "created":
        results.append(setup("experiment ready", PASS, f"{name!r} (id {experiment['id']}) exists and hasn't started"))
    elif status == "paused":
        results.append(setup("experiment ready", WARN, f"{name!r} is paused"))
    elif status == "running":
        results.append(setup("experiment ready", FAIL, f"{name!r} is already running: '{flag}' is locked, so setup fixes come too late"))
    else:
        results.append(setup("experiment ready", FAIL, f"{name!r} has ended and can't restart: create a new experiment"))
    return results, events


def check_setup(admin: Admin, flag: str) -> tuple[list[Result], dict]:
    feature = admin.feature(flag)
    experiment = admin.experiment_for(flag) if feature else None
    exp_results, events = check_experiment(admin, experiment, flag)
    results = [check_warehouse(admin), *check_flag(feature, flag), *exp_results]
    return results, {"feature": feature, "experiment": experiment, "metric_events": events}
