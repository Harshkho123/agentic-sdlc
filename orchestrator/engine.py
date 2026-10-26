"""Stateful DAG engine with governance.

Control loop (single coordinating thread; agents run in a worker pool and can only *propose*):

  ready? -> entry gates -> run agent -> [stale? discard] -> policy -> human approval -> commit
         -> exit gates -> publish artifacts -> mark DONE -> re-plan (invalidate anything built on changed inputs)

Failure ladder (all bounded): retry w/ backoff | repair upstream producer w/ feedback | fallback agent
                              -> rollback -> safe-stop (freeze, hand to human).
"""
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

from .approvals import ApprovalRequest
from .core import RunState, Status
from .graph import Graph, GraphError
from .metrics import Metrics

MAX_DYNAMIC_TASKS = 20


class Engine:
    def __init__(self, graph: Graph, ctx, policy, max_workers=4, max_replans=6, max_executions=60, backoff_base=0.05):
        self.graph, self.ctx, self.policy = graph, ctx, policy
        self.max_workers, self.max_replans, self.max_executions = max_workers, max_replans, max_executions
        self.backoff_base = backoff_base
        self.metrics = Metrics()
        self.failures: dict[str, int] = {}
        self.executions = 0
        self.dynamic_added = 0
        self.running = {}
        self.state = RunState.RUNNING
        self.stop_reason = ""
        self.audit = ctx.audit

    # ------------------------------------------------------------------ main loop
    def run(self):
        self.graph.validate()
        self.audit.emit("run_started", tasks={t.id: {"stage": t.stage, "deps": t.deps} for t in self.graph.tasks.values()},
                        parallel_layers=self.graph.layers())
        with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            while self.state == RunState.RUNNING:
                self._launch_ready(ex)
                if not self.running:
                    if any(t.status == Status.PENDING and t.not_before > time.time() for t in self.graph.tasks.values()):
                        time.sleep(0.01)
                        continue
                    break
                done, _ = wait(list(self.running), return_when=FIRST_COMPLETED)
                for fut in done:
                    tid, epoch, started = self.running.pop(fut)
                    self._on_complete(tid, epoch, started, fut)
            for fut, (tid, _, _) in list(self.running.items()):  # drain in-flight work after a safe-stop
                fut.result()
                self.audit.emit("result_discarded", task=tid, why="run safe-stopped")
        if self.state == RunState.RUNNING:
            if all(t.status == Status.DONE for t in self.graph.tasks.values()):
                self.state = RunState.COMPLETED
            else:
                self._safe_stop("stalled: no runnable tasks remain")
        self.metrics.t_end = time.time()
        self.audit.emit("run_finished", state=self.state.value, reason=self.stop_reason,
                        metrics=self.metrics.summary(len(self.graph.tasks)))
        return self.state

    # ------------------------------------------------------------------ scheduling
    def _launch_ready(self, ex):
        now = time.time()
        for t in list(self.graph.tasks.values()):
            if self.state != RunState.RUNNING:
                return
            if t.status != Status.PENDING or t.not_before > now or len(self.running) >= self.max_workers:
                continue
            if any(self.graph[d].status != Status.DONE for d in t.deps):
                continue
            for gate in t.entry_gates:
                ok, why = gate(self.ctx)
                self.audit.emit("entry_gate", task=t.id, ok=ok, why=why)
                if not ok:
                    self._fail(t, f"entry gate: {why}")
                    break
            else:
                if self.executions >= self.max_executions:
                    return self._safe_stop(f"execution budget ({self.max_executions}) exhausted")
                self.executions += 1
                t.attempts += 1
                self.metrics.attempts[t.id] += 1
                t.status = Status.RUNNING
                t.inputs_seen = {a: self.ctx.hash(a) for a in t.consumes}
                agent = t.fallback if (t.used_fallback and t.fallback) else t.agent
                fut = ex.submit(self._invoke, agent, t, t.attempts)
                self.running[fut] = (t.id, t.epoch, time.time())
                self.audit.emit("task_started", task=t.id, attempt=t.attempts, fallback=t.used_fallback,
                                inputs={a: self.ctx.version(a) for a in t.consumes})

    def _invoke(self, agent, task, attempt):
        try:
            return agent(self.ctx, task, attempt), None
        except Exception as exc:  # agent faults are data, never crashes
            return None, f"{type(exc).__name__}: {exc}"

    # ------------------------------------------------------------------ completion pipeline
    def _on_complete(self, tid, epoch, started, fut):
        t, (result, error) = self.graph[tid], fut.result()
        latency = time.time() - started
        if self.state != RunState.RUNNING:
            return self.audit.emit("result_discarded", task=tid, why="run safe-stopped")
        if epoch != t.epoch:
            return self.audit.emit("result_discarded", task=tid, why="task invalidated while running")
        stale = self._stale_inputs(t)
        if stale:
            t.status = Status.PENDING
            self.metrics.replans += 1
            return self.audit.emit("result_discarded", task=tid, why=f"inputs changed while running: {stale}")
        if error:
            return self._fail(t, error)

        report = self.policy.check_change(result.files, self.ctx.ws.read)
        for f in report.findings:
            self.ctx.findings.append(f)
            self.audit.emit("policy_finding", task=tid, rule=f.rule, severity=f.severity, path=f.path, message=f.message)
        if report.critical:
            self.metrics.policy_blocks += len(report.critical)
            return self._safe_stop(f"critical policy violation by {tid}: {report.critical[0]}")
        if report.blocking:
            self.metrics.policy_blocks += len(report.blocking)
            return self._fail(t, "policy: " + "; ".join(str(f) for f in report.blocking))

        reasons = list(report.approval_reasons) + (["task is marked high-impact"] if t.high_impact else [])
        if reasons and not self._approve(t, reasons, result, report):
            return

        if result.files:
            self.ctx.ws.commit(tid, result.files)
            self.audit.emit("files_committed", task=tid, files=sorted(result.files))
        for gate in t.exit_gates:
            ok, why = gate(self.ctx, result)
            self.audit.emit("exit_gate", task=tid, ok=ok, why=why)
            if not ok:
                self._rollback(tid, "exit gate failed")
                return self._fail(t, f"exit gate: {why}")

        inputs = {a: self.ctx.version(a) for a in t.consumes}
        for name, value in result.artifacts.items():
            self.ctx.publish(name, value, tid, inputs)
        for decision, rationale in result.decisions:
            self.ctx.record(tid, decision, rationale, inputs)
        if result.new_tasks and not self._admit(t, result):
            return
        t.status = Status.DONE
        self.metrics.success(tid, latency)
        self.audit.emit("task_done", task=tid, attempts=t.attempts, latency_s=round(latency, 3), notes=result.notes)
        self._replan()

    def _stale_inputs(self, t):
        return [a for a in t.consumes if a not in t.produces and self.ctx.hash(a) != t.inputs_seen.get(a)]

    # ------------------------------------------------------------------ governance helpers
    def _approve(self, t, reasons, result, report):
        warns = sorted({str(f) for f in self.ctx.findings if f.severity == "warn"})
        files = sorted(result.files)
        req = ApprovalRequest(f"apr-{uuid.uuid4().hex[:6]}", t.id, "release" if t.stage == "release" else "change",
                              f"{t.id}: {t.description or t.stage} — {len(files)} file(s)", reasons + warns,
                              {"files": files, "notes": result.notes, "artifacts": list(result.artifacts)})
        self.audit.emit("approval_requested", task=t.id, request=req.id, reasons=reasons, files=files)
        t0 = time.time()
        dec = self.ctx.approver.decide(req)
        self.metrics.approval_wait_s += time.time() - t0
        self.metrics.approvals["approved" if dec.approved else "rejected"] += 1
        self.ctx.approvals.append({"request": req.id, "task": t.id, "approved": dec.approved, "approver": dec.approver,
                                   "reason": dec.reason, "reasons": reasons})
        self.audit.emit("approval_decided", task=t.id, request=req.id, approved=dec.approved, approver=dec.approver, reason=dec.reason)
        self.ctx.record(t.id, f"{'approved' if dec.approved else 'REJECTED'} {req.kind}", dec.reason, actor=dec.approver)
        if not dec.approved:
            t.status = Status.FAILED
            self._safe_stop(f"human rejected {t.id}: {dec.reason}")
        return dec.approved

    def _rollback(self, tid, why):
        files = self.ctx.ws.rollback(tid)
        if files:
            self.metrics.rollbacks += 1
            self.audit.emit("files_rolled_back", task=tid, files=files, why=why)
        return files

    def _invalidate(self, tid, why):
        """Reset a task and everything built on it; undo their workspace changes (reverse order)."""
        affected = [tid] + self.graph.descendants(tid)
        for x in reversed(affected):
            n = self.graph[x]
            if x == tid or n.status in (Status.DONE, Status.RUNNING):
                self._rollback(x, f"invalidated: {why}")
                n.epoch += 1
                n.status = Status.PENDING
        self.audit.emit("invalidated", root=tid, affected=affected, why=why)

    def _replan(self):
        for tid in self.graph.topo():
            t = self.graph[tid]
            if t.status != Status.DONE:
                continue
            changed = self._stale_inputs(t)
            if changed:
                self.metrics.replans += 1
                if self.metrics.replans > self.max_replans:
                    return self._safe_stop("re-plan budget exhausted (possible oscillation)")
                self.ctx.record(tid, "re-plan", f"inputs {changed} changed after {tid} completed; task and dependents re-run")
                self._invalidate(tid, f"upstream artifacts changed: {changed}")

    def _fail(self, t, reason):
        t.status = Status.PENDING
        self.failures[t.id] = self.failures.get(t.id, 0) + 1
        self.metrics.failure(t.id)
        self.audit.emit("task_failed", task=t.id, attempt=t.attempts, reason=reason[:800])
        if self.failures[t.id] <= t.max_retries:
            self.metrics.retries += 1
            target = t.repair_target(self.ctx, reason) if callable(t.repair_target) else t.repair_target
            if target:
                self.ctx.feedback[target] = reason
                self.ctx.record(t.id, f"route failure to {target}", f"validation failed; repairing producer. {reason[:200]}")
                self._invalidate(target, f"repair requested by {t.id}")
                return self.audit.emit("retry_scheduled", task=t.id, via=f"repair:{target}")
            t.not_before = time.time() + self.backoff_base * 2 ** (self.failures[t.id] - 1)
            return self.audit.emit("retry_scheduled", task=t.id, via="backoff", delay_s=round(t.not_before - time.time(), 3))
        if t.fallback and not t.used_fallback:
            t.used_fallback = True
            self.metrics.fallbacks += 1
            t.not_before = 0.0
            return self.audit.emit("fallback_engaged", task=t.id)
        t.status = Status.FAILED
        for x in [t.id] + t.rollback_scope:
            self._rollback(x, f"terminal failure of {t.id}")
        self._safe_stop(f"task {t.id} failed after bounded retries: {reason[:300]}")

    def _admit(self, parent, result):
        """Dynamic planning: an agent may extend the graph, within governance limits."""
        if self.dynamic_added + len(result.new_tasks) > MAX_DYNAMIC_TASKS:
            self._fail(parent, f"plan extension exceeds limit of {MAX_DYNAMIC_TASKS} tasks")
            return False
        saved = {tid: list(self.graph[tid].deps) for tid in result.join}   # atomic: restore on any failure
        try:
            for target in result.join:
                if self.graph[target].status != Status.PENDING:
                    raise GraphError(f"cannot add dependencies to already-started task {target}")
            for nt in result.new_tasks:
                self.graph.add(nt)
            for target, new_ids in result.join.items():
                self.graph[target].deps.extend(new_ids)
            self.graph.validate()
        except (GraphError, KeyError) as exc:
            for nt in result.new_tasks:
                self.graph.tasks.pop(nt.id, None)
            for tid, deps in saved.items():
                self.graph[tid].deps = deps
            self._fail(parent, f"invalid plan extension: {exc}")
            return False
        self.dynamic_added += len(result.new_tasks)
        self.audit.emit("plan_extended", by=parent.id, tasks={n.id: n.deps for n in result.new_tasks}, join=result.join)
        return True

    def _safe_stop(self, reason):
        if self.state != RunState.RUNNING:
            return
        self.state, self.stop_reason = RunState.SAFE_STOPPED, reason
        for t in self.graph.tasks.values():
            if t.status in (Status.PENDING, Status.RUNNING):
                t.status = Status.SKIPPED
        self.audit.emit("safe_stop", reason=reason)
