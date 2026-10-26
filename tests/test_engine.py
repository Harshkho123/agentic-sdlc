import threading
import time

import pytest

from orchestrator.approvals import ScriptedApprover
from orchestrator.audit import AuditLog
from orchestrator.core import Result, RunState, Status
from orchestrator.graph import Graph, GraphError
from orchestrator.workspace import Workspace, WorkspaceError
from tests.helpers import T, events, files, files_at, make


# ---------------------------------------------------------------- graph / parallelism
def test_cycle_and_unknown_dependency_rejected():
    with pytest.raises(GraphError):
        Graph([T("a", deps=["b"]), T("b", deps=["a"])]).validate()
    with pytest.raises(GraphError):
        Graph([T("a", deps=["ghost"])]).validate()


def test_independent_tasks_really_run_in_parallel_and_join_synchronises(tmp_path):
    barrier, order = threading.Barrier(2, timeout=5), []   # deadlocks (-> failure) unless both run concurrently

    def par(name):
        def agent(c, t, a):
            barrier.wait()
            order.append(name)
            return Result()
        return agent

    def join(c, t, a):
        order.append("join")
        return Result()

    eng = make(tmp_path, [T("a", par("a")), T("b", par("b")), T("j", join, ["a", "b"])])
    assert eng.run() == RunState.COMPLETED
    assert order[-1] == "join" and set(order[:2]) == {"a", "b"}


# ---------------------------------------------------------------- failure ladder
def test_bounded_retry_then_success_records_mttr(tmp_path):
    calls = []

    def flaky(c, t, a):
        calls.append(a)
        if a < 3:
            raise RuntimeError("transient")
        return Result()

    eng = make(tmp_path, [T("a", flaky, max_retries=2)])
    assert eng.run() == RunState.COMPLETED and calls == [1, 2, 3]
    m = eng.metrics.summary(1)
    assert m["retries"] == 2 and m["mttr_s"] is not None and m["first_attempt_success_rate"] == 0


def test_retries_exhausted_rolls_back_and_safe_stops_downstream(tmp_path):
    eng = make(tmp_path, [T("w", files(a__txt="1")),
                          T("bad", lambda c, t, a: (_ for _ in ()).throw(ValueError("nope")), ["w"], max_retries=1, rollback_scope=["w"]),
                          T("after", deps=["bad"])])
    assert eng.run() == RunState.SAFE_STOPPED
    assert eng.graph["bad"].status == Status.FAILED and eng.graph["after"].status == Status.SKIPPED
    assert not (eng.ctx.ws.root / "a.txt").exists()          # rollback_scope undone
    assert eng.graph["bad"].attempts == 2                      # 1 try + 1 retry, never more


def test_fallback_agent_is_used_after_retries(tmp_path):
    def primary(c, t, a):
        raise RuntimeError("primary broken")

    eng = make(tmp_path, [T("a", primary, max_retries=0, fallback=lambda c, t, a: Result(notes="fallback"))])
    assert eng.run() == RunState.COMPLETED and eng.metrics.fallbacks == 1 and events(eng, "fallback_engaged")


def test_exit_gate_failure_rolls_back_the_commit(tmp_path):
    eng = make(tmp_path, [T("a", files(x__txt="data"), max_retries=0, exit_gates=[lambda c, r: (False, "invariant violated")])])
    assert eng.run() == RunState.SAFE_STOPPED
    assert not (eng.ctx.ws.root / "x.txt").exists() and eng.metrics.rollbacks == 1


def test_entry_gate_blocks_execution(tmp_path):
    ran = []
    eng = make(tmp_path, [T("a", lambda c, t, a: ran.append(1) or Result(), max_retries=0, entry_gates=[lambda c: (False, "precondition")])])
    assert eng.run() == RunState.SAFE_STOPPED and not ran


def test_repair_routing_reruns_producer_with_feedback_and_cascades(tmp_path):
    seen = []

    def producer(c, t, a):
        seen.append((a, c.feedback.get("p")))
        return Result(files={"v.txt": "good" if a > 1 else "bad"})

    def check(c, t, a):
        if c.ws.read("v.txt") != "good":
            raise RuntimeError("v.txt is bad")
        return Result()

    eng = make(tmp_path, [T("p", producer), T("c", check, ["p"], repair_target="p", max_retries=1)])
    assert eng.run() == RunState.COMPLETED
    assert seen[1][0] == 2 and "v.txt is bad" in seen[1][1] and eng.metrics.rollbacks == 1


def test_execution_budget_stops_runaway(tmp_path):
    eng = make(tmp_path, [T("a", lambda c, t, a: (_ for _ in ()).throw(RuntimeError("x")), max_retries=100)], max_executions=4)
    assert eng.run() == RunState.SAFE_STOPPED and "budget" in eng.stop_reason


# ---------------------------------------------------------------- governance
def test_critical_policy_violation_safe_stops_and_commits_nothing(tmp_path):
    eng = make(tmp_path, [T("a", files_at('src/cfg.py', 'KEY = "AKIAIOSFODNN7EXAMPLE"\n')), T("b", deps=["a"])])
    assert eng.run() == RunState.SAFE_STOPPED and "P-SEC-001" in eng.stop_reason
    assert eng.ctx.ws.files() == [] and eng.graph["b"].status == Status.SKIPPED


def test_blocking_finding_is_retried_not_committed(tmp_path):
    def agent(c, t, a):
        return Result(files={"src/x.py": "eval(input())\n" if a == 1 else "print(1)\n"})

    eng = make(tmp_path, [T("a", agent)])
    assert eng.run() == RunState.COMPLETED
    assert eng.ctx.ws.read("src/x.py") == "print(1)\n" and eng.metrics.policy_blocks == 1


