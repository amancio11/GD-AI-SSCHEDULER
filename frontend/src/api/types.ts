// ── TypeScript types mirroring Pydantic schemas ──────────────────────────────
// All UUIDs are represented as strings on the frontend.

export type UUID = string;
export type ISODatetime = string;

// ── Enums ─────────────────────────────────────────────────────────────────────

export type MachineOrderStatus = 'PLANNED' | 'IN_PROGRESS' | 'COMPLETED' | 'BLOCKED';

export type ProductionOrderLevel =
  | 'MACHINE'
  | 'MACROAGGREGATE'
  | 'AGGREGATE'
  | 'GROUP'
  | 'COMPONENT';

export type ProductionOrderStatus =
  | 'PLANNED'
  | 'IN_PROGRESS'
  | 'COMPLETED'
  | 'BLOCKED'
  | 'MISSING';

export type OperationType = 'ELECTRICAL' | 'MECHANICAL' | 'GENERAL';
export type OperationStatus =
  | 'PENDING'
  | 'IN_PROGRESS'
  | 'COMPLETED'
  | 'BLOCKED'
  | 'INTERRUPTED';

export type SkillType = 'ELECTRICAL' | 'MECHANICAL' | 'MULTI';

export type ScheduleEntryStatus =
  | 'SCHEDULED'
  | 'IN_PROGRESS'
  | 'COMPLETED'
  | 'INTERRUPTED'
  | 'DELAYED'
  | 'STALE';

export type ObjectiveMode =
  | 'FINISH_BY_DATE'
  | 'MAXIMIZE_RESOURCE_UTILIZATION'
  | 'MINIMIZE_OPERATORS'
  | 'CUSTOM';

export type DelayEventType =
  | 'OPERATOR_ABSENCE'
  | 'COMPONENT_DELAY'
  | 'MANUAL_OPERATION_DELAY'
  | 'OTHER';

export type AiSuggestionType =
  | 'ON_DEMAND'
  | 'PROACTIVE'
  | 'DELAY_ANALYSIS'
  | 'HISTORICAL_PATTERN'
  | 'WHAT_IF'
  | 'EXPLAIN_ENTRY';

// ── Domain types ──────────────────────────────────────────────────────────────

export interface MachineModel {
  id: UUID;
  code: string;
  name: string;
  description: string | null;
}

export interface MachineOrder {
  id: UUID;
  sap_order_id: string;
  machine_model_id: UUID;
  description: string | null;
  status: MachineOrderStatus;
  workcenter_id: UUID | null;
  created_at: ISODatetime;
}

export interface ProductionOrder {
  id: UUID;
  sap_order_id: string;
  parent_order_id: UUID | null;
  parent_material: string | null;
  machine_order_id: UUID;
  level: ProductionOrderLevel;
  material_code: string;
  description: string | null;
  quantity: number;
  unit: string;
  workcenter_id: UUID | null;
  progress_pct: number;
  status: ProductionOrderStatus;
  missing_arrival_date: ISODatetime | null;
  is_purchase_component: boolean;
  is_production_component_untracked: boolean;
  created_at: ISODatetime;
}

export interface BOMTreeNode {
  id: UUID;
  sap_order_id: string;
  material_code: string;
  description: string | null;
  level: ProductionOrderLevel;
  status: ProductionOrderStatus;
  progress_pct: number;
  workcenter_id: UUID | null;
  is_purchase_component: boolean;
  is_production_component_untracked: boolean;
  missing_arrival_date: ISODatetime | null;
  children: BOMTreeNode[];
}

export interface Operation {
  id: UUID;
  routing_id: UUID;
  sap_operation_id: string | null;
  sequence_number: number;
  description: string | null;
  operation_type: OperationType;
  workcenter_id: UUID | null;
  planned_duration_minutes: number;
  actual_duration_minutes: number | null;
  progress_pct: number;
  status: OperationStatus;
  reference_point_id: UUID | null;
  can_be_interrupted: boolean;
}

export interface Workcenter {
  id: UUID;
  code: string;
  name: string;
  location: string | null;
  description: string | null;
  is_active: boolean;
}

