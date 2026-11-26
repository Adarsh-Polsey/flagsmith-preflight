"""Prints a field-scan results.json as a table: usage: summary.py results.json"""

import json
import sys

ORDER = ["exposure recorded", "stable identifier", "identified first", "event name", "same identifier",
         "events enabled", "events flushed", "SDK version"]


def main():
    r = json.load(open(sys.argv[1]))
    print(f"\033[1mField scan · {r['scanned_at'][:10]} · public GitHub repos that call Flagsmith's experiment SDK\033[0m")
    print(f"  {r['qualified']} repos scanned read-only (from {r['candidates']} code-search candidates; names not kept)")
    print(f"  \033[31;1m{r['repos_with_any_failure']} of {r['qualified']}\033[0m have at least one failing check\n")
    print(f"  {'check':<20} {'fail':>5} {'warn':>5} {'pass':>5}")
    for check in ORDER:
        c = r["checks"].get(check, {})
        if c:
            print(f"  {check:<20} \033[31m{c.get('fail', 0):>5}\033[0m \033[33m{c.get('warn', 0):>5}\033[0m \033[32m{c.get('pass', 0):>5}\033[0m")


if __name__ == "__main__":
    main()
