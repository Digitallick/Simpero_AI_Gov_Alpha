from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

settings = get_settings()

# NullPool is required when SQLAlchemy async is paired with PgBouncer in transaction-pooling mode.
# PgBouncer IS the connection pool — it assigns a Postgres backend connection for exactly one
# transaction, then reclaims it. If SQLAlchemy maintained its own async connection pool on top,
# those pooled connections would hold a PgBouncer slot open between requests, undermining the
# pooling contract and causing connection exhaustion under load. NullPool means SQLAlchemy opens
# and closes its PgBouncer connection on each transaction, which is exactly what transaction
# pooling expects.
engine = create_async_engine(
    settings.database_url,
    poolclass=NullPool,
    echo=settings.environment == "development",
)

# autocommit=False: explicit transaction control is required so that SET LOCAL has a defined
# transaction scope. If autocommit were True, there would be no transaction and SET LOCAL
# would have no effect.
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass
