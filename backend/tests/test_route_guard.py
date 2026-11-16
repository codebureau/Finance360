"""Default deny (ADR-0010): no route ships open unless it is on the explicit allow-list.

Two independent checks over the real app, including the static-site routes that exist only when a
built UI is present:

* structural: every route outside ``PUBLIC`` carries the ``current_principal`` dependency;
* behavioural: calling every documented operation with no credentials is refused with 401.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import iter_route_contexts
from starlette.routing import Mount

from finance360.api.deps import current_principal
from finance360.config import Settings
from finance360.main import create_app
from support import ORIGIN, TestClient

#: (method, path) pairs that are public on purpose. The SPA fall-through and the asset mount
#: serve the built UI bundle, which holds no data.
PUBLIC = {
    ("GET", "/health"),
    ("GET", "/api/auth/status"),
    ("POST", "/api/auth/setup"),
    ("POST", "/api/auth/login"),
    ("GET", "/{path:path}"),
}
PUBLIC_MOUNTS = {"/assets"}


def _requires_principal(dependant: Dependant | None) -> bool:
    if dependant is None:
        return False
    return any(
        sub.call is current_principal or _requires_principal(sub) for sub in dependant.dependencies
    )


@pytest.fixture
def app(tmp_path: Path) -> FastAPI:
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<html></html>")
    settings = Settings(data_dir=tmp_path / "data", port=8000, git_sha="dev", static_dir=static)
    return create_app(settings)


def test_every_route_is_guarded_or_explicitly_public(app: FastAPI) -> None:
    open_routes: list[str] = []
    seen_public: set[tuple[str, str]] = set()
    routes = list(iter_route_contexts(app.routes))
    assert len(routes) > 20  # the walk really did find the included routers' routes
    for ctx in routes:
        if isinstance(ctx.original_route, Mount):
            if ctx.original_route.path not in PUBLIC_MOUNTS:
                open_routes.append(f"mount {ctx.original_route.path}")
            continue
        if _requires_principal(getattr(ctx, "dependant", None)):
            continue
        for method in sorted((ctx.methods or {"?"}) - {"HEAD"}):
            if (method, ctx.path) in PUBLIC:
                seen_public.add((method, ctx.path or ""))
            else:
                open_routes.append(f"{method} {ctx.path}")
    assert not open_routes, f"Routes without current_principal: {open_routes}"
    assert seen_public == PUBLIC, f"Stale allow-list entries: {PUBLIC - seen_public}"


def test_the_guard_notices_an_unguarded_route(app: FastAPI) -> None:
    @app.get("/api/oops")
    def oops() -> dict[str, str]:
        return {}

    unguarded = [
        ctx.path
        for ctx in iter_route_contexts(app.routes)
        if not isinstance(ctx.original_route, Mount)
        and not _requires_principal(getattr(ctx, "dependant", None))
    ]
    assert "/api/oops" in unguarded


def test_every_documented_operation_refuses_an_anonymous_caller(app: FastAPI) -> None:
    public = {(m, p) for m, p in PUBLIC if p != "/{path:path}"}
    checked = 0
    paths = app.openapi()["paths"]
    assert "/api/accounts/{account_id}/valuations" in paths  # Valuations are covered
    with TestClient(app, headers=ORIGIN) as client:
        for path, operations in app.openapi()["paths"].items():
            for method in operations:
                if (method.upper(), path) in public:
                    continue
                url = re.sub(r"\{[^}]+\}", "1", path)
                response = client.request(method.upper(), url, json={})
                assert response.status_code == 401, f"{method.upper()} {path} is open"
                checked += 1
    assert checked >= 15
