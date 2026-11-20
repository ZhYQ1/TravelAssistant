import { useEffect, useState } from "react";
import { fetchPlans } from "../api";
import { demoPlan, demoRouteCities, demoTripTitle } from "../demo";
import { listRecentTrips } from "../trips";
import type { RecentTrip, SessionContext } from "../trips";
import type { TripTarget } from "../types";

interface Props {
  session: SessionContext;
  onOpenTrip: (target: TripTarget) => void;
  onNew: () => void;
}

function formatAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(diff)) return iso;
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  return `${Math.floor(hours / 24)} 天前`;
}

function TripCard({
  title,
  subtitle,
  meta,
  stamp,
  onClick,
}: {
  title: string;
  subtitle: string;
  meta: string;
  stamp?: string;
  onClick: () => void;
}) {
  return (
    <button className="trip-card" onClick={onClick}>
      <div className="trip-card-route">
        <span className="trip-card-title">{title}</span>
        {stamp && <span className="stamp">{stamp}</span>}
      </div>
      <p className="trip-card-sub">{subtitle}</p>
      <div className="trip-card-meta">
        <span className="mono">{meta}</span>
        <span className="trip-card-cta">查看行程 →</span>
      </div>
    </button>
  );
}

export default function HomeView({ session, onOpenTrip, onNew }: Props) {
  const [recent, setRecent] = useState<RecentTrip[]>(() => listRecentTrips());

  useEffect(() => {
    let cancelled = false;
    fetchPlans(session.userId, session.sessionId)
      .then((items) => {
        if (cancelled) return;
        setRecent(
          items.map((item) => ({
            planId: item.plan_id,
            title: item.title || "我的出差行程",
            at: item.created_at,
          }))
        );
      })
      .catch(() => {
        // 后端不可用时退化为本地最近记录，保证列表仍可查看
      });
    return () => {
      cancelled = true;
    };
  }, [session.userId, session.sessionId]);

  return (
    <>
      <section className="hero">
        <div className="hero-copy">
          <div className="kicker">XINGZHI TRAVEL · 企业差旅协同</div>
          <h2 className="hero-title">把出差，交给一张行程单</h2>
          <p className="hero-sub">说清目的地、时间与预算，行程、检查、确认都在一处完成。</p>
          <button className="btn primary hero-cta" onClick={onNew}>
            发起出差
          </button>
        </div>
        <div className="hero-ticket" aria-hidden="true">
          <div className="hero-ticket-inner">
            <span className="hero-ticket-code mono">TRIP / 行知</span>
            <div className="hero-ticket-cities">
              <span>出发</span>
              <i />
              <span>抵达</span>
            </div>
            <div className="hero-ticket-foot mono">一张行程单 · 管到底</div>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="section-head">
          <h3>进行中的行程</h3>
          <span className="section-hint">最近浏览</span>
        </div>
        {recent.length > 0 ? (
          <div className="trip-cards">
            {recent.map((trip: RecentTrip) => (
              <TripCard
                key={trip.planId}
                title={trip.title}
                subtitle={`最近浏览 · ${formatAgo(trip.at)}`}
                meta={trip.planId}
                onClick={() => onOpenTrip({ mode: "real", planId: trip.planId })}
              />
            ))}
          </div>
        ) : (
          <div className="empty">
            <strong>还没有行程</strong>
            <p>从一次出差开始，几句话就能生成第一张行程单。</p>
            <button className="btn" onClick={onNew}>发起出差</button>
          </div>
        )}
      </section>

      <section className="section">
        <div className="section-head">
          <h3>体验示例</h3>
          <span className="section-hint">不产生真实预订</span>
        </div>
        <div className="trip-cards">
          <TripCard
            title={demoTripTitle}
            subtitle={`${demoRouteCities.from} → ${demoRouteCities.to} · ${demoPlan.days.length} 天 · 预算 ¥${demoPlan.preferences.budget_cny}`}
            meta={`创建于 ${demoPlan.created_at.slice(0, 10)}`}
            stamp="示例"
            onClick={() => onOpenTrip({ mode: "demo", planId: demoPlan.plan_id })}
          />
        </div>
      </section>
    </>
  );
}