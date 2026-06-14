import os
import sys
from datetime import date
from collections import defaultdict
from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(os.getcwd(),'..','.env'))
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models.schedule import ScheduleScenario
from app.models.routing import Operation, Routing
from app.models.production import ProductionOrder
from app.models.operator import Operator, OperatorCalendar, Shift
from app.models.missing import MissingComponent
from app.enums import OperationStatus, OperationType, SkillType
from app.core.scheduler.cpsat_types import QualifiedOperator, operator_can_do
from app.core.scheduler.shift_preprocessor import compute_epoch, _shift_slots_for_day

url = os.environ['DATABASE_URL'].replace('postgresql+asyncpg://','postgresql+psycopg2://').replace('postgresql://','postgresql+psycopg2://')
engine = create_engine(url, pool_pre_ping=True)
Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
session = Session()
try:
    scenario = session.query(ScheduleScenario).first()
    if not scenario:
        print('no scenario')
        sys.exit(1)
    print('scenario', scenario.id, scenario.name)
    machine_order_id = scenario.machine_order_id
    rows = session.query(Operation, Routing, ProductionOrder).select_from(Operation).join(Routing, Operation.routing_id==Routing.id).join(ProductionOrder, Routing.production_order_id==ProductionOrder.id).filter(ProductionOrder.machine_order_id==machine_order_id, Operation.status!=OperationStatus.COMPLETED).all()
    print('ops total', len(rows))
    operators = session.query(Operator).filter(Operator.is_active==True).all()
    print('operators total', len(operators))
    today = date.today()
    epoch = compute_epoch(today)
    cal_rows = session.query(OperatorCalendar).filter(OperatorCalendar.date>=today).all()
    shifts = {s.id:s for s in session.query(Shift).all()}
    qual_ops=[]
    for oper in operators:
        slots=[]
        for cal in cal_rows:
            if cal.operator_id==oper.id and cal.is_available and cal.shift_id:
                shift = shifts.get(cal.shift_id)
                if shift:
                    slots.extend(_shift_slots_for_day(cal.date, shift.start_time, shift.end_time, shift.break_duration_minutes, epoch))
        qual_ops.append(QualifiedOperator(id=oper.id, skill=SkillType(oper.skill.value), workcenter_id=oper.workcenter_id, available_slots=slots))
    print('operator slots total', sum(len(o.available_slots) for o in qual_ops), 'avg per operator', (sum(len(o.available_slots) for o in qual_ops)/len(qual_ops)) if qual_ops else 0)
    wc_ops = defaultdict(int)
    wc_opers = defaultdict(int)
    for op, routing, po in rows:
        wc_ops[po.workcenter_id]+=1
    for oper in qual_ops:
        wc_opers[oper.workcenter_id]+=1
    print('workcenter counts ops', dict(wc_ops))
    print('workcenter counts opers', dict(wc_opers))
    impossible_ops=[]
    zero_slot_ops=[]
    for op, routing, po in rows:
        q=[oper for oper in qual_ops if oper.workcenter_id==po.workcenter_id and operator_can_do(oper, OperationType(op.operation_type.value))]
        if not q:
            impossible_ops.append((str(op.id), str(op.routing_id), str(po.workcenter_id), op.operation_type.value, op.status.value, po.sap_order_id, op.workcenter_id))
        elif all(len(oper.available_slots)==0 for oper in q):
            zero_slot_ops.append((str(op.id), str(op.routing_id), str(po.workcenter_id), op.operation_type.value, len(q), op.workcenter_id))
    print('ops with no qualified operators', len(impossible_ops))
    if impossible_ops:
        for ex in impossible_ops[:10]:
            print('IMPOSSIBLE', ex)
    print('ops with qualified operators but no slots', len(zero_slot_ops))
    if zero_slot_ops:
        for zs in zero_slot_ops[:10]:
            print('ZERO SLOTS', zs)
    missing = session.query(MissingComponent).filter(MissingComponent.is_arrived==False).all()
    print('missing components active', len(missing))
    for mc in missing[:20]:
        print(' missing', mc.production_order_id, mc.component_material, mc.expected_arrival_date)
    dist = defaultdict(int)
    for op,routing,po in rows:
        dist[op.operation_type.value]+=1
    print('operation type distribution', dict(dist))
    print('unique workcenters', len(set(po.workcenter_id for _,_,po in rows)))
finally:
    session.close()
