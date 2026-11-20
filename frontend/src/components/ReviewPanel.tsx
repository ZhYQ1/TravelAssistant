import type { ActionOption, ContinuationAction, ReviewReport } from "../types";

interface Props {
  report: ReviewReport | null;
  actions: ActionOption[];
  loading: boolean;
  actionLoading: boolean;
  message: string | null;
  onReview: () => void;
  onCancelReview: () => void;
  onAction: (action: ContinuationAction) => void;
}

function severityClass(severity: string) {
  if (severity === "critical" || severity === "high") return "red";
  if (severity === "medium") return "amber";
  return "green";
}

function severityLabel(severity: string) {
  if (severity === "critical") return "优先处理";
  if (severity === "high") return "建议调整";
  if (severity === "medium") return "可以优化";
  return "供参考";
}

function dimensionLabel(dimension: string) {
  if (dimension === "experience") return "出行体验";
  if (dimension === "resilience") return "行程稳定性";
  return "预算与偏好";
}

function buttonClass(action: ContinuationAction) {
  if (action === "confirm") return "btn primary";
  if (action === "cancel") return "btn red";
  if (action === "replan") return "btn amber";
  return "btn";
}

export default function ReviewPanel({
  report,
  actions,
  loading,
  actionLoading,
  message,
  onReview,
  onCancelReview,
  onAction,
}: Props) {
  const outcomeText =
    report?.outcome === "approved"
      ? "检查通过"
      : report?.outcome === "review_failed"
        ? "检查未完成"
        : "有待调整";
  const messageIsError = message !== null && /失败|错误|不可用|更新|请回到|找不到/.test(message);

  return (
    <section className="card">
      <div className="card-head">
        <h2>出发前检查</h2>
        <span className={`badge ${report?.outcome === "approved" ? "green" : report?.outcome === "review_failed" ? "red" : "amber"}`}>
          {outcomeText}
        </span>
      </div>
      <div className="card-body">
        <button className="btn primary" onClick={onReview} disabled={loading} style={{ width: "100%", marginBottom: 12 }}>
          {loading ? "正在检查…" : "重新检查方案"}
        </button>
        {message && (
          <div className={messageIsError ? "alert" : "notice"} role={messageIsError ? "alert" : "status"}>
            {message}
          </div>
        )}
        {report && (
          <>
            <div className="review-intro">
              <strong>{report.issues.length > 0 ? `发现 ${report.issues.length} 项可以调整` : "暂未发现需要调整的地方"}</strong>
              <p>{report.issues.length > 0 ? "先处理优先级较高的建议，再确认行程。" : "确认行程后仍需按公司流程完成预订。"}</p>
            </div>
            {report.outcome === "review_failed" && (
              <div className="alert">
                {report.error_message || "暂时无法完成检查，请稍后重试。"}
              </div>
            )}
            {report.issues.length === 0 && report.outcome !== "review_failed" && (
              <div className="empty">行程检查已完成，没有发现需要处理的建议。</div>
            )}
            {report.issues.map((issue) => (
              <article key={issue.issue_id} className="issue">
                <div className="issue-top">
                  <h3 className="issue-title">
                    <span className={`badge ${severityClass(issue.severity)}`}>{severityLabel(issue.severity)}</span>
                    {issue.message}
                  </h3>
                </div>
                <p className="issue-desc">{dimensionLabel(issue.dimension)}</p>
                <details>
                  <summary className="muted" style={{ cursor: "pointer", marginTop: 4 }}>查看检查依据</summary>
                  <pre className="evidence">{JSON.stringify(issue.evidence, null, 2)}</pre>
                </details>
              </article>
            ))}
          </>
        )}
        <div className="actions">
          <div className="actions-head">
            <strong>当前阶段可用操作</strong>
            <span className="muted">点击直达子 Agent 续跑，无需重新路由</span>
          </div>
          {actions.map((option) => (
            <button
              key={option.action}
              className={buttonClass(option.action)}
              disabled={actionLoading}
              onClick={() => onAction(option.action)}
            >
              {actionLoading ? "处理中…" : option.label}
            </button>
          ))}
          {actions.length === 0 && <div className="empty">目前没有需要你处理的事项。</div>}
          <p className="note">确认只会继续当前流程，不会直接完成订票。</p>
          <details className="secondary-actions">
            <summary>更多操作</summary>
            <button className="btn red" onClick={onCancelReview} disabled={actionLoading}>停止本次检查</button>
          </details>
        </div>
      </div>
    </section>
  );
}