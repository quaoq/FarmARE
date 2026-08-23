# Professor Runbook: Farm Agent Architecture Suite

For the Farm D-CORE AAMAS work, begin with the ordered professor packet:

1. [`PROFESSOR_01_END_TO_END_OVERVIEW.md`](AAMAS/PROFESSOR_01_END_TO_END_OVERVIEW.md)
2. [`PROFESSOR_02_TECHNICAL_SPECIFICATION.md`](AAMAS/PROFESSOR_02_TECHNICAL_SPECIFICATION.md)
3. [`PROFESSOR_03_EXECUTION_GUIDE.md`](AAMAS/PROFESSOR_03_EXECUTION_GUIDE.md)

## Farm D-CORE AAMAS experiments

Farm D-CORE is a separate, stricter distributed-evaluation package. Do not use
the architecture-suite commands below to generate its paper tables. Its frozen
protocol is documented in
[`are/simulation/distributed/EXPERIMENT_PROTOCOL.md`](are/simulation/distributed/EXPERIMENT_PROTOCOL.md)
and its definitions in
[`are/simulation/distributed/SCIENTIFIC_CONTRACT_V5.md`](are/simulation/distributed/SCIENTIFIC_CONTRACT_V5.md).

Install the development environment (including pinned PM4Py), then run:

```bash
uv sync --extra dev
uv run are-dcore doctor --output-dir results/dcore
uv run pytest -q are/simulation/tests/distributed
uv run are-dcore matrix \
  are/simulation/distributed/configs/farm_dcore_primary_pass1.yaml \
  --output-dir results/dcore --dry-run
```

`healthy: true` means the software can run. It is not authorization to launch
paper experiments. Final runs require `paper_ready: true`, obtained only when
all three confirmed `farm_process_spec_v5` files plus reviewed team/refinement
files and completed scientific-gate manifests are passed to `doctor`. The gate
is bound to a clean release tag, exact `uv.lock`, and frozen analysis protocol.
The matrix must contain no placeholders and the bounded real-model smoke must
succeed. Until then the repository is a research preview.

Only `dcore_trace_v5` / `dcore_eval_v5` rows are accepted by paper aggregation.
The primary matrix uses ten world seeds and two controller repeats per cell;
secondary robustness/scalability blocks use five seeds. Fault rows are invalid
unless the artifact proves the intended fault manifested.

### Frozen execution sequence

The old `farm_dcore_paper.yaml` is intentionally disabled because it generated
an uncontrolled 1,680-row Cartesian product. The release manifests contain
480 primary-pass-1 runs (450 model + 30 oracle), 450 primary-pass-2 runs, 270
controller-robustness runs, and 45 scalability runs. The Qwen/DeepSeek
continuity template adds an optional 60 runs.

Run `are-dcore preflight` first, then pass 1, inspect integrity without changing
cells, and only then run pass 2 and the secondary blocks. `matrix` supports
stable `--shard-count N --shard-index I`; shards are disjoint and resume by
immutable run key. Fresh `farmare_direct` and `farmare_a2a` rows use the same
public task, exogenous world, model, repeat, and aggregate budget.

```bash
uv run are-dcore preflight \
  are/simulation/distributed/configs/farm_dcore_primary_pass1.yaml \
  --output-dir results/preflight
uv run are-dcore matrix \
  are/simulation/distributed/configs/farm_dcore_primary_pass1.yaml \
  --output-dir results/pass1
```

After all passes and secondary blocks finish, point `aggregate --paper-mode`
at their common results directory; it recursively merges pass and shard rows
and rejects duplicate run keys. Then run `are-dcore report` on the aggregate
directory. The report emits six CSV/LaTeX tables, five PNG/PDF
figures, and a digest-bound analysis manifest. `handoff build` refuses unless
the three expert reviews, all scientific gates, bounded OpenAI smoke, clean
commit, release tag, and exact block counts are present.

```bash
uv run are-dcore aggregate results --output-dir analysis/merged --paper-mode
# Run the digest-bound `scientific_validation` command from COMMANDS.json here.
uv run are-dcore report analysis/merged --output-dir paper_outputs
```

### Verification snapshot (2026-08-23)

This snapshot is evidence about the implementation, not a waiver of the
scientific release gates above.

- The post-suite distributed regression is **127 passed** in 377.61 seconds.
- Ruff, byte-compilation, `git diff --check`, exact matrix-count dry runs, and
  the native full-season CLI smoke pass.
- The no-model CLI smoke completed all three conditions, aggregation completed,
  and the report rendered six tables plus five figures.
- Bounded engineering preflight passes; unbounded scientific preflight refuses
  unresolved review artifacts with a clear error.
- The exported v5 suite contains 20 invariance, defect, recovery, and
  localization fixtures. Its engineering properties pass. The harmful-write
  paper property remains explicitly pending until experts freeze the negative
  obligation; it is not silently treated as validated.
