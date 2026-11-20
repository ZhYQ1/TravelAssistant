import type { CSSProperties } from "react";
import type { RouteDecision } from "../types";

const layerMeta: Record<string, { label: string; className: string }> = {
  l1_rule: { label: "L1 规则", className: "green" },
  l2_vector: { label: "L2 向量", className: "amber" },
  jev: { label: "JEV 快判", className: "blue" },
  llm: { label: "LLM 兜底", className: "gray" },
  clarify: { label: "澄清", className: "" },
};

const layerColorVar: Record<string, string> = {
  l1_rule: "var(--route-l1)",
  l2_vector: "var(--route-l2)",
  jev: "var(--route-jev)",
  llm: "var(--route-llm)",
};

const layerOrder = ["l1_rule", "l2_vector", "jev", "llm"] as const;

const agentMeta: Record<string, string> = {
  task_coordination_agent: "任务协调",
  knowledge_qa_agent: "知识咨询",
  workflow_agent: "Workflow",
};

function isContinuation(d: RouteDecision): boolean {
  return d.fallback_reason?.startsWith("active_agent_continuation") ?? false;
}

function routeName(route: RouteDecision["route_type"]): string {
  switch (route) {
    case "task_coordination":
      return "任务协调";
    case "knowledge_qa":
      return "知识咨询";
    case "workflow":
      return "Workflow";
    default:
      return "未指定";
  }
}

export default function ObservabilityPanel({
  decisions,
}: {
  decisions: RouteDecision[];
}) {
  const latest = decisions[decisions.length - 1];
  const counts: Record<string, number> = { l1_rule: 0, l2_vector: 0, jev: 0, llm: 0 };
  for (const d of decisions) {
    if (d.decision_layer in counts) counts[d.decision_layer] += 1;
  }

  return (
    <div className="obs-panel">
      <div className="obs-head">
        <div>
          <div className="kicker">运行观察</div>
          <h2>Agent 运行流</h2>
        </div>
      </div>

      <section className="card">
        <div className="card-head">
          <h2>最近一次路由判定</h2>
        </div>
        <div className="card-body">
          {latest ? (
            <>
              <div className="obs-latest">
                <span className={`badge ${layerMeta[latest.decision_layer]?.className ?? ""}`}>
                  {layerMeta[latest.decision_layer]?.label ?? latest.decision_layer}
                </span>
                <strong className="obs-route">{routeName(latest.route_type)}</strong>
                <span className="mono obs-confidence">{Math.round(latest.confidence * 100)}%</span>
              </div>
              <dl className="obs-meta">
                <div>
                  <dt>目标 Agent</dt>
                  <dd>{latest.target_agent || "—"}</dd>
                </div>
                <div>
                  <dt>单次耗时</dt>
                  <dd>{latest.latency_ms != null ? `${Math.round(latest.latency_ms)} ms` : "—"}</dd>
                </div>
                <div>
                  <dt>trace_id</dt>
                  <dd className="mono">{latest.trace_id}</dd>
                </div>
              </dl>
            </>
          ) : (
            <p className="note">暂无路由判定，发送一条消息后可见。</p>
          )}
        </div>
      </section>

      <section className="card">
        <div className="card-head">
          <h2>判定层分布</h2>
          <span className="badge">{decisions.length} 次</span>
        </div>
        <div className="card-body">
          {decisions.length === 0 ? (
            <p className="note">发送一条消息后，这里会展示 L1/L2/JEV/LLM 的命中分布。</p>
          ) : (
            <>
              <div className="route-stack" role="img" aria-label="判定层命中分布">
                {layerOrder.map((key) => (
                  <span
                    key={key}
                    className={`route-segment ${layerMeta[key].className}`}
                    style={{ width: `${(counts[key] / decisions.length) * 100}%` }}
                    title={`${layerMeta[key].label} ${counts[key]} 次`}
                  />
                ))}
              </div>
              <div className="obs-layers">
                {layerOrder.map((key) => (
                  <div
                    className="obs-layer-row"
                    key={key}
                    style={{ "--layer-color": layerColorVar[key] } as CSSProperties}
                  >
                    <span className={`badge ${layerMeta[key].className}`}>{layerMeta[key].label}</span>
                    <span className="obs-layer-count">{counts[key]} 次</span>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </section>

      <section className="card">
        <div className="card-head">
          <h2>Agent 链路</h2>
        </div>
        <div className="card-body">
          <div className="obs-chain">
            <span className="obs-node obs-node-done">主控</span>
            <span className="obs-arrow">→</span>
            {latest?.target_agent ? (
              <span className="obs-node obs-node-active" title={latest.target_agent}>
                {agentMeta[latest.target_agent] ?? latest.target_agent}
              </span>
            ) : (
              <span className="obs-node">待路由</span>
            )}
            {latest && isContinuation(latest) && (
              <span className="obs-continue" title="直达续跑：未重新调度，延续当前子 Agent">⚡ 续跑</span>
            )}
          </div>
          <p className="obs-last-decision">
            {latest
              ? `${layerMeta[latest.decision_layer]?.label ?? latest.decision_layer} · 置信度 ${Math.round(latest.confidence * 100)}%${latest.latency_ms != null ? ` · ${Math.round(latest.latency_ms)}ms` : ""}`
              : "暂无链路活动，发送一条消息后可见。"}
          </p>
        </div>
      </section>
    </div>
  );
}