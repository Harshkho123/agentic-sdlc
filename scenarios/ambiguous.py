"""Scenario 3 — ambiguous requirement: clarification checkpoint + dynamic re-planning."""
from orchestrator.agents import *  # noqa: F401,F403
from orchestrator.approvals import ScriptedApprover
from orchestrator.core import Task
from orchestrator.graph import Graph
from .common import baseline_task, standard_tail, failure_files

TITLE = "Ambiguous: \"make it faster and more private\""
REQUIREMENT = "Make the shortener faster and more private."
CONFIG = {"design_latency": 0.3}   # lets the draft design overlap with the human clarification, so re-planning is observable
ANSWERS = {"speed": "p95 redirect < 20 ms on read-heavy traffic; in-process caching is acceptable",
           "privacy": "Do not store raw visitor IPs; only admins may see analytics; scrub raw IPs already stored"}
CHANGE_SETS = [("cache_privacy", {"cache", "privacy_ip", "admin_stats", "scrub_legacy_ip"},
                P.CACHE_PRIVACY_SRC, P.CACHE_PRIVACY_TESTS, P.CACHE_PRIVACY_TEST_MIGRATION)]
META = {
    "title": TITLE,
    "assumptions": ["Answers to blocking questions come from a human owner (simulated reviewer in the demo run).",
                    "In-process cache is acceptable because the service is single-node."],
    "risks": [("Agent guesses what 'private' means and ships the wrong thing", "blocking ambiguity -> human checkpoint; design/implementation built on the draft are discarded and re-planned"),
              ("Irreversible scrub of stored IPs", "destructive-SQL policy rule forces explicit human approval before commit"),
              ("Breaking API change (/stats now needs a token)", "fails closed; legacy tests migrated only after failure feedback, audit-visible"),
              ("Trade-off: cache staleness", "LRU+TTL (60 s) bounds staleness; delete invalidates explicitly")],
    "limitations": ["Hash salt is per-process unless VISITOR_SALT is set: unique-visitor counts reset on restart.",
                    "Column is still named `ip` (holds a hash); renaming is a separate migration.",
                    "Cache is per-node."],
}


def default_approver():
    return ScriptedApprover(
        answers=ANSWERS,
        decisions={"impl_cache_privacy": (True, "owner confirms raw IPs have no business use; irreversible scrub accepted"),
                   "release": (True, "all gates green; privacy WARN resolved; /stats contract change communicated")})


def router(ctx, reason):
    return "tests_cache_privacy" if "tests/test_api.py" in reason else "impl_cache_privacy"


def build(policy):
    g = [
        baseline_task(),
        Task("requirements", "requirements", requirements_agent, [], produces=["requirements"], entry_gates=[requirement_present],
             description="draft interpretation; flags blocking ambiguities"),
        Task("clarify", "requirements", clarify_agent, ["requirements"], ["requirements"], ["requirements"],
             description="HUMAN CHECKPOINT: answer blocking questions; republishes requirements"),
        Task("design", "design", design_agent, ["requirements"], ["requirements"], ["design"],
             description="drafts under assumptions in parallel with clarification; re-run when requirements change"),
        Task("impact", "design", impact_agent_factory(CHANGE_SETS), ["design", "clarify"],
             ["requirements", "design"], ["impact"], description="impact analysis + dynamic implementation sub-graph"),
    ]
    g += standard_tail(policy, ["baseline", "impact"], router, ["impact"])  # nothing downstream may start before the dynamic plan exists
    return Graph(g)


def seed(ws):
    ws.seed(load_template(("src/", "tests/", "requirements.txt")))
