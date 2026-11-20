import { useEffect, useRef, useState } from "react";
import {
  createDraftPlan,
  fetchContinuationState,
  resumeAction,
  streamAssistantMessage,
} from "../api";
import type {
  AssistantStreamMeta,
  ContinuationAction,
  ContinuationState,
  PlanReference,
  RouteDecision,
} from "../types";
import { recordRecentTrip } from "../trips";
import type { SessionContext } from "../trips";

interface Props {
  session: SessionContext;
  onBack: () => void;
  onDecision?: (decision: RouteDecision) => void;
  showObservability?: boolean;
  onToggleObservability?: () => void;
}

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  meta?: AssistantStreamMeta;
  pending?: boolean;
  error?: boolean;
}

const quickAsks = [
  "下周二去深圳出差三天，预算 2500 元以内",
  "帮我把酒店换到客户公司附近",
  "这周五去北京见客户，当天往返",
];

const followUpAsks = [
  "确认行程，就按这个来",
  "把高铁改到下午出发",
  "酒店换到客户公司附近",
];

let messageSeq = 0;
const nextId = () => `m${Date.now()}-${messageSeq++}`;

function friendlyError(err: unknown): string {
  if (!(err instanceof Error)) return "暂时没能连上行程服务，请稍后再试。";
  if (/HTTP 500|HTTP 502|HTTP 503|HTTP 504|Failed to fetch|fetch failed/i.test(err.message)) {
    return "行程服务暂时不可用，请稍后再试，或联系管理员确认服务状态。";
  }
  return `暂时没能连上行程服务：${err.message}`;
}

function layerLabel(layer: string): string {
  switch (layer) {
    case "l1_rule":
      return "L1 规则";
    case "l2_vector":
      return "L2 向量";
    case "jev":
      return "JEV 快判";
    case "llm":
      return "LLM 兜底";
    case "clarify":
      return "澄清";
    default:
      return layer;
  }
}

function layerClass(layer: string): string {
  switch (layer) {
    case "l1_rule":
      return "green";
    case "l2_vector":
      return "amber";
    case "jev":
      return "blue";
    default:
      return "";
  }
}

function routeLabel(route: string | null): string {
  switch (route) {
    case "task_coordination":
      return "任务协调";
    case "knowledge_qa":
      return "知识咨询";
    case "workflow":
      return "Workflow";
    case null:
      return "未指定";
    default:
      return route;
  }
}

function actionButtonClass(action: ContinuationAction): string {
  if (action === "confirm") return "btn primary";
  if (action === "cancel") return "btn red";
  if (action === "replan") return "btn amber";
  return "btn";
}

function deriveDraftTitle(content: string): string {
  const first =
    content
      .split("\n")
      .map((line) => line.trim())
      .find(Boolean) ?? "";
  const cleaned = first.replace(/^[#*\->\s]+/, "").replace(/\*\*/g, "").trim();
  if (!cleaned) return "差旅草案";
  return cleaned.length > 24 ? `${cleaned.slice(0, 24)}…` : cleaned;
}

function renderRichText(text: string) {
  const escapeHtml = (s: string) =>
    s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const inline = (s: string) =>
    escapeHtml(s)
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/\*([^*]+)\*/g, "<em>$1</em>");

  const blocks: string[] = [];
  let openList: "ul" | "ol" | null = null;
  const closeList = () => {
    if (openList) {
      blocks.push(`</${openList}>`);
      openList = null;
    }
  };

  for (const rawLine of text.split("\n")) {
    const line = rawLine.trimEnd();
    const trimmed = line.trim();
    if (!trimmed) {
      closeList();
      continue;
    }
    const heading = trimmed.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      closeList();
      const level = heading[1].length;
      blocks.push(`<h${level}>${inline(heading[2])}</h${level}>`);
      continue;
    }
    const ulItem = trimmed.match(/^[-*]\s+(.+)$/);
    if (ulItem) {
      if (openList !== "ul") {
        closeList();
        blocks.push("<ul>");
        openList = "ul";
      }
      blocks.push(`<li>${inline(ulItem[1])}</li>`);
      continue;
    }
    const olItem = trimmed.match(/^\d+[.、]\s*(.+)$/);
    if (olItem) {
      if (openList !== "ol") {
        closeList();
        blocks.push("<ol>");
        openList = "ol";
      }
      blocks.push(`<li>${inline(olItem[1])}</li>`);
      continue;
    }
    closeList();
    blocks.push(`<p>${inline(line)}</p>`);
  }
  closeList();

  return <div className="rich-text" dangerouslySetInnerHTML={{ __html: blocks.join("") }} />;
}

