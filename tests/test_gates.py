import copy
import json

import pytest
from fake import EXPERIMENT, FEATURE, FakeAdmin, results

from preflight import cli

GOOD_APP = """
from flask import Flask, request
from flagsmith import Flagsmith
flagsmith = Flagsmith(environment_key="x", enable_events=True)

def home():
    return flagsmith.get_experiment_flag("signup_cta", identifier=request.cookies["vid"])

def signup():
    flagsmith.track_event("signup", identifier=request.cookies["vid"])
"""


@pytest.fixture
def app(tmp_path, monkeypatch):
    (tmp_path / "app.py").write_text(GOOD_APP)
    (tmp_path / "requirements.txt").write_text("flagsmith>=6.3.0\n")
    (tmp_path / ".preflight").mkdir()
    (tmp_path / ".preflight" / "experiment.json").write_text(json.dumps({"flag": "signup_cta"}))
    monkeypatch.setenv("FLAGSMITH_ENVIRONMENT", "env-key")
    monkeypatch.setenv("FLAGSMITH_PROJECT", "55614")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def use(monkeypatch, admin):
    monkeypatch.setattr(cli, "Admin", lambda env, project: admin)
    return admin


def answer(monkeypatch, text):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: text)


def log(app):
    path = app / ".preflight" / "approvals.jsonl"
    return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []


def test_start_refuses_while_a_check_fails(app, monkeypatch, capsys):
    feature = copy.deepcopy(FEATURE)
    feature["variants"][0]["key"] = None
    admin = use(monkeypatch, FakeAdmin(feature=feature))
    answer(monkeypatch, "y")
    assert cli.main(["start"]) == 1
    assert admin.calls == [] and log(app) == []
    assert "Refusing to start" in capsys.readouterr().out


def test_start_asks_and_logs_the_yes(app, monkeypatch, capsys):
    admin = use(monkeypatch, FakeAdmin())
    prompts = []
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda p: prompts.append(p) or "y")
    assert cli.main(["start"]) == 0
    assert prompts == ["Start 'Signup CTA'? This locks signup_cta. [y/N] "]
    assert admin.calls == [("start", 7)]
    [entry] = log(app)
    assert entry["stage"] == "start" and entry["answer"] == "y" and entry["via"] == "prompt"


def test_start_does_nothing_on_no(app, monkeypatch):
    admin = use(monkeypatch, FakeAdmin())
    answer(monkeypatch, "")
    assert cli.main(["start"]) == 1
    assert admin.calls == []
    assert log(app)[0]["answer"] == "n"


def test_start_without_a_terminal_needs_yes_and_note(app, monkeypatch):
    admin = use(monkeypatch, FakeAdmin())
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert cli.main(["start"]) == 1
    assert cli.main(["start", "--yes"]) == 1
    assert admin.calls == []
    assert cli.main(["start", "--yes", "--note", "adarsh: yes, start it"]) == 0
    assert admin.calls == [("start", 7)]
    assert log(app)[0]["via"] == "chat" and log(app)[0]["note"] == "adarsh: yes, start it"


def running(inf, **kw):
    exp = {**EXPERIMENT, "status": "running"}
    return FakeAdmin(experiment=exp, results=results(inference=inf, **kw))


def test_ship_refuses_without_a_callable_result(app, monkeypatch, capsys):
    admin = use(monkeypatch, running({"lift": 0.4, "ci_low": -0.1, "ci_high": 0.9, "chance_to_win": 0.9}))
    answer(monkeypatch, "y")
    assert cli.main(["ship"]) == 1
    assert admin.calls == [] and log(app) == []
    assert "Refusing to ship" in capsys.readouterr().out


def test_ship_refuses_on_sample_ratio_mismatch(app, monkeypatch):
    admin = use(monkeypatch, running({"lift": 0.4, "ci_low": 0.1, "ci_high": 0.7, "chance_to_win": 0.99}, srm=0.00001))
    answer(monkeypatch, "y")
    assert cli.main(["ship"]) == 1
    assert admin.calls == []


def test_ship_serves_the_winner_after_a_yes(app, monkeypatch, capsys):
    admin = use(monkeypatch, running({"lift": 0.4, "ci_low": 0.1, "ci_high": 0.7, "chance_to_win": 0.99}))
    answer(monkeypatch, "y")
    assert cli.main(["ship"]) == 0
    assert admin.calls == [("complete", 7, 11)]
    assert "5/5 new identities get 'Get early access'" in capsys.readouterr().out
    assert log(app)[0]["stage"] == "ship"


def test_archive_refuses_while_code_reads_the_flag(app, monkeypatch, capsys):
    admin = use(monkeypatch, FakeAdmin(experiment={**EXPERIMENT, "status": "completed"}))
    answer(monkeypatch, "y")
    assert cli.main(["archive"]) == 1
    assert admin.calls == []
    assert "app.py:7" in capsys.readouterr().out


def test_archive_after_cleanup(app, monkeypatch):
    admin = use(monkeypatch, FakeAdmin(experiment={**EXPERIMENT, "status": "completed"}))
    (app / "app.py").write_text("def home():\n    return 'Get early access'\n")
    answer(monkeypatch, "y")
    assert cli.main(["archive"]) == 0
    assert admin.calls == [("archive", "signup_cta")]


def test_ship_reports_a_winner_that_isnt_served(app, monkeypatch, capsys):
    admin = use(monkeypatch, running({"lift": 0.4, "ci_low": 0.1, "ci_high": 0.7, "chance_to_win": 0.99}))
    admin.evaluate = lambda flag, identity: {"value": "Join the waitlist"}
    clock = iter(range(0, 10_000, 10))
    monkeypatch.setattr(cli.time, "time", lambda: next(clock))
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    answer(monkeypatch, "y")
    assert cli.main(["ship"]) == 1
    assert "✗ only 0/5" in capsys.readouterr().out


def test_code_only_check_needs_no_flagsmith(app, monkeypatch, capsys):
    monkeypatch.delenv("FLAGSMITH_ENVIRONMENT")
    (app / "app.py").write_text(GOOD_APP.replace('"signup"', '"Signup"'))
    assert cli.main(["check", "--code-only", "--event", "signup"]) == 1
    out = capsys.readouterr().out
    assert "did you mean 'signup'" in out and "Setup" not in out
