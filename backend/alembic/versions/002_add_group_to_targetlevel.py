"""add GROUP to targetlevel enum
 
Revision ID: 002
Revises: 001
Create Date: 2026-06-15
 
ALTER TYPE in PostgreSQL non supporta rollback transazionale,
ma supporta IF NOT EXISTS dalla versione 14+.
"""
from __future__ import annotations
 
from typing import Sequence, Union
 
from alembic import op
 
revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
 
 
def upgrade() -> None:
    # ALTER TYPE ADD VALUE IF NOT EXISTS è idempotente su PostgreSQL 14+.
    # Non può girare dentro una transazione aperta — Alembic la gestisce.
    op.execute("ALTER TYPE targetlevel ADD VALUE IF NOT EXISTS 'GROUP'")
 
 
def downgrade() -> None:
    # PostgreSQL non supporta la rimozione di valori da enum esistenti.
    # Per rollback: drop e recreate il DB in ambienti di test.
    pass