"""Alembic environment — usa psycopg2 (sync) per le migrazioni.

FastAPI usa asyncpg per le query runtime, ma Alembic è uno strumento batch
che non ha bisogno di async. Usiamo psycopg2 (sync) per semplicità e
per evitare problemi noti di asyncpg con la creazione di ENUM type.
Il DATABASE_URL viene convertito automaticamente da asyncpg → psycopg2.
"""
from __future__ import annotations

import os
from logging.config import fileConfig

from dotenv import load_dotenv
from sqlalchemy import create_engine, pool

from alembic import context

load_dotenv()

config = context.config

# Converti postgresql+asyncpg:// → postgresql+psycopg2:// per Alembic
database_url = os.getenv("DATABASE_URL", "")
sync_url = database_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://").replace("postgresql://", "postgresql+psycopg2://")
if sync_url:
    config.set_main_option("sqlalchemy.url", sync_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Le migrazioni sono scritte a mano (non autogenerate), quindi target_metadata
# non è necessario per la normale esecuzione. L'import dei modelli ORM
# registra globalmente i sa.Enum nel registry SQLAlchemy, causando
# CREATE TYPE duplicati anche con create_type=False nella migration.
# Lasciamo target_metadata=None per l'esecuzione normale.
# Se si vuole usare --autogenerate in futuro, decommentare il blocco qui sotto
# e impostare create_type=False su tutti gli Enum nei modelli ORM.

# try:
#     import app.models  # noqa: F401
#     from app.models.base import Base
#     target_metadata = Base.metadata
# except ImportError:
#     target_metadata = None

target_metadata = None


def run_migrations_offline() -> None:
    """Modalità offline: genera SQL senza connettersi al DB."""
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
    """Modalità online: esegue le migrazioni con una connessione psycopg2 sync."""
    connectable = create_engine(
        sync_url,
        poolclass=pool.NullPool,  # nessun pool: Alembic apre/chiude la connessione una sola volta
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

