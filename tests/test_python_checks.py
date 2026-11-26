import textwrap

from conftest import only, status

HEADER = """
import os, uuid, time
from flask import Flask, request, session
from flagsmith import Flagsmith

app = Flask(__name__)
flagsmith = Flagsmith(environment_key=os.environ["KEY"], enable_events=True)
"""

REQS = {"requirements.txt": "flagsmith>=6.3.0\n"}


def app(body: str) -> dict:
    return {"app.py": HEADER + textwrap.dedent(body), **REQS}


GOOD = app("""
def visitor_id():
    return request.cookies.get("visitor_id") or str(uuid.uuid4())

@app.get("/")
def home():
    flag = flagsmith.get_experiment_flag("signup_cta", identifier=visitor_id())
    return flag.value

@app.post("/signup")
def signup():
    flagsmith.track_event("signup", identifier=visitor_id())
""")


def test_good_app_passes_every_check(run):
    results = run(GOOD)
    assert [r for r in results if r.status != "pass"] == []
    assert {r.check for r in results} == {
        "exposure recorded", "stable identifier", "identified first", "event name",
        "same identifier", "events enabled", "events flushed", "SDK version",
    }


# exposure recorded

def test_no_exposure_call_fails(run):
    results = run(app("""
    @app.get("/")
    def home():
        return flagsmith.get_identity_flags("u").get_flag("signup_cta").value
    """))
    assert status(results, "exposure recorded") == "fail"


def test_exposure_for_another_flag_does_not_count(run):
    results = run(app("""
    def home(user_id):
        flagsmith.get_experiment_flag("pricing_page", identifier=user_id)
    """))
    assert status(results, "exposure recorded") == "fail"


def test_flag_name_from_module_constant_counts(run):
    results = run(app("""
    FLAG = "signup_cta"
    def home(user_id):
        flagsmith.get_experiment_flag(FLAG, identifier=user_id)
    """))
    assert status(results, "exposure recorded") == "pass"


# stable identifier

def test_uuid4_per_request_fails(run):
    results = run(app("""
    @app.get("/")
    def home():
        identifier = str(uuid.uuid4())
        return flagsmith.get_experiment_flag("signup_cta", identifier=identifier).value
    """))
    [r] = only(results, "stable identifier")
    assert r.status == "fail"
    assert "uuid.uuid4() (line 10)" in r.reason and r.where == "app.py:11"


def test_time_based_identifier_fails(run):
    results = run(app("""
    def home():
        return flagsmith.get_experiment_flag("signup_cta", identifier=f"anon-{time.time()}")
    """))
    assert status(results, "stable identifier") == "fail"


def test_request_id_header_fails(run):
    results = run(app("""
    def home():
        rid = request.headers.get("X-Request-ID")
        return flagsmith.get_experiment_flag("signup_cta", identifier=rid)
    """))
    assert status(results, "stable identifier") == "fail"


def test_cookie_identifier_passes(run):
    results = run(app("""
    def home():
        return flagsmith.get_experiment_flag("signup_cta", identifier=request.cookies["vid"])
    """))
    assert status(results, "stable identifier") == "pass"


def test_session_identifier_passes(run):
    results = run(app("""
    def home():
        if "sid" not in session:
            session["sid"] = uuid.uuid4().hex
        return flagsmith.get_experiment_flag("signup_cta", identifier=session["sid"])
    """))
    assert status(results, "stable identifier") == "pass"


def test_user_id_passes(run):
    results = run(app("""
    def home(current_user):
        return flagsmith.get_experiment_flag("signup_cta", identifier=str(current_user.id))
    """))
    assert status(results, "stable identifier") == "pass"


def test_get_or_create_with_reassignment_passes(run):
    results = run(app("""
    def home():
        vid = request.cookies.get("vid")
        if not vid:
            vid = str(uuid.uuid4())
        return flagsmith.get_experiment_flag("signup_cta", identifier=vid)
    """))
    assert status(results, "stable identifier") == "pass"


def test_unknown_identifier_warns(run):
    results = run(app("""
    def home(key):
        return flagsmith.get_experiment_flag("signup_cta", identifier=key)
    """))
    assert status(results, "stable identifier") == "warn"


def test_constant_identifier_warns(run):
    results = run(app("""
    def home():
        return flagsmith.get_experiment_flag("signup_cta", identifier="user_42")
    """))
    assert status(results, "stable identifier") == "warn"


# identified first

