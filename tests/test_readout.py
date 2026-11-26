from fake import EXPERIMENT, results

from preflight.readout import analyse, more_users_needed, users_per_variation
from preflight.smoke import judge


def inference(lift, low, high, chance):
    return {"lift": lift, "ci_low": low, "ci_high": high, "chance_to_win": chance}


def test_interval_crossing_zero_is_not_callable():
    r = analyse(results(inference=inference(0.4, -0.15, 0.95, 0.9)), None, EXPERIMENT, mde=0.2)
    assert not r.callable and r.winner is None
    text = "\n".join(r.lines)
    assert "not callable yet" in text and "-15.0% to +95.0%" in text
    assert "more users per variation" in text


def test_high_chance_alone_does_not_call_it():
    r = analyse(results(inference=inference(0.15, -0.14, 0.44, 0.84)), None, EXPERIMENT)
    assert not r.callable


def test_interval_above_zero_names_the_winner():
    r = analyse(results(control=(1000, 100), treatment=(1000, 140), inference=inference(0.4, 0.12, 0.68, 0.998)), None, EXPERIMENT)
    assert r.callable and r.winner == "early_access" and r.winner_id == 11


def test_interval_below_zero_names_control():
    r = analyse(results(inference=inference(-0.3, -0.5, -0.1, 0.01)), None, EXPERIMENT)
    assert r.callable and r.winner == "control" and r.winner_id is None


def test_sample_ratio_mismatch_blocks_a_call():
    r = analyse(results(inference=inference(0.4, 0.12, 0.68, 0.998), srm=0.0001), None, EXPERIMENT)
    assert r.srm and not r.callable
    assert any("Sample ratio mismatch" in line for line in r.lines)


def test_collecting_data_is_not_callable():
    r = analyse(results(control=(30, 2), treatment=(28, 3), inference=None), None, EXPERIMENT)
    assert not r.callable
    assert any("Collecting data" in line for line in r.lines)


def test_quarantined_identities_are_shown():
    r = analyse(results(inference=inference(0.4, -0.1, 0.9, 0.9)), {"payload": {"excluded_identities": 12}}, EXPERIMENT)
    assert r.excluded == 12 and any("12 quarantined" in line for line in r.lines)


def test_more_users_needed_scales_with_interval_width():
    # Half-width 0.55 at n=150 against a 0.4 lift: n must grow by (0.55/0.4)^2.
    assert more_users_needed(150, -0.15, 0.95, 0.4) == 284 - 150
    assert more_users_needed(150, 0.1, 0.3, 0.4) == 0


def test_users_per_variation_for_plan():
    assert 3700 < users_per_variation(0.10, 0.20) < 3900
    assert users_per_variation(0.10, 0.40) < users_per_variation(0.10, 0.20)


def test_smoke_counts_one_identity_per_visitor():
    after = {"control": {"n": 52, "sum": 6}, "early_access": {"n": 49, "sum": 7}}
    r = {x.check: x.status for x in judge({}, after, ["control", "early_access"], 100, 1.0)}
    assert r == {"both sides exposed": "pass", "conversions arrive": "pass", "one identity per visitor": "pass"}


def test_smoke_catches_double_counting_one_sided_and_flat_conversions():
    after = {"control": {"n": 200, "sum": 0}}
    r = {x.check: x.status for x in judge({}, after, ["control", "early_access"], 100, 1.0)}
    assert r == {"both sides exposed": "fail", "conversions arrive": "fail", "one identity per visitor": "fail"}
