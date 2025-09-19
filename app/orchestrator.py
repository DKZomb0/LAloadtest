"""Core orchestration logic for managing load tests."""
from __future__ import annotations

import asyncio
import copy
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import mean, median
from typing import Any, Dict, Iterable, List, Optional, Set
from uuid import UUID, uuid4

from .models import (
    CallListResponse,
    CallRecordSnapshot,
    CallState,
    CallbackPayload,
    CallbackReceipt,
    HttpMethod,
    ResponseTimeMetrics,
    TestConfig,
    TestCounts,
    TestListResponse,
    TestStatus,
    TestSummary,
)

LOGGER = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class CallRecord:
    """Mutable state for a single dispatched call."""

    correlation_token: str
    sent_at: datetime
    state: CallState = CallState.PENDING
    ack_status: Optional[int] = None
    ack_error: Optional[str] = None
    ack_excerpt: Optional[str] = None
    callback_status: Optional[str] = None
    callback_code: Optional[int] = None
    completed_at: Optional[datetime] = None

    def to_snapshot(self) -> CallRecordSnapshot:
        return CallRecordSnapshot(
            correlation_token=self.correlation_token,
            state=self.state,
            sent_at=self.sent_at.timestamp(),
            ack_status=self.ack_status,
            ack_error=self.ack_error,
            callback_status=self.callback_status,
            callback_code=self.callback_code,
            completed_at=self.completed_at.timestamp() if self.completed_at else None,
            duration_ms=self.duration_ms,
        )

    @property
    def duration_ms(self) -> Optional[float]:
        if not self.completed_at:
            return None
        return (self.completed_at - self.sent_at).total_seconds() * 1000.0


