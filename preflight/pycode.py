import ast
from dataclasses import dataclass, field

from .facts import (
    CONSTANT, MISSING, RANDOM, STABLE, UNKNOWN,
    Call, FileFacts, Ident, hint,
)

EXPOSURE_FUNCS = {"get_experiment_flag", "track_exposure_event"}
RANDOM_FUNCS = {
    "uuid1", "uuid4", "token_hex", "token_urlsafe", "token_bytes", "urandom",
    "getrandbits", "random", "randint", "randrange", "time", "time_ns", "now",
    "utcnow", "monotonic", "perf_counter",
}
WRAPPERS = {"str", "int", "format", "repr", "lower", "upper", "strip", "encode", "decode"}
WEB_FRAMEWORKS = {"flask", "fastapi", "django", "starlette", "aiohttp", "tornado", "sanic", "bottle", "quart", "litestar"}
SERVERLESS_MODULES = {"functions_framework", "azure", "mangum", "chalice"}
FUNC_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)


@dataclass
class Scope:
    parent: "Scope | None" = None
    assigns: dict = field(default_factory=dict)
    params: set = field(default_factory=set)
    funcs: dict = field(default_factory=dict)

    def lookup(self, name: str):
        scope = self
        while scope:
            if name in scope.assigns:
                return "assign", scope.assigns[name], scope
            if name in scope.params:
                return "param", None, scope
            if name in scope.funcs:
                return "func", scope.funcs[name], scope
            scope = scope.parent
        return None


def own_nodes(node: ast.AST):
    # Walk a body without descending into nested functions or classes.
    stack = list(ast.iter_child_nodes(node))
    while stack:
        n = stack.pop()
        yield n
        if not isinstance(n, (*FUNC_NODES, ast.ClassDef, ast.Lambda)):
            stack.extend(ast.iter_child_nodes(n))


def build_scope(node: ast.AST, parent: Scope | None) -> Scope:
    scope = Scope(parent)
    if isinstance(node, FUNC_NODES):
        a = node.args
        scope.params = {p.arg for p in [*a.posonlyargs, *a.args, *a.kwonlyargs]}
        scope.params |= {p.arg for p in (a.vararg, a.kwarg) if p}
    for n in own_nodes(node):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    scope.assigns.setdefault(t.id, []).append(n.value)
        elif isinstance(n, (ast.AnnAssign, ast.NamedExpr)) and n.value is not None:
            if isinstance(n.target, ast.Name):
                scope.assigns.setdefault(n.target.id, []).append(n.value)
        elif isinstance(n, FUNC_NODES):
            scope.funcs[n.name] = n
    return scope


def call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def receiver(func: ast.expr) -> str:
    if isinstance(func, ast.Attribute):
        return ast.unparse(func.value)
    return ""


def tail(expr: ast.expr) -> str:
    if isinstance(expr, ast.Attribute):
        return expr.attr
    if isinstance(expr, ast.Name):
        return expr.id
    return ast.unparse(expr)


def leaf_key(expr: ast.expr) -> str:
    # request.cookies["vid"] and request.cookies.get("vid") are the same source.
    if isinstance(expr, ast.Subscript) and isinstance(expr.slice, ast.Constant):
        return f"{tail(expr.value)}[{expr.slice.value}]"
    if (
        isinstance(expr, ast.Call)
        and isinstance(expr.func, ast.Attribute)
        and expr.func.attr == "get"
        and expr.args
        and isinstance(expr.args[0], ast.Constant)
    ):
        return f"{tail(expr.func.value)}[{expr.args[0].value}]"
    return ast.unparse(expr)


def any_of(idents: list[Ident]) -> Ident:
    # Alternatives (a or b, x if c else y, reassignments): one stable source is
    # enough, because get-or-create patterns persist the random fallback.
    if not idents:
        return Ident(UNKNOWN, "no value found")
    stable = [i for i in idents if i.status == STABLE]
    if stable:
        return Ident(STABLE, stable[0].reason, frozenset().union(*(i.sources for i in stable)))
    for status in (RANDOM, UNKNOWN, CONSTANT, MISSING):
        hits = [i for i in idents if i.status == status]
        if hits and (status != RANDOM or len(hits) == len(idents)):
            return Ident(status, hits[0].reason, frozenset().union(*(i.sources for i in hits)))
    return idents[0]


