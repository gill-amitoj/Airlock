"""
Schema migration runner.

Applies migrations/*.sql in filename order, recording each one in a
schema_migrations table so re-runs skip what is already applied. Each file
runs in its own transaction, so a failed migration leaves no partial schema.

An advisory lock serialises concurrent runners (e.g. two replicas starting at
once). Run as a container init step before the API and worker start:

    python -m src.persistence.migrate
"""

import logging
import sys
from pathlib import Path

import psycopg2

from src.config import get_config

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"

# Arbitrary constant identifying this app's migration lock.
MIGRATION_LOCK_ID = 7_340_021


def pending_migrations(applied: set, migrations_dir: Path = MIGRATIONS_DIR) -> list:
    """Return migration files not yet applied, in the order they must run."""
    return [p for p in sorted(migrations_dir.glob("*.sql")) if p.name not in applied]


def migrate(database_url: str, migrations_dir: Path = MIGRATIONS_DIR) -> list:
    """Apply pending migrations and return the names of those applied."""
    conn = psycopg2.connect(database_url)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_ID,))
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    filename VARCHAR(255) PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            conn.commit()

            cur.execute("SELECT filename FROM schema_migrations")
            applied = {row[0] for row in cur.fetchall()}

        done = []
        for path in pending_migrations(applied, migrations_dir):
            logger.info(f"Applying migration {path.name}")
            with conn.cursor() as cur:
                cur.execute(path.read_text())
                cur.execute(
                    "INSERT INTO schema_migrations (filename) VALUES (%s)",
                    (path.name,),
                )
            conn.commit()
            done.append(path.name)

        if not done:
            logger.info("Schema is up to date")
        return done
    except Exception:
        conn.rollback()
        raise
    finally:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_ID,))
        conn.commit()
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    try:
        applied = migrate(get_config().DATABASE_URL)
    except Exception as e:
        logger.error(f"Migration failed: {e}")
        sys.exit(1)
    logger.info(f"Applied {len(applied)} migration(s)")
