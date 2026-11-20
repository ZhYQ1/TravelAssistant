import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import { demoMetrics } from "../demo";
import { ApiError, fetchMetrics } from "../api";
import type { ApiSettings, MetricsSnapshot } from "../types";

const ratioMetrics = [
  { key: "route_llm_fallback_rate", label: "LLM 回退率", direction: "down", sampleKey: "route_total" },
  { key: "review_first_pass_rate", label: "审核首轮通过", direction: "up", sampleKey: "review_total" },
  { key: "review_read_failure_rate", label: "审核读取失败", direction: "down", sampleKey: "review_total" },
  { key: "continuation_direct_rate", label: "续跑直达率", direction: "up", sampleKey: "continuation_total" },
] as const;

const routeLayers = [
  { key: "route_l1_rule", label: "L1 规则", color: "green" },
  { key: "route_l2_vector", label: "L2 向量", color: "amber" },
  { key: "route_jev", label: "JEV 快判", color: "blue" },
  { key: "route_llm", label: "LLM 兜底", color: "gray" },
] as const;

const LATENCY_TARGET_MS = 800;
const LATENCY_MAX_MS = 4000;

const numberFormat = new Intl.NumberFormat("zh-CN");

function ratio(n: number) {
  return `${(n * 100).toFixed(1)}%`;
}

function ratioTone(value: number, direction: "up" | "down"): "good" | "warn" | "bad" {
  if (direction === "up") {
    if (value >= 0.8) return "good";
    if (value >= 0.5) return "warn";
    return "bad";
  }
  if (value <= 0.1) return "good";
  if (value <= 0.2) return "warn";
  return "bad";
}

function ratioHint(key: string, tone: string): string | null {
  if (tone !== "bad" && tone !== "warn") return null;
  switch (key) {
    case "route_llm_fallback_rate":
      return tone === "bad"
        ? "⚠ 回退率偏高，建议排查 L1/L2/JEV 命中"
        : "回退率偏高，关注 L1/L2/JEV 命中";
    case "review_first_pass_rate":
      return "首轮通过率偏低，请关注审核规则覆盖";
    case "review_read_failure_rate":
      return "读取失败偏高，请检查 Redis 与方案键";
    case "continuation_direct_rate":
      return "续跑直达率偏低，关注活跃子 Agent 状态";
    default:
      return null;
  }
}

function count(n: number) {
  return numberFormat.format(n);
}

function ms(n: number) {
  return `${numberFormat.format(Math.round(n))} ms`;
}

function MetricCard({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div className="metric-card">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </div>
  );
}

function UpdatedAgo({ at }: { at: number | null }) {
  const [, force] = useState(0);
  useEffect(() => {
    const timer = window.setInterval(() => force((n) => n + 1), 1000);
    return () => window.clearInterval(timer);
  }, []);
  if (at === null) return null;
  const seconds = Math.max(0, Math.round((Date.now() - at) / 1000));
  const text = seconds < 60 ? `${seconds}s 前` : `${Math.floor(seconds / 60)}m 前`;
  return <span className="updated-ago">更新于 {text}</span>;
}

