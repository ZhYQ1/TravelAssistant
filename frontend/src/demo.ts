import type {
  ActionOption,
  ContinuationAction,
  ContinuationResult,
  ItineraryPlan,
  MetricsSnapshot,
  ReviewReport,
  RouteDecision,
} from "./types";

export const demoTripTitle = "上海 → 深圳，3 天 2 晚";
export const demoRouteCities = { from: "上海", to: "深圳", code: "SHA → SZX" };

export const demoPlan: ItineraryPlan = {
  plan_id: "PLAN-DEMO-20261119",
  user_id: "demo-user",
  session_id: "session-demo-20261119",
  version: 1,
  created_at: "2026-11-19T09:20:00+08:00",
  transport_options: [
    {
      option_id: "T1",
      mode: "flight",
      carrier: "中国国航 CA1891",
      depart_at: "2026-11-24T07:25:00+08:00",
      arrive_at: "2026-11-24T10:05:00+08:00",
      price_cny: 1480,
      transfer_count: 0,
      refundable: true,
    },
    {
      option_id: "T2",
      mode: "train",
      carrier: "高铁 G99",
      depart_at: "2026-11-24T08:00:00+08:00",
      arrive_at: "2026-11-25T02:30:00+08:00",
      price_cny: 1260,
      transfer_count: 2,
      refundable: true,
    },
  ],
  hotel_options: [
    {
      option_id: "H1",
      name: "深圳南山商务酒店",
      check_in: "2026-11-24",
      check_out: "2026-11-26",
      nightly_price_cny: 980,
      distance_km: 18.5,
      refundable: true,
    },
    {
      option_id: "H2",
      name: "福田会展中心精选酒店",
      check_in: "2026-11-24",
      check_out: "2026-11-26",
      nightly_price_cny: 1020,
      distance_km: 3.8,
      refundable: false,
    },
  ],
  days: [
    {
      day: "2026-11-24",
      activities: ["抵达深圳宝安机场", "前往酒店办理入住", "晚间客户预备会议"],
      transport_option_id: "T1",
      hotel_option_id: "H1",
    },
    {
      day: "2026-11-25",
      activities: ["上午需求评审", "下午系统对接", "晚餐工作沟通"],
      hotel_option_id: "H1",
    },
    {
      day: "2026-11-26",
      activities: ["上午最终确认", "返程"],
      transport_option_id: "T1",
    },
  ],
  preferences: { budget_cny: "2500", avoid_red_eye: "true", hotel_area: "福田" },
};

export const demoReport: ReviewReport = {
  review_id: "REV-DEMO-001",
  trace_id: "trace-demo-20261119",
  user_id: demoPlan.user_id,
  session_id: demoPlan.session_id,
  plan_id: demoPlan.plan_id,
  plan_version: 1,
  rules_version: "review-rules-v1",
  outcome: "changes_required",
  requires_user_decision: false,
  review_round: 1,
  created_at: "2026-11-19T09:22:00+08:00",
  issues: [
    {
      issue_id: "ISS-001",
      code: "EARLY_DEPARTURE",
      dimension: "experience",
      severity: "medium",
      message: "出发时间过早，可能影响差旅体验。",
      evidence: { option_id: "T1", depart_at: "2026-11-24T07:25:00+08:00", threshold: "08:00" },
      repeat_count: 1,
      priority: 1,
      priority_score: 24,
    },
    {
      issue_id: "ISS-002",
      code: "HOTEL_TOO_FAR",
      dimension: "experience",
      severity: "high",
      message: "当前引用酒店距离主要工作区域过远。",
      evidence: { hotel_id: "H1", distance_km: 18.5, threshold_km: 12 },
      repeat_count: 2,
      priority: 2,
      priority_score: 33,
    },
    {
      issue_id: "ISS-003",
      code: "MULTIPLE_TRANSFERS",
      dimension: "resilience",
      severity: "high",
      message: "备选高铁存在多次换乘，延误风险较高。",
      evidence: { option_id: "T2", transfer_count: 2, threshold: 1 },
      repeat_count: 0,
      priority: 3,
      priority_score: 32,
    },
    {
      issue_id: "ISS-004",
      code: "BUDGET_EXCEEDED",
      dimension: "preference",
      severity: "critical",
      message: "当前引用的交通和酒店总价超出预算。",
      evidence: { total_cny: 3440, budget_cny: 2500 },
      repeat_count: 1,
      priority: 4,
      priority_score: 42,
    },
  ],
};

