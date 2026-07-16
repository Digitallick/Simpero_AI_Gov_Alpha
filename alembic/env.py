import asyncio
import os
import ssl
from logging.config import fileConfig
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from alembic import context

load_dotenv()

import app.models  # noqa: E402, F401 — registers all models on Base.metadata

# load_dotenv() must run before app imports so config.py sees the env vars.
from app.core.database import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_alembic_url() -> tuple[str, bool]:
    # Migrations run as doadmin (DDL privileges). The runtime app uses dd_app (DML only,
    # RLS-restricted). ALEMBIC_DATABASE_URL must point directly at the DigitalOcean cluster,
    # bypassing PgBouncer — DDL statements are not safe in transaction-pooling mode.
    # NEVER substitute DATABASE_URL here.
    url = os.environ.get("ALEMBIC_DATABASE_URL")
    if not url:
        raise RuntimeError(
            "ALEMBIC_DATABASE_URL environment variable is not set. "
            "This must be a direct connection to the database as doadmin (not via PgBouncer)."
        )
    # Ensure the URL uses the asyncpg driver regardless of what scheme was set in .env.
    for scheme in ("postgresql+psycopg2://", "postgresql://"):
        if url.startswith(scheme):
            url = "postgresql+asyncpg://" + url[len(scheme) :]
            break

    # asyncpg doesn't accept sslmode= (libpq param). Strip it and return ssl flag separately.
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    sslmode = params.pop("sslmode", [None])[0]
    clean_query = urlencode({k: v[0] for k, v in params.items()})
    url = urlunparse(parsed._replace(query=clean_query))
    use_ssl = sslmode in ("require", "verify-full", "verify-ca")
    return url, use_ssl


def run_migrations_offline() -> None:
    url, _ = get_alembic_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


def _make_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    # DO_CA_CERT_PATH or the default project location for the DO CA certificate.
    ca_cert = os.environ.get("DO_CA_CERT_PATH", "docker/ca-certificate.crt")
    if os.path.exists(ca_cert):
        ctx.load_verify_locations(ca_cert)
    else:
        # No CA cert on disk — disable verification. Set DO_CA_CERT_PATH in production.
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def run_migrations_online() -> None:
    url, use_ssl = get_alembic_url()
    connect_args = {"ssl": _make_ssl_context()} if use_ssl else {}
    connectable = create_async_engine(url, poolclass=NullPool, connect_args=connect_args)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
