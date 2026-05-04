#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -n "${PYTHON_BIN:-}" ]]; then
  RESOLVED_PYTHON_BIN="${PYTHON_BIN}"
elif [[ -x "${ROOT_DIR}/.venv312/bin/python" ]]; then
  RESOLVED_PYTHON_BIN="${ROOT_DIR}/.venv312/bin/python"
elif [[ -x "${ROOT_DIR}/.venv/bin/python" ]]; then
  RESOLVED_PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
else
  RESOLVED_PYTHON_BIN="$(command -v python3 || command -v python)"
fi

echo "[check] repo root: ${ROOT_DIR}"
echo "[check] python: $(${RESOLVED_PYTHON_BIN} --version)"
cd "${ROOT_DIR}"

echo "[check] validating research agent registration"
"${RESOLVED_PYTHON_BIN}" - <<'PY'
from are.simulation.agents.agent_builder import AgentBuilder, AppAgentBuilder

required_agents = {
    "farm_baseline_react",
    "farm_planner_executor",
    "farm_reflective_memory",
    "farm_skill_rag",
    "farm_multi_specialist",
    "farm_adaptive_verifier",
    "farm_rewoo_modular",
    "farm_tree_search",
    "farm_critic_refiner",
    "farm_graph_memory",
}
available = set(AgentBuilder().list_agents())
missing = sorted(required_agents - available)
if missing:
    raise SystemExit(f"Missing required research agents: {missing}")
print("registered agents:", sorted(required_agents))

required_app_agents = {
    "default_app_agent",
    "weather_expert_app_agent",
    "sensor_expert_app_agent",
    "machinery_expert_app_agent",
    "operations_expert_app_agent",
}
available_app_agents = set(AppAgentBuilder().list_agents())
missing_app_agents = sorted(required_app_agents - available_app_agents)
if missing_app_agents:
    raise SystemExit(f"Missing required app-agents: {missing_app_agents}")
print("registered app-agents:", sorted(required_app_agents))
PY

echo "[check] validating suite config dry-run"
"${RESOLVED_PYTHON_BIN}" "${ROOT_DIR}/scripts/run_agent_suite.py" \
  --config "${ROOT_DIR}/configs/agent_suite/smoke.yaml" \
  --dry-run \
  --mock >/tmp/farmare_suite_dry_run.json
echo "[check] dry-run manifest written to /tmp/farmare_suite_dry_run.json"

echo "[check] validating expected smoke/full packs"
"${RESOLVED_PYTHON_BIN}" - <<'PY'
import json
from pathlib import Path

from are.simulation.agent_suite.suite_runner import expand_run_specs, load_suite_config

root = Path.cwd()
smoke = load_suite_config(root / "configs" / "agent_suite" / "smoke.yaml")
full = load_suite_config(root / "configs" / "agent_suite" / "full_compare.yaml")
smoke_specs = expand_run_specs(smoke, force_mock=True, enable_real_model_preflight=False)
full_specs = expand_run_specs(full, force_mock=True, enable_real_model_preflight=False)

smoke_packs = sorted({spec.pack_name for spec in smoke_specs})
full_packs = sorted({spec.pack_name for spec in full_specs})
expected_smoke = ["smoke_a2a_off", "smoke_a2a_on_typed"]
expected_full = ["full_compare_a2a_off", "full_compare_a2a_on_typed"]
if smoke_packs != expected_smoke:
    raise SystemExit(f"Unexpected smoke packs: {smoke_packs}")
if full_packs != expected_full:
    raise SystemExit(f"Unexpected full packs: {full_packs}")
print("smoke packs:", smoke_packs)
print("full packs:", full_packs)
PY

echo "[check] validating scenario registration"
SCENARIOS_OUTPUT="$(${RESOLVED_PYTHON_BIN} -m are.simulation.main --list-scenarios)"
for SCENARIO in \
  scenario_farm_world_field_prep \
  scenario_farm_world_pesticide \
  scenario_farm_world_harvest \
  scenario_farm_world_irrigation \
  scenario_find_image_file
do
  if ! grep -q "${SCENARIO}" <<<"${SCENARIOS_OUTPUT}"; then
    echo "missing scenario: ${SCENARIO}"
    exit 1
  fi
done

echo "[check] readiness checks passed"
