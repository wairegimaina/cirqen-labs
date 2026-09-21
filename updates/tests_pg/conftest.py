# Reuse the sync suite's throwaway PostgreSQL cluster fixture.
from sync.tests.conftest import PG_BIN, pg_dsn  # noqa: F401