function RouteDecisionMeta({ decision }: { decision: RouteDecision }) {
  const confidence = Math.round(decision.confidence * 100);
  const continued = (decision.fallback_reason ?? "").startsWith(
    "active_agent_continuation"
  );
  return (
    <details className="route-meta">
      <summary className="route-meta-summary">
        <span className={`badge ${layerClass(decision.decision_layer)}`}>
          {layerLabel(decision.decision_layer)}
        </span>
        <span>{routeLabel(decision.route_type)}</span>
        <span className="mono">置信度 {confidence}%</span>
      </summary>
      <div className="route-meta-body">
        {continued && (
          <div className="notice">
            <span className="badge blue">⚡ 续跑</span>
            <span style={{ marginLeft: 8 }}>延续当前子 Agent，未重新做意图路由。</span>
          </div>
        )}
        <dl>
          <div>
            <dt>目标 Agent</dt>
            <dd>{decision.target_agent || "—"}</dd>
          </div>
          <div>
            <dt>判定层</dt>
            <dd>{layerLabel(decision.decision_layer)}</dd>
          </div>
          <div>
            <dt>置信度</dt>
            <dd>{confidence}%</dd>
          </div>
          <div>
            <dt>候选</dt>
            <dd>
              {decision.candidates.length
                ? decision.candidates
                    .map(
                      (candidate) =>
                        `${routeLabel(candidate.route_type)} ${Math.round(candidate.score * 100)}%`
                    )
                    .join(" / ")
                : "—"}
            </dd>
          </div>
        </dl>
        <div className="mono route-trace">trace_id · {decision.trace_id}</div>
      </div>
    </details>
  );
}

