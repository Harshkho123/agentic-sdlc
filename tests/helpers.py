from orchestrator.approvals import ScriptedApprover
from orchestrator.audit import AuditLog
from orchestrator.context import Context
from orchestrator.core import Result, Task
from orchestrator.engine import Engine
from orchestrator.graph import Graph
from orchestrator.policy import Policy
from orchestrator.workspace import Workspace


def make(tmp_path, tasks, approver=None, **kw):
    ws = Workspace(tmp_path / "ws")
    ctx = Context("t", ws, AuditLog(tmp_path / "audit.jsonl", "t"), approver or ScriptedApprover(), "req")
    return Engine(Graph(tasks), ctx, Policy(), backoff_base=0.001, **kw)


def T(id, agent=None, deps=(), **kw):
    return Task(id, kw.pop("stage", "implementation"), agent or (lambda c, t, a: Result()), list(deps), **kw)


def files(**fs):
    return lambda c, t, a: Result(files={k.replace("__", "/"): v for k, v in fs.items()})


def events(engine, name):
    return [e for e in engine.ctx.audit.events() if e["event"] == name]


def files_at(path, content):
    return lambda c, t, a: Result(files={path: content})
