"""Core Pydantic data models and schemas for Relay."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"


class EventType(StrEnum):
    MODEL_OUTPUT = "model_output"
    TOOL_RESULT = "tool_result"
    CLOCK_READ = "clock_read"
    RANDOM_VALUE = "random_value"


class StepStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class EffectStatus(StrEnum):
    PENDING = "pending"
    APPLIED = "applied"
    FAILED = "failed"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class RunContext(BaseModel):
    id: UUID
    status: RunStatus
    claim_epoch: int
    worker_id: str | None = None
    input_payload: dict[str, Any] = Field(default_factory=dict)
    output_payload: dict[str, Any] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class RunEventRecord(BaseModel):
    id: UUID | None = None
    run_id: UUID
    sequence_num: int
    event_type: EventType
    payload: dict[str, Any] = Field(default_factory=dict)
    recorded_at: datetime | None = None


class EffectRecord(BaseModel):
    id: UUID | None = None
    run_id: UUID
    step_sequence: int
    tool_name: str
    idempotency_key: str
    status: EffectStatus = EffectStatus.PENDING
    payload: dict[str, Any] = Field(default_factory=dict)
    response: dict[str, Any] | None = None
    applied_at: datetime | None = None


class ApprovalRecord(BaseModel):
    id: UUID | None = None
    run_id: UUID
    step_sequence: int
    token: str
    action_type: str
    proposed_payload: dict[str, Any] = Field(default_factory=dict)
    status: ApprovalStatus = ApprovalStatus.PENDING
    decision_reason: str | None = None
    expires_at: datetime
    created_at: datetime | None = None
    decided_at: datetime | None = None
