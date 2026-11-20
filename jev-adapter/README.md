# AgentScope Java Jev adapter

The Python router calls this local service for the Jev stage. The adapter uses
the official `io.agentscope:agentscope-extensions-jev` client; it does not
emulate Jev with a chat-completions prompt.

## Requirements

- JDK 17 or newer
- Maven 3.8+
- A TypeSafe Jev API key (`TYPESAFE_API_KEY`, or `JEV_API_KEY` as fallback)

## Run locally

From the repository root, start the adapter in one terminal:

```powershell
$env:TYPESAFE_API_KEY = "your-key"
Set-Location .\jev-adapter
mvn spring-boot:run
```

The service binds to `127.0.0.1:8090` by default. Configure `JEV_ADAPTER_HOST`
and `JEV_ADAPTER_PORT` only when the Python backend runs on a different host or
container network.

Configure the Python backend with:

```dotenv
JEV_ADAPTER_URL=http://127.0.0.1:8090
JEV_ADAPTER_TIMEOUT_SECONDS=6
```

The Jev client defaults to `https://api.typesafe.ai`, model `jev-latest`, a
5-second request timeout, and two retries for retryable provider errors. These
can be overridden with `JEV_BASE_URL`, `JEV_MODEL`, and `JEV_TIMEOUT_SECONDS`.

The adapter exposes:

- `GET /v1/healthz`
- `POST /v1/route-choice` for closed-set Choice decisions
- `POST /v1/noul` for a caller-defined yes/no question; `noul` is the
  probability that the supplied true meaning applies
- `POST /v1/score` for a caller-defined ordered rubric; `criteria` contains
  2-10 `{ "name", "description" }` levels, and the response includes the
  probability-weighted score, level distribution, and confidence

The Python assistant uses Noul to check whether a message continues the
currently active task/knowledge agent. A low probability or adapter failure
returns to the full route chain. Score remains a generic API until a business
scoring rubric is defined. Provider failures are explicit; the adapter never
substitutes a chat-model response for a Jev answer.
