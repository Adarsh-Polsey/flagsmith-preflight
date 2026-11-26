import difflib
from pathlib import Path

from .deps import SKIP_DIRS, check_deps, find_deps
from .facts import CONSTANT, MISSING, RANDOM, STABLE, Call, FileFacts
from .jscode import scan_js
from .pycode import scan_python
from .result import FAIL, PASS, WARN, Result

PY_EXT = {".py"}
JS_EXT = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
MAX_FILE_BYTES = 400_000


def scan(root: Path) -> list[FileFacts]:
    found = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in PY_EXT | JS_EXT:
            continue
        rel_parts = path.relative_to(root).parts
        if SKIP_DIRS & set(rel_parts) or path.stat().st_size > MAX_FILE_BYTES:
            continue
        src = path.read_text(errors="ignore")
        rel = str(path.relative_to(root))
        facts = scan_python(src, rel) if path.suffix in PY_EXT else scan_js(src, rel)
        if facts:
            found.append(facts)
    return found


def where(call: Call) -> str:
    return f"{call.file}:{call.line}"


def check_code(root: Path, flag: str | None = None, metric_events: list[str] | None = None) -> list[Result]:
    files = scan(root)
    calls = [c for f in files for c in f.calls]
    exposures = [c for c in calls if c.kind == "exposure" and (flag is None or c.flag in (flag, None))]
    conversions = [c for c in calls if c.kind == "conversion"]
    results = [check_exposure_found(exposures, flag)]
    if exposures:
        results += check_stable(exposures)
        results += check_identified(exposures)
    results += check_event_names(conversions, metric_events)
    results += check_same_identifier(exposures, conversions, metric_events)
    results += check_events_enabled([c for c in calls if c.kind == "init"], bool(exposures or conversions))
    results += check_flushed(files)
    results += check_deps(find_deps(root), {f.lang for f in files})
    return results


def check_exposure_found(exposures: list[Call], flag: str | None) -> Result:
    name = f"'{flag}'" if flag else "an experiment flag"
    if not exposures:
        return Result("exposure recorded", FAIL, f"no get_experiment_flag / getExperimentFlag / useExperiment call reads {name}: nothing records exposures")
    first = exposures[0]
    more = f" (+{len(exposures) - 1} more)" if len(exposures) > 1 else ""
    return Result("exposure recorded", PASS, f"{first.note}({first.flag or '…'!r}) records the exposure{more}", where(first))


def check_stable(exposures: list[Call]) -> list[Result]:
    results = []
    for c in exposures:
        s = c.ident.status
        if s == RANDOM:
            results.append(Result("stable identifier", FAIL, f"{c.ident.reason}: visitors switch variations and get counted twice", where(c)))
        elif s == CONSTANT:
            results.append(Result("stable identifier", WARN, c.ident.reason, where(c)))
        elif s != STABLE and s != MISSING:
            results.append(Result("stable identifier", WARN, c.ident.reason, where(c)))
    if not results:
        stable = [c for c in exposures if c.ident.status == STABLE]
        if stable:
            results.append(Result("stable identifier", PASS, stable[0].ident.reason, where(stable[0])))
    return results


def check_identified(exposures: list[Call]) -> list[Result]:
    missing = [c for c in exposures if c.ident.status == MISSING]
    if missing:
        return [Result("identified first", FAIL, f"{c.ident.reason}: exposures without an identity are skipped", where(c)) for c in missing]
    c = exposures[0]
    how = "identify() runs before the flag is read" if c.lang == "js" and c.note != "getExperimentFlag" else "the identifier is passed with the flag read"
    return [Result("identified first", PASS, how, where(c))]


def near_miss(name: str, targets: list[str]) -> str | None:
    for t in targets:
        if name.lower() == t.lower():
            return t
    close = difflib.get_close_matches(name, targets, n=1, cutoff=0.75)
    return close[0] if close else None


