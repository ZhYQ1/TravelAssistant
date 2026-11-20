# 行知差旅 · Travel Assistant

[English](README.en.md) | 简体中文

多 Agent 协作智能差旅助手：从意图识别、方案规划、合规审核到会话续跑的全链路服务。

## 项目简介

行知差旅是一个面向企业差旅场景的多 Agent 协作助手。系统采用「三级意图识别 + 全链路降级」的路由架构：规则与向量层提供毫秒级快速决策，Jev（TypeSafe System One）给出带置信度的类型化决策，LLM 负责兜底精判。任何一层出现故障都不会阻断服务，而是自动下沉到下一层。

## 核心特性

- **三级意图路由**：L1 规则 → L2 向量召回 → Jev（System One）→ LLM 兜底，全链路可降级，任何单点故障不阻断路由。
- **Jev 类型化决策**：通过官方 TypeSafe Python SDK 直连，提供 Choice / Noul / Score 三类结构化输出，均自带置信度概率，毫秒级决策。
- **方案生成与审核闭环**：基于 Redis 状态机的多轮审核，整改项携带优先级、严重度、证据与重复计数，审核失败不会被误判为通过。
- **会话续跑**：用 Noul 判断当前消息是否延续活跃 Agent，避免不必要的重新路由，降低延迟与 Token 消耗。
- **第三方 Skill 隔离执行**：AgentScope `pre_acting` Hook + 用户级凭证注入 + 独立执行目录 + 秘密脱敏，安全地委托第三方 CLI。
- **双模式前端操作台**：演示数据 / 真实 API 两套模式，默认可完整浏览；真实模式只展示后端真实返回，失败时绝不伪造成功。
- **可观测性**：路由审计链路、运行指标（路由 / 审核 / 续跑计数、平均与 P95 延迟、Token 用量）。

## 架构

```
用户消息
   │
   ▼
L1 规则匹配 ──命中──▶ 直接路由（置信度 1.0）
   │ 未命中/冲突
   ▼
L2 向量召回 ──区分度足够──▶ 路由
   │ 区分度不足
   ▼
Jev (TypeSafe System One) ──置信度≥阈值──▶ 路由
   │ 低置信度 / 不可用
   ▼
LLM 兜底精判 ──失败──▶ 澄清
```

- **L1 规则**：确定性规则命中差旅任务、知识问答、流程办理等意图。
- **L2 向量**：Redis 存储意图向量，Embedding 召回 + 区分度阈值判断。
- **Jev**：System One 快速决策，`Choice` 做封闭集路由，`Noul` 做续跑判断，`Score` 做有序评分。
- **LLM 兜底**：使用讯飞星辰 OpenAI 兼容接口做精判，仅在前面各层都无法高置信决策时触发。

## 目录结构

```
TravelAssistant/
├── backend/          # FastAPI 后端：三级路由、多 Agent、审核闭环、续跑、Skill 隔离
│   ├── app/
│   │   ├── routing/  # L1 规则 / L2 向量 / Jev / LLM 路由链
│   │   ├── agents/   # Agent 执行协调与续跑判断
│   │   ├── plan/     # 方案模型与存储
│   │   ├── review/   # 审核状态机与规则
│   │   ├── skill/    # 第三方 Skill 隔离执行 + AgentScope Hook
│   │   └── ...
│   ├── tests/        # unit / integration 测试
│   └── .env.example  # 环境变量样例
├── frontend/         # Vite + React + TypeScript 差旅操作台（演示/真实双模式）
└── jev-adapter/      # （已弃用）Java Jev sidecar，现已由 Python SDK 直接取代
```

## 快速开始

### 后端

```powershell
cd backend
python -m pip install -e ".[dev]"
Copy-Item .env.example .env   # 然后填写必填项
python -m uvicorn app.api.main:app --reload
```

服务默认监听 `http://localhost:8000`，健康检查为 `GET /healthz`。

### 前端

```powershell
cd frontend
npm install
npm run dev
```

Vite 开发服务器默认监听 `http://localhost:5173`，并将 `/api` 代理到后端（默认 `http://localhost:8000`，可通过 `VITE_API_PROXY_TARGET` 覆盖）。

## 环境变量

在后端 `.env` 中配置（完整清单见 [.env.example](backend/.env.example)）：

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `REDIS_URL` | 是 | Redis 连接地址 |
| `LLM_BASE_URL` | 是 | LLM 兼容接口地址（讯飞星辰） |
| `LLM_API_KEY` | 是 | LLM 密钥 |
| `LLM_MODEL` | 是 | LLM 模型名 |
| `EMBEDDING_API_KEY` | 是 | Embedding 密钥 |
| `EMBEDDING_BASE_URL` | 否 | 默认 `https://api.siliconflow.cn/v1` |
| `EMBEDDING_MODEL` | 否 | 默认 `BAAI/bge-m3` |
| `TYPESAFE_API_KEY` | 否 | Jev（TypeSafe）密钥，缺省时 Jev 层自动降级到 LLM |
| `JEV_MODEL` | 否 | Jev 模型，留空使用官方默认 |
| `TYPESAFE_BASE_URL` | 否 | Jev 自托管端点，留空使用官方默认 |
| `JEV_CONFIDENCE_THRESHOLD` | 否 | Jev 路由置信度阈值，默认 `0.75` |
| `VECTOR_MIN_SCORE` / `VECTOR_MARGIN` | 否 | 向量召回最高相似度下限与区分度 |

## API 端点

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/healthz` | 健康检查 |
| POST | `/v1/route` | 意图识别与路由 |
| POST | `/v1/assistant/message` | Agent 消息处理 |
| POST | `/v1/plans` | 创建行程方案 |
| GET | `/v1/plans/{plan_id}` | 获取行程方案 |
| POST | `/v1/plans/{plan_id}/reviews` | 发起合规审核 |
| POST | `/v1/plans/{plan_id}/cancel` | 取消方案 |
| GET | `/v1/continuation/actions` | 获取续跑动作 |
| POST | `/v1/continuation/resume` | 恢复会话续跑 |
| GET | `/metrics` | 运行指标 |

## 测试

后端（在 `backend` 目录）：

```powershell
python -m pytest tests/unit -q   # 单元测试
python -m pytest -q              # 全量（含需真实服务的集成测试）
```

前端（在 `frontend` 目录）：

```powershell
npm run typecheck
npm run test
npm run build
```

## 技术栈

- **后端**：Python 3.11+、FastAPI、Redis、AgentScope 1.0.21、TypeSafe SDK、Pydantic、httpx
- **前端**：Vite、React 18、TypeScript
- **模型**：LLM 使用讯飞星辰（OpenAI 兼容）；Embedding 使用 SiliconFlow `BAAI/bge-m3`；Jev 使用 TypeSafe System One