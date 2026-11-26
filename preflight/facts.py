from dataclasses import dataclass, field

STABLE, RANDOM, UNKNOWN, MISSING, CONSTANT = "stable", "random", "unknown", "missing", "constant"

# Words that mark an identifier as tied to a person or browser rather than a request.
STABLE_WORDS = (
    "cookie", "session", "user", "uid", "visitor", "account", "customer",
    "member", "device", "anon", "clientid", "identity", "email", "login",
)
PER_REQUEST_WORDS = ("requestid", "traceid", "correlation", "nonce")


@dataclass(frozen=True)
class Ident:
    status: str
    reason: str
    sources: frozenset = frozenset()


@dataclass
class Call:
    kind: str  # exposure, conversion, identify, init
    file: str
    line: int
    lang: str
    flag: str | None = None
    event: str | None = None
    ident: Ident | None = None
    events_enabled: bool | None = None
    note: str = ""


@dataclass
class FileFacts:
    file: str
    lang: str
    calls: list[Call] = field(default_factory=list)
    short_lived: str = ""  # why this file exits soon after running: "script" or "serverless handler"
    short_lived_line: int = 0
    flushes: bool = False


def hint(text: str) -> str | None:
    low = text.lower().replace("_", "").replace("-", "")
    if any(w in low for w in PER_REQUEST_WORDS):
        return RANDOM
    if any(w in low for w in STABLE_WORDS):
        return STABLE
    return None
