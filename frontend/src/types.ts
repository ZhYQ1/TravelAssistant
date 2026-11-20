export type RouteType = "task_coordination" | "knowledge_qa" | "workflow";
export type DecisionLayer = "l1_rule" | "l2_vector" | "jev" | "llm" | "clarify";

export interface Candidate {
  route_type: RouteType;
  score: number;
}

export interface RouteDecision {
  trace_id: string;
  route_type: RouteType | null;
  confidence: number;
  decision_layer: DecisionLayer;
  fallback: boolean;
  fallback_reason?: string;
  clarification?: string;
  rule_id?: string;
  candidates: Candidate[];
  target_agent?: string;
  latency_ms?: number;
}

export interface AgentMessageResponse {
  decision: RouteDecision;
  agent_id: string;
  mode: "draft" | "general_generated" | "mock" | "clarify";
  content: string;
  simulation: boolean;
  policy_verified: boolean;
  live_inventory_checked: boolean;
  bookable: boolean;
  source_label: string;
}

export type AssistantStreamMeta = Omit<AgentMessageResponse, "content">;

export type AssistantStreamEvent =
  | { event: "meta"; data: AssistantStreamMeta }
  | { event: "delta"; data: { content: string } }
  | { event: "done"; data: AgentMessageResponse };

export type TransportMode = "train" | "flight" | "other";

export interface TransportOption {
  option_id: string;
  mode: TransportMode;
  carrier: string;
  depart_at: string;
  arrive_at: string;
  price_cny: number;
  transfer_count: number;
  refundable: boolean;
}

export interface HotelOption {
  option_id: string;
  name: string;
  check_in: string;
  check_out: string;
  nightly_price_cny: number;
  distance_km: number;
  refundable: boolean;
}

export interface DayPlan {
  day: string;
  activities: string[];
  transport_option_id?: string | null;
  hotel_option_id?: string | null;
}

export interface ItineraryPlan {
  plan_id: string;
  user_id: string;
  session_id: string;
  version: number;
  created_at: string;
  transport_options: TransportOption[];
  hotel_options: HotelOption[];
  days: DayPlan[];
  preferences: Record<string, string>;
}

export interface PlanReference {
  plan_id: string;
  version: number;
}

export type Dimension = "experience" | "resilience" | "preference";
export type Severity = "critical" | "high" | "medium" | "low";
export type ReviewOutcome = "approved" | "changes_required" | "review_failed";

export interface ReviewIssue {
  issue_id: string;
  code: string;
  dimension: Dimension;
  severity: Severity;
  message: string;
  evidence: Record<string, unknown>;
  repeat_count: number;
  priority: number;
  priority_score: number;
}

export interface ReviewReport {
  review_id: string;
  trace_id: string;
  user_id: string;
  session_id: string;
  plan_id: string;
  plan_version: number;
  rules_version: string;
  outcome: ReviewOutcome;
  issues: ReviewIssue[];
  error_code?: string;
  error_message?: string;
  requires_user_decision: boolean;
  review_round: number;
  created_at: string;
  passed?: boolean;
}

export type ContinuationAction = "confirm" | "modify" | "replan" | "cancel";

export interface ActionOption {
  action: ContinuationAction;
  label: string;
}

export interface PlanSummary {
  plan_id: string;
  version: number;
  title: string;
  created_at: string;
}

export interface DraftPlanResult extends PlanSummary {
  start_date?: string | null;
  days?: number | null;
  budget_cny?: number | null;
  hotel_area?: string;
}

export interface ContinuationState {
  active: boolean;
  phase?: string | null;
  agent_id?: string | null;
  task_id?: string | null;
  plan_id?: string | null;
  plan_version?: number | null;
  options: ActionOption[];
}

export interface ContinuationResult {
  status: "resumed" | "fallback" | "processing";
  session_id: string;
  action: ContinuationAction;
  agent_id?: string;
  task_id?: string;
  reason?: string;
  error_code?: string;
  duplicate: boolean;
  output?: Record<string, unknown>;
}

export interface ContinuationRequest {
  session_id: string;
  user_id: string;
  agent_id: string;
  task_id: string;
  action: ContinuationAction;
  click_id: string;
  plan_id?: string;
  plan_version?: number;
  instruction?: string;
}

export interface LatencyStat {
  count: number;
  mean: number;
  p95: number;
}

export interface MetricsSnapshot {
  counters: Record<string, number>;
  latency_ms: Record<string, LatencyStat>;
  token_usage: Record<string, Record<string, number>>;
  ratios: {
    route_llm_fallback_rate: number;
    review_first_pass_rate: number;
    review_read_failure_rate: number;
    continuation_direct_rate: number;
  };
}

export type TripMode = "real" | "demo";

export interface TripTarget {
  mode: TripMode;
  planId: string;
}

export type DataMode = "demo" | "live";

export interface ApiSettings {
  mode: DataMode;
  user_id: string;
  session_id: string;
  plan_id: string;
  version: string;
  agent_id: string;
  task_id: string;
}

export interface ReviewTaskState {
  state: string;
  review_round: number;
  replan_round: number;
  requires_user_decision: boolean;
  updated_at: string;
}