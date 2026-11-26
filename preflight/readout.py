import math
from dataclasses import dataclass, field

SRM_THRESHOLD = 0.001  # Flagsmith warns when the observed split is a one-in-a-thousand event
Z95, Z80 = 1.959964, 0.841621


@dataclass
class Readout:
    callable: bool = False
    winner: str | None = None
    winner_id: int | None = None
    lines: list[str] = field(default_factory=list)
    srm: bool = False
    excluded: int = 0


def users_per_variation(baseline: float, lift: float) -> int:
    # Classic two-proportion size at 95% / 80% power; a Bayesian 95% interval clears zero at about the same point.
    p1, p2 = baseline, baseline * (1 + lift)
    variance = p1 * (1 - p1) + p2 * (1 - p2)
    return math.ceil((Z95 + Z80) ** 2 * variance / (p2 - p1) ** 2)


def round_users(n: float) -> str:
    if n < 100:
        return str(math.ceil(n))
    digits = 2 - len(str(int(n)))
    return f"{int(round(n, digits)):,}"


def more_users_needed(n: int, ci_low: float, ci_high: float, effect: float) -> int:
    # Interval width shrinks with the square root of the sample, so n * (half-width / effect)^2 clears zero.
    half = (ci_high - ci_low) / 2
    if effect <= 0:
        return 0
    return max(0, math.ceil(n * (half / effect) ** 2) - n)


def pct(x: float, sign: bool = True) -> str:
    return f"{x * 100:+.1f}%" if sign else f"{x * 100:.1f}%"


def analyse(results: dict, exposures: dict | None, experiment: dict, mde: float | None = None, planned: int | None = None) -> Readout:
    out = Readout()
    payload = results.get("payload") or {}
    options = {o["key"]: o["id"] for o in experiment["feature"]["multivariate_options"] if o.get("key")}
    primary = experiment["metrics"][0]
    metric = next((m for m in payload.get("metrics", []) if m["metric_id"] == primary["metric"]), None)
    if metric is None:
        out.lines.append("No results yet: refresh again in a few minutes.")
        return out

    variants = metric["variants"]
    out.excluded = ((exposures or {}).get("payload") or {}).get("excluded_identities", 0)
    counts = " · ".join(f"{k} {v['n']:,}" for k, v in sorted(variants.items(), key=lambda kv: kv[0] != "control"))
    out.lines.append(f"Exposures: {counts} · {out.excluded:,} quarantined")
    if out.excluded:
        out.lines.append(f"  {out.excluded:,} identities saw more than one variation and were excluded; a growing number means users are switching.")

    srm_p = payload.get("srm_p_value")
    if srm_p is not None and srm_p < SRM_THRESHOLD:
        out.srm = True
        out.lines.append(f"Sample ratio mismatch (p = {srm_p:.4f}): traffic didn't split as configured. Don't act on these results.")

    out.lines.append(f"Metric {primary['metric_name']!r}:")
    inference = metric.get("inference") or {}
    control = variants.get("control")
    for key, v in sorted(variants.items(), key=lambda kv: kv[0] != "control"):
        rate = f"{pct(v['sum'] / v['n'], False)} ({int(v['sum'])}/{v['n']})" if v["n"] else "no users"
        row = f"  {key:<14} {rate}"
        inf = inference.get(key)
        if key != "control":
            if inf:
                row += f"  lift {pct(inf['lift'])}  95% CI {pct(inf['ci_low'])} to {pct(inf['ci_high'])}  chance to beat control {inf['chance_to_win'] * 100:.0f}%"
            else:
                row += "  collecting data (Flagsmith needs 50 users and 5 conversions per variation)"
        out.lines.append(row)

    treatments = {k: inf for k, inf in inference.items() if k != "control"}
    winners = {k: inf for k, inf in treatments.items() if inf and inf["ci_low"] > 0}
    if winners and not out.srm:
        key = max(winners, key=lambda k: winners[k]["lift"])
        out.callable, out.winner, out.winner_id = True, key, options.get(key)
        inf = winners[key]
        out.lines.append(f"Verdict: {key} wins. Its 95% interval ({pct(inf['ci_low'])} to {pct(inf['ci_high'])}) is above zero.")
        smallest = min(v["n"] for v in variants.values())
        if planned and smallest < planned:
            out.lines.append(f"  Called below the planned {round_users(planned)} users per variation: early leads often shrink, so expect the true lift nearer the interval's low end.")
        return out
    if treatments and all(inf and inf["ci_high"] < 0 for inf in treatments.values()) and not out.srm:
        out.callable, out.winner, out.winner_id = True, "control", None
        out.lines.append("Verdict: control wins. Every variation's 95% interval is below zero.")
        return out

    if out.srm:
        out.lines.append("Verdict: not callable: fix the split first.")
        return out
    live = {k: inf for k, inf in treatments.items() if inf}
    if not live:
        n = min((v["n"] for v in variants.values()), default=0)
        out.lines.append(f"Verdict: not callable yet. Collecting data: {n} users in the smallest variation, Flagsmith shows results from 50 (and 5 conversions).")
        return out
    key = max(live, key=lambda k: live[k]["chance_to_win"])
    inf = live[key]
    n = min(control["n"], variants[key]["n"]) if control else variants[key]["n"]
    effect = abs(inf["lift"]) or (mde or 0)
    more = more_users_needed(n, inf["ci_low"], inf["ci_high"], effect)
    out.lines.append(f"Verdict: not callable yet. {key}'s 95% interval ({pct(inf['ci_low'])} to {pct(inf['ci_high'])}) crosses zero.")
    if more:
        out.lines.append(f"  Roughly {round_users(more)} more users per variation would make a {pct(effect)} lift callable, if it holds.")
    if mde and abs(inf["lift"]) < mde:
        out.lines.append(f"  The observed lift is below the plan's minimum ({pct(mde)}).")
    return out
