"""Human-in-the-loop checkpoints. Agents only *request*; a Approver decides."""
from dataclasses import dataclass, field


@dataclass
class ApprovalRequest:
    id: str
    task_id: str
    kind: str                 # change | release | clarification
    summary: str
    risks: list = field(default_factory=list)
    evidence: dict = field(default_factory=dict)


@dataclass
class ApprovalDecision:
    approved: bool
    approver: str
    reason: str
    answers: dict = field(default_factory=dict)


class Approver:
    def decide(self, req: ApprovalRequest) -> ApprovalDecision:  # pragma: no cover - interface
        raise NotImplementedError

    def clarify(self, req: ApprovalRequest, questions: list) -> ApprovalDecision:  # pragma: no cover
        raise NotImplementedError


class ScriptedApprover(Approver):
    """Deterministic stand-in for a human so scenarios are reproducible. Always labelled as simulated in the audit log."""
    name = "scripted-reviewer (SIMULATED human)"

    def __init__(self, decisions=None, answers=None, default=(True, "reviewed evidence; within risk appetite")):
        self.decisions, self.answers, self.default = decisions or {}, answers or {}, default

    def decide(self, req):
        ok, why = self.decisions.get(req.task_id, self.default)
        return ApprovalDecision(ok, self.name, why)

    def clarify(self, req, questions):
        got = {q["id"]: self.answers[q["id"]] for q in questions if q["id"] in self.answers}
        return ApprovalDecision(bool(got), self.name, "answered clarification questions", got)


class InteractiveApprover(Approver):
    name = "interactive-reviewer"

    def decide(self, req):
        print(f"\n=== APPROVAL REQUIRED [{req.kind}] task={req.task_id}\n{req.summary}")
        for r in req.risks:
            print("  risk:", r)
        ans = input("approve? [y/N] ").strip().lower()
        why = input("reason: ").strip() or "(none given)"
        return ApprovalDecision(ans == "y", self.name, why)

    def clarify(self, req, questions):
        print(f"\n=== CLARIFICATION NEEDED task={req.task_id}")
        answers = {q["id"]: input(f"{q['question']}\n  (assumed: {q['assumed']}) > ").strip() for q in questions}
        return ApprovalDecision(all(answers.values()), self.name, "interactive answers", answers)
