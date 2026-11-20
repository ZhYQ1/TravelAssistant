# 行知差旅 · Travel Assistant

English | [简体中文](README.md)

A multi-agent collaborative intelligent travel assistant: intent routing, itinerary planning, compliance review, and session continuation in one service.

## Overview

Travel Assistant is a multi-agent collaboration system for enterprise travel scenarios. It uses a three-tier intent-routing architecture with full-path graceful degradation: rule and vector tiers provide millisecond-level fast decisions, Jev (TypeSafe System One) returns typed decisions with confidence, and an LLM handles final fallback. A failure in any single tier never blocks the service — the request simply cascades to the next tier.

## Key Features

- **Three-tier intent routing**: L1 rules → L2 vector recall → Jev (System One) → LLM fallback, with full-path degradation so no single point of failure breaks routing.
- **Typed Jev decisions**: Talks to the official TypeSafe Python SDK directly, exposing Choice / Noul / Score structured outputs, each with a confidence probability, decided in milliseconds.
- **Planning & review loop**: Redis state-machine driven multi-round review, where rectification items carry priority, severity, evidence, and repeat counts — a failed review is never misreported as passed.
- **Session continuation**: Uses Noul to decide whether the current message continues the active agent, avoiding unnecessary re-routing and lowering latency and token usage.
- **Isolated third-party Skill execution**: AgentScope `pre_acting` Hook + per-user credential injection + isolated working directory + secret redaction to safely delegate third-party CLIs.
- **Dual-mode frontend workbench**: Demo-data / real-API modes; browsable by default, while real mode only shows genuine backend responses and never fakes success.
- **Observability**: routing audit trail and runtime metrics (route / review / continuation counts, average & P95 latency, token usage).

## Architecture

```
User message
   │
   ▼
L1 rule match ──hit──▶ route directly (confidence 1.0)
   │ miss / conflict
   ▼
L2 vector recall ──margin sufficient──▶ route
   │ margin insufficient
   ▼
Jev (TypeSafe System One) ──confidence ≥ threshold──▶ route
   │ low confidence / unavailable
   ▼
LLM fallback ──failure──▶ clarify
```

- **L1 rules**: deterministic rules for task coordination, knowledge Q&A, workflow, etc.
- **L2 vector**: Redis-backed intent vectors, embedding recall with a margin threshold.
- **Jev**: System One fast decision — `Choice` for closed-set routing, `Noul` for continuation, `Score` for ordered rubrics.
- **LLM fallback**: iFlytek Spark (OpenAI-compatible) for final adjudication, only triggered when earlier tiers cannot decide confidently.

## Directory Structure

```
TravelAssistant/
├── backend/          # FastAPI backend: routing, multi-agent, review loop, continuation, Skill isolation
│   ├── app/
│   │   ├── routing/  # L1 rules / L2 vector / Jev / LLM routing chain
│   │   ├── agents/   # agent execution coordination & continuation check
│   │   ├── plan/     # plan models & storage
│   │   ├── review/   # review state machine & rules
│   │   ├── skill/    # isolated Skill execution + AgentScope Hook
│   │   └── ...
│   ├── tests/        # unit / integration tests
│   └── .env.example  # environment variable sample
├── frontend/         # Vite + React + TypeScript travel workbench (demo / real dual mode)
└── jev-adapter/      # (deprecated) Java Jev sidecar, now superseded by the Python SDK
```

## Quick Start

### Backend

```powershell
cd backend
python -m pip install -e ".[dev]"
Copy-Item .env.example .env   # then fill in the required values
python -m uvicorn app.api.main:app --reload
```

The service listens on `http://localhost:8000` by default; the health check is `GET /healthz`.

### Frontend

```powershell
cd frontend
npm install
npm run dev
```

The Vite dev server listens on `http://localhost:5173` and proxies `/api` to the backend (`http://localhost:8000` by default, overridable via `VITE_API_PROXY_TARGET`).

## Environment Variables

Configure in the backend `.env` (full list in [.env.example](backend/.env.example)):

| Variable | Required | Description |
| --- | --- | --- |
| `REDIS_URL` | Yes | Redis connection URL |
| `LLM_BASE_URL` | Yes | LLM-compatible API base URL (iFlytek Spark) |
| `LLM_API_KEY` | Yes | LLM API key |
| `LLM_MODEL` | Yes | LLM model name |
| `EMBEDDING_API_KEY` | Yes | Embedding API key |
| `EMBEDDING_BASE_URL` | No | Default `https://api.siliconflow.cn/v1` |
| `EMBEDDING_MODEL` | No | Default `BAAI/bge-m3` |
| `TYPESAFE_API_KEY` | No | Jev (TypeSafe) API key; when absent, the Jev tier degrades to the LLM |
| `JEV_MODEL` | No | Jev model; leave empty for the official default |
| `TYPESAFE_BASE_URL` | No | Self-hosted Jev endpoint; leave empty for the official default |
| `JEV_CONFIDENCE_THRESHOLD` | No | Jev routing confidence threshold, default `0.75` |
| `VECTOR_MIN_SCORE` / `VECTOR_MARGIN` | No | Vector recall min score and margin |

## API Endpoints

| Method | Path | Description |
| --- | --- | --- |
| GET | `/healthz` | Health check |
| POST | `/v1/route` | Intent recognition & routing |
| POST | `/v1/assistant/message` | Agent message handling |
| POST | `/v1/plans` | Create an itinerary plan |
| GET | `/v1/plans/{plan_id}` | Fetch an itinerary plan |
| POST | `/v1/plans/{plan_id}/reviews` | Trigger a compliance review |
| POST | `/v1/plans/{plan_id}/cancel` | Cancel a plan |
| GET | `/v1/continuation/actions` | List continuation actions |
| POST | `/v1/continuation/resume` | Resume a session continuation |
| GET | `/metrics` | Runtime metrics |

## Testing

Backend (in `backend`):

```powershell
python -m pytest tests/unit -q   # unit tests
python -m pytest -q              # full suite (including integration tests that need real services)
```

Frontend (in `frontend`):

```powershell
npm run typecheck
npm run test
npm run build
```

## Tech Stack

- **Backend**: Python 3.11+, FastAPI, Redis, AgentScope 1.0.21, TypeSafe SDK, Pydantic, httpx
- **Frontend**: Vite, React 18, TypeScript
- **Models**: LLM via iFlytek Spark (OpenAI-compatible); Embedding via SiliconFlow `BAAI/bge-m3`; Jev via TypeSafe System One