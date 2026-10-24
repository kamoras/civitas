"""Every alert condition the code can raise is in ops_alerts.CONDITIONS, and
every pattern there is still raised somewhere.

The registry is what lets the pipeline process close, at start, an alert
whose condition no code raises any more (``resolve_orphaned_alerts``): a
check that was renamed or removed leaves its open alert behind, and nothing
else would ever resolve it (2026-10: ``election-ready-town-ballots`` after
#1247, the two search calibrations' ``data-stale-*`` after #1257). Both
directions are held here, so the registry can neither miss a condition (the
sweep would close a live alert) nor keep one the code dropped (the sweep
would leave its orphans open).

The condition each ``send_ops_alert`` call passes is read from the source:
a string or f-string (its text up to the first placeholder, then ``*``), a
name bound to one (in the function, the module, or a module-level constant
anywhere in app/), a call to a function whose returns are one, a loop over a
tuple of them, and a parameter, by reading what every caller passes for it.
"""

import ast
import fnmatch
import pathlib
from unittest.mock import patch

from app import ops_alerts

APP = pathlib.Path(__file__).resolve().parents[1] / "app"


def _modules() -> dict[pathlib.Path, ast.Module]:
    return {p: ast.parse(p.read_text(), str(p)) for p in sorted(APP.rglob("*.py"))}


MODULES = _modules()


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


PARENTS = {path: _parents(tree) for path, tree in MODULES.items()}


def _enclosing_function(path, node):
    parents = PARENTS[path]
    while node in parents:
        node = parents[node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node
    return None


def _module_constants() -> dict[str, list[tuple[pathlib.Path, ast.expr]]]:
    found: dict[str, list] = {}
    for path, tree in MODULES.items():
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        found.setdefault(target.id, []).append((path, node.value))
    return found


CONSTANTS = _module_constants()


def _functions() -> dict[str, list[tuple[pathlib.Path, ast.FunctionDef]]]:
    found: dict[str, list] = {}
    for path, tree in MODULES.items():
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found.setdefault(node.name, []).append((path, node))
    return found


FUNCTIONS = _functions()


class Unresolved(Exception):
    pass


def _patterns(path, expr, scope, depth=0) -> set[str]:
    """The condition patterns `expr` can evaluate to."""
    if depth > 8:
        raise Unresolved("too deep")
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return {expr.value}
    if isinstance(expr, ast.Constant) and expr.value is None:
        return set()
    if isinstance(expr, ast.JoinedStr):
        head = ""
        for part in expr.values:
            if isinstance(part, ast.Constant):
                head += part.value
            else:
                return {head + "*"}
        return {head}
    if isinstance(expr, ast.IfExp):
        return _patterns(path, expr.body, scope, depth + 1) | _patterns(path, expr.orelse, scope, depth + 1)
    if isinstance(expr, ast.Call):
        name = expr.func.attr if isinstance(expr.func, ast.Attribute) else getattr(expr.func, "id", None)
        defs = FUNCTIONS.get(name or "")
        if not defs:
            raise Unresolved(f"call to {name}")
        out = set()
        for fpath, fn in defs:
            for ret in (n for n in ast.walk(fn) if isinstance(n, ast.Return) and n.value is not None):
                out |= _patterns(fpath, ret.value, fn, depth + 1)
        return out
    if isinstance(expr, ast.Name):
        return _name(path, expr.id, scope, depth)
    raise Unresolved(ast.dump(expr)[:80])


# Names whose loop binding is being read: `for a, cond in alerts` over
# tuples appended with `cond` itself reads cond's other bindings only.
_UNPACKING: set = set()


def _name(path, name, scope, depth) -> set[str]:
    if scope is not None:
        bound = []
        for node in ast.walk(scope):
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
                bound.append(node.value)
            elif (isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Tuple)
                    and isinstance(node.value, ast.Tuple)):
                names = [getattr(e, "id", None) for e in node.targets[0].elts]
                if name in names:
                    bound.append(node.value.elts[names.index(name)])
            elif (isinstance(node, ast.For) and isinstance(node.target, ast.Name) and node.target.id == name
                    and (path, id(scope), name) not in _UNPACKING):
                bound.append(("iter", node.iter))
            elif (isinstance(node, ast.For) and isinstance(node.target, ast.Tuple)
                    and (path, id(scope), name) not in _UNPACKING):
                names = [getattr(e, "id", None) for e in node.target.elts]
                if name in names:
                    bound.append(("tuple-iter", names.index(name), node.iter))
        if bound:
            out = set()
            for b in bound:
                if isinstance(b, tuple) and b[0] == "iter":
                    out |= _iterated(path, b[1], scope, depth)
                elif isinstance(b, tuple):
                    _UNPACKING.add((path, id(scope), name))
                    try:
                        out |= _unpacked(path, b[1], b[2], scope, depth)
                    finally:
                        _UNPACKING.discard((path, id(scope), name))
                else:
                    out |= _patterns(path, b, scope, depth + 1)
            return out
        params = [a.arg for a in (*scope.args.posonlyargs, *scope.args.args, *scope.args.kwonlyargs)]
        if name in params:
            return _passed(scope, params.index(name), name, depth)
        outer = _enclosing_function(path, scope)
        if outer is not None:
            return _name(path, name, outer, depth)
    for cpath, value in [c for c in CONSTANTS.get(name, []) if c[0] == path] or CONSTANTS.get(name, []):
        return _patterns(cpath, value, None, depth + 1)
    raise Unresolved(f"name {name}")


