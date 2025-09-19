# Middleware Load Test Orchestrator

This project provides a FastAPI application that orchestrates configurable load tests for middleware systems exposed through Azure API Management or any other HTTP endpoint. It is designed to help you drive asynchronous end-to-end flows that rely on correlation tokens and callbacks, while giving you live visibility into execution progress and timing metrics.

## Key capabilities

- Define reusable test scenarios that describe the downstream endpoint, HTTP method, payload, headers, call rate (per minute) and duration.
- Automatically inject a unique `correlation_token` into each outbound request (JSON body for non-`GET` requests and query parameters for `GET`) and expose it through the `X-Correlation-Token` header.
- Receive asynchronous callbacks from the system under test and match them to their originating requests using the correlation token.
- Track per-call status, outstanding requests, and aggregate statistics such as success rate, throughput, and response time distribution (min/max/average/median).
- Stream live metrics over WebSockets so you can build real-time dashboards during a load test.

## Project structure

```
app/
├── __init__.py
├── main.py           # FastAPI entry point
├── models.py         # Pydantic models and enums
└── orchestrator.py   # Core orchestration engine

tests/
└── test_orchestrator.py
```

## Getting started

1. **Create and activate a virtual environment (recommended):**
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```

2. **Install dependencies:**
   ```bash
   pip install -e .[dev]
   ```

3. **Run the API server:**
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
   ```

   The application requires outbound internet access so that it can reach the target endpoints you configure. When running locally ensure your network allows the necessary traffic.

4. **Interact with the API:**
   - Open the automatically generated API docs at `http://localhost:8000/docs` to explore and invoke endpoints.
   - Launch the real-time dashboard at `http://localhost:8000/ui` to create tests and monitor them through a graphical interface.
   - Use the `/tests` endpoints to create, start, stop, and inspect load tests.
   - Subscribe to real-time updates via WebSocket at `ws://localhost:8000/ws/tests/{test_id}`.
   - Point your downstream service callbacks to `POST http://localhost:8000/callbacks` and include the `correlation_token` returned in each outbound request.

## Defining and running tests

To define a test, POST to `/tests` with a payload such as:

```json
{
  "name": "evening-peak",
  "target_url": "https://<your-api-management-endpoint>/api/process",
  "method": "POST",
  "rate_per_minute": 120,
  "duration_seconds": 600,
  "headers": {
    "Ocp-Apim-Subscription-Key": "<subscription-key>"
  },
  "payload": {
    "message": "hello from orchestrator"
  },
  "start_immediately": true
}
```

Each outbound call will carry the generated correlation token in the request body (as `correlation_token`) and the `X-Correlation-Token` header. When your middleware completes processing, it should call back:

```http
POST /callbacks
Content-Type: application/json

{
  "correlation_token": "<token from request>",
  "status": "success",
  "code": 202,
  "detail": {
    "received": "2024-01-01T12:34:56Z"
  }
}
```

`status` and `code` are optional but help the orchestrator determine whether a callback represents success or failure. Any additional properties are stored for inspection.

### Monitoring live progress

Connect to the WebSocket endpoint for a test to receive periodic JSON payloads containing:

- Current status (`pending`, `running`, `completed`, `failed`, `cancelled`)
- Counts for total sent, dispatched, completed, failed, and pending calls
- Success rate and average throughput time
- Response time statistics (min/max/average/median in milliseconds)
- Outstanding correlation tokens that are still awaiting callbacks (truncated to 50 entries)

These updates can be used to power dashboards or feed alerting workflows during a load test.

### Using the built-in dashboard

The repository bundles a single-page application that gives you a visual overview of the orchestrator. After the API is running, navigate to `http://localhost:8000/ui` to:

- Inspect all configured tests, including their status, call counts, success rate, and response-time statistics.
- Start, stop, and refresh tests without leaving the page.
- Watch live metrics that stream in over WebSockets, with automatic refresh of recent call activity.
- Review outstanding correlation tokens and recent callback activity.
- Create new tests with a guided form that accepts headers, payload JSON, and an option to start immediately.

The dashboard automatically reconnects to the WebSocket feed when you switch between tests so you can keep the UI open while a test is in progress.

## Running tests

Execute the automated test suite with:

```bash
pytest
```

The tests mock outbound HTTP dispatches to ensure they run quickly without requiring external services.

## Next steps

- Integrate the WebSocket stream with your preferred visualization framework to build custom charts.
- Extend the payload templating logic if you need to shape requests differently for each call (e.g., per-user data).
- Persist test definitions or results to a database or message queue if you require long-term storage beyond the in-memory orchestrator.

