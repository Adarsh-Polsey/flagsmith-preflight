"""SIMULATED traffic for the demo app. Every visitor here is a script, not a person."""

import argparse
import http.cookiejar
import random
import re
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

# The true conversion rate behind each button text, so a real winner can emerge.
TRUE_RATES = {"Join the waitlist": 0.10, "Get early access": 0.14}


def visitor(base: str, rng: random.Random) -> tuple[str, bool]:
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    for _ in range(2):  # each visitor comes back once, with the same cookies
        html = opener.open(base + "/", timeout=30).read().decode()
    cta = re.search(r'id="cta">([^<]+)<', html).group(1).strip()
    converted = rng.random() < TRUE_RATES.get(cta, 0)
    if converted:
        opener.open(urllib.request.Request(base + "/signup", data=b"email=sim%40example.com", method="POST"), timeout=30).read()
    return cta, converted


def main():
    p = argparse.ArgumentParser(description="Send simulated visitors through the demo app.")
    p.add_argument("--url", default="http://localhost:5055")
    p.add_argument("--visitors", type=int, default=300)
    p.add_argument("--seed", type=int)
    args = p.parse_args()

    rng = random.Random(args.seed)
    seeds = [rng.random() for _ in range(args.visitors)]
    with ThreadPoolExecutor(max_workers=16) as pool:
        outcomes = list(pool.map(lambda s: visitor(args.url.rstrip("/"), random.Random(s)), seeds))

    seen = Counter(cta for cta, _ in outcomes)
    converted = Counter(cta for cta, ok in outcomes if ok)
    print(f"SIMULATED traffic: {args.visitors} visitors, each visited twice with its own cookies")
    for cta in sorted(seen):
        print(f"  {cta!r:<22} saw {seen[cta]:>5}  signed up {converted[cta]:>4}  (true rate {TRUE_RATES.get(cta, 0):.0%})")


if __name__ == "__main__":
    main()
