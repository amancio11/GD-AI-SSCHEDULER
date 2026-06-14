import os, sys
from datetime import date, datetime, timezone
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv()
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from app.models.schedule import ScheduleScenario
from app.models.operator import Operator, OperatorCalendar, Shift
from app.models.routing import Operation, Routing
from app.models.production import ProductionOrder
from app.enums import OperationStatus, SkillType
from app.core.scheduler.shift_preprocessor import compute_epoch, _shift_slots_for_day

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
    rows = session.query(Operation, Routing, ProductionOrder).select_from(Operation).join(Routing, Operation.routing_id==Routing.id).join(ProductionOrder, Routing.production_order_id==ProductionOrder.id).filter(ProductionOrder.machine_order_id==machine_order_id, Operation.status!=OperationStatus.COMPLETED).all()
    print('ops total', len(rows))
    durs = [op.planned_duration_minutes for op,_,_ in rows]
    print('durations min/max/mean', min(durs), max(durs), sum(durs)/len(durs))
    if rows:
        print('sample op', rows[0][0].id, rows[0][0].operation_type, rows[0][0].planned_duration_minutes, rows[0][0].progress_pct, rows[0][0].reference_point_id, rows[0][2].workcenter_id)
    wcs = {}
    for _,_,po in rows:
        wc = po.workcenter_id
        wcs[wc] = wcs.get(wc, 0) + 1
    print('workcenters count', len(wcs))
    print('workcenter breakdown (first 20)', list(wcs.items())[:20])
    active_ops = session.query(Operator).filter(Operator.is_active==True).all()
    print('active operators', len(active_ops))
    cal_rows = session.query(OperatorCalendar).filter(OperatorCalendar.date>=date.today()).all()
    print('calendar rows from today', len(cal_rows))
    shifts = {s.id: s for s in session.query(Shift).all()}
    slot_map = {}
    total_slots=0
    for cal in cal_rows:
        if cal.is_available and cal.shift_id:
            shift = shifts.get(cal.shift_id)
            if shift:
                slots=_shift_slots_for_day(cal.date, shift.start_time, shift.end_time, shift.break_duration_minutes, compute_epoch(date.today()))
                slot_map.setdefault(cal.operator_id,0)
                slot_map[cal.operator_id]+=len(slots)
                total_slots+=len(slots)
    print('operator slots defined', total_slots, 'operators with slots', len(slot_map))
    if rows:
        from app.core.scheduler.cpsat_types import SchedulableOperation, QualifiedOperator
        from app.core.scheduler.cpsat_model_builder import CpsatModelBuilder
        from app.core.scheduler.shift_preprocessor import build_unavailable_intervals
        from app.enums import SkillType as SkillTypeEnum, OperationType

        # build ops and operators as in reschedule_engine
        sched_ops=[]
        for op,routing,po in rows:
            sched_ops.append(SchedulableOperation(id=op.id, routing_id=routing.id, production_order_id=po.id, operation_type=OperationType(op.operation_type.value), workcenter_id=po.workcenter_id, planned_duration_minutes=op.planned_duration_minutes, progress_pct=op.progress_pct, can_be_interrupted=op.can_be_interrupted, earliest_start_minutes=0, reference_point_id=op.reference_point_id))
        qual_ops=[]
        for oper in active_ops:
            slots=[]
            for cal in cal_rows:
                if cal.operator_id == oper.id and cal.is_available and cal.shift_id:
                    shift = shifts.get(cal.shift_id)
                    if shift:
                        slots.extend(_shift_slots_for_day(cal.date, shift.start_time, shift.end_time, shift.break_duration_minutes, compute_epoch(date.today())))
            qual_ops.append(QualifiedOperator(id=oper.id, skill=SkillTypeEnum(oper.skill.value), workcenter_id=oper.workcenter_id, available_slots=slots))
        no_qual=[]
        for op in sched_ops:
            quals=[oper for oper in qual_ops if oper.workcenter_id==op.workcenter_id and op.operation_type in {SkillTypeEnum.ELECTRICAL if oper.skill==SkillTypeEnum.ELECTRICAL else None}]
            # purposely skip qualification check since skill logic is in builder
        print('built ops', len(sched_ops), 'quals', len(qual_ops))
        print('operators with no slots', sum(1 for o in qual_ops if not o.available_slots))
        # check if any op has zero qualified operators by workcenter/skill
        missing=0
        for op in sched_ops:
            q=[oper for oper in qual_ops if oper.workcenter_id==op.workcenter_id and ((oper.skill.name=='MULTI') or (oper.skill.name=='ELECTRICAL' and op.operation_type.name in ('ELECTRICAL','GENERAL')) or (oper.skill.name=='MECHANICAL' and op.operation_type.name in ('MECHANICAL','GENERAL')))]
            if not q:
                missing+=1
        print('ops with zero qualified operators', missing)

finally:
    session.close()
