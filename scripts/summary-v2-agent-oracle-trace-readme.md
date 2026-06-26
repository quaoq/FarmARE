# summary_v2 Agent -> Oracle -> Trace Runbook

This README explains the execution order for producing `summary_v2*.csv` from
FARM agent runs. The real path is:

```text
1. Run agent(s) -> produce scenario_*.json traces
2. Build oracle baselines -> produce oracle_baselines/<scenario_id>.json
3. Rebatch/replay traces -> produce summary_v2*.csv
```

The current paper-facing metrics are:

```text
ktc(%)  yield_loss(%)  bfcl_success(%)  path_correctness(%)
```

## Important Provider Choice

Use the provider name deliberately:

| Provider | What the runner passes to `are.simulation.main` | Required env | Use when |
|---|---|---|---|
| `qwen` | `-mp llama-api` | `QWEN_API_KEY` or `DASHSCOPE_API_KEY`; optional `QWEN_API_BASE` / `DASHSCOPE_API_BASE` | DashScope/OpenAI-compatible Qwen endpoint, normal text mode. |
| `qwen-json` | `-mp qwen-json` | `QWEN_API_KEY` or `DASHSCOPE_API_KEY`; optional `QWEN_API_BASE` / `DASHSCOPE_API_BASE` | ARE JSON-mode Qwen adapter; the LLM call asks for JSON object responses. |
| `deepseek` | `-mp deepseek` | `DEEPSEEK_API_KEY`; optional `DEEPSEEK_API_BASE` | DeepSeek normal mode. |
| `deepseek-json` | `-mp deepseek-json` | `DEEPSEEK_API_KEY`; optional `DEEPSEEK_API_BASE` | DeepSeek JSON-mode adapter. |
| `qwen-json` via vLLM wrapper | `-mp qwen-json` | wrapper sets `QWEN_API_KEY`, `QWEN_API_BASE`, `LLAMA_API_KEY`, `LLAMA_API_BASE` | Local/OpenAI-compatible vLLM server, usually `http://localhost:8000/v1`. |

Code locations:

- Provider/env mapping:
  [scripts/iclr_validation_runner.py](/Users/quao/Desktop/Second-year/CWSN/FarmARE_physics/scripts/iclr_validation_runner.py:213)
- Agent command construction:
  [scripts/iclr_validation_runner.py](/Users/quao/Desktop/Second-year/CWSN/FarmARE_physics/scripts/iclr_validation_runner.py:88)
- Qwen JSON-mode engine:
  [are/simulation/agents/llm/litellm/litellm_engine.py](/Users/quao/Desktop/Second-year/CWSN/FarmARE_physics/are/simulation/agents/llm/litellm/litellm_engine.py:379)

## Step 1: Run Agent Traces

This step costs LLM tokens. It creates one cell directory per
`(family, scenario, repeat)` and writes a `scenario_*.json` trace.

### Qwen normal mode

Set credentials first:

```bash
export QWEN_API_KEY="<your_key>"
# optional; default is https://dashscope.aliyuncs.com/compatible-mode/v1
export QWEN_API_BASE="https://dashscope.aliyuncs.com/compatible-mode/v1"
```

Run one smoke cell:

```bash
.venv312/bin/python scripts/iclr_validation_runner.py \
  --phase qwen_trace_smoke \
  --output-root validation_runs/qwen_trace_smoke/phase5_paper_matrix \
  --families farm_baseline_react \
  --scenarios scenario_full_season_hb_base_hn84_std_normal \
  --repeats 1 \
  --provider qwen \
  --model qwen3.6-flash-2026-04-16 \
  --agent-max-iterations 400 \
  --cell-timeout-s 1800 \
  --max-concurrent 1 \
  --cost-cap-dollars 5
```

Expected trace:

```text
validation_runs/qwen_trace_smoke/phase5_paper_matrix/
  farm_baseline_react__scenario_full_season_hb_base_hn84_std_normal__r1/
    scenario_full_season_hb_base_hn84_std_normal_*.json
```

### Qwen JSON mode

Set credentials first:

```bash
export QWEN_API_KEY="<your_key>"
export QWEN_API_BASE="https://dashscope.aliyuncs.com/compatible-mode/v1"
```

Run one smoke cell:

```bash
.venv312/bin/python scripts/iclr_validation_runner.py \
  --phase qwen_json_trace_smoke \
  --output-root validation_runs/qwen_json_trace_smoke/phase5_paper_matrix \
  --families farm_baseline_react \
  --scenarios scenario_full_season_hb_base_hn84_std_normal \
  --repeats 1 \
  --provider qwen-json \
  --model qwen3.6-flash-2026-04-16 \
  --agent-max-iterations 400 \
  --cell-timeout-s 1800 \
  --max-concurrent 1 \
  --cost-cap-dollars 5
```

### Local vLLM using qwen-json

The convenience wrapper defaults to `--provider qwen-json` and is useful for a
local OpenAI-compatible vLLM server:

