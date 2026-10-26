"""Scenario 2 — brownfield: enhance an existing codebase (expiry + rate limiting)."""
from orchestrator.agents import *  # noqa: F401,F403
from orchestrator.approvals import ScriptedApprover
from orchestrator.core import Task
from orchestrator.graph import Graph
from .common import baseline_task, standard_tail, failure_files

TITLE = "Brownfield: link expiry + rate limiting on an existing shortener"
REQUIREMENT = "Add optional link expiry (TTL) and rate limiting on link creation to curb abuse, without breaking existing clients or data."
CONFIG = {}
CHANGE_SETS = [("expiry", {"expiry"}, P.EXPIRY_SRC, P.EXPIRY_TESTS, None),
               ("ratelimit", {"ratelimit"}, P.RATELIMIT_SRC, P.RATELIMIT_TESTS, None)]
META = {
    "title": TITLE,
    "assumptions": ["Existing clients must keep working: new request fields are optional, old DBs are upgraded in place.",
                    "Rate limit keyed by direct client IP; deployment is behind no trusted proxy."],
    "risks": [("Schema change on a live DB", "additive ALTER TABLE only, forward-only, tested against a legacy-schema DB file"),
              ("Two features edit the same file (app.py)", "impact analysis serialises overlapping changes; disjoint ones run in parallel"),
              ("Rate limiter memory growth", "LRU-bounded bucket map (10k keys)"),
              ("Trade-off: in-process limiter", "no extra infra, but per-node; Redis-backed limiter is the multi-node path")],
    "limitations": ["Limiter ignores X-Forwarded-For (would be spoofable if trusted blindly).",
                    "Expiry is lazy (checked on read); expired rows are not purged.",
                    "Failure→producer routing is heuristic (test file name → feature)."],
}


def default_approver():
    return ScriptedApprover(decisions={"release": (True, "backward-compatible; additive migration; tests green")})


def router(ctx, reason):
    impact = ctx.get("impact")["features"]
    for name in failure_files(reason):
        if name in impact:
            return f"impl_{name}"
        module = {"service": "service.py", "api": "app.py"}.get(name)
        for f, info in impact.items():
            if module and any(x.endswith(module) for x in info["modified"]):
                return f"impl_{f}"
    return f"impl_{next(reversed(impact))}"


def build(policy):
    g = [
        baseline_task(),
        Task("requirements", "requirements", requirements_agent, [], produces=["requirements"], entry_gates=[requirement_present]),
        Task("impact", "design", impact_agent(), ["requirements"], ["requirements"], ["impact"],
             description="AST import graph -> impacted modules/routes/tests; plans implementation sub-graph dynamically"),
        Task("design", "design", design_agent, ["impact"], ["requirements", "impact"], ["design"], description="design delta + ADRs"),
    ]
    g += standard_tail(policy, ["baseline", "design"], router, ["design"])
    return Graph(g)


def impact_agent():
    return impact_agent_factory(CHANGE_SETS)


def seed(ws):
    ws.seed(load_template(("src/", "tests/", "requirements.txt")))