export function demoRoute(utterance: string): RouteDecision {
  const text = utterance.trim();
  if (!text) {
    return {
      trace_id: "demo-trace-empty",
      route_type: null,
      confidence: 0,
      decision_layer: "clarify",
      fallback: true,
      fallback_reason: "empty_input",
      clarification: "请补充你的差旅需求，例如出行日期、目的地或要修改的行程。",
      candidates: [],
    };
  }
  const isKnowledge = /报销|发票|政策|标准|能不能报/.test(text);
  const isWorkflow = /审批|提交审批|发起|流程/.test(text);
  const routeType = isWorkflow ? "workflow" : isKnowledge ? "knowledge_qa" : "task_coordination";
  const label = isWorkflow
    ? "Workflow"
    : isKnowledge
      ? "知识咨询"
      : "任务协调";
  return {
    trace_id: "demo-trace-" + Math.random().toString(16).slice(2),
    route_type: routeType,
    confidence: 0.93,
    decision_layer: "l1_rule",
    fallback: false,
    candidates: [{ route_type: routeType, score: 0.93 }],
    target_agent: label,
  };
}

export function demoActions(phase: string): ActionOption[] {
  switch (phase) {
    case "awaiting_confirmation":
      return [
        { action: "confirm", label: "确认方案" },
        { action: "modify", label: "要求修改" },
        { action: "cancel", label: "取消行程" },
      ];
    case "changes_required":
      return [
        { action: "modify", label: "要求修改" },
        { action: "replan", label: "重新规划" },
        { action: "cancel", label: "取消行程" },
      ];
    case "replanning":
      return [];
    case "approved":
      return [];
    case "cancelled":
      return [];
    default:
      return [{ action: "cancel", label: "取消行程" }];
  }
}

export function applyDemoAction(_phase: string, action: ContinuationAction): string {
  if (action === "confirm") return "approved";
  if (action === "cancel") return "cancelled";
  if (action === "replan") return "replanning";
  return "changes_required";
}

export function demoResultFor(action: ContinuationAction, phase: string): ContinuationResult {
  return {
    status: "resumed",
    session_id: demoPlan.session_id,
    action,
    agent_id: "task_coordination_agent",
    task_id: "TASK-DEMO-001",
    reason: "演示数据：仅推进本地阶段，不调用后端续跑服务。",
    duplicate: false,
    output: { phase: applyDemoAction(phase, action) },
  };
}

export const demoMetrics: MetricsSnapshot = {
  counters: {
    route_total: 48,
    route_l1_rule: 29,
    route_l2_vector: 9,
    route_jev: 7,
    route_llm: 3,
    review_total: 12,
    review_first_pass: 6,
    continuation_total: 16,
    continuation_resumed: 13,
  },
  latency_ms: {
    route: { count: 48, mean: 548, p95: 886 },
    review: { count: 12, mean: 210, p95: 310 },
    continuation: { count: 16, mean: 90, p95: 140 },
  },
  token_usage: {
    "llm_route|spark-x2.6-4b": { prompt_tokens: 14200, completion_tokens: 1800, total_tokens: 16000 },
    "jev_route|spark-x2.6-4b": { prompt_tokens: 7600, completion_tokens: 960, total_tokens: 8560 },
  },
  ratios: {
    route_llm_fallback_rate: 0.0625,
    review_first_pass_rate: 0.5,
    review_read_failure_rate: 0,
    continuation_direct_rate: 0.8125,
  },
};