```bash
.venv312/bin/python scripts/run_vllm_table3_20.py \
  --families farm_baseline_react \
  --scenarios scenario_full_season_hb_base_hn84_std_normal \
  --model qwen3.6-flash-2026-04-16 \
  --endpoint http://localhost:8000/v1 \
  --repeats 1 \
  --max-concurrent 1 \
  --cost-cap-dollars 5
```

Dry-run the exact runner command without calling the model:

```bash
.venv312/bin/python scripts/run_vllm_table3_20.py \
  --dry-run \
  --families farm_baseline_react \
  --scenarios scenario_full_season_hb_base_hn84_std_normal \
  --model qwen3.6-flash-2026-04-16 \
  --endpoint http://localhost:8000/v1 \
  --repeats 1 \
  --max-concurrent 1 \
  --cost-cap-dollars 1
```

### DeepSeek JSON mode

```bash
export DEEPSEEK_API_KEY="<your_key>"

.venv312/bin/python scripts/iclr_validation_runner.py \
  --phase deepseek_json_trace_smoke \
  --output-root validation_runs/deepseek_json_trace_smoke/phase5_paper_matrix \
  --families farm_baseline_react \
  --scenarios scenario_full_season_hb_base_hn84_std_normal \
  --repeats 1 \
  --provider deepseek-json \
  --model deepseek-chat \
  --agent-max-iterations 400 \
  --cell-timeout-s 1800 \
  --max-concurrent 1 \
  --cost-cap-dollars 5
```

## Step 2: Build Oracle Baselines

This step is no-LLM-cost. It runs each scenario's hard-coded oracle path through
live FARM physics and writes `oracle_baselines/<scenario_id>.json`.

Single scenario:

```bash
.venv312/bin/python scripts/build_oracle_baselines.py \
  --scenarios scenario_full_season_hb_base_hn84_std_normal \
  --output-dir oracle_baselines \
  --include-donothing \
  --force
```

Formal L3 full-season pool:

```bash
.venv312/bin/python scripts/build_oracle_baselines.py \
  --scenarios formal90 \
  --output-dir oracle_baselines \
  --include-donothing \
  --continue-on-error
```

Code locations:

- Oracle baseline CLI:
  [scripts/build_oracle_baselines.py](/Users/quao/Desktop/Second-year/CWSN/FarmARE_physics/scripts/build_oracle_baselines.py:1)
- Oracle biological yield loader used by summary:
  [are/simulation/scenarios/fos/evaluation.py](/Users/quao/Desktop/Second-year/CWSN/FarmARE_physics/are/simulation/scenarios/fos/evaluation.py:647)

## Step 3: Rebatch / Replay Agent Traces

This step is no-LLM-cost. It reads existing `scenario_*.json` traces, replays
their tool calls into FARM physics, loads oracle baselines, and writes
`summary_v2*.csv`.

Single root:

```bash
.venv312/bin/python scripts/rebatch_fos_from_traces.py \
  --root validation_runs/qwen_trace_smoke/phase5_paper_matrix \
  --out-root rebatch_outputs/qwen_trace_smoke \
  --extrapolate \
  --oracle-baselines oracle_baselines \
  --workers 1
```

Multiple roots:

```bash
.venv312/bin/python scripts/rebatch_fos_from_traces.py \
  --root validation_runs/qwen_trace_smoke/phase5_paper_matrix \
         validation_runs/qwen_json_trace_smoke/phase5_paper_matrix \
  --out-root rebatch_outputs/qwen_combined_trace_smoke \
  --extrapolate \
  --oracle-baselines oracle_baselines \
  --workers 4
```

Only one cell for debugging:

```bash
.venv312/bin/python scripts/rebatch_fos_from_traces.py \
  --root validation_runs/qwen_trace_smoke/phase5_paper_matrix \
  --out-root rebatch_outputs/qwen_trace_smoke_debug_one \
  --extrapolate \
  --oracle-baselines oracle_baselines \
  --workers 1 \
  --cells farm_baseline_react__scenario_full_season_hb_base_hn84_std_normal__r1
```

Output:

```text
rebatch_outputs/qwen_trace_smoke/
  summary_v2_qwen_trace_smoke.csv
  summary_v2.csv
  <cell>/fos/fos_<scenario_id>.json
```

Code locations:

- Trace replay:
  [scripts/rebatch_fos_from_traces.py](/Users/quao/Desktop/Second-year/CWSN/FarmARE_physics/scripts/rebatch_fos_from_traces.py:279)
- Per-cell row generation:
  [scripts/rebatch_fos_from_traces.py](/Users/quao/Desktop/Second-year/CWSN/FarmARE_physics/scripts/rebatch_fos_from_traces.py:992)
- Metric assignment:
  [scripts/rebatch_fos_from_traces.py](/Users/quao/Desktop/Second-year/CWSN/FarmARE_physics/scripts/rebatch_fos_from_traces.py:1168)
