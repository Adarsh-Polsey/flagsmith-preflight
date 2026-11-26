import json

from conftest import only, status

PKG = {"package.json": json.dumps({"dependencies": {"@flagsmith/nodejs": "^9.1.0"}})}
WEB_PKG = {"package.json": json.dumps({"dependencies": {"@flagsmith/flagsmith": "^12.4.0"}})}

NODE_HEAD = """
import { Flagsmith } from '@flagsmith/nodejs';
import { randomUUID } from 'crypto';
const flagsmith = new Flagsmith({ environmentKey: process.env.KEY, enableEvents: true });
"""


def node(body: str) -> dict:
    return {"server.ts": NODE_HEAD + body, **PKG}


def test_node_good_app_passes(run):
    results = run(node("""
    app.get('/', async (req, res) => {
      const visitorId = req.cookies.visitor_id ?? randomUUID();
      res.cookie('visitor_id', visitorId);
      const flag = await flagsmith.getExperimentFlag('signup_cta', visitorId);
      res.render('index', { cta: flag.value });
    });
    app.post('/signup', async (req, res) => {
      flagsmith.trackEvent('signup', { identifier: req.cookies.visitor_id });
    });
    """))
    assert [r for r in results if r.status != "pass"] == []


def test_node_random_uuid_per_request_fails(run):
    results = run(node("""
    app.get('/', async (req, res) => {
      const id = randomUUID();
      const flag = await flagsmith.getExperimentFlag('signup_cta', id);
    });
    """))
    [r] = only(results, "stable identifier")
    assert r.status == "fail" and "randomUUID" in r.reason


def test_math_random_and_date_now_fail(run):
    for expr in ("`anon-${Math.random()}`", "String(Date.now())"):
        results = run(node(f"""
        app.get('/', async (req, res) => {{
          await flagsmith.getExperimentFlag('signup_cta', {expr});
        }});
        """))
        assert status(results, "stable identifier") == "fail", expr


def test_node_track_event_case_mismatch_fails(run):
    results = run(node("""
    app.post('/signup', (req, res) => {
      flagsmith.getExperimentFlag('signup_cta', req.user.id);
      flagsmith.trackEvent('Signup', { identifier: req.user.id });
    });
    """))
    assert status(results, "event name") == "fail"


def test_node_conversion_with_other_identifier_fails(run):
    results = run(node("""
    app.post('/signup', (req, res) => {
      flagsmith.getExperimentFlag('signup_cta', req.user.id);
      flagsmith.trackEvent('signup', { identifier: req.sessionID });
    });
    """))
    assert status(results, "same identifier") == "fail"


def test_node_events_disabled_fails(run):
    results = run({"server.ts": NODE_HEAD.replace(", enableEvents: true", "") + "flagsmith.getExperimentFlag('signup_cta', req.user.id);", **PKG})
    assert status(results, "events enabled") == "fail"


def test_node_script_without_close_fails(run):
    results = run(node("""
    for (const userId of users) {
      await flagsmith.getExperimentFlag('signup_cta', userId);
    }
    process.exit(0);
    """))
    assert status(results, "events flushed") == "fail"


def test_node_script_with_close_passes(run):
    results = run(node("""
    for (const userId of users) {
      await flagsmith.getExperimentFlag('signup_cta', userId);
    }
    await flagsmith.close();
    process.exit(0);
    """))
    assert status(results, "events flushed") == "pass"


def test_next_route_handler_without_flush_fails(run):
    results = run(node("""
    export async function POST(req) {
      flagsmith.trackEvent('signup', { identifier: req.cookies.get('vid') });
    }
    """))
    assert status(results, "events flushed") == "fail"


BROWSER_HEAD = """
import flagsmith from '@flagsmith/flagsmith';
await flagsmith.init({ environmentID: 'x', enableEvents: true });
"""


def test_browser_identify_before_read_passes(run):
    results = run({"main.js": BROWSER_HEAD + """
    await flagsmith.identify(localStorage.getItem('visitor_id'));
    const flag = flagsmith.getExperimentFlag('signup_cta');
    button.onclick = () => flagsmith.trackEvent('signup');
    """, **WEB_PKG})
    assert [r for r in results if r.status != "pass"] == []


def test_browser_identify_after_read_fails(run):
    results = run({"main.js": BROWSER_HEAD + """
    const flag = flagsmith.getExperimentFlag('signup_cta');
    await flagsmith.identify(user.id);
    """, **WEB_PKG})
    assert status(results, "identified first") == "fail"


def test_browser_identify_with_random_uuid_fails(run):
    results = run({"main.js": BROWSER_HEAD + """
    await flagsmith.identify(crypto.randomUUID());
    const flag = flagsmith.getExperimentFlag('signup_cta');
    """, **WEB_PKG})
    assert status(results, "stable identifier") == "fail"


