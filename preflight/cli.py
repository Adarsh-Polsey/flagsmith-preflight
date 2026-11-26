import argparse
import json
import re
import shutil
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

from . import approvals, state
from .admin import Admin, FlagsmithError
from .code_checks import JS_EXT, PY_EXT, check_code
from .deps import SKIP_DIRS
from .readout import analyse, round_users, users_per_variation
from .result import failed, paint, render
from .setup_checks import check_setup
from .smoke import judge, send, totals


class Context:
    def __init__(self, path: str | None):
        self.root = state.find_root(Path(path or ".").resolve())
        self.state = state.load(self.root)
        env, project = state.binding(self.root)
        if not env or not project:
            sys.exit("No Flagsmith binding: run `flagsmith init` here, or set FLAGSMITH_ENVIRONMENT and FLAGSMITH_PROJECT.")
        self.admin = Admin(env, project)
        self.flag = self.state.get("flag")

    def need_flag(self) -> str:
        if not self.flag:
            sys.exit("No flag yet: run `preflight init --flag <name>` or `preflight create`.")
        return self.flag

    def experiment(self) -> dict:
        exp = self.admin.experiment_for(self.need_flag())
        if exp is None:
            sys.exit(f"No experiment uses '{self.flag}'.")
        return exp

    def dashboard(self, exp: dict) -> str:
        return f"https://app.flagsmith.com/project/{self.admin.project}/environment/{self.admin.environment}/experiments/{exp['id']}"


def run_checks(ctx: Context):
    setup_results, info = check_setup(ctx.admin, ctx.need_flag())
    code_results = check_code(ctx.root, ctx.flag, info["metric_events"] or None)
    return setup_results + code_results, info


def cmd_init(args) -> int:
    root = Path(args.path or ".").resolve()
    data = state.load(root)
    data["flag"] = args.flag
    if args.baseline:
        data["baseline"] = args.baseline
    if args.mde:
        data["mde"] = args.mde
    state.save(root, data)
    print(f"Preflight will watch '{args.flag}' from {root}/.preflight/experiment.json")
    return 0


def cmd_estimate(args) -> int:
    n = users_per_variation(args.baseline, args.lift)
    print(f"About {round_users(n)} users per variation to detect a {args.lift:+.0%} relative lift on a {args.baseline:.1%} baseline (95% interval, 80% power).")
    return 0


def cmd_check(args) -> int:
    ctx = Context(args.path)
    results, info = run_checks(ctx)
    if args.json:
        print(json.dumps([r.to_dict() for r in results], indent=2))
    else:
        exp = info["experiment"]
        about = f" · experiment {exp['name']!r} ({exp['status']})" if exp else ""
        print(paint(f"Preflight · {ctx.flag}{about}", "1") + "\n")
        print(render(results))
    return 1 if failed(results) else 0


def cmd_create(args) -> int:
    root = Path(args.path or ".").resolve()
    ctx = Context(str(root))
    variants = dict(v.split("=", 1) for v in args.variant)
    feature = ctx.admin.feature(args.flag)
    plan = [
        f"reuse flag '{args.flag}'" if feature else f"create multivariate flag '{args.flag}': control {args.control!r}, " + ", ".join(f"{k} {v!r}" for k, v in variants.items()),
        f"metric {args.metric!r} counting event {args.event!r} (occurrence, higher is better)",
        f"experiment {args.name!r}, split evenly, not started",
    ]
    print("This creates in Flagsmith:\n" + "\n".join(f"  - {p}" for p in plan))
    if not approvals.confirm(root, "design", "Create these?", "; ".join(plan), args.yes, args.note):
        print("Nothing created.")
        return 1
    if not feature:
        feature = ctx.admin.create_flag(args.flag, args.control, variants)
    metric = next((m for m in ctx.admin.metrics() if m["definition"].get("event") == args.event), None)
    metric = metric or ctx.admin.create_metric(args.metric, args.event)
    share = round(100 / (len(feature["variants"]) + 1), 2)
    split = {v["id"]: share for v in feature["variants"]}
    exp = ctx.admin.create_experiment(feature, args.name, args.hypothesis, metric["id"], split)
    data = state.load(root)
    data.update(flag=args.flag, experiment=exp["id"])
    state.save(root, data)
    print(f"✓ flag '{args.flag}', metric {metric['id']}, experiment {exp['id']} ({exp['status']}). Next: preflight check")
    return 0