function useMetricsPoll(load: (silent: boolean) => Promise<boolean>, enabled: boolean, intervalMs = 20000) {
  useEffect(() => {
    if (!enabled) return;
    let timer: number | undefined;
    let fails = 0;

    const tick = async () => {
      const ok = await load(true);
      fails = ok ? 0 : fails + 1;
      if (!document.hidden) {
        timer = window.setTimeout(tick, intervalMs * Math.min(2 ** fails, 8));
      }
    };

    const onVisibility = () => {
      if (document.hidden) {
        if (timer !== undefined) window.clearTimeout(timer);
      } else {
        void tick();
      }
    };

    document.addEventListener("visibilitychange", onVisibility);
    timer = window.setTimeout(tick, intervalMs);

    return () => {
      if (timer !== undefined) window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [load, enabled, intervalMs]);
}

export default function MetricsView({ settings }: { settings: ApiSettings }) {
  const [metrics, setMetrics] = useState<MetricsSnapshot | null>(settings.mode === "demo" ? demoMetrics : null);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);
  const dismissTimer = useRef<number | null>(null);

  const showToast = useCallback((msg: string) => {
    setMessage(msg);
    if (dismissTimer.current !== null) window.clearTimeout(dismissTimer.current);
    dismissTimer.current = window.setTimeout(() => setMessage(null), 3000);
  }, []);

  const load = useCallback(async (silent = false): Promise<boolean> => {
    if (settings.mode === "demo") {
      setMetrics(demoMetrics);
      setLastUpdated(Date.now());
      showToast("演示快照，不代表真实模型运行结果。");
      return true;
    }
    if (!silent) setLoading(true);
    if (!silent) setMessage(null);
    try {
      setMetrics(await fetchMetrics());
      setLastUpdated(Date.now());
      showToast("真实进程累计快照已刷新。");
      return true;
    } catch (error) {
      setMetrics(null);
      setMessage(
        error instanceof ApiError && error.status === 503
          ? `业务依赖未就绪（503）：${error.detail}`
          : `指标读取失败：${error instanceof Error ? error.message : String(error)}`
      );
      return false;
    } finally {
      if (!silent) setLoading(false);
    }
  }, [settings.mode, showToast]);

  useEffect(() => {
    void load();
  }, [load]);

  useMetricsPoll(load, settings.mode === "live");

  useEffect(() => () => {
    if (dismissTimer.current !== null) window.clearTimeout(dismissTimer.current);
  }, []);

  const latencyRows = metrics ? Object.entries(metrics.latency_ms) : [];
  const tokenTotal = metrics
    ? Object.values(metrics.token_usage).reduce((sum, value) => sum + (value.total_tokens ?? 0), 0)
    : 0;
  const routes = metrics
    ? routeLayers.map((layer) => ({
        ...layer,
        value: metrics.counters[layer.key] ?? 0,
      }))
    : [];
  const routeTotal = metrics?.counters.route_total ?? 0;
  const routeLayersTotal = routes.reduce((sum, item) => sum + item.value, 0);
  const messageIsError = message !== null && (message.includes("失败") || message.includes("503"));

  return (
    <>
      <div className="topbar">
        <div>
          <div className="kicker">运行观测 / PROCESS SNAPSHOT</div>
          <h2 className="page-title">运行指标</h2>
          <p className="page-sub">
            {settings.mode === "demo" ? "演示数据 · 用于查看指标结构与分布" : "当前 FastAPI 进程累计值 · 非历史趋势"}
          </p>
        </div>
        <div className="topbar-actions">
          <UpdatedAgo at={lastUpdated} />
          <button className="btn ghost" onClick={() => void load()} disabled={loading}>
            {loading ? "刷新中…" : "刷新指标"}
          </button>
        </div>
      </div>

      {message && messageIsError && (
        <div className="alert" style={{ marginBottom: 16 }} role="alert">
          {message}
        </div>
      )}
      {message && !messageIsError && (
        <div className="toast success" role="status">
          <span aria-hidden="true">✓</span>
          {message}
        </div>
      )}

      {metrics ? (
        <>
          <div className="metric-grid">
            <MetricCard label="路由请求" value={count(routeTotal)} detail="进程累计" />
            <MetricCard label="方案审核" value={count(metrics.counters.review_total ?? 0)} detail="已处理请求" />
            <MetricCard label="任务续跑" value={count(metrics.counters.continuation_total ?? 0)} detail="累计触发" />
            <MetricCard label="Token 合计" value={count(tokenTotal)} detail="输入 + 输出" />
          </div>

          <div className="metrics-panels">
            <section className="card">
              <div className="card-head">
                <div>
                  <h2>路由判定分布</h2>
                  <p className="panel-caption">按判定层统计 · {count(routeTotal)} 次请求</p>
                </div>
                <span className="badge">累计</span>
              </div>
              <div className="card-body">
                {routeLayersTotal > 0 ? (
                  <>
                    <div className="route-stack" role="img" aria-label="各判定层路由请求占比">
                      {routes.map((item) => (
                        <span
                          key={item.key}
                          className={`route-segment ${item.color}`}
                          style={{ width: `${(item.value / routeLayersTotal) * 100}%` }}
                          title={`${item.label} ${count(item.value)} 次`}
                        />
                      ))}
                    </div>
                    <div className="route-legend">
                      {routes.map((item) => (
                        <div className="route-legend-item" key={item.key}>
                          <span className={`legend-dot ${item.color}`} />
                          <span>{item.label}</span>
                          <strong>{count(item.value)}</strong>
                          <small>{ratio(item.value / routeLayersTotal)}</small>
                        </div>
                      ))}
                    </div>
                    {routeLayersTotal !== routeTotal && (
                      <p className="note">
                        判定层统计合计 {count(routeLayersTotal)} 次；与路由总数的差值可能来自未分类或其他判定。
                      </p>
                    )}
                  </>
                ) : (
                  <div className="empty">
                    <strong>暂无路由判定样本</strong>
                    <p>发送消息后，此处展示 L1/L2/JEV/LLM 命中分布。</p>
                  </div>
                )}
              </div>
            </section>

            <section className="card">
              <div className="card-head">
                <div>
                  <h2>接口延迟</h2>
                  <p className="panel-caption">平均耗时与 P95 · 毫秒</p>
                </div>
              </div>
              <div className="card-body latency-list">
                {latencyRows.length > 0 ? latencyRows.map(([name, value]) => {
                  const overTarget = value.p95 > LATENCY_TARGET_MS;
                  return (
                    <div className={`latency-row${overTarget ? " over-target" : ""}`} key={name}>
                      <div className="latency-heading">
                        <strong>{name}</strong>
                        <span>
                          {count(value.count)} 次 · <em className="latency-target-label">目标 {ms(LATENCY_TARGET_MS)}</em>
                        </span>
                      </div>
                      {name === "route_llm" && (
                        <p className="note">当前样本可能全部由 LLM 兜底产生。</p>
                      )}
                      <div
                        className="latency-track"
                        role="img"
                        aria-label={`${name} 平均 ${value.mean} 毫秒，P95 ${value.p95} 毫秒`}
                        style={{ "--target-pct": `${(LATENCY_TARGET_MS / LATENCY_MAX_MS) * 100}%` } as CSSProperties}
                      >
                        <i style={{ width: `${Math.min(100, (value.p95 / LATENCY_MAX_MS) * 100)}%` }} />
                        <b style={{ left: `${Math.min(100, (value.mean / LATENCY_MAX_MS) * 100)}%` }} />
                        <s className="latency-target" style={{ left: `${(LATENCY_TARGET_MS / LATENCY_MAX_MS) * 100}%` }} />
                      </div>
                      <div className="latency-values">
                        <span>均值 <strong className="num">{ms(value.mean)}</strong></span>
                        <span>P95 <strong className="num">{ms(value.p95)}</strong></span>
                      </div>
                    </div>
                  );
                }) : <div className="empty">暂无延迟样本。</div>}
                <div className="chart-legend">
                  <span><i className="legend-bar" />P95 范围</span>
                  <span><i className="legend-marker" />平均值</span>
                  <span><i className="legend-target" />目标线 {ms(LATENCY_TARGET_MS)}</span>
                </div>
              </div>
            </section>
          </div>

          <h3 className="section-title">运行质量</h3>
          <div className="metric-grid ratio-grid">
            {ratioMetrics.map(({ key, label, direction, sampleKey }) => {
              const value = metrics.ratios[key];
              const n = metrics.counters[sampleKey] ?? 0;
              const insufficient = n < 10;
              const tone = insufficient ? "insufficient" : ratioTone(value, direction);
              return (
                <div className={`ratio-card ${tone}`} key={key}>
                  <div className="ratio-heading">
                    <span>{label}</span>
                    <strong className={insufficient ? "muted" : ""}>{insufficient ? "—" : ratio(value)}</strong>
                  </div>
                  {insufficient ? (
                    <p className="sample-note">样本不足（n={n}）</p>
                  ) : (
                    <>
                      <div className="bar" role="img" aria-label={`${label} ${ratio(value)}`}>
                        <i style={{ width: `${Math.min(100, Math.max(0, value * 100))}%` }} />
                      </div>
                      {ratioHint(key, tone) && (
                        <p className={`ratio-hint ${tone}`}>{ratioHint(key, tone)}</p>
                      )}
                    </>
                  )}
                </div>
              );
            })}
          </div>

          <h3 className="section-title">Token 用量</h3>
          <div className="card">
            <div className="card-head">
              <div>
                <h2>模型调用明细</h2>
                <p className="panel-caption">仅展示当前进程可用的累计计量</p>
              </div>
            </div>
            <div className="card-body table-wrap">
              <table className="table">
                <thead><tr><th>调用 / 模型</th><th>输入 Token</th><th>输出 Token</th><th>合计</th></tr></thead>
                <tbody>
                  {Object.entries(metrics.token_usage).map(([name, value]) => (
                    <tr key={name}>
                      <td className="mono">{name}</td>
                      <td>{count(value.prompt_tokens ?? 0)}</td>
                      <td>{count(value.completion_tokens ?? 0)}</td>
                      <td><strong>{count(value.total_tokens ?? 0)}</strong></td>
                    </tr>
                  ))}
                  {Object.keys(metrics.token_usage).length === 0 && (
                    <tr><td colSpan={4} className="muted">暂无 Token 计量。</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </>
      ) : (
        <div className="empty">暂无指标，请刷新或检查后端依赖配置。</div>
      )}
    </>
  );
}