def check_event_names(conversions: list[Call], metric_events: list[str] | None) -> list[Result]:
    if metric_events is None:
        if not conversions:
            return [Result("event name", WARN, "no track_event / trackEvent call: conversions won't be counted")]
        return [Result("event name", PASS, f"{len(conversions)} conversion call(s) found; no metric to compare against", where(conversions[0]))]
    results = []
    for event in metric_events:
        exact = [c for c in conversions if c.event == event]
        if exact:
            results.append(Result("event name", PASS, f"track_event({event!r}) matches the metric's event exactly", where(exact[0])))
            continue
        near = [(c, c.event) for c in conversions if c.event and near_miss(c.event, [event])]
        if near:
            c, sent = near[0]
            case = " (event names are case-sensitive)" if sent.lower() == event.lower() else ""
            results.append(Result("event name", FAIL, f"sends {sent!r} but the metric counts {event!r}{case}: did you mean {event!r}?", where(c)))
        elif any(c.event is None for c in conversions):
            c = next(c for c in conversions if c.event is None)
            results.append(Result("event name", WARN, f"event name isn't a literal: check it equals {event!r}", where(c)))
        else:
            results.append(Result("event name", FAIL, f"nothing sends {event!r}, the metric's event: conversions will read zero"))
    return results


def relevant_conversions(conversions: list[Call], metric_events: list[str] | None) -> list[Call]:
    if not metric_events:
        return conversions
    return [c for c in conversions if c.event is None or near_miss(c.event, metric_events)]


def joinable(sources: frozenset) -> frozenset:
    # Two calls to uuid4() share a source name but never a value.
    return frozenset(s for s in sources if not s.startswith("random:"))


def describe(sources: frozenset) -> str:
    names = []
    for s in sorted(sources):
        kind, _, rest = s.partition(":")
        if kind == "random":
            names.append(f"a fresh {rest}()")
        elif kind in ("name", "const"):
            names.append(rest)
        elif kind != "call":
            names.append(s)
    return ", ".join(names) or "unknown"


def check_same_identifier(exposures: list[Call], conversions: list[Call], metric_events: list[str] | None) -> list[Result]:
    convs = relevant_conversions(conversions, metric_events)
    if not exposures or not convs:
        return []
    exp_sources = frozenset().union(*(c.ident.sources for c in exposures))
    exp_joinable = joinable(exp_sources)
    results = []
    for c in convs:
        if c.ident.status == MISSING:
            results.append(Result("same identifier", FAIL, "conversion sent without an identifier: it can't be joined to an exposure", where(c)))
        elif joinable(c.ident.sources) & exp_joinable:
            continue
        elif c.ident.status in (STABLE, RANDOM, CONSTANT) and any(e.ident.status in (STABLE, RANDOM, CONSTANT) for e in exposures):
            results.append(Result("same identifier", FAIL, f"conversion uses {describe(c.ident.sources)} but the exposure uses {describe(exp_sources)}: they never join", where(c)))
        else:
            results.append(Result("same identifier", WARN, f"can't confirm the conversion ({describe(c.ident.sources)}) uses the exposure's identifier ({describe(exp_sources)})", where(c)))
    if not results:
        results.append(Result("same identifier", PASS, f"exposure and conversion both use {describe(exp_joinable & convs[0].ident.sources)}", where(convs[0])))
    return results


def check_events_enabled(inits: list[Call], uses_events: bool) -> list[Result]:
    off = [c for c in inits if not c.events_enabled]
    if off:
        return [Result("events enabled", FAIL, "SDK client created without enable_events / enableEvents: no exposures or conversions are sent", where(c)) for c in off]
    if inits:
        return [Result("events enabled", PASS, "SDK client created with events enabled", where(inits[0]))]
    if uses_events:
        return [Result("events enabled", WARN, "couldn't find where the SDK client is created: check events are enabled")]
    return []


def check_flushed(files: list[FileFacts]) -> list[Result]:
    results = []
    for f in files:
        sends = any(c.kind in ("exposure", "conversion") for c in f.calls)
        if not (f.short_lived and sends):
            continue
        at = f"{f.file}:{f.short_lived_line}"
        if f.flushes:
            results.append(Result("events flushed", PASS, f"{f.short_lived} sends buffered events before it exits", at))
        else:
            results.append(Result("events flushed", FAIL, f"{f.short_lived} exits with events still buffered: call flush_events() / close() (Python) or flushEvents() / close() (JS)", at))
    if not results and files:
        results.append(Result("events flushed", PASS, "no scripts or serverless handlers send events; long-running apps flush every 10 s"))
    return results