- `doctor --output-dir ...` reports `healthy: true`, a writable output path,
  the pinned PM4Py version, and `paper_ready: false` because the real expert
  specifications and release-gate manifests do not yet exist.

The latest repository-wide legacy audit reported 2,472 passes and 93 failures
in untouched legacy areas. The failures were dominated by tests
that download unavailable Hugging Face fixtures, assume local sandbox sample
files, or apply the old one-folder/one-`scenario.py` convention to the existing
FarmARE scenario collection. They are not D-CORE failures, but they must be
resolved or formally classified in the release test report before the final
professor tag. Do not make those tests green by weakening D-CORE checks or by
silently deleting them.

Final aggregation must use:

```bash
uv run are-dcore aggregate results/dcore --paper-mode
```

---

This repository is implementation-ready for architecture comparisons.
It includes 10 controller families and two Agent2Agent modes:
- `A2A OFF` (baseline)
- `A2A ON` with typed app experts (`weather`, `sensor`, `machinery`, `operations`)

## 1) Local Setup (No Docker, uv-first)

```bash
uv sync
```

Fallback (pip):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-gui.txt
cp .env.example.professor .env
```

## 2) Readiness Check (non-destructive)

```bash
uv run ./scripts/check_readiness.sh
```

This verifies:
- 10 research controller families are registered
- typed app-expert agents are registered
- smoke/full suite packs expand correctly in dry-run mode
- key scenarios are discoverable

## 3) 5-Minute No-Key Smoke

Dry-run only (prints run matrix):

```bash
uv run python scripts/run_agent_suite.py --config configs/agent_suite/smoke.yaml --dry-run --mock
```

Execute smoke (both `smoke_a2a_off` and `smoke_a2a_on_typed`):

```bash
uv run python scripts/run_agent_suite.py --config configs/agent_suite/smoke.yaml --mock
```

The smoke configs cap agent loop length (`agent_max_iterations`) and user-input waits for predictable runtime.

## 4) Real-Model Runs (Optional)

Run real smoke:

```bash
uv run python scripts/run_agent_suite.py --config configs/agent_suite/smoke.yaml --real
```

Run full compare:

```bash
uv run python scripts/run_agent_suite.py --config configs/agent_suite/full_compare.yaml --real
```

Run single family:

```bash
uv run python scripts/run_agent_suite.py \
  --config configs/agent_suite/smoke.yaml \
  --mock \
  --family farm_graph_memory
```

Real-mode resolver behavior:
- preflights `o4-mini` first
- falls back automatically to `gpt-4o-mini` when `o4-mini` is unavailable
- records selected model + resolution strategy in suite artifacts

## 5) A2A and Typed Experts

`A2A ON` transforms a fraction of scenario apps into app-agents (`a2a_app_prop`).
Typed routing policy maps app classes to domain experts:
- `WeatherApp` → `weather_expert_app_agent`
- `SensorApp` → `sensor_expert_app_agent`
- `TractorApp`, `FieldOpsApp` → `machinery_expert_app_agent`
- `FarmWorldApp`, `DroneApp`, `RobotApp` → `operations_expert_app_agent`

Compatibility mode remains available via policy `generic` and `default_app_agent`.

## 6) Agent Families Included (10)

- `farm_baseline_react`
- `farm_planner_executor`
- `farm_reflective_memory`
- `farm_skill_rag`
- `farm_multi_specialist`
- `farm_adaptive_verifier`
- `farm_rewoo_modular`
- `farm_tree_search`
- `farm_critic_refiner`
- `farm_graph_memory`

Paper mapping and concise architecture notes are documented in:
- `AGENT_FAMILIES_AND_PAPERS.md`

## 7) Outputs and Interpretation

Suite artifacts:
- `outputs/agent_suite_runs/<timestamp>/suite_manifest.json`
- `outputs/agent_suite_runs/<timestamp>/suite_results.json`
- `outputs/agent_suite_runs/<timestamp>/suite_results.csv`

Each row includes:
- controller metadata + A2A metadata (`a2a_enabled`, `a2a_policy`, `a2a_app_prop`, selected app-agent model/profile)
- telemetry fields
- infra-health fields (`infra_exit_ok`, `infra_auth_ok`, `infra_connectivity_ok`, `infra_llm_calls_positive`, `infra_trace_exported`, `infra_pass`)

For smoke validation, use `infra_pass` as the release gate.
Use `score` as informational only (not an infra pass/fail criterion).

## 8) Troubleshooting

- **Auth/base-url issues**: confirm `.env` keys and optional `OPENAI_BASE_URL`/`LLAMA_API_BASE`
- **Unexpected 0 score**: this can still be infra-pass; inspect `status`, `rationale`, and trace
- **Slow run**: start with `configs/agent_suite/smoke.yaml` before `full_compare.yaml`
- **Python mismatch**: run with `uv run ...` so commands always use the project environment
