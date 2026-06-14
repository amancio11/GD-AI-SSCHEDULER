import os
import sys
from datetime import datetime, date, timezone
from pathlib import Path
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv()
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models.schedule import ScheduleScenario
from app.core.scheduler.reschedule_engine import _run_reschedule

url = os.environ.get('DATABASE_URL', 'postgresql+asyncpg://scheduler:scheduler@localhost:5432/scheduler')
url = url.replace('postgresql+asyncpg://', 'postgresql+psycopg2://').replace('postgresql://', 'postgresql+psycopg2://')
engine = create_engine(url, pool_pre_ping=True)
Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
session = Session()
try:
    scenarios = session.query(ScheduleScenario).limit(3).all()
    print('Scenarios:', len(scenarios))
    for s in scenarios:
        print('id', s.id, 'name', s.name, 'target', s.target_finish_date)
    if not scenarios:
        sys.exit(0)
    result = _run_reschedule(session, scenarios[0].id, 'manual-script')
    print('RESULT', result)
    session.commit()
finally:
    session.close()