def cmd_set_key(args) -> int:
    ctx = Context(args.path)
    flag = ctx.need_flag()
    action = f"set key '{args.key}' on variation {args.variant} of {flag}"
    if not approvals.confirm(ctx.root, "fix", f"Set key '{args.key}' on variation {args.variant}?", action, args.yes, args.note):
        return 1
    ctx.admin.set_variant_key(flag, args.variant, args.key)
    print(f"✓ {action}")
    return 0


def cmd_log(args) -> int:
    root = state.find_root(Path(args.path or ".").resolve())
    entry = approvals.record(root, args.stage, args.action, "y", "chat", args.note)
    print(f"logged: {entry['stage']} · {entry['action']}")
    return 0


def cmd_start(args) -> int:
    ctx = Context(args.path)
    results, info = run_checks(ctx)
    print(render(results) + "\n")
    bad = failed(results)
    if bad:
        print(paint(f"Refusing to start: {len(bad)} check(s) fail. Fix them, then run preflight check.", "31;1"))
        return 1
    exp = info["experiment"]
    action = f"start experiment {exp['id']} ({exp['name']}), locking {ctx.flag}"
    if not approvals.confirm(ctx.root, "start", f"Start {exp['name']!r}? This locks {ctx.flag}.", action, args.yes, args.note):
        print("Not started.")
        return 1
    ctx.admin.start(exp["id"])
    exp = ctx.admin.experiment(exp["id"])
    if exp["status"] != "running":
        print(f"Flagsmith reports {exp['status']!r}, not running.")
        return 1
    print(paint(f"✓ {exp['name']!r} is running (started {exp['started_at']}). {ctx.flag} is locked.", "32;1"))
    print(f"  {ctx.dashboard(exp)}")
    return 0


def parse_time(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text) if text else None


def wait_for(ctx: Context, exp_id: int, timeout: int) -> dict | None:
    deadline = time.time() + timeout
    while True:
        res = ctx.admin.results(exp_id)
        asked = parse_time((res or {}).get("refresh_requested_at"))
        done = [parse_time(res.get(k)) for k in ("as_of", "last_error_at")] if res else []
        if res and asked and any(d and d >= asked for d in done):
            return res
        if time.time() > deadline:
            return res
        time.sleep(5)


def refresh_results(ctx: Context, exp: dict, timeout: int) -> dict | None:
    wait = ctx.admin.refresh(exp["id"], "results")
    if wait and wait <= 90:
        print(f"Flagsmith allows one refresh every 5 minutes; waiting {wait} s for the window.")
        time.sleep(wait + 2)
        wait = ctx.admin.refresh(exp["id"], "results")
    ctx.admin.refresh(exp["id"], "exposures")
    if wait:
        print(f"Results were refreshed under 5 minutes ago (Flagsmith's limit); next refresh in {wait // 60}m {wait % 60}s. Showing the last results.\n")
        return ctx.admin.results(exp["id"])
    return wait_for(ctx, exp["id"], timeout)


def show_readout(ctx: Context, exp: dict, results: dict | None):
    if not results or not results.get("payload"):
        print("No results computed yet. Events can take a few minutes to reach the warehouse; run readout again shortly.")
        return None
    as_of = parse_time(results["as_of"]).astimezone().strftime("%a %H:%M")
    print(paint(f"{exp['name']} · {exp['status']} · results as of {as_of}", "1"))
    mde, baseline = ctx.state.get("mde"), ctx.state.get("baseline")
    planned = users_per_variation(baseline, mde) if mde and baseline else None
    r = analyse(results, ctx.admin.exposures(exp["id"]), exp, mde, planned)
    for line in r.lines:
        if line.startswith("Verdict"):
            line = paint(line, "32;1" if r.callable else "33;1")
        elif line.startswith("Sample ratio"):
            line = paint(line, "31;1")
        print(line)
    return r