def all_of(idents: list[Ident]) -> Ident:
    # Compositions (f"{a}-{b}", a + b): one random part makes the whole thing random.
    for status in (RANDOM, STABLE, UNKNOWN):
        hits = [i for i in idents if i.status == status]
        if hits:
            return Ident(status, hits[0].reason, frozenset().union(*(i.sources for i in hits)))
    return Ident(CONSTANT, idents[0].reason if idents else "constant", frozenset())


def random_call(expr: ast.AST) -> ast.Call | None:
    for n in ast.walk(expr):
        if isinstance(n, ast.Call) and call_name(n.func) in RANDOM_FUNCS:
            return n
    return None


def leaf(expr: ast.expr) -> Ident:
    text = ast.unparse(expr)
    rc = random_call(expr)
    if rc is not None:
        return Ident(RANDOM, f"{ast.unparse(rc)} (line {rc.lineno}) is new on every request", frozenset({f"random:{ast.unparse(rc.func)}"}))
    kind = hint(text)
    if kind == STABLE:
        return Ident(STABLE, f"{text} is tied to the visitor", frozenset({leaf_key(expr)}))
    if kind == RANDOM:
        return Ident(RANDOM, f"{text} (line {expr.lineno}) changes on every request", frozenset({leaf_key(expr)}))
    return Ident(UNKNOWN, f"can't tell where {text} comes from", frozenset({leaf_key(expr)}))


def classify(expr: ast.expr | None, scope: Scope, depth: int = 0) -> Ident:
    if expr is None:
        return Ident(MISSING, "no identifier passed")
    if depth > 8:
        return Ident(UNKNOWN, f"can't tell where {ast.unparse(expr)} comes from")
    if isinstance(expr, ast.Constant):
        if expr.value in (None, ""):
            return Ident(MISSING, "identifier is empty")
        return Ident(CONSTANT, f"constant {expr.value!r}: every visitor is the same identity", frozenset({f"const:{expr.value}"}))
    if isinstance(expr, ast.Name):
        return classify_name(expr, scope, depth)
    if isinstance(expr, ast.BoolOp):
        return any_of([classify(v, scope, depth + 1) for v in expr.values])
    if isinstance(expr, ast.IfExp):
        return any_of([classify(expr.body, scope, depth + 1), classify(expr.orelse, scope, depth + 1)])
    if isinstance(expr, ast.JoinedStr):
        parts = [classify(v.value, scope, depth + 1) for v in expr.values if isinstance(v, ast.FormattedValue)]
        return all_of(parts)
    if isinstance(expr, ast.BinOp):
        return all_of([classify(expr.left, scope, depth + 1), classify(expr.right, scope, depth + 1)])
    if isinstance(expr, ast.Await):
        return classify(expr.value, scope, depth + 1)
    if isinstance(expr, ast.Call):
        return classify_call(expr, scope, depth)
    if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Call):
        inner = classify(expr.value, scope, depth + 1)
        if inner.status == RANDOM:
            return inner
    return leaf(expr)


def classify_name(expr: ast.Name, scope: Scope, depth: int) -> Ident:
    found = scope.lookup(expr.id)
    if found and found[0] == "assign":
        values = [v for v in found[1] if not uses_name(v, expr.id)] or found[1]
        return any_of([classify(v, found[2], depth + 1) for v in values])
    kind = hint(expr.id)
    if kind == STABLE:
        return Ident(STABLE, f"'{expr.id}' names a user or session id", frozenset({f"name:{expr.id}"}))
    if kind == RANDOM:
        return Ident(RANDOM, f"'{expr.id}' changes on every request", frozenset({f"name:{expr.id}"}))
    where = "parameter" if found else "name"
    return Ident(UNKNOWN, f"can't tell where {where} '{expr.id}' comes from", frozenset({f"name:{expr.id}"}))


def classify_call(expr: ast.Call, scope: Scope, depth: int) -> Ident:
    name = call_name(expr.func)
    if name in RANDOM_FUNCS:
        return Ident(RANDOM, f"{ast.unparse(expr)} (line {expr.lineno}) is new on every request", frozenset({f"random:{ast.unparse(expr.func)}"}))
    if isinstance(expr.func, ast.Name):
        found = scope.lookup(name)
        if found and found[0] == "func":
            fn = found[1]
            inner = build_scope(fn, found[2])
            returns = [n.value for n in own_nodes(fn) if isinstance(n, ast.Return) and n.value is not None]
            result = any_of([classify(r, inner, depth + 1) for r in returns])
            if result.status == STABLE:
                return Ident(STABLE, f"{name}() returns a stable id", result.sources | {f"call:{name}"})
            return result
    if name in WRAPPERS:
        target = expr.args[0] if expr.args else expr.func.value if isinstance(expr.func, ast.Attribute) else None
        if target is not None:
            return classify(target, scope, depth + 1)
    return leaf(expr)


