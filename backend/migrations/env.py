"""Alembic migration environment for FinSight.

Design decisions:
- The database URL is injected from the SAME pydantic Settings the app uses
  (backend/.env), never hardcoded in alembic.ini. One source of truth for
  credentials means migrations can never run against a different database
  than the application -- a classic foot-gun when URLs are copy-pasted.
- target_metadata comes from our SQLAlchemy models so `alembic revision
  --autogenerate` can diff models against the live schema.
"""

import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# Make the `app` package importable when alembic runs from backend/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.database.models import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject the real URL from application settings (not from the .ini file).
config.set_main_option(
    "sqlalchemy.url", get_settings().database_url.replace("%", "%%")
    # %-escaping: configparser interpolates % characters, and passwords may
    # legitimately contain them. Without this, a password with % breaks the
    # URL silently at migration time.
)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit migration SQL without a live DB connection (for review/dry-run)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against the live database."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
