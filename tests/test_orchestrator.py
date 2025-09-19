import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest

from app.models import CallState, CallbackPayload, HttpMethod, TestConfig, TestStatus
from app.orchestrator import CallRecord, TestOrchestrator, TestRun, _utcnow


def test_run_lifecycle_and_metrics(monkeypatch):
    async def scenario():
        orchestrator = TestOrchestrator()
        dispatched_tokens = []

        async def fake_dispatch(self: TestRun, client):
            token = str(uuid4())
            record = CallRecord(correlation_token=token, sent_at=_utcnow(), state=CallState.DISPATCHED)
            async with self._lock:
                self.calls[token] = record
            self._orchestrator.register_correlation(self.id, token)
            dispatched_tokens.append(token)
            await self.publish()

        monkeypatch.setattr(TestRun, "_dispatch_call", fake_dispatch)

        @asynccontextmanager
        async def fake_client():
            class DummyClient:
                async def request(self, **kwargs):  # pragma: no cover - should not be called in this test
                    raise AssertionError("HTTP requests should not be executed in the unit test")

            yield DummyClient()

        monkeypatch.setattr(TestRun, "_create_http_client", lambda self: fake_client())

        config = TestConfig(
            name="demo",
            target_url="https://example.org/api",
            method=HttpMethod.POST,
            rate_per_minute=120,
            duration_seconds=1,
            payload={"hello": "world"},
        )
        test = await orchestrator.create_test(config)

        assert test.status == TestStatus.PENDING

        await orchestrator.start_test(test.id)
        await test.wait_until_complete()

        summary = await orchestrator.get_summary(test.id)
        assert summary.status == TestStatus.COMPLETED
        assert summary.counts.sent == len(dispatched_tokens)
        assert summary.counts.completed == 0
        assert summary.counts.pending == summary.counts.sent

        # Process callback for the first token and ensure metrics update
        first_token = dispatched_tokens[0]
        receipt = await orchestrator.handle_callback(CallbackPayload(correlation_token=first_token, status="success"))
        assert receipt.accepted is True
        updated = await orchestrator.get_summary(test.id)
        assert updated.counts.completed == 1
        assert updated.success_rate >= 50.0
        assert first_token not in updated.outstanding_correlation_tokens

        # Remaining tokens should still be pending
        if len(dispatched_tokens) > 1:
            assert any(token in updated.outstanding_correlation_tokens for token in dispatched_tokens[1:])

        # list_calls should expose the recorded dispatches
        call_listing = await orchestrator.list_calls(test.id, limit=10)
        assert call_listing.test_id == test.id
        assert len(call_listing.calls) == len(dispatched_tokens)

        # Unmatched callbacks should not be accepted
        receipt = await orchestrator.handle_callback(CallbackPayload(correlation_token="missing", status="success"))
        assert receipt.accepted is False
        assert receipt.test_id is None

        # Attempting to start again should raise a runtime error
        with pytest.raises(RuntimeError):
            await orchestrator.start_test(test.id)

    asyncio.run(scenario())


def test_list_tests_contains_created_runs():
    async def scenario():
        orchestrator = TestOrchestrator()

        config = TestConfig(
            name="another",
            target_url="https://example.org/api",
            method=HttpMethod.GET,
            rate_per_minute=60,
            duration_seconds=10,
        )
        test = await orchestrator.create_test(config)

        listing = await orchestrator.list_tests()
        assert any(item.id == test.id for item in listing.tests)

    asyncio.run(scenario())
