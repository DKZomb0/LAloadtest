"""FastAPI application exposing the load test orchestrator."""
from __future__ import annotations

from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder

from . import __version__
from .models import (
    CallListResponse,
    CallbackPayload,
    CallbackReceipt,
    TestConfig,
    TestListResponse,
    TestSummary,
)
from .orchestrator import TestOrchestrator

app = FastAPI(title="Middleware Load Test Orchestrator", version=__version__)
orchestrator = TestOrchestrator()


async def _get_test_summary_or_404(test_id: UUID) -> TestSummary:
    try:
        return await orchestrator.get_summary(test_id)
    except KeyError as exc:  # pragma: no cover - FastAPI handles HTTPException conversion
        raise HTTPException(status_code=404, detail=str(exc)) from exc


async def _get_test_or_404(test_id: UUID):
    try:
        return orchestrator.get_test(test_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/", response_model=dict)
async def root() -> dict:
    """Basic health endpoint."""
    return {"service": app.title, "version": app.version}


@app.get("/tests", response_model=TestListResponse)
async def list_tests() -> TestListResponse:
    """Return all known tests and their current state."""
    return await orchestrator.list_tests()


@app.post("/tests", response_model=TestSummary, status_code=201)
async def create_test(config: TestConfig) -> TestSummary:
    """Create a new test definition."""
    test = await orchestrator.create_test(config)
    summary = await test.build_summary()
    if config.start_immediately:
        try:
            summary = await orchestrator.start_test(test.id)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return summary


@app.get("/tests/{test_id}", response_model=TestSummary)
async def get_test(test_id: UUID) -> TestSummary:
    """Fetch a specific test summary."""
    return await _get_test_summary_or_404(test_id)


@app.post("/tests/{test_id}/start", response_model=TestSummary)
async def start_test(test_id: UUID) -> TestSummary:
    """Begin executing the specified test."""
    await _get_test_or_404(test_id)
    try:
        return await orchestrator.start_test(test_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/tests/{test_id}/stop", response_model=TestSummary)
async def stop_test(test_id: UUID) -> TestSummary:
    """Stop execution of the specified test."""
    await _get_test_or_404(test_id)
    try:
        return await orchestrator.stop_test(test_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/tests/{test_id}/calls", response_model=CallListResponse)
async def list_calls(test_id: UUID, limit: int = Query(100, ge=1, le=1000)) -> CallListResponse:
    """Return recent calls for the selected test."""
    await _get_test_or_404(test_id)
    return await orchestrator.list_calls(test_id, limit=limit)


@app.post("/callbacks", response_model=CallbackReceipt)
async def receive_callback(payload: CallbackPayload) -> CallbackReceipt:
    """Endpoint that downstream services can call with test results."""
    return await orchestrator.handle_callback(payload)


@app.websocket("/ws/tests/{test_id}")
async def test_updates(websocket: WebSocket, test_id: UUID) -> None:
    """WebSocket endpoint streaming live test metrics."""
    try:
        test = orchestrator.get_test(test_id)
    except KeyError:
        await websocket.accept()
        await websocket.send_json({"error": "Unknown test id."})
        await websocket.close(code=1008)
        return

    await websocket.accept()
    queue = await test.register_subscriber()
    try:
        while True:
            summary = await queue.get()
            await websocket.send_json(jsonable_encoder(summary))
    except WebSocketDisconnect:  # pragma: no cover - WebSocket transports aren't exercised in tests
        pass
    finally:
        await test.unregister_subscriber(queue)


__all__ = ["app", "orchestrator"]