def test_high_impact_requires_recorded_human_approval(tmp_path):
    eng = make(tmp_path, [T("rel", files(R__md="x"), high_impact=True)])
    assert eng.run() == RunState.COMPLETED
    assert eng.ctx.approvals[0]["task"] == "rel" and "SIMULATED" in eng.ctx.approvals[0]["approver"]


def test_rejected_approval_halts_before_any_change(tmp_path):
    eng = make(tmp_path, [T("rel", files(R__md="x"), high_impact=True), T("n", deps=["rel"])],
               approver=ScriptedApprover(decisions={"rel": (False, "not ready")}))
    assert eng.run() == RunState.SAFE_STOPPED and eng.ctx.ws.files() == [] and eng.metrics.approvals["rejected"] == 1


def test_change_control_protected_path_needs_approval(tmp_path):
    eng = make(tmp_path, [T("a", files_at("requirements.txt", "fastapi\n"))])
    eng.run()
    assert eng.ctx.approvals and "protected path" in eng.ctx.approvals[0]["reasons"][0]


def test_non_allowlisted_dependency_is_blocked(tmp_path):
    eng = make(tmp_path, [T("a", files_at("requirements.txt", "left-pad-evil\n"), max_retries=0)])
    assert eng.run() == RunState.SAFE_STOPPED and "allowlist" in eng.stop_reason


# ---------------------------------------------------------------- dynamic re-planning
def test_upstream_change_invalidates_dependents_and_rolls_back_their_files(tmp_path):
    state = {"n": 0}

    def source(c, t, a):
        state["n"] += 1
        return Result(artifacts={"spec": f"v{state['n']}"})

    def reviser(c, t, a):          # publishes a *new* spec after `consumer` may already have used v1
        time.sleep(0.2)
        return Result(artifacts={"spec": "v-final"})

    def consumer(c, t, a):
        return Result(files={"out.txt": c.get("spec")})

    eng = make(tmp_path, [T("src", source, produces=["spec"]),
                          T("rev", reviser, ["src"], consumes=["spec"], produces=["spec"]),
                          T("use", consumer, ["src"], consumes=["spec"])])
    assert eng.run() == RunState.COMPLETED
    assert eng.ctx.ws.read("out.txt") == "v-final"           # rebuilt on the final input
    assert eng.metrics.replans >= 1 and eng.ctx.version("spec") == 2


def test_unchanged_artifact_does_not_trigger_replan(tmp_path):
    eng = make(tmp_path, [T("a", lambda c, t, x: Result(artifacts={"s": 1}), produces=["s"]),
                          T("b", lambda c, t, x: Result(artifacts={"s": 1}), ["a"], consumes=["s"], produces=["s"])])
    eng.run()
    assert eng.metrics.replans == 0 and eng.ctx.version("s") == 1


def test_dynamic_plan_extension_adds_and_joins_tasks(tmp_path):
    def planner(c, t, a):
        return Result(new_tasks=[T("dyn1"), T("dyn2")], join={"final": ["dyn1", "dyn2"]})

    eng = make(tmp_path, [T("plan", planner), T("final", deps=["plan"])])
    assert eng.run() == RunState.COMPLETED and eng.graph["final"].deps == ["plan", "dyn1", "dyn2"]


def test_invalid_plan_extension_is_rejected_atomically(tmp_path):
    def planner(c, t, a):   # tries to add a dependency to a task that already started
        return Result(new_tasks=[T("dyn")], join={"started": ["dyn"]})

    eng = make(tmp_path, [T("started"), T("plan", planner, ["started"], max_retries=0)])
    assert eng.run() == RunState.SAFE_STOPPED
    assert "dyn" not in eng.graph.tasks and eng.graph["started"].deps == []


# ---------------------------------------------------------------- audit / workspace / metrics
def test_audit_log_is_hash_chained_and_detects_tampering(tmp_path):
    eng = make(tmp_path, [T("a", files(x__txt="1")), T("b", deps=["a"])])
    eng.run()
    path = eng.ctx.audit.path
    assert AuditLog.verify(path)[0]
    lines = path.read_text().splitlines()
    path.write_text("\n".join(lines[:3] + [lines[3].replace('"a"', '"z"')] + lines[4:]) + "\n")
    assert not AuditLog.verify(path)[0]
    path.write_text("\n".join(lines[:2] + lines[3:]) + "\n")          # deletion
    assert not AuditLog.verify(path)[0]


def test_decision_lineage_records_input_versions(tmp_path):
    eng = make(tmp_path, [T("a", lambda c, t, x: Result(artifacts={"s": 1}), produces=["s"]),
                          T("b", lambda c, t, x: Result(decisions=[("chose X", "because s")]), ["a"], consumes=["s"])])
    eng.run()
    d = [d for d in eng.ctx.decisions if d.task == "b"][0]
    assert d.inputs == {"s": 1}


def test_workspace_blocks_path_escape_and_rolls_back_exactly(tmp_path):
    ws = Workspace(tmp_path / "w")
    ws.seed({"a.txt": "orig"})
    with pytest.raises(WorkspaceError):
        ws.commit("t", {"../evil.txt": "x"})
    ws.commit("t", {"a.txt": "new", "b.txt": "added"})
    ws.rollback("t")
    assert ws.read("a.txt") == "orig" and ws.read("b.txt") is None
