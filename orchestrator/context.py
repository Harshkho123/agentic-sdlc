"""Shared run context: versioned artifacts, decision lineage, feedback, findings, approvals."""
import threading
import uuid

from .approvals import ApprovalRequest
from .core import ArtifactVersion, Decision, digest


class Context:
    def __init__(self, run_id, workspace, audit, approver, requirement, config=None):
        self.run_id, self.ws, self.audit, self.approver = run_id, workspace, audit, approver
        self.requirement, self.config = requirement, config or {}
        self.artifacts: dict[str, list[ArtifactVersion]] = {}
        self.decisions: list[Decision] = []
        self.feedback: dict[str, str] = {}
        self.findings: list = []
        self.approvals: list[dict] = []
        self._lock = threading.Lock()

    # ---- artifacts -------------------------------------------------------------
    def get(self, name, default=None):
        vs = self.artifacts.get(name)
        return vs[-1].value if vs else default

    def version(self, name):
        vs = self.artifacts.get(name)
        return vs[-1].version if vs else 0

    def hash(self, name):
        vs = self.artifacts.get(name)
        return vs[-1].hash if vs else ""

    def publish(self, name, value, producer, inputs):
        with self._lock:
            h = digest(value)
            vs = self.artifacts.setdefault(name, [])
            if vs and vs[-1].hash == h:
                return vs[-1]  # unchanged content never triggers re-planning
            av = ArtifactVersion(name, len(vs) + 1, h, producer, dict(inputs), value)
            vs.append(av)
        self.audit.emit("artifact_published", artifact=name, version=av.version, hash=h, producer=producer, inputs=inputs)
        return av

    # ---- decision lineage --------------------------------------------------------
    def record(self, task, decision, rationale, inputs=None, actor="agent"):
        with self._lock:
            d = Decision(len(self.decisions) + 1, task, decision, rationale, dict(inputs or {}), actor)
            self.decisions.append(d)
        self.audit.emit("decision", seq=d.seq, task=task, decision=decision, rationale=rationale, inputs=d.inputs, actor=actor)
        return d

    # ---- human interaction ---------------------------------------------------------
    def clarify(self, task_id, questions):
        req = ApprovalRequest(f"clr-{uuid.uuid4().hex[:6]}", task_id, "clarification",
                              "Blocking ambiguities in the requirement", evidence={"questions": questions})
        self.audit.emit("clarification_requested", task=task_id, questions=[q["id"] for q in questions])
        dec = self.approver.clarify(req, questions)
        self.audit.emit("clarification_answered", task=task_id, approver=dec.approver, answers=dec.answers)
        self.record(task_id, "clarification received", "; ".join(f"{k}: {v}" for k, v in dec.answers.items()), actor=dec.approver)
        return dec
