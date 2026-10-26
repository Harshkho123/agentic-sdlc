"""End-to-end: the three required scenarios and the failure-injection modes, against real code and real pytest runs."""
import pytest

from orchestrator.audit import AuditLog
from orchestrator.core import RunState
from scenarios.runner import run_scenario


def run(tmp_path, name, **kw):
    return run_scenario(name, tmp_path, quiet=True, **kw)


def ev(eng, name):
    return [e["data"] for e in eng.ctx.audit.events() if e["event"] == name]


@pytest.fixture(scope="module")
def tmp_mod(tmp_path_factory):
    return tmp_path_factory.mktemp("runs")


def test_greenfield_end_to_end(tmp_mod):
    eng = run(tmp_mod, "greenfield")
    assert eng.state == RunState.COMPLETED and eng.metrics.summary(9)["tasks_done"] == 9
    assert eng.ctx.get("test_report")["passed"] and eng.ctx.ws.read("docs/API.md") and eng.ctx.ws.read("RELEASE_NOTES.md")
    assert [a["task"] for a in eng.ctx.approvals][-1] == "release"
    assert AuditLog.verify(eng.ctx.audit.path)[0]
    assert (tmp_mod / "greenfield" / "SUMMARY.md").exists()


def test_brownfield_dynamic_plan_serialises_overlapping_changes(tmp_mod):
    eng = run(tmp_mod, "brownfield")
    assert eng.state == RunState.COMPLETED
    impact = eng.ctx.get("impact")["features"]
    assert "src/shortener/app.py" in impact["expiry"]["modified"] and impact["ratelimit"]["serialised_after"] == ["impl_expiry"]
    assert "impl_ratelimit" in eng.graph.tasks and "impl_expiry" in eng.graph["impl_ratelimit"].deps
    assert ev(eng, "plan_extended") and "32 passed" in eng.ctx.get("test_report")["summary"]


def test_ambiguous_clarifies_replans_repairs_and_gates_irreversible_change(tmp_mod):
    eng = run(tmp_mod, "ambiguous")
    assert eng.state == RunState.COMPLETED
    assert ev(eng, "clarification_requested") and eng.ctx.version("requirements") == 2
    assert any("inputs changed" in d["why"] for d in ev(eng, "result_discarded"))          # draft design discarded
    assert eng.metrics.retries >= 1 and eng.metrics.rollbacks >= 1                          # legacy-test repair loop
    assert any(a["task"] == "impl_cache_privacy" and "destructive" in a["reasons"][0] for a in eng.ctx.approvals)
    assert not [f for f in eng.ctx.get("security_report")["findings"] if "P-COMP-001" in f]  # privacy warning resolved


def test_fault_bug_is_detected_by_tests_repaired_and_recovered(tmp_mod):
    eng = run(tmp_mod, "greenfield", fault="bug")
    assert eng.state == RunState.COMPLETED and eng.graph["implement"].attempts == 2
    assert eng.metrics.retries == 1 and eng.metrics.rollbacks >= 1 and eng.metrics.summary(9)["mttr_s"] is not None


def test_fault_secret_safe_stops_with_nothing_committed(tmp_mod):
    eng = run(tmp_mod, "greenfield", fault="secret")
    assert eng.state == RunState.SAFE_STOPPED and "P-SEC-001" in eng.stop_reason
    assert not [f for f in eng.ctx.ws.files() if f.startswith("src/")]


def test_fault_drift_exhausts_retries_rolls_back_and_safe_stops(tmp_mod):
    eng = run(tmp_mod, "brownfield", fault="drift", fault_feature="expiry")
    assert eng.state == RunState.SAFE_STOPPED and "impl_expiry" in eng.stop_reason
    assert "expires_at" not in eng.ctx.ws.read("src/shortener/storage.py")                  # baseline code untouched
    assert eng.graph["release"].status.value == "SKIPPED"


def test_human_rejection_blocks_release(tmp_mod):
    from orchestrator.approvals import ScriptedApprover
    eng = run(tmp_mod, "greenfield", approver=ScriptedApprover(decisions={"release": (False, "privacy review pending")}))
    assert eng.state == RunState.SAFE_STOPPED and "human rejected release" in eng.stop_reason
    assert eng.ctx.ws.read("RELEASE_NOTES.md") is None
