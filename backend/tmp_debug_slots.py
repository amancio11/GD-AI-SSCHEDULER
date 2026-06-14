import os, sys
from datetime import date, timezone
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv()
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from app.models.schedule import ScheduleScenario
from app.models.operator import Operator, OperatorCalendar
from app.models.routing import Operation, Routing
from app.models.production import ProductionOrder
from app.models.operator import Shift

url = os.environ.get('DATABASE_URL', 'postgresql+asyncpg://scheduler:scheduler@localhost:5432/scheduler')
url = url.replace('postgresql+asyncpg://','postgresql+psycopg2://').replace('postgresql://','postgresql+psycopg2://')
engine = create_engine(url, pool_pre_ping=True)
Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
session = Session()
try:
    scenario = session.query(ScheduleScenario).first()
    print('scenario', scenario.id if scenario else None, scenario.name if scenario else None, scenario.target_finish_date if scenario else None)
    if not scenario:
        sys.exit(0)
    machine_order_id = scenario.machine_order_id
    op_count = session.query(Operation).join(Routing).join(ProductionOrder).filter(ProductionOrder.machine_order_id==machine_order_id, Operation.status!='COMPLETED').count()
    print('ops not completed', op_count)
    opp_rows = session.query(Operation, Routing, ProductionOrder).join(Routing).join(ProductionOrder).filter(ProductionOrder.machine_order_id==machine_order_id, Operation.status!='COMPLETED').all()
    print('sample op count', len(opp_rows))
    if opp_rows:
        print('first op', opp_rows[0][0].id, opp_rows[0][0].operation_type, opp_rows[0][0].planned_duration_minutes, opp_rows[0][0].progress_pct)
    ops_wc = set(p[2].workcenter_id for p in opp_rows)
    print('workcenters in ops', len(ops_wc), list(ops_wc)[:5])
    operators = session.query(Operator).filter(Operator.is_active==True).all()
    print('active operators', len(operators))
    calendar = session.query(OperatorCalendar).filter(OperatorCalendar.date>=date.today()).all()
    print('calendar rows from today', len(calendar))
    shifts = {s.id: s for s in session.query(Shift).all()}
    slot_count = 0
    per_op = {}
    for op in operators:
        slots = []
        rows = [c for c in calendar if c.operator_id == op.id and c.is_available and c.shift_id]
        for row in rows:
            shift = shifts.get(row.shift_id)
            if shift:
                start = 0
                end = 0
                print('operator', op.id, 'cal date', row.date, 'shift', shift.start_time, shift.end_time, 'break', shift.break_duration_minutes)
                break
    print('done debug')
finally:
    session.close()
