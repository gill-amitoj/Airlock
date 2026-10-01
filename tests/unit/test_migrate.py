"""
Unit tests for the migration runner's file selection.
"""

from src.persistence.migrate import MIGRATIONS_DIR, pending_migrations


class TestPendingMigrations:
    """Tests for choosing which migration files still need to run."""

    def test_all_pending_in_filename_order(self, tmp_path):
        for name in ["002_b.sql", "001_a.sql", "010_c.sql"]:
            (tmp_path / name).write_text("SELECT 1;")

        names = [p.name for p in pending_migrations(set(), tmp_path)]

        assert names == ["001_a.sql", "002_b.sql", "010_c.sql"]

    def test_skips_applied(self, tmp_path):
        for name in ["001_a.sql", "002_b.sql"]:
            (tmp_path / name).write_text("SELECT 1;")

        names = [p.name for p in pending_migrations({"001_a.sql"}, tmp_path)]

        assert names == ["002_b.sql"]

    def test_ignores_non_sql_files(self, tmp_path):
        (tmp_path / "001_a.sql").write_text("SELECT 1;")
        (tmp_path / "README.md").write_text("notes")

        assert [p.name for p in pending_migrations(set(), tmp_path)] == ["001_a.sql"]

    def test_repo_migrations_found(self):
        assert "001_initial_schema.sql" in [p.name for p in pending_migrations(set(), MIGRATIONS_DIR)]
