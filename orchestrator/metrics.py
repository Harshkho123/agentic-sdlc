"""Reliability metrics: success rate, retries, rollbacks, MTTR, latency."""
import time
from collections import defaultdict


class Metrics:
    def __init__(self):
        self.t0 = time.time()
        self.t_end = None
        self.attempts = defaultdict(int)
        self.first_try_success = set()
        self.done = set()
        self.retries = 0
        self.rollbacks = 0
        self.replans = 0
        self.fallbacks = 0
        self.policy_blocks = 0
        self.approvals = {"approved": 0, "rejected": 0}
        self.approval_wait_s = 0.0
        self.task_latency = {}
        self._first_failure = {}
        self.recoveries = []

    def failure(self, task_id):
        self._first_failure.setdefault(task_id, time.time())

    def success(self, task_id, latency):
        self.done.add(task_id)
        self.task_latency[task_id] = latency
        if self.attempts[task_id] == 1:
            self.first_try_success.add(task_id)
        if task_id in self._first_failure:
            self.recoveries.append(time.time() - self._first_failure.pop(task_id))

    def summary(self, total_tasks):
        end = self.t_end or time.time()
        started = len(self.attempts)
        return {
            "tasks_total": total_tasks,
            "tasks_done": len(self.done),
            "task_success_rate": round(len(self.done) / total_tasks, 3) if total_tasks else 0,
            "first_attempt_success_rate": round(len(self.first_try_success) / started, 3) if started else 0,
            "retries": self.retries, "fallbacks": self.fallbacks, "rollbacks": self.rollbacks,
            "replans": self.replans, "policy_blocks": self.policy_blocks,
            "approvals": dict(self.approvals), "approval_wait_s": round(self.approval_wait_s, 3),
            "mttr_s": round(sum(self.recoveries) / len(self.recoveries), 3) if self.recoveries else None,
            "end_to_end_latency_s": round(end - self.t0, 3),
        }
