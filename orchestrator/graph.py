"""Explicit task dependency graph with cycle detection."""
from .core import Task


class GraphError(Exception):
    pass


class Graph:
    def __init__(self, tasks=()):
        self.tasks: dict[str, Task] = {}
        for t in tasks:
            self.add(t)

    def __getitem__(self, tid):
        return self.tasks[tid]

    def add(self, task: Task):
        if task.id in self.tasks:
            raise GraphError(f"duplicate task id {task.id}")
        self.tasks[task.id] = task

    def validate(self):
        for t in self.tasks.values():
            for d in t.deps:
                if d not in self.tasks:
                    raise GraphError(f"{t.id} depends on unknown task {d}")
        self.topo()  # raises on cycles

    def topo(self):
        order, state = [], {}

        def visit(tid, path):
            if state.get(tid) == 2:
                return
            if state.get(tid) == 1:
                raise GraphError("cycle: " + " -> ".join(path + [tid]))
            state[tid] = 1
            for d in self.tasks[tid].deps:
                visit(d, path + [tid])
            state[tid] = 2
            order.append(tid)

        for tid in self.tasks:
            visit(tid, [])
        return order

    def children(self, tid):
        return [t.id for t in self.tasks.values() if tid in t.deps]

    def descendants(self, tid):
        """Transitive dependents, in topological order."""
        seen, stack = set(), [tid]
        while stack:
            for c in self.children(stack.pop()):
                if c not in seen:
                    seen.add(c)
                    stack.append(c)
        return [t for t in self.topo() if t in seen]

    def layers(self):
        """Parallelism view: tasks in the same layer have no path between them."""
        depth = {}
        for tid in self.topo():
            depth[tid] = 1 + max((depth[d] for d in self.tasks[tid].deps), default=-1)
        out = {}
        for tid, d in depth.items():
            out.setdefault(d, []).append(tid)
        return [out[k] for k in sorted(out)]

    def mermaid(self):
        lines = ["graph TD"]
        for t in self.tasks.values():
            mark = {"DONE": "✓", "FAILED": "✗", "SKIPPED": "–"}.get(t.status.value, "")
            lines.append(f'  {t.id}["{t.id} {mark}<br/><i>{t.stage}</i>"]')
        for t in self.tasks.values():
            for d in t.deps:
                lines.append(f"  {d} --> {t.id}")
        return "\n".join(lines)
