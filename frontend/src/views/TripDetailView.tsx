import { useCallback, useEffect, useState } from "react";
import ItinerarySheet from "../components/ItinerarySheet";
import ReviewPanel from "../components/ReviewPanel";
import {
  applyDemoAction,
  demoActions,
  demoPlan,
  demoReport,
  demoResultFor,
  demoTripTitle,
} from "../demo";
import {
  cancelReview,
  fetchActions,
  fetchPlan,
  resumeAction,
  reviewPlan,
} from "../api";
import { recordRecentTrip } from "../trips";
import type { SessionContext } from "../trips";
import type {
  ActionOption,
  ContinuationAction,
  ItineraryPlan,
  ReviewReport,
  TripTarget,
} from "../types";

interface Props {
  target: TripTarget;
  session: SessionContext;
  onBack: () => void;
}

const DEFAULT_AGENT_ID = "task_coordination_agent";
const DEFAULT_TASK_ID = "task-active";

function errorText(error: unknown, fallback = "请求失败"): string {
  if (error instanceof Error) {
    if (/503/.test(error.message)) return "行程服务暂时不可用，请稍后再试。";
    if (/404/.test(error.message)) return "没有找到这份行程，可能已被更新或删除。";
    return error.message;
  }
  return fallback;
}

export default function TripDetailView({ target, session, onBack }: Props) {
  const isDemo = target.mode === "demo";
  const [plan, setPlan] = useState<ItineraryPlan | null>(isDemo ? demoPlan : null);
  const [report, setReport] = useState<ReviewReport | null>(isDemo ? demoReport : null);
  const [phase, setPhase] = useState<string>(isDemo ? "changes_required" : "unknown");
  const [actions, setActions] = useState<ActionOption[]>(isDemo ? demoActions("changes_required") : []);
  const [message, setMessage] = useState<string | null>(null);
  const [planLoading, setPlanLoading] = useState(!isDemo);
  const [reviewing, setReviewing] = useState(false);
  const [acting, setActing] = useState(false);

  const title = isDemo
    ? demoTripTitle
    : plan?.preferences.title || "我的出差行程";

  useEffect(() => {
    if (isDemo) return;
    let cancelled = false;
    setPlanLoading(true);
    setMessage(null);
    fetchPlan(target.planId, session.userId, session.sessionId)
      .then(async (loaded) => {
        if (cancelled) return;
        setPlan(loaded);
        recordRecentTrip({
          planId: loaded.plan_id,
          title: loaded.preferences.title || "我的出差行程",
          at: new Date().toISOString(),
        });
        try {
          const loadedActions = await fetchActions(session.sessionId, session.userId);
          if (!cancelled) setActions(loadedActions);
        } catch {
          if (!cancelled) setActions([]);
        }
      })
      .catch((error) => {
        if (cancelled) return;
        setPlan(null);
        setActions([]);
        setMessage(errorText(error, "读取行程失败"));
      })
      .finally(() => {
        if (!cancelled) setPlanLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [isDemo, target.planId, session.userId, session.sessionId]);

  const handleReview = useCallback(async () => {
    if (!plan) return;
    setReviewing(true);
    setMessage(null);
    try {
      if (isDemo) {
        const next: ReviewReport =
          phase === "approved"
            ? { ...demoReport, outcome: "approved", issues: [], review_round: demoReport.review_round + 1 }
            : { ...demoReport, review_round: demoReport.review_round + 1 };
        setReport(next);
        setMessage("已重新检查这份示例行程。");
        return;
      }
      const result = await reviewPlan(
        plan.plan_id,
        session.userId,
        session.sessionId,
        plan.version
      );
      setReport(result);
      setMessage("行程检查已完成。");
    } catch (error) {
      setMessage(errorText(error, "检查失败"));
    } finally {
      setReviewing(false);
    }
  }, [plan, session, isDemo, phase]);

  const handleCancel = useCallback(async () => {
    if (!plan) return;
    setActing(true);
    try {
      if (isDemo) {
        setPhase("cancelled");
        setActions(demoActions("cancelled"));
        setMessage("已停止本次检查。");
        return;
      }
      await cancelReview(plan.plan_id, session.userId, session.sessionId, plan.version);
      setActions([]);
      setMessage("已停止本次检查。");
    } catch (error) {
      setMessage(errorText(error, "操作失败"));
    } finally {
      setActing(false);
    }
  }, [plan, session, isDemo]);

  const handleAction = useCallback(
    async (action: ContinuationAction) => {
      if (!plan) return;
      setActing(true);
      setMessage(null);
      try {
        if (isDemo) {
          const nextPhase = applyDemoAction(phase, action);
          const result = demoResultFor(action, phase);
          setPhase(nextPhase);
          setActions(demoActions(nextPhase));
          if (action === "confirm") setReport({ ...demoReport, outcome: "approved", issues: [] });
          if (action === "cancel") {
            setReport({ ...demoReport, outcome: "review_failed", error_code: "CANCELLED", error_message: "已停止检查" });
          }
          setMessage(result.reason ?? "已记录你的选择。");
          return;
        }
        const result = await resumeAction({
          session_id: session.sessionId,
          user_id: session.userId,
          agent_id: DEFAULT_AGENT_ID,
          task_id: DEFAULT_TASK_ID,
          action,
          click_id: crypto.randomUUID(),
          plan_id: plan.plan_id,
          plan_version: plan.version,
        });
        if (result.status === "fallback") {
          setMessage("行程状态已更新，请回到首页重新描述需求。");
        } else if (result.duplicate) {
          setMessage("这次操作正在处理中，请稍候。");
        } else {
          setMessage("已记录你的选择。");
        }
        try {
          const nextActions = await fetchActions(session.sessionId, session.userId);
          setActions(nextActions);
        } catch {
          setActions([]);
        }
      } catch (error) {
        setMessage(errorText(error, "操作失败"));
      } finally {
        setActing(false);
      }
    },
    [plan, session, isDemo, phase]
  );

  return (
    <>
      <div className="topbar">
        <div>
          <div className="kicker">{isDemo ? "SAMPLE TRIP" : "TRIP DETAIL"} · 行程详情</div>
          <h2 className="page-title">{title}</h2>
          <p className="page-sub">
            {isDemo ? "示例行程，不产生真实预订。" : `方案编号 ${target.planId}`}
          </p>
        </div>
        <button className="btn ghost" onClick={onBack}>返回</button>
      </div>

      {planLoading ? (
        <div className="empty">
          <strong>正在读取行程…</strong>
        </div>
      ) : plan ? (
        <div className="layout">
          <ItinerarySheet plan={plan} isDemo={isDemo} />
          <ReviewPanel
            report={report}
            actions={actions}
            loading={reviewing}
            actionLoading={acting}
            message={message}
            onReview={handleReview}
            onCancelReview={handleCancel}
            onAction={handleAction}
          />
        </div>
      ) : (
        <div className="empty">
          <strong>没有找到这份行程</strong>
          <p>{message || "可能已被更新或删除。"}</p>
          <button className="btn" onClick={onBack}>回到首页</button>
        </div>
      )}
    </>
  );
}