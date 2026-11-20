import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, fetchPlan, reviewPlan, sendAssistantMessage } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("真实 API 封装", () => {
  it("读取方案时携带用户、会话和版本查询参数", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      text: async () => JSON.stringify({ plan_id: "P1", version: 1 }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await fetchPlan("P1", "u1", "s1", 2);
    const url = fetchMock.mock.calls[0][0] as string;
    expect(url).toBe("/api/v1/plans/P1?user_id=u1&session_id=s1&version=2");
  });

  it("审核接口使用 POST 并发送版本绑定请求体", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      text: async () => JSON.stringify({ outcome: "approved", issues: [] }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await reviewPlan("P1", "u1", "s1", 3, "trace-x");
    const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/plans/P1/reviews");
    expect(options.method).toBe("POST");
    expect(JSON.parse(String(options.body))).toMatchObject({
      user_id: "u1",
      session_id: "s1",
      version: 3,
      trace_id: "trace-x",
    });
  });

  it("助手接口发送用户、会话和消息，并返回 Agent 结果", async () => {
    const result = {
      agent_id: "task_coordination_agent",
      mode: "draft",
      content: "行程建议草案",
      simulation: false,
      policy_verified: false,
      live_inventory_checked: false,
      bookable: false,
      source_label: "模型生成草案 · 无实时库存",
      decision: {
        trace_id: "trace-1",
        route_type: "task_coordination",
        confidence: 0.9,
        decision_layer: "jev",
        fallback: false,
        candidates: [],
        target_agent: "task_coordination_agent",
      },
    };
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      text: async () => JSON.stringify(result),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(sendAssistantMessage("u1", "s1", "帮我规划深圳差旅"))
      .resolves.toMatchObject({ agent_id: "task_coordination_agent", mode: "draft" });
    const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/assistant/message");
    expect(JSON.parse(String(options.body))).toEqual({
      user_id: "u1",
      session_id: "s1",
      utterance: "帮我规划深圳差旅",
    });
  });

  it("FastAPI 503 会转换为 ApiError，保留 detail 供界面提示", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 503,
        text: async () => JSON.stringify({ detail: "缺少 EMBEDDING_API_KEY" }),
      })
    );
    await expect(fetchPlan("P1", "u1", "s1")).rejects.toEqual(expect.any(ApiError));
  });
});
