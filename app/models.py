"""Pydantic models and enums used by the load test orchestrator."""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, PositiveInt, root_validator
from pydantic.networks import AnyHttpUrl


class HttpMethod(str, Enum):
    """Supported HTTP methods for outbound test calls."""

    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


class TestStatus(str, Enum):
    """Lifecycle status of a load test."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CallState(str, Enum):
    """State of an individual orchestrated call."""

    PENDING = "pending"
    DISPATCHED = "dispatched"
    COMPLETED = "completed"
    FAILED = "failed"


class TestConfig(BaseModel):
    """Configuration parameters for a load test run."""

    name: str = Field(..., min_length=1, max_length=120)
    target_url: AnyHttpUrl = Field(..., description="Endpoint under test")
    method: HttpMethod = Field(HttpMethod.POST, description="HTTP method to use")
    rate_per_minute: PositiveInt = Field(
        ...,
        description="Number of calls per minute to dispatch",
        ge=1,
        le=6000,
    )
    duration_seconds: PositiveInt = Field(
        ..., description="How long the test should run", ge=1, le=24 * 60 * 60
    )
    headers: Dict[str, str] = Field(
        default_factory=dict,
        description="Optional additional headers to include in the outbound call.",
    )
    payload: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional JSON payload to include with non-GET requests.",
    )
    start_immediately: bool = Field(
        False,
        description="Whether the orchestrator should immediately start the test after creation.",
    )

    class Config:
        extra = "forbid"


class TestIdentifier(BaseModel):
    """Simple model containing a test id."""

    id: UUID = Field(default_factory=uuid4)


class CallbackPayload(BaseModel):
    """Representation of the callback payload received from the tested middleware."""

    correlation_token: str = Field(..., description="Correlation token sent in the request.")
    status: Optional[str] = Field(
        None, description="Optional textual status returned by the downstream service."
    )
    code: Optional[int] = Field(
        None,
        description="Optional numeric status returned by the downstream service.",
    )
    detail: Optional[Any] = Field(
        None, description="Optional payload returned by the downstream service."
    )

    class Config:
        extra = "allow"

    SUCCESS_STATUSES = {"success", "ok", "completed", "done"}

    @root_validator(pre=True)
    def _coerce_correlation_token(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        token = (
            values.get("correlation_token")
            or values.get("correlationToken")
            or values.get("correlation_id")
            or values.get("correlationId")
        )
        if not token:
            raise ValueError("A correlation token is required on callback payloads.")
        values["correlation_token"] = token
        return values

    def is_successful(self) -> Optional[bool]:
        """Infer whether the callback should be treated as successful."""

        if self.status:
            lowered = self.status.lower()
            if lowered in self.SUCCESS_STATUSES:
                return True
            if lowered in {"failure", "failed", "error"}:
                return False
        if self.code is not None:
            return 200 <= self.code < 400
        return None


class TestCounts(BaseModel):
    """Aggregate counter information returned to the client."""

    expected: int
    sent: int
    dispatched: int
    completed: int
    failed: int
    pending: int


class ResponseTimeMetrics(BaseModel):
    """Summary metrics for call durations."""

    minimum_ms: Optional[float]
    maximum_ms: Optional[float]
    average_ms: Optional[float]
    median_ms: Optional[float]


class TestSummary(BaseModel):
    """High level overview of a load test."""

    id: UUID
    name: str
    status: TestStatus
    created_at: float
    started_at: Optional[float]
    finished_at: Optional[float]
    counts: TestCounts
    success_rate: float
    average_throughput_ms: Optional[float]
    response_times: ResponseTimeMetrics
    outstanding_correlation_tokens: List[str] = Field(default_factory=list)
    notes: Optional[str] = None


class TestListResponse(BaseModel):
    """Response payload for the list tests endpoint."""

    tests: List[TestSummary]


class CallRecordSnapshot(BaseModel):
    """Serializable snapshot of an orchestrated call."""

    correlation_token: str
    state: CallState
    sent_at: float
    ack_status: Optional[int]
    ack_error: Optional[str]
    callback_status: Optional[str]
    callback_code: Optional[int]
    completed_at: Optional[float]
    duration_ms: Optional[float]


class CallListResponse(BaseModel):
    """Response payload containing call snapshots."""

    test_id: UUID
    calls: List[CallRecordSnapshot]


class CallbackReceipt(BaseModel):
    """Response returned when the orchestrator receives a callback."""

    accepted: bool
    test_id: Optional[UUID]
    message: str