def cmd_smoke(args) -> int:
    ctx = Context(args.path)
    exp = ctx.experiment()
    if exp["status"] != "running":
        print("Smoke needs a running experiment: Flagsmith only records exposures once it starts.")
        return 1
    metric_id = exp["metrics"][0]["metric"]
    keys = ["control", *(o["key"] or f"id {o['id']}" for o in exp["feature"]["multivariate_options"])]
    before = totals(ctx.admin.results(exp["id"]), metric_id)
    base = args.url.rstrip("/")
    print(f"Sending {args.visitors} test visitors to {base}, each twice with its own cookies...")
    converted = send(base + "/", base + args.convert if args.convert else "", args.visitors, args.rate)
    print(f"Sent. {converted} converted. Waiting 60 s for events to reach the warehouse...")
    time.sleep(60)
    while (wait := ctx.admin.refresh(exp["id"], "results")) is not None:
        print(f"Flagsmith allows one refresh every 5 minutes; waiting {wait} s.")
        time.sleep(wait + 2)
    after = totals(wait_for(ctx, exp["id"], args.wait), metric_id)
    rollout = exp.get("experiment_rollout", {}).get("rollout_percentage", 100) / 100
    results = judge(before, after, keys, args.visitors, rollout)
    print(render(results))
    return 1 if failed(results) else 0


def cmd_readout(args) -> int:
    ctx = Context(args.path)
    exp = ctx.experiment()
    if exp["status"] == "created":
        print(f"{exp['name']!r} hasn't started: run preflight start.")
        return 1
    results = ctx.admin.results(exp["id"])
    if exp["status"] == "running" and not args.no_refresh:
        results = refresh_results(ctx, exp, args.wait)
    r = show_readout(ctx, exp, results)
    return 0 if r and r.callable else 2


def served_value(feature: dict, key: str) -> str:
    if key == "control":
        return feature["default_value"]
    return next(v["value"] for v in feature["variants"] if v.get("key") == key)


def cmd_ship(args) -> int:
    ctx = Context(args.path)
    exp = ctx.experiment()
    if exp["status"] != "running":
        print(f"{exp['name']!r} is {exp['status']}: only a running experiment can ship.")
        return 1
    r = show_readout(ctx, exp, ctx.admin.results(exp["id"]))
    if not r or not r.callable:
        print(paint("\nRefusing to ship: there's no callable winner yet. Keep the experiment running.", "31;1"))
        return 1
    feature = ctx.admin.feature(ctx.flag)
    value = served_value(feature, r.winner)
    action = f"end experiment {exp['id']} ({exp['name']}) and serve {r.winner} ({value!r}) to everyone"
    print()
    if not approvals.confirm(ctx.root, "ship", f"End {exp['name']!r} and serve {r.winner} ({value!r}) to everyone?", action, args.yes, args.note):
        print("Not shipped.")
        return 1
    ctx.admin.complete(exp["id"], r.winner_id)
    exp = ctx.admin.experiment(exp["id"])
    print(paint(f"✓ {exp['name']!r} is {exp['status']}: results are frozen and {ctx.flag} is unlocked.", "32;1"))
    ok = served_to_new_identities(ctx, value)
    if ok == 5:
        print(paint(f"✓ 5/5 new identities get {value!r}. No redeploy.", "32;1"))
        return 0
    print(paint(f"✗ only {ok}/5 new identities get {value!r} after 60 s: check the flag in Flagsmith.", "31;1"))
    return 1


def served_to_new_identities(ctx: Context, value: str, timeout: int = 60) -> int:
    # Flagsmith's edge API takes a few seconds to pick up the change, so poll.
    deadline = time.time() + timeout
    while True:
        served = [ctx.admin.evaluate(ctx.flag, f"preflight-ship-check-{uuid.uuid4().hex[:8]}").get("value") for _ in range(5)]
        ok = sum(v == value for v in served)
        if ok == 5 or time.time() > deadline:
            return ok
        time.sleep(3)


def flag_references(root: Path, flag: str) -> list[str]:
    hits = []
    for path in sorted(root.rglob("*")):
        if path.suffix in PY_EXT | JS_EXT and path.is_file() and not SKIP_DIRS & set(path.relative_to(root).parts):
            for i, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
                if re.search(rf"""['"`]{re.escape(flag)}['"`]""", line):
                    hits.append(f"{path.relative_to(root)}:{i}")
    return hits


