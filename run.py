#!/usr/bin/env python3
"""Run an agentic SDLC scenario end to end.

  python run.py greenfield|brownfield|ambiguous|all [--fault bug|secret|drift] [--interactive]
"""
import argparse
import sys

from orchestrator.approvals import InteractiveApprover
from scenarios.runner import run_scenario

SCENARIOS = ["greenfield", "brownfield", "ambiguous"]

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario", choices=SCENARIOS + ["all"])
    ap.add_argument("--fault", choices=["bug", "secret", "drift"], help="inject a failure into the implementation agent")
    ap.add_argument("--fault-feature", help="restrict the fault to one change (brownfield), e.g. expiry")
    ap.add_argument("--interactive", action="store_true", help="a real human answers approvals/clarifications on stdin")
    ap.add_argument("--out", default="runs")
    a = ap.parse_args()
    code = 0
    for name in (SCENARIOS if a.scenario == "all" else [a.scenario]):
        print(f"\n=== {name} " + "=" * 60)
        eng = run_scenario(name, a.out, InteractiveApprover() if a.interactive else None, a.fault, a.fault_feature)
        m = eng.metrics.summary(len(eng.graph.tasks))
        print(f"--> {eng.state.value} {eng.stop_reason}\n    tasks {m['tasks_done']}/{m['tasks_total']}, retries={m['retries']}, "
              f"rollbacks={m['rollbacks']}, replans={m['replans']}, mttr={m['mttr_s']}s, e2e={m['end_to_end_latency_s']}s")
        print(f"    summary: {a.out}/{name}/SUMMARY.md   audit: {a.out}/{name}/audit.jsonl   code: {a.out}/{name}/workspace/")
        code |= eng.state.value != "COMPLETED"
    sys.exit(int(code))
