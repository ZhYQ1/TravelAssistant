import type {
  ActionOption,
  AgentMessageResponse,
  AssistantStreamEvent,
  ContinuationRequest,
  ContinuationResult,
  ContinuationState,
  DraftPlanResult,
  ItineraryPlan,
  MetricsSnapshot,
  PlanReference,
  PlanSummary,
  ReviewReport,
  RouteDecision,
  ReviewTaskState,
} from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
    public body?: unknown
  ) {
    super(detail);
    this.name = "ApiError";
  }
}

const BASE = "/api";

function query(params: Record<string, string | number | undefined | null>): string {
  const entries = Object.entries(params).filter(
    ([, value]) => value !== undefined && value !== null && value !== ""
  );
  if (!entries.length) return "";
  return "?" + new URLSearchParams(entries as [string, string][]).toString();
}

async function parseResponse<T>(response: Response): Promise<T> {
  const text = await response.text();
  let data: unknown = text;
  try {
    data = text ? JSON.parse(text) : undefined;
  } catch {
    // FastAPI 某些错误可能返回纯文本，保留原文用于展示。
  }
  if (!response.ok) {
    const detail =
      data && typeof data === "object" && "detail" in data
        ? String((data as { detail: unknown }).detail)
        : `HTTP ${response.status}`;
    throw new ApiError(response.status, detail, data);
  }
  return data as T;
}

export async function fetchHealth(signal?: AbortSignal): Promise<{ status: string }> {
  const response = await fetch(`${BASE}/healthz`, { signal });
  return parseResponse(response);
}

export async function fetchRoute(utterance: string, traceId?: string, signal?: AbortSignal) {
  const response = await fetch(`${BASE}/v1/route`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ utterance, trace_id: traceId || undefined }),
    signal,
  });
  return parseResponse<RouteDecision>(response);
}

export async function sendAssistantMessage(
  userId: string,
  sessionId: string,
  utterance: string,
  traceId?: string,
  signal?: AbortSignal
) {
  const response = await fetch(`${BASE}/v1/assistant/message`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      user_id: userId,
      session_id: sessionId,
      utterance,
      trace_id: traceId || undefined,
    }),
    signal,
  });
  return parseResponse<AgentMessageResponse>(response);
}

function parseSseEvent(raw: string): AssistantStreamEvent | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of raw.split("\n")) {
    if (line.startsWith("event:")) {
      event = line.slice("event:".length).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trimStart());
    }
  }
  if (!dataLines.length) return null;
  const payload = dataLines.join("\n");
  try {
    return {
      event: event as AssistantStreamEvent["event"],
      data: JSON.parse(payload),
    };
  } catch {
    return null;
  }
}

export async function* streamAssistantMessage(
  userId: string,
  sessionId: string,
  utterance: string,
  traceId?: string,
  signal?: AbortSignal
): AsyncGenerator<AssistantStreamEvent> {
  const response = await fetch(`${BASE}/v1/assistant/message/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({
      user_id: userId,
      session_id: sessionId,
      utterance,
      trace_id: traceId || undefined,
    }),
    signal,
  });

  if (!response.ok || !response.body) {
    await parseResponse<unknown>(response);
    throw new ApiError(response.status, `HTTP ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split("\n\n");
      buffer = frames.pop() ?? "";
      for (const frame of frames) {
        const event = parseSseEvent(frame);
        if (event) yield event;
      }
    }
    const tail = parseSseEvent(buffer);
    if (tail) yield tail;
  } finally {
    reader.releaseLock();
  }
}

export async function fetchPlans(
  userId: string,
  sessionId: string,
  limit = 20
): Promise<PlanSummary[]> {
  const query = new URLSearchParams({
    user_id: userId,
    session_id: sessionId,
    limit: String(limit),
  });
  const response = await fetch(`${BASE}/v1/plans?${query}`);
  return parseResponse<PlanSummary[]>(response);
}

export async function createDraftPlan(payload: {
  user_id: string;
  session_id: string;
  draft: string;
  plan_id?: string;
  version?: number;
}): Promise<DraftPlanResult> {
  const response = await fetch(`${BASE}/v1/plans/draft`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseResponse<DraftPlanResult>(response);
}

export async function createPlan(plan: ItineraryPlan): Promise<PlanReference> {
  const response = await fetch(`${BASE}/v1/plans`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(plan),
  });
  return parseResponse<PlanReference>(response);
}

export async function fetchPlan(
  planId: string,
  userId: string,
  sessionId: string,
  version?: number,
  signal?: AbortSignal
) {
  const response = await fetch(
    `${BASE}/v1/plans/${encodeURIComponent(planId)}${query({
      user_id: userId,
      session_id: sessionId,
      version,
    })}`,
    { signal }
  );
  return parseResponse<ItineraryPlan>(response);
}

export async function reviewPlan(
  planId: string,
  userId: string,
  sessionId: string,
  version: number,
  traceId?: string,
  signal?: AbortSignal
) {
  const response = await fetch(`${BASE}/v1/plans/${encodeURIComponent(planId)}/reviews`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      user_id: userId,
      session_id: sessionId,
      version,
      trace_id: traceId || undefined,
    }),
    signal,
  });
  return parseResponse<ReviewReport>(response);
}

export async function cancelReview(
  planId: string,
  userId: string,
  sessionId: string,
  version: number,
  signal?: AbortSignal
) {
  const response = await fetch(`${BASE}/v1/plans/${encodeURIComponent(planId)}/cancel`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: userId, session_id: sessionId, version }),
    signal,
  });
  return parseResponse<ReviewTaskState>(response);
}

export async function fetchActions(sessionId: string, userId: string, signal?: AbortSignal) {
  const response = await fetch(
    `${BASE}/v1/continuation/actions${query({ session_id: sessionId, user_id: userId })}`,
    { signal }
  );
  return parseResponse<ActionOption[]>(response);
}

export async function fetchContinuationState(
  sessionId: string,
  userId: string,
  signal?: AbortSignal
) {
  const response = await fetch(
    `${BASE}/v1/continuation/state${query({ session_id: sessionId, user_id: userId })}`,
    { signal }
  );
  return parseResponse<ContinuationState>(response);
}

export async function resumeAction(request: ContinuationRequest, signal?: AbortSignal) {
  const response = await fetch(`${BASE}/v1/continuation/resume`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
    signal,
  });
  return parseResponse<ContinuationResult>(response);
}

export async function fetchMetrics(signal?: AbortSignal) {
  const response = await fetch(`${BASE}/metrics`, { signal });
  return parseResponse<MetricsSnapshot>(response);
}