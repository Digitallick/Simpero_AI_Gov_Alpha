from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import deals, health
from app.config import get_settings
from app.core.exceptions import (
    AuthenticationError,
    AuthorizationError,
    TenantContextError,
)

settings = get_settings()

app = FastAPI(
    title="Simpero API",
    version="0.1.0",
    description="AI-powered due diligence platform — backend API",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # Origins are loaded from CORS_ALLOWED_ORIGINS in .env — never hardcode them here.
)


@app.exception_handler(AuthenticationError)
async def authentication_error_handler(request: Request, exc: AuthenticationError) -> JSONResponse:
    return JSONResponse(status_code=401, content={"detail": str(exc)})


@app.exception_handler(AuthorizationError)
async def authorization_error_handler(request: Request, exc: AuthorizationError) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(TenantContextError)
async def tenant_context_error_handler(request: Request, exc: TenantContextError) -> JSONResponse:
    # 401 not 400: missing tenant context is an auth failure, not a malformed request.
    return JSONResponse(status_code=401, content={"detail": str(exc)})


app.include_router(health.router)
app.include_router(deals.router)

# Do not open DB connections at startup. PgBouncer transaction pooling requires sessions to be
# opened per-transaction, not per-application-lifecycle. A startup DB connection would hold a
# PgBouncer slot open indefinitely and bypass the pooling contract.
