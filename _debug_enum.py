"""Script di debug per tracciare la fonte del CREATE TYPE skilltype."""
import os, sys
os.chdir(r'C:\Users\andreamancini\Downloads\gd-scheduler\backend')
sys.path.insert(0, r'C:\Users\andreamancini\Downloads\gd-scheduler\backend')

from sqlalchemy import create_engine, event, text, pool
import sqlalchemy as sa

engine = create_engine(
    'postgresql+psycopg2://scheduler:scheduler@localhost:5432/scheduler',
    poolclass=pool.NullPool,
)

sqls_with_skilltype = []

@event.listens_for(engine, 'before_cursor_execute')
def intercept(conn, cursor, statement, params, context, executemany):
    if 'skilltype' in statement.lower():
        sqls_with_skilltype.append(statement[:300])
        print(f"[INTERCEPT] {statement[:200]}")

with engine.connect() as conn:
    # Verifica che il DB sia vuoto
    result = conn.execute(text("SELECT typname FROM pg_catalog.pg_type WHERE typname = 'skilltype'"))
    rows = result.fetchall()
    print(f"skilltype nel DB prima: {rows}")

    # Testa il DO block
    do_sql = """
DO $body$
BEGIN
  CREATE TYPE skilltype AS ENUM ('ELECTRICAL', 'MECHANICAL', 'MULTI');
EXCEPTION WHEN duplicate_object THEN
  NULL;
END
$body$
"""
    conn.execute(text(do_sql))
    conn.commit()
    print("DO block eseguito OK")

    # Verifica dopo
    result = conn.execute(text("SELECT typname FROM pg_catalog.pg_type WHERE typname = 'skilltype'"))
    rows = result.fetchall()
    print(f"skilltype nel DB dopo DO block: {rows}")

    # Ora testa sa.Enum create_type=False in create_table
    from sqlalchemy import Table, Column, MetaData
    m = MetaData()
    test_table = Table('_test_enum', m,
        Column('id', sa.Integer, primary_key=True),
        Column('skill', sa.Enum('ELECTRICAL','MECHANICAL','MULTI', name='skilltype', create_type=False)),
    )
    print("Creazione tabella test con sa.Enum create_type=False...")
    m.create_all(engine)
    print("Tabella test creata OK")

    # Pulisci
    conn.execute(text("DROP TABLE IF EXISTS _test_enum"))
    conn.commit()

print(f"\nSQL con 'skilltype' intercettati: {len(sqls_with_skilltype)}")
for s in sqls_with_skilltype:
    print(f"  - {s[:150]}")