- CSV write:
  [scripts/rebatch_fos_from_traces.py](/Users/quao/Desktop/Second-year/CWSN/FarmARE_physics/scripts/rebatch_fos_from_traces.py:2004)

## Step 4: Check the Current Metrics

```bash
.venv312/bin/python - <<'PY'
import csv
from pathlib import Path

p = Path("rebatch_outputs/qwen_trace_smoke/summary_v2.csv")
with p.open() as f:
    rows = list(csv.DictReader(f))

for r in rows[:10]:
    print({
        "cell": r.get("cell"),
        "ktc(%)": r.get("ktc(%)"),
        "yield_loss(%)": r.get("yield_loss(%)"),
        "bfcl_success(%)": r.get("bfcl_success(%)"),
        "path_correctness(%)": r.get("path_correctness(%)"),
    })
PY
```

Metric code locations:

| Metric | Summary assignment | Core computation |
|---|---|---|
| `ktc(%)` | [rebatch_fos_from_traces.py:1345](/Users/quao/Desktop/Second-year/CWSN/FarmARE_physics/scripts/rebatch_fos_from_traces.py:1345) | [workflow_validation.py:89](/Users/quao/Desktop/Second-year/CWSN/FarmARE_physics/are/simulation/scenarios/workflow_validation.py:89) |
| `yield_loss(%)` | [rebatch_fos_from_traces.py:1175](/Users/quao/Desktop/Second-year/CWSN/FarmARE_physics/scripts/rebatch_fos_from_traces.py:1175) | [evaluation.py:914](/Users/quao/Desktop/Second-year/CWSN/FarmARE_physics/are/simulation/scenarios/fos/evaluation.py:914) |
| `bfcl_success(%)` | [rebatch_fos_from_traces.py:1390](/Users/quao/Desktop/Second-year/CWSN/FarmARE_physics/scripts/rebatch_fos_from_traces.py:1390) | [spatiotemporal.py:969](/Users/quao/Desktop/Second-year/CWSN/FarmARE_physics/are/simulation/scenarios/fos/spatiotemporal.py:969) |
| `path_correctness(%)` | [rebatch_fos_from_traces.py:1305](/Users/quao/Desktop/Second-year/CWSN/FarmARE_physics/scripts/rebatch_fos_from_traces.py:1305) | [path_correctness_v2.py:253](/Users/quao/Desktop/Second-year/CWSN/FarmARE_physics/are/simulation/scenarios/fos/path_correctness_v2.py:253) |

## What Was Verified In This Workspace

These commands were run in this workspace on 2026-06-26:

| Check | Command shape | Result |
|---|---|---|
| Runner CLI parses | `.venv312/bin/python scripts/iclr_validation_runner.py --help` | Passed. |
| Main simulation CLI parses | `.venv312/bin/python -m are.simulation.main --help` | Passed. |
| Oracle baseline CLI parses | `.venv312/bin/python scripts/build_oracle_baselines.py --help` | Passed. |
| Rebatch CLI parses | `.venv312/bin/python scripts/rebatch_fos_from_traces.py --help` | Passed. |
| Provider mapping for `qwen`, `qwen-json`, `deepseek`, `deepseek-json` | Imported `_prepare_provider_env()` and `_build_cell_command()` with test env keys | Passed. `qwen -> llama-api`; `qwen-json -> qwen-json`; `deepseek-json -> deepseek-json`. |
| vLLM qwen-json wrapper dry-run | `scripts/run_vllm_table3_20.py --dry-run ...` | Passed and printed the underlying `iclr_validation_runner.py` command. |
| Qwen missing-key boundary | `iclr_validation_runner.py --provider qwen ...` without Qwen key | Passed as expected failure: result row has `return_code=-3` and missing-key exception. |
| Qwen JSON missing-key boundary | `iclr_validation_runner.py --provider qwen-json ...` without Qwen key | Passed as expected failure: result row has `return_code=-3` and missing-key exception. |
| Oracle baseline smoke | `build_oracle_baselines.py --scenarios scenario_full_season_hb_base_hn84_std_normal --output-dir /tmp/... --max-days 5 --include-donothing --force` | Passed, wrote one baseline JSON. |
| Rebatch smoke from existing trace | `rebatch_fos_from_traces.py --root validation_runs/deepseek_e2/... --cells farm_baseline_react__scenario_full_season_hb_base_hn84_std_normal__r1 --out-root /tmp/...` | Passed, wrote `summary_v2.csv` with the four current metrics present. |

Important honesty note: a full paid Qwen/Qwen-JSON agent run was not completed
in this workspace because `QWEN_API_KEY` / `DASHSCOPE_API_KEY` was not present.
The README commands above are therefore split into:

1. verified no-cost commands and provider-command construction; and
2. formal paid agent templates to run after credentials are set.

Do not claim a new Qwen run exists until a `scenario_*.json` trace appears
under the requested `validation_runs/.../phase5_paper_matrix/<cell>/` directory.

