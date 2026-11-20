import { useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { demoRouteCities, demoTripTitle } from "../demo";
import type { ItineraryPlan } from "../types";

interface Props {
  plan: ItineraryPlan | null;
  isDemo: boolean;
}

function time(value: string) {
  return value.slice(11, 16);
}

function money(value: number) {
  return `¥${value.toLocaleString("zh-CN")}`;
}

function normalizeDraftMarkdown(text: string): string {
  // 模型常把加粗写成 `** 标题 **`，标记内侧带空格时 Markdown 不会解析为加粗，
  // 会原样露出 `*`。这里只处理行首开标记与行尾闭标记，避免误伤正文中的空格。
  return text
    .split("\n")
    .map((line) =>
      line
        .replace(/^(\s*\*\*)[ \t]+/, "$1")
        .replace(/[ \t]+(\*\*)\s*$/, "$1")
    )
    .join("\n");
}

function dateRangeLabel(start: string, days: number): string {
  const startDate = new Date(`${start}T00:00:00`);
  if (Number.isNaN(startDate.getTime())) return start;
  const format = (value: Date) =>
    `${String(value.getMonth() + 1).padStart(2, "0")}/${String(value.getDate()).padStart(2, "0")}`;
  if (days <= 1) return format(startDate);
  const end = new Date(startDate.getTime() + (days - 1) * 86_400_000);
  return `${format(startDate)} – ${format(end)}`;
}

export default function ItinerarySheet({ plan, isDemo }: Props) {
  const [tab, setTab] = useState<"days" | "transport" | "hotel">("days");

  const referenced = useMemo(() => {
    if (!plan) return new Set<string>();
    return new Set(
      plan.days.flatMap((day) => [day.transport_option_id, day.hotel_option_id].filter(Boolean) as string[])
    );
  }, [plan]);

  const prefDays = Number(plan?.preferences.days);
  const daysCount = plan?.days.length
    ? plan.days.length
    : Number.isFinite(prefDays) && prefDays > 0
      ? prefDays
      : 0;
  const dateRange = plan?.days.length
    ? `${plan.days[0].day.slice(5).replace("-", "/")} – ${plan.days[plan.days.length - 1].day.slice(5).replace("-", "/")}`
    : plan?.preferences.start_date
      ? dateRangeLabel(plan.preferences.start_date, daysCount)
      : "日期待定";
  const budget = plan?.preferences.budget_cny;
  const budgetLabel = budget && Number.isFinite(Number(budget)) ? money(Number(budget)) : "未填写";

  if (!plan) {
    return (
      <section className="card">
        <div className="card-head"><h2>行程方案</h2></div>
        <div className="card-body">
          <div className="empty">暂时没有读取到这份行程。</div>
        </div>
      </section>
    );
  }

  return (
    <section className="card">
      <div className="card-head">
        <h2>你的行程</h2>
        <div className="badges">
          <span className="badge green">当前方案</span>
          <span className="badge">v{plan.version}</span>
        </div>
      </div>
      <div className="card-body">
        <div className="ticket">
          <div className="trip-notice">{isDemo ? "示例行程 · 不会产生预订" : "行程参考 · 尚未出票"}</div>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", flexWrap: "wrap", gap: 10 }}>
            <strong style={{ fontSize: 20 }}>{isDemo ? demoTripTitle : "本次差旅安排"}</strong>
            {!isDemo && <span className="muted">方案编号 {plan.plan_id}</span>}
          </div>
          {isDemo && (
            <div className="route-line">
              <span className="city">{demoRouteCities.from}</span>
              <div className="route-track" />
              <span className="city">{demoRouteCities.to}</span>
            </div>
          )}
          <div className="meta-grid">
            <div className="meta-cell"><span>出差日期</span><strong>{dateRange}</strong></div>
            <div className="meta-cell"><span>行程天数</span><strong>{daysCount} 天</strong></div>
            <div className="meta-cell"><span>预算上限</span><strong>{budgetLabel}</strong></div>
            <div className="meta-cell"><span>住宿区域</span><strong>{plan.preferences.hotel_area || "未指定"}</strong></div>
          </div>
        </div>

        {plan.preferences.draft && (
          <div className="draft-block">
            <div className="draft-block-head">
              <strong>草案正文</strong>
              <span className="muted">由对话生成 · 可返回继续修改</span>
            </div>
            <div className="draft-text">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {normalizeDraftMarkdown(plan.preferences.draft)}
              </ReactMarkdown>
            </div>
          </div>
        )}

        <div className="tabs" role="tablist">
          <button className={`tab ${tab === "days" ? "active" : ""}`} onClick={() => setTab("days")}>日程</button>
          <button className={`tab ${tab === "transport" ? "active" : ""}`} onClick={() => setTab("transport")}>交通候选 {plan.transport_options.length}</button>
          <button className={`tab ${tab === "hotel" ? "active" : ""}`} onClick={() => setTab("hotel")}>酒店候选 {plan.hotel_options.length}</button>
        </div>

        {tab === "days" && (
          <div className="day-list">
            {plan.days.map((day) => (
              <div key={day.day} className="day-row">
                <div className="day-date">
                  <strong>{day.day.slice(5).replace("-", "/")}</strong>
                  <span>{day.day}</span>
                </div>
                <div>
                  <ul className="activity-list">
                    {day.activities.map((activity, index) => (
                      <li key={`${day.day}-${index}`}>{activity}</li>
                    ))}
                  </ul>
                  <div className="refs">
                    {day.transport_option_id && (
                      <span className="ref">
                        {plan.transport_options.find((item) => item.option_id === day.transport_option_id)?.carrier || "交通已安排"}
                      </span>
                    )}
                    {day.hotel_option_id && (
                      <span className="ref">
                        {plan.hotel_options.find((item) => item.option_id === day.hotel_option_id)?.name || "住宿已安排"}
                      </span>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}

        {tab === "transport" && (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>备选</th><th>方式</th><th>承运</th><th>出发</th><th>到达</th><th>价格</th><th>换乘</th><th>退改</th>
                </tr>
              </thead>
              <tbody>
                {plan.transport_options.map((item, index) => (
                  <tr key={item.option_id}>
                    <td>{referenced.has(item.option_id) ? "行程采用" : `备选 ${index + 1}`}</td>
                    <td>{item.mode === "flight" ? "航班" : item.mode === "train" ? "火车" : "其他"}</td>
                    <td>{item.carrier}</td>
                    <td className="mono">{item.depart_at.replace("T", " ")}</td>
                    <td className="mono">{item.arrive_at.replace("T", " ")}</td>
                    <td>{money(item.price_cny)}</td>
                    <td>{item.transfer_count} 次</td>
                    <td>{item.refundable ? "可退" : "不可退"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {tab === "hotel" && (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr><th>备选</th><th>酒店</th><th>入住</th><th>离店</th><th>每晚</th><th>距离</th><th>退改</th></tr>
              </thead>
              <tbody>
                {plan.hotel_options.map((item, index) => (
                  <tr key={item.option_id}>
                    <td>{referenced.has(item.option_id) ? "行程采用" : `备选 ${index + 1}`}</td>
                    <td>{item.name}</td>
                    <td className="mono">{item.check_in}</td>
                    <td className="mono">{item.check_out}</td>
                    <td>{money(item.nightly_price_cny)}</td>
                    <td>{item.distance_km} km</td>
                    <td>{item.refundable ? "可退" : "不可退"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="note">方案中的交通和酒店仅为参考选项，仍需按公司流程完成预订。</p>
      </div>
    </section>
  );
}

export { time };