from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.caching import NotModified, cache_headers_middleware, not_modified_handler
from app.api.v1 import router as api_v1_router
from app.auth.bootstrap import bootstrap_admins
from app.auth.router import router as auth_router
from app.claims.router import router as claims_router
from app.config import get_settings
from app.db import get_db
from app.private.router import router as private_router
from app.uploads.router import router as uploads_router

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Not exercised by tests using httpx's ASGITransport, which doesn't
    # send the ASGI lifespan scope - tests call bootstrap_admins() directly
    # instead (same pattern as app.cli's publish_meet/unpublish_meet).
    await bootstrap_admins()
    yield


app = FastAPI(
    title="Fastwave Results API",
    version="0.1.0",
    # openapi_url (the actual spec Lovable codegens from) is never gated -
    # only the interactive Swagger/Redoc UIs are, and only if DOCS_PUBLIC=false.
    docs_url="/docs" if settings.docs_enabled else None,
    redoc_url="/redoc" if settings.docs_enabled else None,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    # Exact-match allow_origins can't express "any Lovable preview
    # subdomain" - allow_origin_regex is the mechanism Starlette provides
    # for that (see Settings.cors_origin_regex for the pattern + why it's
    # fully anchored).
    allow_origin_regex=settings.cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.middleware("http")(cache_headers_middleware)
app.add_exception_handler(NotModified, not_modified_handler)
app.include_router(api_v1_router)
app.include_router(auth_router)
app.include_router(claims_router)
app.include_router(uploads_router)
app.include_router(private_router)


@app.get("/healthz")
async def healthz(db: AsyncSession = Depends(get_db)) -> dict:
    try:
        await db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    return {"status": "ok", "db": db_ok}
