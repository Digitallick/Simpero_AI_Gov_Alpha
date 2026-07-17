-- Local sandbox: create the app role, mirroring the DigitalOcean cluster.
--
-- The migrations reference doadmin and dd_app BY NAME (see
-- alembic/versions/bootstrap_dd_app_privliges.py and the RLS policies), so a
-- local database that behaves identically must have those exact roles.
--
-- doadmin is created by the postgres image itself (POSTGRES_USER=doadmin), so it
-- is the superuser that owns the `simpero` database and runs migrations -- the
-- same division as production, where doadmin does DDL. This script runs as
-- doadmin against the freshly-created simpero database and adds the one role the
-- image cannot: dd_app, the RLS-restricted runtime role.
--
-- Everything else dd_app needs -- USAGE on the schema, and INSERT/SELECT/etc on
-- tables doadmin creates -- is granted by the bootstrap migration, exactly as on
-- the real cluster. dd_app only has to exist and be able to connect.
--
-- The password here is a well-known local-only secret; it never leaves the
-- sandbox and matches DATABASE_URL in sandbox/.env.sandbox.

CREATE ROLE dd_app WITH LOGIN PASSWORD 'sandbox_dd_app';
GRANT CONNECT ON DATABASE simpero TO dd_app;