def uses_name(expr: ast.expr, name: str) -> bool:
    return any(isinstance(n, ast.Name) and n.id == name for n in ast.walk(expr))


def arg(call: ast.Call, index: int, keyword: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == keyword:
            return kw.value
    return call.args[index] if len(call.args) > index else None


def literal(expr: ast.expr | None, module: Scope) -> str | None:
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value
    if isinstance(expr, ast.Name):
        found = module.lookup(expr.id)
        if found and found[0] == "assign" and len(found[1]) == 1:
            return literal(found[1][0], module)
    return None


def imported_modules(tree: ast.Module) -> set[str]:
    names = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            names |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            names.add(n.module.split(".")[0])
    return names


def short_lived(tree: ast.Module, modules: set[str]) -> tuple[str, int]:
    for n in tree.body:
        if isinstance(n, FUNC_NODES):
            params = [a.arg for a in n.args.args]
            if n.name in ("handler", "lambda_handler") and params[:2] == ["event", "context"]:
                return "serverless handler", n.lineno
    if modules & SERVERLESS_MODULES:
        return "serverless handler", 1
    if modules & WEB_FRAMEWORKS:
        return "", 0
    for n in tree.body:
        if isinstance(n, ast.If) and "__name__" in ast.unparse(n.test) and "__main__" in ast.unparse(n.test):
            return "script", n.lineno
    return "", 0


def scan_python(source: str, rel: str) -> FileFacts | None:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    modules = imported_modules(tree)
    module = build_scope(tree, None)
    facts = FileFacts(rel, "python")

    clients = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call) and call_name(n.value.func) == "Flagsmith":
            clients |= {ast.unparse(t) for t in n.targets}
        elif isinstance(n, ast.Call) and call_name(n.func) in EXPOSURE_FUNCS:
            clients.add(receiver(n.func))

    scopes = {}

    def visit(node: ast.AST, scope: Scope):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, FUNC_NODES):
                scopes[child] = build_scope(child, scope)
                visit(child, scopes[child])
                continue
            if isinstance(child, ast.Call):
                record(child, scope)
            visit(child, scope)

    def record(call: ast.Call, scope: Scope):
        name = call_name(call.func)
        recv = receiver(call.func)
        uses_sdk = "flagsmith" in modules or recv in clients
        if name in EXPOSURE_FUNCS:
            flag = literal(arg(call, 0, "feature_name"), module)
            ident = classify(arg(call, 1, "identifier"), scope)
            facts.calls.append(Call("exposure", rel, call.lineno, "python", flag=flag, ident=ident, note=name))
        elif name == "track_event" and uses_sdk:
            event = literal(arg(call, 0, "event"), module)
            ident = classify(arg(call, 1, "identifier"), scope)
            facts.calls.append(Call("conversion", rel, call.lineno, "python", event=event, ident=ident))
        elif name == "Flagsmith" and (uses_sdk or isinstance(call.func, ast.Attribute)):
            enabled = next((k.value for k in call.keywords if k.arg == "enable_events"), None)
            on = enabled is not None and not (isinstance(enabled, ast.Constant) and not enabled.value)
            facts.calls.append(Call("init", rel, call.lineno, "python", events_enabled=on))
            if buffer_of_one(call):
                facts.flushes = True
        elif name == "flush_events" or (name == "close" and recv in clients):
            facts.flushes = True
        elif name == "EventProcessorConfig" and buffer_of_one(call):
            facts.flushes = True

    visit(tree, module)
    if not facts.calls:
        return None
    facts.short_lived, facts.short_lived_line = short_lived(tree, modules)
    return facts


def buffer_of_one(call: ast.Call) -> bool:
    for n in ast.walk(call):
        if isinstance(n, ast.keyword) and n.arg == "max_buffer_items":
            return isinstance(n.value, ast.Constant) and n.value.value == 1
    return False
