import os
import sys
from dataclasses import asdict, dataclass

PASS, FAIL, WARN = "pass", "fail", "warn"


@dataclass
class Result:
    check: str
    status: str
    reason: str
    where: str = ""
    group: str = "code"

    def to_dict(self) -> dict:
        return asdict(self)


def color_enabled() -> bool:
    return sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def paint(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if color_enabled() else text


MARKS = {PASS: ("✓", "32"), FAIL: ("✗", "31"), WARN: ("!", "33")}


def render(results: list[Result]) -> str:
    lines = []
    width = max((len(r.check) for r in results), default=10)
    for group in ("setup", "code", "smoke"):
        rows = [r for r in results if r.group == group]
        if not rows:
            continue
        lines.append(paint(group.capitalize(), "1"))
        for r in rows:
            mark, code = MARKS[r.status]
            where = f"{r.where}  " if r.where else ""
            lines.append(f"  {paint(mark, code)} {r.check.ljust(width)}  {paint(where, '2')}{r.reason}")
        lines.append("")
    lines.append(summary(results))
    return "\n".join(lines)


def summary(results: list[Result]) -> str:
    counts = {s: sum(r.status == s for r in results) for s in (FAIL, WARN, PASS)}
    text = f"{counts[FAIL]} failed · {counts[WARN]} warnings · {counts[PASS]} passed"
    return paint(text, "31;1" if counts[FAIL] else "32;1")


def failed(results: list[Result]) -> list[Result]:
    return [r for r in results if r.status == FAIL]
