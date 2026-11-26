import http.cookiejar
import random
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from .result import FAIL, PASS, WARN, Result


def visit(url: str, convert_url: str, rate: float) -> bool:
    # One visitor with its own cookie jar: two page views, then maybe a conversion.
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    for _ in range(2):
        opener.open(url, timeout=30).read()
    if convert_url and random.random() < rate:
        try:
            opener.open(urllib.request.Request(convert_url, data=b"", method="POST"), timeout=30).read()
        except urllib.error.HTTPError:
            pass
        return True
    return False


def send(url: str, convert_url: str, visitors: int, rate: float) -> int:
    with ThreadPoolExecutor(max_workers=16) as pool:
        return sum(pool.map(lambda _: visit(url, convert_url, rate), range(visitors)))


def totals(results: dict | None, metric_id: int) -> dict[str, dict]:
    for m in ((results or {}).get("payload") or {}).get("metrics", []):
        if m["metric_id"] == metric_id:
            return m["variants"]
    return {}


def judge(before: dict, after: dict, keys: list[str], visitors: int, rollout: float) -> list[Result]:
    def smoke(check, status, reason):
        return Result(check, status, reason, group="smoke")

    gained = {k: after.get(k, {}).get("n", 0) - before.get(k, {}).get("n", 0) for k in keys}
    empty = [k for k, n in gained.items() if n <= 0]
    results = [
        smoke("both sides exposed", FAIL, f"no new exposures on {', '.join(empty)}: the flag is read in one place and rendered in another, or a variation has no key")
        if empty else smoke("both sides exposed", PASS, " · ".join(f"{k} +{n}" for k, n in gained.items()))
    ]
    converted = sum(after.get(k, {}).get("sum", 0) - before.get(k, {}).get("sum", 0) for k in keys)
    results.append(
        smoke("conversions arrive", PASS, f"+{converted:.0f} conversions")
        if converted > 0 else smoke("conversions arrive", FAIL, "exposures rose but conversions didn't: the event isn't firing, or uses another name or identifier")
    )
    expected = visitors * rollout
    ratio = sum(gained.values()) / expected if expected else 0
    if 0.8 <= ratio <= 1.25:
        results.append(smoke("one identity per visitor", PASS, f"{sum(gained.values())} new identities for {visitors} visitors who each came twice"))
    elif ratio >= 1.6:
        results.append(smoke("one identity per visitor", FAIL, f"{sum(gained.values())} new identities for {visitors} visitors: each visit made a new identity"))
    else:
        results.append(smoke("one identity per visitor", WARN, f"{sum(gained.values())} new identities for {visitors} visitors (expected about {expected:.0f}); events may still be arriving"))
    return results
