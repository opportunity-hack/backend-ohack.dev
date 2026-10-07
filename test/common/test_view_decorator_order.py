"""
Flask registers whatever function `@bp.route` sees. An auth decorator placed
ABOVE `@bp.route` wraps the already-registered function, so the live route
runs WITHOUT the check — two admin routes were publicly readable/writable
this way (checkins list, problem-statement events PATCH). This walks every
views module and requires the route decorator to be the outermost one on
any auth-decorated function.
"""
import ast
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _decorator_name(node):
    target = node.func if isinstance(node, ast.Call) else node
    parts = []
    while isinstance(target, ast.Attribute):
        parts.append(target.attr)
        target = target.value
    if isinstance(target, ast.Name):
        parts.append(target.id)
    return ".".join(reversed(parts))


def _is_auth(name):
    return name.startswith("auth.require_") or name == "auth.optional_user"


def _is_route(node):
    return isinstance(node, ast.Call) and _decorator_name(node).endswith(".route")


def find_misordered():
    offenders = []
    for path in sorted((REPO_ROOT / "api").rglob("*_views.py")):
        if "/tests/" in path.as_posix():
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            decs = fn.decorator_list
            if not any(_is_auth(_decorator_name(d)) for d in decs):
                continue
            if not decs or not _is_route(decs[0]):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{fn.name}")
    return offenders


def test_route_decorator_is_outermost_on_auth_gated_views():
    offenders = find_misordered()
    assert offenders == [], (
        "@bp.route must be the FIRST (outermost) decorator, otherwise the auth "
        "decorators above it are never applied: " + ", ".join(offenders)
    )