def test_missing_identifier_fails(run):
    results = run(app("""
    def home():
        return flagsmith.get_experiment_flag("signup_cta", identifier=None)
    """))
    assert status(results, "identified first") == "fail"


def test_identifier_passed_with_read_passes(run):
    assert status(run(GOOD), "identified first") == "pass"


# event name

def test_case_mismatch_fails_with_suggestion(run):
    results = run(app("""
    def home(user_id):
        flagsmith.get_experiment_flag("signup_cta", identifier=user_id)
    def signup(user_id):
        flagsmith.track_event("Signup", identifier=user_id)
    """))
    [r] = only(results, "event name")
    assert r.status == "fail"
    assert "did you mean 'signup'" in r.reason and "case-sensitive" in r.reason


def test_near_miss_fails_with_suggestion(run):
    results = run(app("""
    def signup(user_id):
        flagsmith.track_event("sign_up", identifier=user_id)
    """), events=["signup"])
    [r] = only(results, "event name")
    assert r.status == "fail" and "did you mean 'signup'" in r.reason


def test_metric_event_never_sent_fails(run):
    results = run(app("""
    def signup(user_id):
        flagsmith.track_event("page_view", identifier=user_id)
    """))
    assert status(results, "event name") == "fail"


def test_exact_event_passes(run):
    assert status(run(GOOD), "event name") == "pass"


def test_event_name_from_variable_warns(run):
    results = run(app("""
    def signup(user_id, name):
        flagsmith.track_event(name, identifier=user_id)
    """))
    assert status(results, "event name") == "warn"


# same identifier

def test_conversion_with_session_id_fails(run):
    results = run(app("""
    def home():
        uid = request.cookies.get("user_id")
        flagsmith.get_experiment_flag("signup_cta", identifier=uid)
    def signup():
        flagsmith.track_event("signup", identifier=request.cookies.get("session"))
    """))
    [r] = only(results, "same identifier")
    assert r.status == "fail"
    assert "cookies[session]" in r.reason and "cookies[user_id]" in r.reason


def test_conversion_without_identifier_fails(run):
    results = run(app("""
    def home(user_id):
        flagsmith.get_experiment_flag("signup_cta", identifier=user_id)
    def signup():
        flagsmith.track_event("signup")
    """))
    assert status(results, "same identifier") == "fail"


def test_two_fresh_uuids_never_join(run):
    results = run(app("""
    def home():
        flagsmith.get_experiment_flag("signup_cta", identifier=str(uuid.uuid4()))
    def signup():
        flagsmith.track_event("signup", identifier=str(uuid.uuid4()))
    """))
    assert status(results, "same identifier") == "fail"


def test_same_cookie_in_both_handlers_passes(run):
    results = run(app("""
    def home():
        flagsmith.get_experiment_flag("signup_cta", identifier=request.cookies["vid"])
    def signup():
        vid = request.cookies.get("vid")
        flagsmith.track_event("signup", identifier=vid)
    """))
    assert status(results, "same identifier") == "pass"


def test_unrelated_events_are_not_compared(run):
    results = run(app("""
    def home():
        flagsmith.get_experiment_flag("signup_cta", identifier=request.cookies["vid"])
        flagsmith.track_event("page_view", identifier="system")
    def signup():
        flagsmith.track_event("signup", identifier=request.cookies["vid"])
    """))
    assert status(results, "same identifier") == "pass"


# events enabled

def test_client_without_events_fails(run):
    results = run({"app.py": """
    from flagsmith import Flagsmith
    flagsmith = Flagsmith(environment_key="x")
    def home(user_id):
        flagsmith.get_experiment_flag("signup_cta", identifier=user_id)
    """, **REQS})
    assert status(results, "events enabled") == "fail"


def test_client_with_events_false_fails(run):
    results = run({"app.py": """
    from flagsmith import Flagsmith
    flagsmith = Flagsmith(environment_key="x", enable_events=False)
    """, **REQS})
    assert status(results, "events enabled") == "fail"


def test_client_init_in_another_module_is_found(run):
    results = run({
        "flags.py": """
        from flagsmith import Flagsmith
        client = Flagsmith(environment_key="x", enable_events=True)
        """,
        "views.py": """
        from flags import client
        def home(user_id):
            client.get_experiment_flag("signup_cta", identifier=user_id)
        """,
        **REQS,
    })
    assert status(results, "events enabled") == "pass"


