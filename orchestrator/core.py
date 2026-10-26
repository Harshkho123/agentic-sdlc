"""Data model: tasks, results, gates, versioned artifacts, decision lineage."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional


class Status(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"  # never ran because the run was safe-stopped


class RunState(str, Enum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    SAFE_STOPPED = "SAFE_STOPPED"


def digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()[:12]


@dataclass
class Result:
    """What an agent *proposes*. Nothing is applied until policy, approval and gates pass."""
    artifacts: dict[str, Any] = field(default_factory=dict)
    files: dict[str, str] = field(default_factory=dict)       # workspace-relative path -> full new content
    decisions: list[tuple[str, str]] = field(default_factory=list)  # (decision, rationale)
    new_tasks: list["Task"] = field(default_factory=list)      # dynamic planning
    join: dict[str, list[str]] = field(default_factory=dict)   # existing task id -> new ids it must now wait for
    notes: str = ""


Gate = Callable[..., tuple[bool, str]]


@dataclass
class Task:
    id: str
    stage: str                                   # requirements|design|implementation|testing|security|documentation|release
    agent: Callable[..., Result]                 # (ctx, task, attempt) -> Result
    deps: list[str] = field(default_factory=list)
    consumes: list[str] = field(default_factory=list)   # artifact names read (drives staleness / re-planning)
    produces: list[str] = field(default_factory=list)
    entry_gates: list[Gate] = field(default_factory=list)   # (ctx) -> (ok, why)
    exit_gates: list[Gate] = field(default_factory=list)    # (ctx, result) -> (ok, why); run after commit
    high_impact: bool = False                    # always needs a human approval before commit
    max_retries: int = 2
    repair_target: Any = None                    # task id, or fn(ctx, reason)->id: upstream task re-run with feedback
    fallback: Optional[Callable[..., Result]] = None
    rollback_scope: list[str] = field(default_factory=list)  # extra tasks whose commits are undone on terminal failure
    description: str = ""

    # runtime state (owned by the engine)
    status: Status = Status.PENDING
    attempts: int = 0
    epoch: int = 0
    not_before: float = 0.0
    used_fallback: bool = False
    inputs_seen: dict[str, str] = field(default_factory=dict)


@dataclass
class ArtifactVersion:
    name: str
    version: int
    hash: str
    producer: str
    inputs: dict[str, int]       # artifact name -> version consumed (lineage)
    value: Any


@dataclass
class Decision:
    seq: int
    task: str
    decision: str
    rationale: str
    inputs: dict[str, int]
    actor: str = "agent"