export default function NewTripView({
  session,
  onBack,
  onDecision,
  showObservability,
  onToggleObservability,
}: Props) {
  const [value, setValue] = useState("");
  const [loading, setLoading] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [continuation, setContinuation] = useState<ContinuationState | null>(null);
  const [acting, setActing] = useState(false);
  const [actionNotice, setActionNotice] = useState<string | null>(null);
  const [pendingAction, setPendingAction] = useState<ContinuationAction | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const planRef = useRef<PlanReference | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const publishDraft = async (content: string) => {
    const text = content.trim();
    if (!text) return;
    const current = planRef.current;
    const saved = await createDraftPlan({
      user_id: session.userId,
      session_id: session.sessionId,
      draft: text,
      plan_id: current?.plan_id,
      version: current ? current.version + 1 : undefined,
    });
    planRef.current = { plan_id: saved.plan_id, version: saved.version };
    recordRecentTrip({
      planId: saved.plan_id,
      title: saved.title || deriveDraftTitle(text),
      at: saved.created_at,
    });
  };

  const refreshContinuation = async () => {
    try {
      const state = await fetchContinuationState(session.sessionId, session.userId);
      setContinuation(state.active ? state : null);
    } catch {
      setContinuation(null);
    }
  };

  useEffect(() => {
    void refreshContinuation();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session.sessionId, session.userId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  const handleAction = async (action: ContinuationAction, instruction?: string) => {
    if (!continuation?.active || acting) return;
    if (action === "modify" && !instruction) {
      setPendingAction("modify");
      setActionNotice("请描述要修改的内容，然后发送；也可以点「重新规划」直接换一版。");
      inputRef.current?.focus();
      return;
    }
    setPendingAction(null);
    setActing(true);
    setActionNotice(null);
    try {
      const result = await resumeAction({
        session_id: session.sessionId,
        user_id: session.userId,
        agent_id: continuation.agent_id ?? "",
        task_id: continuation.task_id ?? "",
        action,
        click_id: crypto.randomUUID(),
        plan_id: continuation.plan_id ?? undefined,
        plan_version: continuation.plan_version ?? undefined,
        instruction,
      });
      if (result.status === "resumed") {
        const draft =
          typeof result.output?.draft === "string" ? result.output.draft : null;
        if (draft) {
          setMessages((prev) => [
            ...prev,
            { id: nextId(), role: "assistant", content: draft },
          ]);
          setActionNotice("已由当前子 Agent 重生成一版草案，请确认或继续修改。");
        } else {
          setActionNotice("已直达当前子 Agent 继续推进。");
        }
      } else {
        setActionNotice(
          result.duplicate
            ? "这次操作正在处理中，请稍候。"
            : result.reason || "当前阶段已变化，请重新描述需求。"
        );
      }
      await refreshContinuation();
    } catch (err) {
      setActionNotice(friendlyError(err));
    } finally {
      setActing(false);
    }
  };

  const submit = async (text?: string) => {
    const utterance = (text ?? value).trim();
    if (!utterance || loading) return;
    if (pendingAction) {
      const action = pendingAction;
      setPendingAction(null);
      setValue("");
      setMessages((prev) => [
        ...prev,
        { id: nextId(), role: "user", content: utterance },
      ]);
      await handleAction(action, utterance);
      return;
    }
    setValue("");
    setError(null);
    setActionNotice(null);

    const userMsg: ChatMessage = { id: nextId(), role: "user", content: utterance };
    const assistantMsg: ChatMessage = {
      id: nextId(),
      role: "assistant",
      content: "",
      pending: true,
    };
    setMessages((prev) => [...prev, userMsg, assistantMsg]);
    setLoading(true);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      for await (const event of streamAssistantMessage(
        session.userId,
        session.sessionId,
        utterance,
        undefined,
        controller.signal
      )) {
        if (event.event === "meta") {
          setMessages((prev) =>
            prev.map((m) => (m.id === assistantMsg.id ? { ...m, meta: event.data } : m))
          );
          if (event.data.decision) onDecision?.(event.data.decision);
        } else if (event.event === "delta") {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantMsg.id ? { ...m, content: m.content + event.data.content } : m
            )
          );
        } else if (event.event === "done") {
          const { content, ...meta } = event.data;
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantMsg.id
                ? { ...m, content, meta, pending: false }
                : m
            )
          );
          if (meta.mode === "draft") {
            void publishDraft(content).catch(() => {});
          }
          void refreshContinuation();
        }
      }
    } catch (err) {
      const aborted = err instanceof Error && err.name === "AbortError";
      setMessages((prev) =>
        prev.map((m) =>
          m.id === assistantMsg.id
            ? { ...m, pending: false, error: !aborted }
            : m
        )
      );
      if (!aborted) setError(friendlyError(err));
    } finally {
      setLoading(false);
      abortRef.current = null;
    }
  };

  const stop = () => {
    abortRef.current?.abort();
  };

  return (
    <>
      <div className="topbar">
        <div>
          <div className="kicker">NEW TRIP · 发起出差</div>
          <h2 className="page-title">{messages.length === 0 ? "这次出差，怎么安排？" : "行程安排中"}</h2>
          {messages.length === 0 && (
            <p className="page-sub">说清目的地、时间和预算，我会一步步帮你规划。</p>
          )}
        </div>
        <div className="topbar-actions">
          <button className="btn ghost" onClick={onToggleObservability}>
            {showObservability ? "收起观察" : "运行观察"}
          </button>
          <button className="btn ghost" onClick={onBack}>返回</button>
        </div>
      </div>

      <section className="chat-panel" aria-live="polite">
        {messages.length === 0 ? (
          <div className="chat-empty">
            <p className="chat-empty-hint">✨ 说清目的地、时间和预算，我会一步步帮你规划</p>
          </div>
        ) : (
          <div className="chat-messages">
            {messages.map((msg) =>
              msg.role === "user" ? (
                <div key={msg.id} className="chat-msg user">
                  <div className="chat-bubble user-bubble">{msg.content}</div>
                </div>
              ) : (
                <div key={msg.id} className="chat-msg assistant">
                  <div className="chat-bubble assistant-bubble-chat">
                    <div className="assistant-bubble-head">
                      <span className="badge gray">行程助手</span>
                    </div>
                    <div className="assistant-bubble-body">
                      {renderRichText(msg.content)}
                      {msg.pending && <span className="typing-caret" aria-hidden="true" />}
                    </div>
                    {msg.error && (
                      <p className="note">本次生成中断，请重新发送。</p>
                    )}
                    {msg.meta?.simulation && (
                      <p className="note">这是一份草案，尚未提交到真实业务系统。</p>
                    )}
                    {msg.meta?.decision && <RouteDecisionMeta decision={msg.meta.decision} />}
                  </div>
                </div>
              )
            )}
            <div ref={bottomRef} />
          </div>
        )}
        {continuation?.active && continuation.options.length > 0 && (
          <div className="chat-actions">
            <div className="chat-actions-head">
              <strong>当前阶段可用操作</strong>
              <span className="muted">点击直达子 Agent 续跑，无需重新路由</span>
            </div>
            <div className="chat-actions-row">
              {continuation.options.map((option) => (
                <button
                  key={option.action}
                  className={actionButtonClass(option.action)}
                  disabled={acting}
                  onClick={() => void handleAction(option.action)}
                >
                  {acting ? "处理中…" : option.label}
                </button>
              ))}
            </div>
            {actionNotice && <p className="note">{actionNotice}</p>}
          </div>
        )}
      </section>

      {error && (
        <div className="alert" role="alert">
          {error}
        </div>
      )}

      <section className="composer-panel">
        <div className="composer">
          <textarea
            id="trip-need"
            ref={inputRef}
            value={value}
            maxLength={500}
            aria-label="输入差旅需求"
            placeholder={
              pendingAction
                ? "描述要修改的内容，例如：把酒店换到客户公司附近"
                : "继续补充：目的地、出行时间、预算、住宿偏好……"
            }
            onChange={(event) => setValue(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void submit();
              }
            }}
          />
          <div className="composer-row">
            <div className="quick-asks">
              {(messages.length === 0 ? quickAsks : followUpAsks).map((item) => (
                <button key={item} className="quick-ask" onClick={() => void submit(item)}>
                  {item}
                </button>
              ))}
            </div>
            {loading ? (
              <button className="btn red" onClick={stop}>停止</button>
            ) : (
              <button
                className="btn primary"
                onClick={() => void submit()}
                disabled={!value.trim()}
              >
                发送
              </button>
            )}
          </div>
          <p className="note">行程由模型起草，不产生实时库存；预订仍按公司流程完成。</p>
        </div>
      </section>
    </>
  );
}