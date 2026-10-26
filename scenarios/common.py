import re

from orchestrator.core import Task
from orchestrator.agents import (baseline_agent, clarify_agent, design_agent, docs_agent, release_agent, requirements_agent,
                                 requirement_present, security_agent_factory, tests_green, validate_agent)


def failure_files(reason):
    return re.findall(r"tests/test_(\w+)\.py", reason)


def standard_tail(policy, validate_deps, repair, security_deps):
    """validate -> security -> docs -> release, shared by all scenarios."""
    return [
        Task("validate", "testing", validate_agent, validate_deps, repair_target=repair, max_retries=2,
             description="run full test suite; failures are routed to the producing task"),
        Task("security", "security", security_agent_factory(policy), security_deps, description="whole-tree security/compliance review"),
        Task("docs", "documentation", docs_agent, ["validate", "security"], consumes=["design"], description="README + API docs from OpenAPI"),
        Task("release", "release", release_agent, ["validate", "security", "docs"], high_impact=True,
             description="release readiness checklist; human sign-off"),
    ]


def baseline_task():
    return Task("baseline", "testing", baseline_agent, [], exit_gates=[tests_green], max_retries=0,
                description="establish green baseline before touching existing code")
