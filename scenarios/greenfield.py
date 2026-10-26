"""Scenario 1 — greenfield: well-defined requirement, new system."""
from orchestrator.agents import *  # noqa: F401,F403
from orchestrator.approvals import ScriptedApprover
from orchestrator.core import Task
from orchestrator.graph import Graph
from .common import standard_tail

TITLE = "Greenfield: URL shortener service"
REQUIREMENT = ("Build a URL shortener service from scratch with core APIs (shorten, redirect, custom alias, delete), "
               "analytics, and reliability features.")
CONFIG = {}
META = {
    "title": TITLE,
    "assumptions": ["Single-node deployment; SQLite is acceptable persistence for a prototype.",
                    "URLs are public web resources; no authentication is required to create links (rate limiting is a brownfield follow-up)."],
    "risks": [("Open redirect / SSRF-style abuse via malicious targets", "scheme allowlist, credential and internal-address rejection at creation; 302 only"),
              ("Raw client IP stored for analytics (personal data)", "policy rule P-COMP-001 flags it as a WARN, carried into release approval; remediated in the ambiguous scenario"),
              ("Collision on random codes", "bounded retry then 503; 62^7 code space"),
              ("Trade-off: write-per-click analytics", "exact and simple now; async batching is the scale path")],
    "limitations": ["DNS-based SSRF (hostname resolving to a private IP) is not checked.",
                    "SQLite single-writer: not horizontally scalable; no auth on delete/create.",
                    "Generation is template-driven; an LLM agent can replace it behind the same agent interface."],
}


def default_approver():
    return ScriptedApprover(decisions={"release": (True, "tests+security green; accepts WARN P-COMP-001 (raw IP) for this prototype; tracked for remediation")})


def build(policy):
    g = [
        Task("requirements", "requirements", requirements_agent, [], produces=["requirements"], entry_gates=[requirement_present],
             description="interpret intent, normalize into stories, flag ambiguity"),
        Task("design", "design", design_agent, ["requirements"], ["requirements"], ["design"], description="architecture + ADRs"),
        Task("api_contract", "design", api_contract_agent, ["requirements"], ["requirements"], ["api_contract"], description="API/schema contract"),
        Task("implement", "implementation", implement_greenfield, ["design", "api_contract"], ["requirements", "design"], description="generate service source"),
        Task("tests", "testing", tests_greenfield, ["api_contract"], ["api_contract"], description="author unit + API tests (parallel with implement)"),
    ]
    g += standard_tail(policy, ["implement", "tests"], lambda ctx, reason: "implement", ["implement"])
    return Graph(g)


def seed(ws):
    pass