class TestRun:
    """Manages the lifecycle of a single load test execution."""

    def __init__(self, orchestrator: "TestOrchestrator", config: TestConfig, test_id: Optional[UUID] = None) -> None:
        self.id: UUID = test_id or uuid4()
        self._orchestrator = orchestrator
        self.config = config
        self.status: TestStatus = TestStatus.PENDING
        self.created_at: datetime = _utcnow()
        self.started_at: Optional[datetime] = None
        self.finished_at: Optional[datetime] = None
        self.error: Optional[str] = None
        self.calls: Dict[str, CallRecord] = {}
        self.orphan_callbacks: List[CallbackPayload] = []
        self._lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._subscribers: Set[asyncio.Queue] = set()

    # ------------------------------------------------------------------
    # Lifecycle management
    # ------------------------------------------------------------------
    @property
    def expected_calls(self) -> int:
        return math.ceil((self.config.rate_per_minute * self.config.duration_seconds) / 60.0)

    def is_running(self) -> bool:
        return self.status == TestStatus.RUNNING

    async def start(self) -> None:
        if self.is_running():
            raise RuntimeError("Test is already running")
        if self.status != TestStatus.PENDING:
            raise RuntimeError(f"Cannot start a test in status {self.status}")
        self.status = TestStatus.RUNNING
        self.started_at = _utcnow()
        self.finished_at = None
        self.error = None
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop(), name=f"load-test-{self.id}")
        await self.publish()

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._stop_event.set()
            await self._task
        elif self.status == TestStatus.RUNNING:
            self.status = TestStatus.CANCELLED
            self.finished_at = _utcnow()
            await self.publish()

    async def wait_until_complete(self) -> None:
        if self._task:
            await self._task

    async def _run_loop(self) -> None:
        interval_seconds = 60.0 / self.config.rate_per_minute
        end_time = self.started_at + timedelta(seconds=self.config.duration_seconds)
        next_dispatch = self.started_at
        try:
            async with self._create_http_client() as client:
                while not self._stop_event.is_set():
                    now = _utcnow()
                    if now >= end_time:
                        break
                    if now < next_dispatch:
                        await asyncio.sleep(min(interval_seconds, (next_dispatch - now).total_seconds()))
                        continue
                    await self._dispatch_call(client)
                    next_dispatch += timedelta(seconds=interval_seconds)
        except Exception as exc:  # pragma: no cover - safety net
            LOGGER.exception("Unexpected failure in test %s", self.id)
            self.status = TestStatus.FAILED
            self.error = str(exc)
        finally:
            if self._stop_event.is_set() and self.status != TestStatus.FAILED:
                self.status = TestStatus.CANCELLED
            elif self.status == TestStatus.RUNNING:
                self.status = TestStatus.COMPLETED
            self.finished_at = _utcnow()
            await self.publish()

    def _create_http_client(self):
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - exercised when dependency missing at runtime
            raise RuntimeError(
                "httpx is required to dispatch HTTP requests. Install the optional dependency with 'pip install httpx'."
            ) from exc
        timeout = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=30.0)
        return httpx.AsyncClient(timeout=timeout)

    async def _dispatch_call(self, client: Any) -> None:
        correlation_token = str(uuid4())
        record = CallRecord(correlation_token=correlation_token, sent_at=_utcnow())
        async with self._lock:
            self.calls[correlation_token] = record
        self._orchestrator.register_correlation(self.id, correlation_token)

        headers = {**self.config.headers}
        headers.setdefault("X-Correlation-Token", correlation_token)

        payload: Any
        if self.config.payload is None:
            payload = {"correlation_token": correlation_token}
        else:
            payload = copy.deepcopy(self.config.payload)
            if isinstance(payload, dict):
                payload.setdefault("correlation_token", correlation_token)
            else:
                payload = {"payload": payload, "correlation_token": correlation_token}

        request_kwargs: Dict[str, Any] = {
            "method": self.config.method.value,
            "url": str(self.config.target_url),
            "headers": headers,
        }
        if self.config.method == HttpMethod.GET:
            request_kwargs["params"] = payload
        else:
            request_kwargs["json"] = payload

        ack_status: Optional[int] = None
        ack_error: Optional[str] = None
        new_state = CallState.DISPATCHED
        completed_at: Optional[datetime] = None

        try:
            response = await client.request(**request_kwargs)
            ack_status = response.status_code
            if response.status_code >= 400:
                ack_error = f"HTTP {response.status_code}"
                new_state = CallState.FAILED
                completed_at = _utcnow()
                self._orchestrator.unregister_correlation(correlation_token)
            excerpt = response.text[:256]
        except Exception as exc:  # pragma: no cover - network failures are exercised in runtime
            ack_error = str(exc)
            new_state = CallState.FAILED
            completed_at = _utcnow()
            excerpt = None
            self._orchestrator.unregister_correlation(correlation_token)
        async with self._lock:
            stored = self.calls.get(correlation_token)
            if stored:
                stored.ack_status = ack_status
                stored.ack_error = ack_error
                stored.state = new_state
                stored.ack_excerpt = excerpt
                if completed_at and not stored.completed_at:
                    stored.completed_at = completed_at
        await self.publish()

    # ------------------------------------------------------------------
    # Metrics and reporting
    # ------------------------------------------------------------------
    async def build_summary(self) -> TestSummary:
        async with self._lock:
            calls_snapshot = list(self.calls.values())
            error_message = self.error
        durations = [c.duration_ms for c in calls_snapshot if c.duration_ms is not None]
        completed = sum(1 for c in calls_snapshot if c.state == CallState.COMPLETED)
        failed = sum(1 for c in calls_snapshot if c.state == CallState.FAILED)
        dispatched = sum(1 for c in calls_snapshot if c.state in {CallState.DISPATCHED, CallState.COMPLETED})
        sent = len(calls_snapshot)
        pending = sent - completed - failed
        success_rate = (completed / sent * 100.0) if sent else 0.0
        avg_throughput = mean(durations) if durations else None
        response_times = ResponseTimeMetrics(
            minimum_ms=min(durations) if durations else None,
            maximum_ms=max(durations) if durations else None,
            average_ms=avg_throughput,
            median_ms=median(durations) if durations else None,
        )
        outstanding = [c.correlation_token for c in calls_snapshot if c.state == CallState.DISPATCHED]
        summary = TestSummary(
            id=self.id,
            name=self.config.name,
            status=self.status,
            created_at=self.created_at.timestamp(),
            started_at=self.started_at.timestamp() if self.started_at else None,
            finished_at=self.finished_at.timestamp() if self.finished_at else None,
            counts=TestCounts(
                expected=self.expected_calls,
                sent=sent,
                dispatched=dispatched,
                completed=completed,
                failed=failed,
                pending=max(pending, 0),
            ),
            success_rate=success_rate,
            average_throughput_ms=avg_throughput,
            response_times=response_times,
            outstanding_correlation_tokens=outstanding[:50],
            notes=error_message,
        )
        return summary

    async def list_calls(self, limit: int = 100) -> CallListResponse:
        async with self._lock:
            calls = list(self.calls.values())
        calls.sort(key=lambda c: c.sent_at, reverse=True)
        limited = calls[: limit if limit > 0 else 100]
        return CallListResponse(test_id=self.id, calls=[c.to_snapshot() for c in limited])

    async def contains_correlation(self, token: str) -> bool:
        async with self._lock:
            return token in self.calls

    async def process_callback(self, payload: CallbackPayload) -> bool:
        async with self._lock:
            record = self.calls.get(payload.correlation_token)
            if not record:
                self.orphan_callbacks.append(payload)
                matched = False
            else:
                record.callback_status = payload.status
                record.callback_code = payload.code
                record.completed_at = record.completed_at or _utcnow()
                inferred = payload.is_successful()
                if inferred is False:
                    record.state = CallState.FAILED
                elif inferred is True:
                    record.state = CallState.COMPLETED
                elif record.state != CallState.FAILED:
                    record.state = CallState.COMPLETED
                matched = True
        if matched:
            self._orchestrator.unregister_correlation(payload.correlation_token)
        await self.publish()
        return matched

    # ------------------------------------------------------------------
    # Websocket helpers
    # ------------------------------------------------------------------
    async def register_subscriber(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=1)
        self._subscribers.add(queue)
        summary = await self.build_summary()
        queue.put_nowait(summary)
        return queue

    async def unregister_subscriber(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    async def publish(self) -> None:
        if not self._subscribers:
            return
        summary = await self.build_summary()
        for queue in list(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:  # pragma: no cover - defensive
                    pass
            try:
                queue.put_nowait(summary)
            except asyncio.QueueFull:  # pragma: no cover - defensive
                LOGGER.debug("Dropping update for subscriber queue")


class TestOrchestrator:
    """Creates, manages and aggregates the defined load tests."""

    def __init__(self) -> None:
        self._tests: Dict[UUID, TestRun] = {}
        self._correlations: Dict[str, UUID] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Test lifecycle
    # ------------------------------------------------------------------
    async def create_test(self, config: TestConfig) -> TestRun:
        test = TestRun(self, config)
        async with self._lock:
            self._tests[test.id] = test
        return test

    def get_test(self, test_id: UUID) -> TestRun:
        try:
            return self._tests[test_id]
        except KeyError as exc:  # pragma: no cover - FastAPI handles HTTP raising
            raise KeyError(f"Unknown test id {test_id}") from exc

    async def list_tests(self) -> TestListResponse:
        async with self._lock:
            runs: Iterable[TestRun] = list(self._tests.values())
        summaries = [await run.build_summary() for run in sorted(runs, key=lambda r: r.created_at, reverse=True)]
        return TestListResponse(tests=summaries)

    async def get_summary(self, test_id: UUID) -> TestSummary:
        run = self.get_test(test_id)
        return await run.build_summary()

    async def start_test(self, test_id: UUID) -> TestSummary:
        run = self.get_test(test_id)
        await run.start()
        return await run.build_summary()

    async def stop_test(self, test_id: UUID) -> TestSummary:
        run = self.get_test(test_id)
        await run.stop()
        return await run.build_summary()

    async def list_calls(self, test_id: UUID, limit: int = 100) -> CallListResponse:
        run = self.get_test(test_id)
        return await run.list_calls(limit)

    # ------------------------------------------------------------------
    # Correlation handling
    # ------------------------------------------------------------------
    def register_correlation(self, test_id: UUID, correlation_token: str) -> None:
        self._correlations[correlation_token] = test_id

    def unregister_correlation(self, correlation_token: str) -> None:
        self._correlations.pop(correlation_token, None)

    async def handle_callback(self, payload: CallbackPayload) -> CallbackReceipt:
        test_id = self._correlations.get(payload.correlation_token)
        target_run: Optional[TestRun] = None
        if test_id:
            target_run = self._tests.get(test_id)
        if not target_run:
            for run in self._tests.values():
                if await run.contains_correlation(payload.correlation_token):
                    target_run = run
                    test_id = run.id
                    break
        if not target_run:
            return CallbackReceipt(
                accepted=False,
                test_id=None,
                message="Correlation token was not recognized by any active test.",
            )
        accepted = await target_run.process_callback(payload)
        message = "Callback processed." if accepted else "Callback did not match any call in the target test."
        return CallbackReceipt(accepted=accepted, test_id=test_id, message=message)


__all__ = [
    "TestOrchestrator",
    "TestRun",
    "CallRecord",
]
