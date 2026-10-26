# Architecture

## 1. Components

```
                 ┌────────────────────────── Engine (single coordinating thread) ──────────────────────────┐
 requirement ──► │  Graph (DAG, gates)   Context (versioned artifacts, decision lineage, feedback, findings)│
                 │        │                              │                                                  │
                 │   scheduler ──► worker pool ──► Agents (propose: files + artifacts + decisions + new tasks)│
                 │        ▲                              │                                                  │
                 │        │      ┌───────────────────────▼──────────────────────┐                           │
                 │  re-plan/    │ Policy ► Approval ► Workspace.commit ► Exit gates ► publish artifacts      │
                 │  invalidate  └───────────────────────┬──────────────────────┘                           │
                 │        └──────── failure ladder ◄────┘   Metrics   AuditLog (hash chain)               │
                 └───────────────────────────────────────────────────────────────────────────────────────────┘
```

| Component | File | Responsibility |
|---|---|---|
| Task graph | `graph.py`, `core.py` | explicit dependencies, cycle detection, descendants, parallel layers, Mermaid export |
| Engine | `engine.py` | scheduling, gates, completion pipeline, failure ladder, re-planning, safe-stop |
| Agents | `agents.py` | requirements, clarify, design, API contract, impact analysis, implement, tests, validate, security, docs, release |
| Policy | `policy.py` | security / compliance / change-control guardrails with severities |
| Approvals | `approvals.py` | human checkpoints (`ScriptedApprover` for reproducible demos, `InteractiveApprover` for real humans) |
| Context | `context.py` | versioned artifacts, decision lineage, feedback channel, findings, approvals |
| Workspace | `workspace.py` | sandboxed, journaled commits → exact rollback; path-escape protection |
| Audit | `audit.py` | append-only JSONL, each record hash-chained (tamper/deletion/reorder detectable) |
| Metrics | `metrics.py` | success rate, first-attempt success, retries, fallbacks, rollbacks, re-plans, MTTR, latency, approval wait |
| Report | `report.py` | final engineering summary generated *from run state*, not hand-written |

## 2. Orchestration model

**Explicit dependency graph with entry/exit gates.** A task is runnable when all dependencies are `DONE` and its entry gates pass.
Exit gates run *after* a tentative commit (so they can inspect real files, e.g. "baseline tests green") and a failure rolls the commit back.

**Sequential and parallel paths with synchronisation.** Independent branches run concurrently in a thread pool
(e.g. `design ∥ api_contract`, `implement ∥ tests`, `validate ∥ security`); joins (`validate`, `release`) are ordinary multi-dependency
tasks. The test-suite proves real concurrency with a 2-party barrier that would deadlock if tasks were serialised.

**Non-linear, stateful execution.** The state of a run is (task statuses, versioned artifacts, workspace journal, decision lineage).
Control can move *backwards*: a failed validation re-runs its producer with feedback and cascades invalidation downstream;
changed requirements invalidate everything built on the old ones.

**Dynamic planning.** An agent may extend the graph (`Result.new_tasks` + `Result.join`). In brownfield work the impact-analysis task
parses the codebase (AST), predicts the blast radius, and emits the implementation sub-graph: changes touching the same file are
*serialised*, disjoint ones run in parallel. Admission is governed: capped task count, cycle check, only not-yet-started tasks can
gain dependencies, and the whole extension is atomic (any failure restores the previous plan).

**Re-planning on upstream change.** Artifacts are versioned by content hash. Each task records the artifact hashes it started with.
If an upstream artifact changes (a) while it runs → its result is **discarded**; (b) after it finished → it and all dependents are
**invalidated, their commits rolled back** (reverse order) and re-run. Re-publishing identical content never triggers anything, and
a re-plan budget stops oscillation.

**Cross-stage context and decision lineage.** Every decision records task, rationale, and the *artifact versions it was based on*
(`ctx.decisions`, mirrored into the audit log). `SUMMARY.md` §3–4 shows who decided what from which inputs.

## 3. Control flow of one task

```
ready → entry gates → RUN agent (proposal only)
      → stale inputs? ──yes──► discard, re-run
      → policy scan ── critical ──► SAFE-STOP     ── block ──► failure ladder
      → human approval needed? (high-impact | protected path | destructive SQL | blast radius | release) ── rejected ──► SAFE-STOP
      → commit (journaled) → exit gates ── fail ──► rollback + failure ladder
      → publish artifacts (+decisions, +new tasks) → DONE → re-plan check
```

**Failure ladder (all bounded):**
1. *Retry with exponential backoff* (`max_retries`).
2. *Repair routing*: `repair_target` (task id or `fn(ctx, reason)`) re-runs the **producer** with the failure text in `ctx.feedback`
   (a validation task cannot fix code by re-running itself). Invalidated downstream work is rolled back.
3. *Fallback agent* (once).
4. *Rollback* of the task's commits plus `rollback_scope`.
5. *Safe-stop*: freeze the run, mark remaining tasks `SKIPPED`, keep audit + good work for a human. Also triggered immediately by
   critical policy findings, human rejection, execution-budget exhaustion, re-plan budget exhaustion, or a stalled graph.

## 4. Governance

| Guardrail | Rule | Effect |
|---|---|---|
| Security | P-SEC-001 secrets (critical) / hard-coded credentials (block); P-SEC-002 eval/exec/os.system/pickle/shell=True | safe-stop / retry |
| Change control | P-CHG-001 protected paths, destructive SQL, blast radius > 15 files / 900 lines → **human approval**; P-CHG-002 dependency allowlist | approval / block |
| Compliance | P-COMP-001 raw client IP persisted (WARN, must be acknowledged at release) | recorded, shown to approver |
| Autonomy | agents cannot write files; cannot add dependencies to started tasks; cannot exceed 20 dynamic tasks; refuse to improvise when no approved change set covers the requirement | |
| Release | high-impact task: readiness checklist (tests, security, docs, approvals) **and** human sign-off | |

Policy checks the *diff* (added lines) so pre-existing content does not re-trigger approvals; the security task re-scans the whole tree.

## 5. Observability and traceability

- `audit.jsonl`: every state transition, gate result, policy finding, approval (with approver identity), rollback, re-plan, and
  decision; `AuditLog.verify` detects edits, deletions and re-ordering. The demo reviewer is labelled `SIMULATED human` in the log.
- Metrics (`SUMMARY.md` §6): task success rate, first-attempt success, retries, fallbacks, rollbacks, re-plans, policy blocks,
  approvals (+wait time), **MTTR** (first failure → eventual success of that task), end-to-end latency.

## 6. Key decisions and why

| Decision | Rationale / trade-off |
|---|---|
| Agents only *propose*; engine commits | the single choke point where policy, approval and rollback live; agents can be swapped (even for an LLM) without weakening governance |
| Single coordinating thread + worker pool | all state mutations are serial → deterministic and fully auditable; parallelism where it matters (agent work). Not distributed. |
| Content-hash artifact versions | cheap, exact staleness detection; no spurious re-plans |
| Commit before exit gates, journal for rollback | gates test real files (pytest) yet failures leave no residue |
| Repair routes to the producer | mirrors how engineers work; avoids "retry the test until it passes" |
| Anchored patches that fail on 0 or >1 matches | brownfield edits are *precise or rejected*; drift is detected, not guessed |
| Baseline tests on an isolated snapshot | found during development: running on the live workspace raced with concurrent commits |
| Refuse to improvise | if the requirement maps to no approved change set the run stops and asks, rather than inventing a design |