def cmd_archive(args) -> int:
    ctx = Context(args.path)
    exp = ctx.experiment()
    if exp["status"] != "completed":
        print(f"{exp['name']!r} is {exp['status']}: ship it before archiving the flag.")
        return 1
    refs = flag_references(ctx.root, ctx.flag)
    if refs:
        print(f"Refusing to archive: code still reads '{ctx.flag}' at {', '.join(refs)}. Apply the cleanup diff first.")
        return 1
    action = f"archive flag {ctx.flag}"
    if not approvals.confirm(ctx.root, "cleanup", f"Archive {ctx.flag}?", action, args.yes, args.note):
        return 1
    ctx.admin.archive(ctx.flag)
    print(paint(f"✓ {ctx.flag} archived.", "32;1"))
    return 0


def cmd_approvals(args) -> int:
    root = state.find_root(Path(args.path or ".").resolve())
    path = root / state.DIR / "approvals.jsonl"
    if not path.exists():
        print("No approvals yet.")
        return 0
    width = shutil.get_terminal_size((160, 40)).columns
    for line in path.read_text().splitlines():
        e = json.loads(line)
        at = datetime.fromisoformat(e["at"]).strftime("%a %H:%M")
        mark = paint("y", "32") if e["answer"] == "y" else paint("n", "31")
        room = width - 46
        action = e["action"] if len(e["action"]) <= room else e["action"][: room - 1] + "…"
        print(f"{at}  {mark}  {e['who']:<14} {e['stage']:<8} {action:<{room}} {paint(e['via'], '2')}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="preflight", description="Gates for Flagsmith experiments.")
    p.add_argument("--path", help="app directory (default: here)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def approval(sp):
        sp.add_argument("--yes", action="store_true", help="a human already approved this in chat")
        sp.add_argument("--note", default="", help="the human's words, for the approvals log")

    s = sub.add_parser("init", help="choose the flag to watch")
    s.add_argument("--flag", required=True)
    s.add_argument("--baseline", type=float)
    s.add_argument("--mde", type=float, help="minimum relative lift worth detecting, e.g. 0.2")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("estimate", help="users per variation for a baseline and lift")
    s.add_argument("--baseline", type=float, required=True)
    s.add_argument("--lift", type=float, required=True)
    s.set_defaults(fn=cmd_estimate)

    s = sub.add_parser("create", help="stage 1: create the flag, metric and experiment (not started)")
    for name in ("flag", "control", "event", "metric", "name", "hypothesis"):
        s.add_argument(f"--{name}", required=True)
    s.add_argument("--variant", action="append", required=True, help="key=value, repeatable")
    approval(s)
    s.set_defaults(fn=cmd_create)

    s = sub.add_parser("check", help="stage 2: setup and code checks")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("set-key", help="stage 3 fix: give a variation its key")
    s.add_argument("variant", type=int)
    s.add_argument("key")
    approval(s)
    s.set_defaults(fn=cmd_set_key)

    s = sub.add_parser("log", help="record an approval given in chat")
    s.add_argument("--stage", required=True)
    s.add_argument("--action", required=True)
    s.add_argument("--note", required=True, help="the human's words")
    s.set_defaults(fn=cmd_log)

    s = sub.add_parser("start", help="stage 4: start, only when every check passes")
    approval(s)
    s.set_defaults(fn=cmd_start)

    s = sub.add_parser("smoke", help="stage 2c: send test visitors and check the plumbing (after start)")
    s.add_argument("--url", required=True)
    s.add_argument("--convert", default="", help="path to POST for a conversion, e.g. /signup")
    s.add_argument("--visitors", type=int, default=100)
    s.add_argument("--rate", type=float, default=0.3)
    s.add_argument("--wait", type=int, default=180)
    s.set_defaults(fn=cmd_smoke)

    s = sub.add_parser("readout", help="stage 5: refresh and read the results honestly")
    s.add_argument("--no-refresh", action="store_true")
    s.add_argument("--wait", type=int, default=180, help="seconds to wait for Flagsmith to compute")
    s.set_defaults(fn=cmd_readout)

    s = sub.add_parser("ship", help="stage 6: end the experiment and serve the winner")
    approval(s)
    s.set_defaults(fn=cmd_ship)

    s = sub.add_parser("archive", help="stage 7: archive the flag once code no longer reads it")
    approval(s)
    s.set_defaults(fn=cmd_archive)

    s = sub.add_parser("approvals", help="show the approvals log")
    s.set_defaults(fn=cmd_approvals)

    args = p.parse_args(argv)
    sys.stdout.reconfigure(line_buffering=True)
    try:
        return args.fn(args)
    except FlagsmithError as e:
        print(f"Flagsmith said: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
