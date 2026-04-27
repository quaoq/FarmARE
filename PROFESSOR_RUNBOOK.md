# Professor Runbook: Farm Agent Architecture Suite

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
