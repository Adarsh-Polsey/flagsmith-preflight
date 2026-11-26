import re

from .facts import CONSTANT, MISSING, RANDOM, STABLE, UNKNOWN, Call, FileFacts, Ident, hint

IMPORT_RE = re.compile(r"""(?:from\s+|require\(\s*|import\(\s*)['"]((?:@flagsmith/[\w-]+|flagsmith(?:-nodejs)?)(?:/[\w/-]*)?)['"]""")
CALL_RE = re.compile(r"(?:([\w$.]+)\s*\.\s*)?\b(getExperimentFlag|useExperiment|trackEvent|trackExposureEvent|identify|init|flushEvents|close)\s*\(")
NEW_RE = re.compile(r"new\s+(?:[\w$]+\.)?Flagsmith\s*\(")
PROVIDER_RE = re.compile(r"<FlagsmithProvider\b")
RANDOM_RE = re.compile(r"randomUUID\s*\(|\buuidv?4?\s*\(|\bv4\s*\(|nanoid\s*\(|Math\.random\s*\(|Date\.now\s*\(|new\s+Date\s*\(|performance\.now\s*\(|getRandomValues\s*\(|\bcuid2?\s*\(|ulid\s*\(")
SHORT_LIVED_RE = [
    (re.compile(r"export\s+(?:async\s+)?function\s+handler\b|exports\.handler\s*=|export\s+const\s+handler\s*="), "serverless handler"),
    (re.compile(r"export\s+(?:async\s+)?function\s+(?:GET|POST|PUT|PATCH|DELETE)\s*\(|getServerSideProps"), "serverless handler"),
    (re.compile(r"^#!.*node|process\.exit\s*\("), "script"),
]
JS_STABLE_WORDS = ("localstorage", "storage")


def strip_comments(src: str) -> str:
    out, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        if c in "'\"`":
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            out.append(src[i:j + 1])
            i = j + 1
        elif src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j == -1 else j
            out.append(" " * (j - i))
            i = j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j == -1 else j + 2
            out.append(re.sub(r"[^\n]", " ", src[i:j]))
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def split_args(src: str, start: int) -> tuple[list[str], int]:
    # start points just past "(": return top-level args and the index after ")".
    depth, i, args, cur = 0, start, [], []
    while i < len(src):
        c = src[i]
        if c in "'\"`":
            j = i + 1
            while j < len(src) and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            cur.append(src[i:j + 1])
            i = j + 1
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                args.append("".join(cur).strip())
                return [a for a in args if a], i + 1
            depth -= 1
        elif c == "," and depth == 0:
            args.append("".join(cur).strip())
            cur = []
            i += 1
            continue
        cur.append(c)
        i += 1
    return [a for a in args if a], i


def string_value(text: str) -> str | None:
    m = re.fullmatch(r"""(['"])(.*)\1|`([^`$]*)`""", text.strip(), re.S)
    if not m:
        return None
    return m.group(2) if m.group(1) else m.group(3)


def object_field(text: str, key: str) -> str | None:
    text = text.strip()
    if not text.startswith("{"):
        return None
    parts, _ = split_args(text, 1)
    for part in parts:
        if part == key:
            return key
        m = re.match(rf"""['"]?{key}['"]?\s*:\s*(.+)""", part, re.S)
        if m:
            return m.group(1).strip()
    return None


def top_level_split(text: str, ops: tuple[str, ...]) -> list[str]:
    depth, parts, cur, i = 0, [], "", 0
    while i < len(text):
        c = text[i]
        if c in "'\"`":
            j = text.find(c, i + 1)
            j = len(text) if j == -1 else j + 1
            cur += text[i:j]
            i = j
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        if depth == 0:
            op = next((o for o in ops if text.startswith(o, i) and not text.startswith("?.", i)), None)
            if op:
                parts.append(cur)
                cur = ""
                i += len(op)
                continue
        cur += c
        i += 1
    parts.append(cur)
    return [p.strip() for p in parts if p.strip()]


def source_key(text: str) -> str:
    t = re.sub(r"\s+", "", text)
    t = re.sub(r"""\.get\(['"](\w+)['"]\)""", r"[\1]", t)
    t = re.sub(r"""\[['"](\w+)['"]\]""", r"[\1]", t)
    t = re.sub(r"\?\.", ".", t)
    m = re.search(r"(\w+)\[(\w+)\]$", t)
    return f"{m.group(1)}[{m.group(2)}]" if m else t


def line_of(src: str, index: int) -> int:
    return src.count("\n", 0, index) + 1