def _iterated(path, it, scope, depth) -> set[str]:
    if isinstance(it, ast.Name):
        values = [v for p, v in CONSTANTS.get(it.id, [])]
        if values and isinstance(values[0], (ast.Tuple, ast.List, ast.Set)):
            return set().union(*(_patterns(path, e, None, depth + 1) for e in values[0].elts))
    if isinstance(it, (ast.Tuple, ast.List, ast.Set)):
        return set().union(*(_patterns(path, e, scope, depth + 1) for e in it.elts))
    raise Unresolved("loop")


def _unpacked(path, index, it, scope, depth) -> set[str]:
    """`for a, b, c in alerts`: the index'th element of each tuple appended
    to `alerts` in the same function."""
    if not isinstance(it, ast.Name):
        raise Unresolved("unpacked loop")
    out = set()
    for node in ast.walk(scope):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "append"
                and isinstance(node.func.value, ast.Name) and node.func.value.id == it.id
                and node.args and isinstance(node.args[0], ast.Tuple)):
            out |= _patterns(path, node.args[0].elts[index], scope, depth + 1)
    if not out:
        raise Unresolved("unpacked loop")
    return out


def _passed(fn, index, name, depth) -> set[str]:
    """A parameter: whatever every call of the function passes for it."""
    out, calls = set(), 0
    offset = 1 if fn.args.args and fn.args.args[0].arg in {"self", "cls"} else 0
    for path, tree in MODULES.items():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            called = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
            if called != fn.name:
                continue
            arg = next((k.value for k in node.keywords if k.arg == name), None)
            positional = index - offset
            if arg is None and 0 <= positional < len(node.args):
                arg = node.args[positional]
            if arg is None:
                continue
            calls += 1
            out |= _patterns(path, arg, _enclosing_function(path, node), depth + 1)
    if not calls:
        raise Unresolved(f"parameter {name} of {fn.name}: no caller passes it")
    return out


def _is_send(expr) -> bool:
    return getattr(expr, "id", getattr(expr, "attr", None)) == "send_ops_alert"


def _at(path, call, cond) -> set[str]:
    """The condition a call passes: a name the loop around the call binds
    is that loop's value, whatever else the function binds to the name."""
    scope = _enclosing_function(path, call)
    node = call
    while isinstance(cond, ast.Name) and node in PARENTS[path] and node is not scope:
        node = PARENTS[path][node]
        if isinstance(node, ast.For):
            target = node.target
            if isinstance(target, ast.Name) and target.id == cond.id:
                return _iterated(path, node.iter, scope, 0)
            if isinstance(target, ast.Tuple):
                names = [getattr(e, "id", None) for e in target.elts]
                if cond.id in names:
                    _UNPACKING.add((path, id(scope), cond.id))
                    try:
                        return _unpacked(path, names.index(cond.id), node.iter, scope, 0)
                    finally:
                        _UNPACKING.discard((path, id(scope), cond.id))
    return _patterns(path, cond, scope)


def raised_conditions() -> tuple[dict[str, set[str]], list[str]]:
    """{site: patterns} for every send_ops_alert call that passes a
    condition, and the sites that couldn't be read."""
    sites, unread = {}, []
    for path, tree in MODULES.items():
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and (_is_send(node.func) or (node.args and _is_send(node.args[0])))):
                continue  # a call, or a hand-off (asyncio.to_thread(send_ops_alert, ...))
            cond = next((k.value for k in node.keywords if k.arg == "condition"), None)
            if cond is None:
                continue
            site = f"{path.relative_to(APP.parent)}:{node.lineno}"
            try:
                sites[site] = _at(path, node, cond)
            except Unresolved as why:
                unread.append(f"{site}: {why}")
    return sites, unread


def _covered(pattern: str) -> bool:
    """A raised pattern ("data-stale-*", or a literal) falls under a registry entry."""
    return any(fnmatch.fnmatchcase(pattern, entry) for entry in ops_alerts.CONDITIONS)


def test_every_raised_condition_is_registered():
    sites, unread = raised_conditions()
    assert not unread, f"condition not readable from the source (pass a string, an f-string or a constant): {unread}"
    missing = {site: sorted(p for p in pats if not _covered(p)) for site, pats in sites.items()}
    missing = {s: p for s, p in missing.items() if p}
    assert not missing, f"add these to ops_alerts.CONDITIONS: {missing}"


def test_every_registered_condition_is_still_raised():
    sites, _ = raised_conditions()
    raised = set().union(*sites.values())
    unused = [entry for entry in ops_alerts.CONDITIONS
              if not any(fnmatch.fnmatchcase(p, entry) for p in raised)]
    assert not unused, f"no code raises these any more; remove them from ops_alerts.CONDITIONS: {unused}"


def test_an_alert_no_code_raises_any_more_closes_at_start(db_session):
    with patch("app.ops_alerts.SessionLocal", return_value=db_session):
        # election-ready-town-ballots: #1247 removed its check.
        ops_alerts.send_ops_alert("Town ballots", "body", dedupe_key="t", condition="election-ready-town-ballots")
        ops_alerts.send_ops_alert("A step", "body", dedupe_key="s", condition="step-failed-PipelineRun-fetch")
        assert ops_alerts.resolve_orphaned_alerts() == ["election-ready-town-ballots"]
        assert ops_alerts.open_conditions() == {"step-failed-PipelineRun-fetch"}
