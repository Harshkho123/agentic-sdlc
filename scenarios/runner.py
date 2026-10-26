import importlib
import shutil
import time
from pathlib import Path

from orchestrator.audit import AuditLog
from orchestrator.context import Context
from orchestrator.engine import Engine
from orchestrator.policy import Policy
from orchestrator.report import write_summary
from orchestrator.workspace import Workspace

SHOW = {"task_started", "task_done", "task_failed", "retry_scheduled", "fallback_engaged", "files_rolled_back", "invalidated",
        "approval_requested", "approval_decided", "clarification_requested", "clarification_answered", "policy_finding",
        "plan_extended", "result_discarded", "safe_stop", "run_finished", "exit_gate"}


def echo(rec):
    ev, d = rec["event"], rec["data"]
    if ev not in SHOW or (ev == "exit_gate" and d["ok"]):
        return
    detail = {k: v for k, v in d.items() if k not in ("metrics", "tasks", "inputs", "request")}
    brief = ", ".join(f"{k}={str(v)[:90]}" for k, v in detail.items())
    print(f"  {rec['seq']:>3} {ev:<24} {brief}")


def run_scenario(name, out_dir="runs", approver=None, fault=None, fault_feature=None, quiet=False, max_workers=4):
    mod = importlib.import_module(f"scenarios.{name}")
    out = Path(out_dir) / name
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    run_id = f"{name}-{int(time.time())}"
    audit = AuditLog(out / "audit.jsonl", run_id, echo=None if quiet else echo)
    ws = Workspace(out / "workspace")
    mod.seed(ws)
    cfg = {**mod.CONFIG, "fault": fault, "fault_feature": fault_feature}
    ctx = Context(run_id, ws, audit, approver or mod.default_approver(), mod.REQUIREMENT, cfg)
    policy = Policy()
    engine = Engine(mod.build(policy), ctx, policy, max_workers=max_workers)
    engine.run()
    write_summary(engine, mod.META, out / "SUMMARY.md")
    return engine
