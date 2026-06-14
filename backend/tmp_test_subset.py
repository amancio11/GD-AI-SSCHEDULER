import os, sys, time
from datetime import date, timezone
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv()
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models.schedule import ScheduleScenario
from app.models.operator import Operator, OperatorCalendar, Shift
from app.models.routing import Operation, Routing
from app.models.production import ProductionOrder
from app.enums import OperationStatus, SkillType as SkillTypeEnum, OperationType
from app.core.scheduler.shift_preprocessor import compute_epoch, _shift_slots_for_day
from app.core.scheduler.cpsat_model_builder import CpsatModelBuilder
from app.core.scheduler.cpsat_types import SchedulableOperation, QualifiedOperator

url = os.environ.get('DATABASE_URL', 'postgresql+asyncpg://scheduler:scheduler@localhost:5432/scheduler')
url = url.replace('postgresql+asyncpg://','postgresql+psycopg2://').replace('postgresql://','postgresql+psycopg2://')
engine = create_engine(url, pool_pre_ping=True)
Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
session = Session()
try:
    scenario = session.query(ScheduleScenario).first()
    if not scenario:
        print('no scenario'); sys.exit(0)
    machine_order_id = scenario.machine_order_id
    rows = session.query(Operation, Routing, ProductionOrder).select_from(Operation).join(Routing, Operation.routing_id==Routing.id).join(ProductionOrder, Routing.production_order_id==ProductionOrder.id).filter(ProductionOrder.machine_order_id==machine_order_id, Operation.status!=OperationStatus.COMPLETED).limit(20).all()
    print('ops', len(rows))
    today = date.today()
    epoch = compute_epoch(today)
    operators = session.query(Operator).filter(Operator.is_active==True).all()
    cal_rows = session.query(OperatorCalendar).filter(OperatorCalendar.date>=today).all()
    shifts = {s.id:s for s in session.query(Shift).all()}
    qual_ops=[]
    for oper in operators:
        slots=[]
        for cal in cal_rows:
            if cal.operator_id==oper.id and cal.is_available and cal.shift_id:
                shift=shifts.get(cal.shift_id)
                if shift:
                    slots.extend(_shift_slots_for_day(cal.date, shift.start_time, shift.end_time, shift.break_duration_minutes, epoch))
        qual_ops.append(QualifiedOperator(id=oper.id, skill=SkillTypeEnum(oper.skill.value), workcenter_id=oper.workcenter_id, available_slots=slots))
    sched_ops=[]
    for op,routing,po in rows:
        sched_ops.append(SchedulableOperation(id=op.id, routing_id=routing.id, production_order_id=po.id, operation_type=OperationType(op.operation_type.value), workcenter_id=po.workcenter_id, planned_duration_minutes=op.planned_duration_minutes, progress_pct=op.progress_pct, can_be_interrupted=op.can_be_interrupted, earliest_start_minutes=0, reference_point_id=op.reference_point_id))
    # count quals
    for op in sched_ops:
        q=[oper for oper in qual_ops if oper.workcenter_id==op.workcenter_id and ((oper.skill==SkillTypeEnum.MULTI) or (oper.skill==SkillTypeEnum.ELECTRICAL and op.operation_type in {OperationType.ELECTRICAL, OperationType.GENERAL}) or (oper.skill==SkillTypeEnum.MECHANICAL and op.operation_type in {OperationType.MECHANICAL, OperationType.GENERAL}))]
        if not q:
            print('no qual for op', op.id, op.operation_type, op.workcenter_id)
    horizon = 60*24*90
    print('operators', len(qual_ops), 'slots total', sum(len(o.available_slots) for o in qual_ops))
    builder = CpsatModelBuilder(sched_ops, qual_ops, horizon, epoch, {}, [])
    t0=time.time(); sol=builder.build_and_solve('FINISH_BY_DATE', {}); t1=time.time();
    print('status', sol.status, 'time', t1-t0, 'entries', len(sol.schedule_entries), 'ops', len(sched_ops))
finally:
    session.close()
