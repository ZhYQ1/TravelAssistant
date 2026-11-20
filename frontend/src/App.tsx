import { useState } from "react";
import HomeView from "./views/HomeView";
import NewTripView from "./views/NewTripView";
import TripDetailView from "./views/TripDetailView";
import SettingsView from "./views/SettingsView";
import MetricsView from "./views/MetricsView";
import ObservabilityPanel from "./views/ObservabilityPanel";
import { loadSession } from "./trips";
import type { SessionContext } from "./trips";
import type { ApiSettings, RouteDecision, TripTarget } from "./types";

type View = "home" | "new" | "detail" | "metrics" | "settings";

const DEFAULT_SETTINGS: ApiSettings = {
  mode: "live",
  user_id: "user-001",
  session_id: "session-001",
  plan_id: "",
  version: "",
  agent_id: "task_coordination_agent",
  task_id: "",
};

export default function App() {
  const [view, setView] = useState<View>("home");
  const [session] = useState<SessionContext>(loadSession);
  const [target, setTarget] = useState<TripTarget | null>(null);
  const [settings, setSettings] = useState<ApiSettings>(DEFAULT_SETTINGS);
  const [routeHistory, setRouteHistory] = useState<RouteDecision[]>([]);
  const [showObservability, setShowObservability] = useState(true);

  const recordDecision = (decision: RouteDecision) => {
    setRouteHistory((prev) => [...prev.slice(-19), decision]);
  };

  const openTrip = (trip: TripTarget) => {
    setTarget(trip);
    setView("detail");
  };

  const goHome = () => setView("home");

  return (
    <div className={view === "new" && showObservability ? "app app-three" : "app"}>
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">
            <span className="brand-seal">行</span>
            <div>
              <h1>行知差旅</h1>
              <p>企业差旅 · 一张行程单管到底</p>
            </div>
          </div>
          <span className={`mode-badge ${settings.mode}`}>
            {settings.mode === "demo" ? "演示数据" : "真实 API"}
          </span>
        </div>
        <nav className="nav" aria-label="主导航">
          <button className={view === "home" ? "active" : ""} onClick={goHome}>
            我的行程
          </button>
          <button className={view === "new" ? "active" : ""} onClick={() => setView("new")}>
            发起出差
          </button>
          <div className="nav-label nav-label-secondary">运行观察</div>
          <button className={view === "metrics" ? "active" : ""} onClick={() => setView("metrics")}>
            运行概览
          </button>
          <button className={view === "settings" ? "active" : ""} onClick={() => setView("settings")}>
            API 设置
          </button>
        </nav>
        <div className="sidebar-note">
          <strong>出票与住宿</strong>
          <p>行程由模型起草，最终预订仍按公司差旅流程完成。</p>
        </div>
      </aside>
      <main className="main">
        {view === "home" && (
          <HomeView
            session={session}
            onOpenTrip={openTrip}
            onNew={() => setView("new")}
          />
        )}
        {view === "new" && (
          <NewTripView
            session={session}
            onBack={goHome}
            onDecision={recordDecision}
            showObservability={showObservability}
            onToggleObservability={() => setShowObservability((v) => !v)}
          />
        )}
        {view === "detail" && target && (
          <TripDetailView target={target} session={session} onBack={goHome} />
        )}
        {view === "metrics" && <MetricsView settings={settings} />}
        {view === "settings" && <SettingsView settings={settings} onChange={setSettings} />}
      </main>
      {view === "new" && showObservability && (
        <aside className="observability">
          <ObservabilityPanel decisions={routeHistory} />
        </aside>
      )}
    </div>
  );
}