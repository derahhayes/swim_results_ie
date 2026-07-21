"""Cache-Control + ETag for public GET endpoints.

Middleware-based so routes don't hand-roll response headers: the
middleware stamps Cache-Control on every /api/v1 GET, and adds an ETag
whenever a meet-scoped dependency (see app/api/deps.py) computed one and
stashed it on request.state.etag. That dependency also does the
If-None-Match comparison and short-circuits via NotModified, caught here
by an exception handler that returns a bodyless 304 (a 304 must not carry
a body per RFC 9110 - which is why this can't just be a normal response
with a response_model).
"""

from starlette.requests import Request
from starlette.responses import Response

CACHE_CONTROL = "public, max-age=300"


class NotModified(Exception):
    def __init__(self, etag: str):
        self.etag = etag


async def not_modified_handler(request: Request, exc: NotModified) -> Response:
    response = Response(status_code=304)
    response.headers["ETag"] = exc.etag
    response.headers["Cache-Control"] = CACHE_CONTROL
    return response


async def cache_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    if request.method == "GET" and request.url.path.startswith("/api/v1"):
        response.headers.setdefault("Cache-Control", CACHE_CONTROL)
        etag = getattr(request.state, "etag", None)
        if etag:
            response.headers.setdefault("ETag", etag)
    return response


def make_etag(*parts: str) -> str:
    return 'W/"' + "-".join(parts) + '"'