export interface Operator {
  id: UUID;
  employee_id: string;
  full_name: string;
  skill: SkillType;
  workcenter_id: UUID;
  is_active: boolean;
}

export interface Shift {
  id: UUID;
  name: string;
  start_time: string;
  end_time: string;
  break_duration_minutes: number;
  is_active: boolean;
}

export interface OperatorCalendarEntry {
  id: UUID;
  operator_id: UUID;
  date: string;
  shift_id: UUID | null;
  is_available: boolean;
  notes: string | null;
  override_reason: string | null;
}

export interface ReferencePoint {
  id: UUID;
  code: string;
  name: string;
  machine_model_id: UUID;
  target_level: 'MACROAGGREGATE' | 'AGGREGATE';
  target_order_material: string | null;
}

export interface ReferencePointPrecedence {
  id: UUID;
  reference_point_id: UUID;
  predecessor_reference_point_id: UUID;
  machine_model_id: UUID;
}

export interface MissingComponent {
  id: UUID;
  production_order_id: UUID;
  component_material: string;
  description: string | null;
  expected_arrival_date: string | null;
  is_arrived: boolean;
  arrival_confirmed_date: string | null;
  manually_flagged: boolean;
  notes: string | null;
}

export interface ScheduleScenario {
  id: UUID;
  name: string;
  description: string | null;
  machine_order_id: UUID;
  objective_mode: ObjectiveMode;
  target_finish_date: string | null;
  resource_set_json: Record<string, unknown> | null;
  is_active: boolean;
  is_baseline: boolean;
  ai_explanation: string | null;
  created_at: ISODatetime;
}

export interface ScheduleEntry {
  id: UUID;
  scenario_id: UUID;
  operation_id: UUID;
  operator_id: UUID;
  workcenter_id: UUID;
  scheduled_start: ISODatetime;
  scheduled_end: ISODatetime;
  actual_start: ISODatetime | null;
  actual_end: ISODatetime | null;
  status: ScheduleEntryStatus;
  interruption_reason: string | null;
  delay_minutes: number;
  is_manual_override: boolean;
}

export interface GanttEntry {
  id: UUID;
  operation_id: UUID;
  operation_desc: string | null;
  order_id: UUID;
  order_desc: string | null;
  operator_id: UUID;
  operator_name: string;
  workcenter_id: UUID;
  start: ISODatetime;
  end: ISODatetime;
  status: ScheduleEntryStatus;
  color: string;
  is_critical_path: boolean;
  is_manual_override: boolean;
}

export interface DelayEvent {
  id: UUID;
  machine_order_id: UUID;
  event_type: DelayEventType;
  affected_entity_id: UUID | null;
  affected_entity_type: string | null;
  delay_from: ISODatetime;
  delay_until: ISODatetime;
  description: string | null;
  reported_at: ISODatetime;
  requires_reschedule: boolean;
}

export interface AiSuggestion {
  id: UUID;
  scenario_id: UUID | null;
  machine_order_id: UUID;
  suggestion_type: AiSuggestionType;
  suggestion_text: string | null;
  suggested_actions_json: unknown[] | null;
  confidence_score: number | null;
  accepted: boolean | null;
  created_at: ISODatetime;
}

// ── Request / response special types ─────────────────────────────────────────

export interface ScheduleRunRequest {
  scenario_id: UUID;
  objective_mode: ObjectiveMode;
  objective_params_json: Record<string, unknown> | null;
}

export interface TaskQueuedResponse {
  task_id: string;
  status: 'queued';
}

export interface ScenarioComparisonResult {
  delta_makespan_days: number | null;
  delta_operators: number | null;
  delta_utilization: number | null;
  gantt_a: GanttEntry[];
  gantt_b: GanttEntry[];
}

export interface DelayImpactResponse {
  impacted_entries: ScheduleEntry[];
  estimated_delta_days: number;
  critical_path_affected: boolean;
}

export interface ChatRequest {
  machine_order_id: UUID;
  scenario_id?: UUID;
  message: string;
  session_id?: UUID;
}

export interface ChatResponse {
  session_id: UUID;
  message: string;
  action_type: string;
  data?: Record<string, unknown>;
  apply_actions?: unknown[];
}

// Pagination wrapper
export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  size: number;
}