def test_no_client_init_warns(run):
    results = run({"views.py": """
    from flags import client
    def home(user_id):
        client.get_experiment_flag("signup_cta", identifier=user_id)
    """, **REQS})
    assert status(results, "events enabled") == "warn"


# events flushed

SCRIPT = """
from flagsmith import Flagsmith
flagsmith = Flagsmith(environment_key="x", enable_events=True)

def main():
    for user_id in ["a", "b"]:
        flagsmith.get_experiment_flag("signup_cta", identifier=user_id)
        flagsmith.track_event("signup", identifier=user_id)
"""


def test_script_without_flush_fails(run):
    results = run({"job.py": SCRIPT + "\nif __name__ == '__main__':\n    main()\n", **REQS})
    [r] = only(results, "events flushed")
    assert r.status == "fail" and r.where == "job.py:9"


def test_script_with_close_passes(run):
    results = run({"job.py": SCRIPT + "\nif __name__ == '__main__':\n    main()\n    flagsmith.close()\n", **REQS})
    assert status(results, "events flushed") == "pass"


def test_script_with_buffer_of_one_passes(run):
    body = SCRIPT.replace(
        'enable_events=True)',
        'enable_events=True, event_processor_config=EventProcessorConfig(max_buffer_items=1))',
    )
    results = run({"job.py": body + "\nif __name__ == '__main__':\n    main()\n", **REQS})
    assert status(results, "events flushed") == "pass"


def test_lambda_handler_without_flush_fails(run):
    results = run({"handler.py": """
    from flagsmith import Flagsmith
    flagsmith = Flagsmith(environment_key="x", enable_events=True)
    def lambda_handler(event, context):
        flagsmith.track_event("signup", identifier=event["user_id"])
    """, **REQS})
    assert status(results, "events flushed") == "fail"


def test_lambda_handler_with_flush_passes(run):
    results = run({"handler.py": """
    from flagsmith import Flagsmith
    flagsmith = Flagsmith(environment_key="x", enable_events=True)
    def lambda_handler(event, context):
        flagsmith.track_event("signup", identifier=event["user_id"])
        flagsmith.flush_events()
    """, **REQS})
    assert status(results, "events flushed") == "pass"


def test_web_app_with_main_guard_is_not_a_script(run):
    results = run(app("""
    def home(user_id):
        flagsmith.get_experiment_flag("signup_cta", identifier=user_id)

    if __name__ == "__main__":
        app.run()
    """))
    assert status(results, "events flushed") == "pass"


def test_unrelated_function_with_the_same_name_is_ignored(run):
    results = run({"sim.py": """
    def get_experiment_flag(name, identifier):
        return name in ("a", "b")

    def main():
        return get_experiment_flag("signup_cta", identifier="x")
    """})
    assert status(results, "exposure recorded") == "fail"


def test_client_built_from_kwargs_warns(run):
    results = run({"flags.py": """
    from flagsmith import Flagsmith
    def client(**kwargs):
        return Flagsmith(**kwargs)
    def home(user_id):
        client().get_experiment_flag("signup_cta", identifier=user_id)
    """, **REQS})
    assert status(results, "events enabled") == "warn"


def test_offline_client_is_ignored(run):
    results = run({"flags.py": """
    from flagsmith import Flagsmith
    offline = Flagsmith(offline_mode=True, offline_handler=handler)
    live = Flagsmith(environment_key="x", enable_events=True)
    def home(user_id):
        live.get_experiment_flag("signup_cta", identifier=user_id)
    """, **REQS})
    assert status(results, "events enabled") == "pass"


def test_test_files_are_skipped(run):
    results = run({
        "tests/test_flags.py": """
        from flagsmith import Flagsmith
        flagsmith = Flagsmith(environment_key="x")
        flagsmith.get_experiment_flag("signup_cta", identifier=str(uuid.uuid4()))
        """,
        **GOOD,
    })
    assert [r for r in results if r.status != "pass"] == []


def test_same_variable_in_one_scope_joins_even_if_random(run):
    results = run({"simulate.py": """
    import uuid
    from flagsmith import Flagsmith
    flagsmith = Flagsmith(environment_key="x", enable_events=True)

    def visit():
        vid = str(uuid.uuid4())
        flagsmith.get_experiment_flag("signup_cta", identifier=vid)
        flagsmith.track_event("signup", identifier=vid)

    if __name__ == "__main__":
        for _ in range(100):
            visit()
        flagsmith.close()
    """, **REQS})
    assert status(results, "stable identifier") == "warn"
    assert status(results, "same identifier") == "pass"