class JsFile:
    def __init__(self, src: str):
        self.src = src

    def assignments(self, name: str) -> list[str]:
        values = []
        pattern = re.compile(rf"(?:\b(?:const|let|var)\s+)?(?<![\w$.]){re.escape(name)}\s*(?::\s*[\w<>\[\]| ]+)?=(?![=>])\s*")
        for m in pattern.finditer(self.src):
            end = m.end()
            depth, i = 0, end
            while i < len(self.src):
                c = self.src[i]
                if c in "([{":
                    depth += 1
                elif c in ")]}":
                    if depth == 0:
                        break
                    depth -= 1
                elif c in ";\n" and depth == 0:
                    break
                i += 1
            values.append(self.src[end:i].strip())
        return values

    def classify(self, text: str | None, depth: int = 0) -> Ident:
        if text is None or text.strip() in ("", "undefined", "null", "''", '""'):
            return Ident(MISSING, "no identifier passed")
        text = text.strip()
        if depth > 6:
            return Ident(UNKNOWN, f"can't tell where {text} comes from")
        literal = string_value(text)
        if literal is not None:
            return Ident(CONSTANT, f"constant {literal!r}: every visitor is the same identity", frozenset({f"const:{literal}"}))
        alternatives = top_level_split(text, ("??", "||", "?", ":"))
        if len(alternatives) > 1:
            return self.any_of([self.classify(a, depth + 1) for a in alternatives])
        text = re.sub(r"^(?:await\s+|String\(|`\$\{)|\)$|\}`$|\.toString\(\)$", "", text).strip()
        if re.fullmatch(r"[\w$]+", text):
            values = [v for v in self.assignments(text) if not re.search(rf"\b{re.escape(text)}\b", v)]
            if values:
                return self.any_of([self.classify(v, depth + 1) for v in values])
        rm = RANDOM_RE.search(text)
        if rm:
            line = line_of(self.src, self.src.find(text)) if text in self.src else 0
            at = f" (line {line})" if line else ""
            return Ident(RANDOM, f"{rm.group(0).rstrip('( ')}(){at} is new on every request", frozenset({f"random:{rm.group(0)}"}))
        kind = hint(text) or (STABLE if any(w in text.lower() for w in JS_STABLE_WORDS) else None)
        if kind == STABLE:
            return Ident(STABLE, f"{text} is tied to the visitor", frozenset({source_key(text)}))
        if kind == RANDOM:
            return Ident(RANDOM, f"{text} changes on every request", frozenset({source_key(text)}))
        return Ident(UNKNOWN, f"can't tell where {text} comes from", frozenset({source_key(text)}))

    @staticmethod
    def any_of(idents: list[Ident]) -> Ident:
        stable = [i for i in idents if i.status == STABLE]
        if stable:
            return Ident(STABLE, stable[0].reason, frozenset().union(*(i.sources for i in stable)))
        if all(i.status == RANDOM for i in idents):
            return Ident(RANDOM, idents[0].reason, frozenset().union(*(i.sources for i in idents)))
        for status in (UNKNOWN, CONSTANT, MISSING):
            hits = [i for i in idents if i.status == status]
            if hits:
                return hits[0]
        return idents[0]


def scan_js(raw: str, rel: str) -> FileFacts | None:
    src = strip_comments(raw)
    packages = set(IMPORT_RE.findall(src))
    server = any(p.startswith(("@flagsmith/nodejs", "flagsmith-nodejs")) for p in packages)
    js = JsFile(src)
    facts = FileFacts(rel, "js")
    identify: Ident | None = None
    identify_line = 0

    for m in CALL_RE.finditer(src):
        recv, name = m.group(1) or "", m.group(2)
        is_sdk = bool(packages) or "flagsmith" in recv.lower()
        if not is_sdk:
            continue
        args, _ = split_args(src, m.end())
        line = line_of(src, m.start())
        if name == "identify" and args:
            identify, identify_line = js.classify(args[0]), line
        elif name in ("getExperimentFlag", "useExperiment", "trackExposureEvent"):
            if name == "useExperiment" and not packages:
                continue
            flag = string_value(args[0]) if args else None
            two_arg = server or (name == "getExperimentFlag" and len(args) > 1)
            if two_arg:
                ident = js.classify(args[1] if len(args) > 1 else None)
            elif name == "trackExposureEvent" and len(args) > 1 and object_field(args[1], "identifier"):
                ident = js.classify(object_field(args[1], "identifier"))
            else:
                ident = browser_identity(js, src, m.start(), identify, identify_line)
            facts.calls.append(Call("exposure", rel, line, "js", flag=flag, ident=ident, note=name))
        elif name == "trackEvent":
            event = string_value(args[0]) if args else None
            field = object_field(args[1], "identifier") if len(args) > 1 else None
            if server or field:
                ident = js.classify(field)
            else:
                ident = browser_identity(js, src, m.start(), identify, identify_line)
            facts.calls.append(Call("conversion", rel, line, "js", event=event, ident=ident))
        elif name == "init" and args:
            facts.calls.append(Call("init", rel, line, "js", events_enabled=events_on(args[0])))
            ident_text = object_field(args[0], "identity")
            if ident_text:
                identify, identify_line = js.classify(ident_text), line
        elif name in ("flushEvents", "close"):
            facts.flushes = True

    for m in NEW_RE.finditer(src):
        args, _ = split_args(src, m.end())
        facts.calls.append(Call("init", rel, line_of(src, m.start()), "js", events_enabled=events_on(args[0] if args else "")))
    for m in PROVIDER_RE.finditer(src):
        tag_end = src.find(">", m.end())
        tag = src[m.end():tag_end if tag_end != -1 else len(src)]
        facts.calls.append(Call("init", rel, line_of(src, m.start()), "js", events_enabled=events_on(tag)))

    if not facts.calls:
        return None
    for pattern, why in SHORT_LIVED_RE:
        found = pattern.search(src)
        if found and (server or why != "script"):
            facts.short_lived, facts.short_lived_line = why, line_of(src, found.start())
            break
    return facts


def browser_identity(js: JsFile, src: str, at: int, identify: Ident | None, identify_line: int) -> Ident:
    provider = re.search(r"identity\s*:\s*([^,}\n]+)", src)
    if identify is not None and identify_line <= line_of(src, at):
        return identify
    if provider:
        return js.classify(provider.group(1))
    if identify is not None:
        return Ident(MISSING, f"identify() runs at line {identify_line}, after the flag is read")
    return Ident(UNKNOWN, "no identify() in this file: check the user is identified before the flag is read", frozenset({"browser-identity"}))


def events_on(text: str) -> bool:
    m = re.search(r"enableEvents\s*(?::|=)\s*\{?\s*([\w$.]+)", text or "")
    return bool(m) and m.group(1) != "false"
