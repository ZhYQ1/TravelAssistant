import { useState } from "react";
import { fetchHealth, ApiError } from "../api";
import type { ApiSettings, DataMode } from "../types";

interface Props {
  settings: ApiSettings;
  onChange: (settings: ApiSettings) => void;
}

export default function SettingsView({ settings, onChange }: Props) {
  const [health, setHealth] = useState<{ ok: boolean; detail: string } | null>(null);
  const [checking, setChecking] = useState(false);
  const [pendingMode, setPendingMode] = useState<DataMode | null>(null);

  const update = (key: keyof ApiSettings, value: string) => onChange({ ...settings, [key]: value });

  const checkHealth = async () => {
    setChecking(true);
    try {
      const result = await fetchHealth();
      setHealth({
        ok: result.status === "ok",
        detail: "healthz 返回正常，但不代表业务依赖（Embedding / Redis / LLM）已就绪。",
      });
    } catch (error) {
      setHealth({
        ok: false,
        detail: error instanceof ApiError
          ? `HTTP ${error.status}：${error.detail}`
          : `${error instanceof Error ? error.message : String(error)}`,
      });
    } finally {
      setChecking(false);
    }
  };

  const confirmSwitch = () => {
    if (pendingMode) onChange({ ...settings, mode: pendingMode });
    setPendingMode(null);
  };

  return (
    <>
      <div className="topbar">
        <div>
          <div className="kicker">连接配置</div>
          <h2 className="page-title">API 设置</h2>
          <p className="page-sub">前端通过 /api 同源代理访问 FastAPI；模型密钥仍由后端 .env 管理。</p>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 18 }}>
        <div className="card-head"><h2>数据来源</h2></div>
        <div className="card-body">
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            {(["demo", "live"] as DataMode[]).map((mode) => (
              <button
                key={mode}
                className={settings.mode === mode ? "btn primary" : "btn ghost"}
                onClick={() => { if (settings.mode !== mode) setPendingMode(mode); }}
              >
                {mode === "demo" ? "演示数据" : "真实 API"}
              </button>
            ))}
          </div>
          {pendingMode && (
            <div className="confirm-bar" role="alertdialog" aria-label="确认切换数据源">
              <p>
                切换为 <strong>{pendingMode === "demo" ? "演示数据" : "真实 API"}</strong> 会清空当前工作台中的方案、审核与动作状态，避免演示数据混入真实视图。是否继续？
              </p>
              <div className="confirm-actions">
                <button className="btn red" onClick={confirmSwitch}>确认切换</button>
                <button className="btn" onClick={() => setPendingMode(null)}>取消</button>
              </div>
            </div>
          )}
          <p className="note">切换模式会清空当前工作台中的方案、审核和动作状态，避免演示数据混入真实视图。</p>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 18 }}>
        <div className="card-head"><h2>业务上下文</h2></div>
        <div className="card-body">
          <div className="form-row">
            <label>用户 ID<input value={settings.user_id} onChange={(e) => update("user_id", e.target.value)} /></label>
            <label>会话 ID<input value={settings.session_id} onChange={(e) => update("session_id", e.target.value)} /></label>
            <label>方案 ID<input value={settings.plan_id} onChange={(e) => update("plan_id", e.target.value)} /></label>
            <label>版本（留空读取最新）<input value={settings.version} onChange={(e) => update("version", e.target.value)} /></label>
            <label>活跃 Agent ID<input value={settings.agent_id} onChange={(e) => update("agent_id", e.target.value)} /></label>
            <label>任务 ID<input value={settings.task_id} onChange={(e) => update("task_id", e.target.value)} /></label>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-head"><h2>连通性</h2></div>
        <div className="card-body">
          <button className="btn primary" onClick={checkHealth} disabled={checking}>{checking ? "检查中…" : "检查健康接口"}</button>
          {health && (
            <div className="health-result" style={{ marginTop: 12 }}>
              <span className={`badge ${health.ok ? "green" : "red"}`}>{health.ok ? "在线" : "不可达"}</span>
              <span>{health.detail}</span>
            </div>
          )}
          <div className="notice" style={{ marginTop: 12 }}>
            缺 Embedding/Redis/LLM 配置时，业务接口会返回 503；页面不会自动切换到演示模式，也不会伪造成功结果。
          </div>
        </div>
      </div>
    </>
  );
}