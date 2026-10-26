# Agentic SDLC Orchestrator — URL Shortener

A working prototype that turns a one-line requirement into a **reviewable engineering outcome** (code, tests, docs, release
notes, audit trail) through a *governed, stateful, DAG-based* agent pipeline. The product being built is a URL shortener
(FastAPI + SQLite); the differentiator is the orchestration layer that builds and changes it safely.

Principle: **agents propose, the engine disposes, humans own approvals and final quality.** Agents never write to the workspace;
every proposal passes policy → (human approval) → commit → exit gates before it becomes state.

## Setup

```bash
python3.12 -m venv .venv && source .venv/bin/activate   # 3.10+ works
pip install -r requirements.txt                          # fastapi uvicorn httpx pytest
```

No API keys or network are needed at run time: generation steps are deterministic (template + anchored patches), analysis steps
(AST import graph, pytest, OpenAPI extraction) run on the real workspace. An LLM-backed agent can replace any agent function
with the same signature `(ctx, task, attempt) -> Result`; nothing else changes.

## Run

```bash
python run.py greenfield          # scenario 1
python run.py brownfield          # scenario 2
python run.py ambiguous           # scenario 3
python run.py all

# controlled chaos (failure injection into the implementation agent)
python run.py greenfield --fault bug      # subtle defect -> tests catch it -> repair loop -> recovery
python run.py greenfield --fault secret   # hard-coded cloud key -> critical policy violation -> safe-stop, nothing committed
python run.py brownfield --fault drift --fault-feature expiry   # patch anchors drift -> bounded retries -> rollback -> safe-stop

python run.py ambiguous --interactive     # YOU answer the clarification questions and approvals on stdin
```

Each run writes `runs/<scenario>/`:

| file | what |
|---|---|
| `workspace/` | the generated/changed codebase (runnable: `PYTHONPATH=src uvicorn shortener.app:app`) |
| `SUMMARY.md` | final engineering summary: plan, rationale, artifacts, validation, risks, assumptions, limitations, metrics |
| `audit.jsonl` | hash-chained audit log (every decision, gate, approval, rollback) |

Pre-generated outputs of all three scenarios are in `examples/`.

## Test

```bash
pytest          # 35 tests: engine governance, policy rules, and the 3 scenarios + 3 failure modes end to end (~10 s)
```

## Layout

```
orchestrator/  core.py graph.py engine.py policy.py approvals.py audit.py context.py workspace.py metrics.py report.py agents.py
scenarios/     greenfield.py brownfield.py ambiguous.py common.py runner.py
templates/     v1/ (shortener source+tests the agents emit)   patches.py (anchored change sets for brownfield work)
tests/         test_engine.py test_policy.py test_scenarios.py
docs/          ARCHITECTURE.md  SCENARIOS.md
```

See `docs/ARCHITECTURE.md` (design, control flow, decisions) and `docs/SCENARIOS.md` (decomposition per scenario).

## Testing approach, trade-offs, limitations (short form)

- **Testing**: the orchestrator is tested with synthetic tasks (parallelism via a barrier that deadlocks if tasks are serialised,
  bounded retries, rollback exactness, safe-stop, replan, atomic plan admission, audit tamper detection). Generated code is
  validated by its own unit/integration tests executed by the pipeline; scenarios are tested end to end including injected faults.
- **Trade-offs**: deterministic generators trade breadth for reproducibility; single-process thread pool trades distribution for a
  simple, fully observable control loop; in-process limiter/cache trade multi-node correctness for zero infrastructure.
- **Limitations**: agents are not LLMs in this prototype (interface is ready); the human in demo runs is a labelled
  *simulated* reviewer (`--interactive` for a real one); no agent timeouts/cancellation of in-flight work (results are discarded
  instead); state is in memory (a crash loses the run, though the audit log survives); SSRF via DNS is not checked in the shortener.
