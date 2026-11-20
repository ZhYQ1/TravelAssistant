import { describe, expect, it } from "vitest";
import {
  applyDemoAction,
  demoActions,
  demoMetrics,
  demoPlan,
  demoReport,
  demoRoute,
} from "./demo";

describe("演示数据边界", () => {
  it("演示方案包含交通、酒店和日程，可被审核模型引用", () => {
    expect(demoPlan.transport_options.length).toBeGreaterThan(0);
    expect(demoPlan.hotel_options.length).toBeGreaterThan(0);
    expect(demoPlan.days.length).toBeGreaterThan(0);
    const ids = new Set([
      ...demoPlan.transport_options.map((item) => item.option_id),
      ...demoPlan.hotel_options.map((item) => item.option_id),
    ]);
    for (const day of demoPlan.days) {
      if (day.transport_option_id) expect(ids.has(day.transport_option_id)).toBe(true);
      if (day.hotel_option_id) expect(ids.has(day.hotel_option_id)).toBe(true);
    }
  });

  it("空输入返回澄清，不指定目标 Agent", () => {
    const decision = demoRoute("   ");
    expect(decision.decision_layer).toBe("clarify");
    expect(decision.route_type).toBeNull();
    expect(decision.clarification).toBeTruthy();
  });

  it("报销类输入路由到知识咨询，审批类输入路由到 Workflow", () => {
    expect(demoRoute("这个舱位能报销吗").route_type).toBe("knowledge_qa");
    expect(demoRoute("帮我提交差旅审批").route_type).toBe("workflow");
  });

  it("阶段动作符合闭环推进规则", () => {
    expect(demoActions("changes_required").map((item) => item.action)).toEqual([
      "modify",
      "replan",
      "cancel",
    ]);
    expect(applyDemoAction("changes_required", "confirm")).toBe("approved");
    expect(applyDemoAction("changes_required", "replan")).toBe("replanning");
  });

  it("演示指标包含四项比率，避免页面把空对象当真实趋势", () => {
    expect(Object.keys(demoMetrics.ratios)).toHaveLength(4);
    expect(demoReport.issues.length).toBeGreaterThan(0);
  });
});