def test_react_provider_identity_and_use_experiment(run):
    results = run({
        "App.tsx": """
        import flagsmith from '@flagsmith/flagsmith';
        import { FlagsmithProvider } from '@flagsmith/flagsmith/react';
        export const App = () => (
          <FlagsmithProvider flagsmith={flagsmith} options={{ environmentID: 'x', identity: user.id, enableEvents: true }}>
            <Signup />
          </FlagsmithProvider>
        );
        """,
        "Signup.tsx": """
        import { useExperiment, useFlagsmith } from '@flagsmith/flagsmith/react';
        export function Signup() {
          const flag = useExperiment('signup_cta');
          const flagsmith = useFlagsmith();
          return <button onClick={() => flagsmith.trackEvent('signup')}>{flag?.value}</button>;
        }
        """,
        **WEB_PKG,
    })
    assert status(results, "exposure recorded") == "pass"
    assert status(results, "events enabled") == "pass"
    assert status(results, "event name") == "pass"


def test_use_experiment_from_another_library_is_ignored(run):
    results = run({"Page.tsx": """
    import { useExperiment } from '@growthbook/growthbook-react';
    const { value } = useExperiment({ key: 'signup_cta', variations: [0, 1] });
    """})
    assert status(results, "exposure recorded") == "fail"


def test_android_style_get_experiment_flag_is_ignored(run):
    results = run({"Flags.ts": """
    const enabled = DeviceConfig.getExperimentFlag('signup_cta', false);
    """})
    assert status(results, "exposure recorded") == "fail"


def test_comments_are_ignored(run):
    results = run(node("""
    // const flag = await flagsmith.getExperimentFlag('signup_cta', randomUUID());
    const flag = await flagsmith.getExperimentFlag('signup_cta', req.user.id);
    """))
    assert status(results, "stable identifier") == "pass"


def test_code_inside_strings_is_not_scanned(run):
    results = run({"lint.ts": """
    import flagsmith from '@flagsmith/flagsmith';
    const fixture = "flagsmith.init({ environmentID: 'x' });\\nflagsmith.trackEvent('a');";
    const other = `flagsmith.getExperimentFlag('f', Date.now())`;
    """, **WEB_PKG})
    assert {r.status for r in only(results, "exposure recorded")} == {"fail"}
    assert not [r for r in results if r.check == "events enabled" and r.status == "fail"]


def test_function_named_track_event_is_a_definition_not_a_call(run):
    results = run(node("""
    export async function trackEvent(event: string, identity: string): Promise<void> {
      flagsmith.trackEvent(event, { identifier: identity });
    }
    flagsmith.getExperimentFlag('signup_cta', req.user.id);
    """), events=None)
    assert status(results, "same identifier") != "fail"


def test_init_options_from_a_variable_are_resolved(run):
    results = run({"main.js": """
    import flagsmith from '@flagsmith/flagsmith';
    const options = { environmentID: 'x', enableEvents: true };
    await flagsmith.init(options);
    await flagsmith.identify(user.id);
    flagsmith.getExperimentFlag('signup_cta');
    """, **WEB_PKG})
    assert status(results, "events enabled") == "pass"


def test_init_options_from_a_spread_warn(run):
    results = run({"main.js": """
    import flagsmith from '@flagsmith/flagsmith';
    await flagsmith.init({ ...shared });
    await flagsmith.identify(user.id);
    flagsmith.getExperimentFlag('signup_cta');
    """, **WEB_PKG})
    assert status(results, "events enabled") == "warn"


def test_simulator_minting_an_id_per_visitor_only_warns(run):
    results = run({"scripts/simulate.mjs": NODE_HEAD + """
    for (let i = 0; i < 500; i++) {
      const id = `sim-${Date.now()}-${i}`;
      await flagsmith.getExperimentFlag('signup_cta', id);
      flagsmith.trackEvent('signup', { identifier: id });
    }
    """, **PKG})
    assert status(results, "stable identifier") == "warn"
    assert status(results, "same identifier") == "pass"


def test_one_client_without_events_beside_one_with_them_warns(run):
    results = run({
        "page.tsx": BROWSER_HEAD + "await flagsmith.identify(user.id);\nflagsmith.getExperimentFlag('signup_cta');\n",
        "debug/page.tsx": "import flagsmith from '@flagsmith/flagsmith';\nflagsmith.init({ environmentID: 'x' });\n",
        **WEB_PKG,
    })
    assert status(results, "events enabled") == "warn"


def test_track_event_options_from_a_variable(run):
    resolved = run(node("""
    const payload = { identifier: req.user.id, value: 1 };
    flagsmith.getExperimentFlag('signup_cta', req.user.id);
    flagsmith.trackEvent('signup', payload);
    """))
    assert status(resolved, "same identifier") == "pass"
    unresolved = run(node("""
    flagsmith.getExperimentFlag('signup_cta', req.user.id);
    flagsmith.trackEvent('signup', buildPayload());
    """))
    assert status(unresolved, "same identifier") != "fail"
