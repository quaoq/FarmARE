#!/usr/bin/env bash

set -uo pipefail

REPO_ROOT="/home/nvidia/workspace/quao/FarmARE"
PYTHON_BIN="$REPO_ROOT/.venv312/bin/python"
ENDPOINT="http://127.0.0.1:8000/v1"

FAMILIES="farm_baseline_react"
WINDOWS=("50" "30" "10")

SCENARIOS="scenario_full_season_hb_cloudy_wet_low_radiation_biomass,scenario_full_season_hb_compacted_headland_stand_recovery,scenario_full_season_hb_cool_august_lategrain_laterain,scenario_full_season_hb_drought_recovery_false_disease_signal,scenario_full_season_hb_dryer_capacity_batch_harvest_storage,scenario_full_season_hb_dryr5r6_hn58_std_waterlimit,scenario_full_season_hb_dryr5r6_insect_threshold_waterstress,scenario_full_season_hb_grain_moisture_sensor_failure_harvest,scenario_full_season_hb_heinong58_resistant_biotic_water_budget_priority,scenario_full_season_hb_hn50_hn84_hn58_mixed_stress,scenario_full_season_hb_hn60_high_fastdrain_dryr5r6,scenario_full_season_hb_laterain_shattering_drying_tradeoff,scenario_full_season_hb_local_soil_constraint_nutrition_patch,scenario_full_season_hb_micronutrient_deficiency_flowering_patch,scenario_full_season_hb_organic_patch_weed_mechanical_capacity,scenario_full_season_hb_organic_residue_weed_establishment,scenario_full_season_hb_poordrainage_wetjune_disease_trafficability,scenario_full_season_hb_soy_after_soy_wetjune_disease,scenario_full_season_hb_staggered_wetjune_canopy_disease,scenario_full_season_heinong84_edge_low_fertility"

cd "$REPO_ROOT" || exit 1

echo "============================================================"
echo "FarmARE history-window series"
echo "Order: 50 -> 30 -> 10"
echo "Started: $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================================"

"$PYTHON_BIN" -c 'import sys; xs=sys.argv[1].split(","); print("scenario count =",len(xs)); print("unique count =",len(set(xs))); raise SystemExit(0 if len(xs)==20 and len(set(xs))==20 else 1)' "$SCENARIOS"

if [ "$?" -ne 0 ]; then
  echo "STOP: 场景数量或唯一性检查失败。"
  exit 2
fi

if pgrep -af 'iclr_validation_runner.py|are.simulation.main'; then
  echo "STOP: 检测到已有实验进程。"
  exit 3
fi

if ! env -u ALL_PROXY -u all_proxy -u HTTP_PROXY -u http_proxy -u HTTPS_PROXY -u https_proxy curl -fsS "$ENDPOINT/models" >/dev/null; then
  echo "STOP: vLLM API 尚未就绪。"
  exit 4
fi

for window in "${WINDOWS[@]}"; do
  if [ "$window" = "all" ]; then
    run_name="qwen36_35b_history_all_detail_true_farm_baseline_react"
  else
    run_name="qwen36_35b_history_w${window}_detail_true_farm_baseline_react"
  fi

  output_root="validation_runs/${run_name}/phase5_paper_matrix"

  if [ -e "$output_root" ]; then
    echo "STOP: 目标目录已经存在，拒绝覆盖：$output_root"
    exit 5
  fi

  echo "NEW: $output_root"
done

if [ "${CHECK_ONLY:-0}" = "1" ]; then
  echo "CHECK_ONLY: all feasibility checks passed."
  exit 0
fi

for window in "${WINDOWS[@]}"; do
  history_args=()

  if [ "$window" = "all" ]; then
    history_label="all"
    run_name="qwen36_35b_history_all_detail_true_farm_baseline_react"
  else
    history_label="$window"
    run_name="qwen36_35b_history_w${window}_detail_true_farm_baseline_react"
    history_args=(--history-window "$window")
  fi

  output_root="validation_runs/${run_name}/phase5_paper_matrix"

  echo
  echo "============================================================"
  echo "Starting history_window=${history_label}"
  echo "Time: $(date '+%Y-%m-%d %H:%M:%S')"
  echo "Output: $output_root"
  echo "============================================================"

  if ! env -u ALL_PROXY -u all_proxy -u HTTP_PROXY -u http_proxy -u HTTPS_PROXY -u https_proxy curl -fsS "$ENDPOINT/models" >/dev/null; then
    echo "STOP: history_window=${history_label} 启动前发现 vLLM API 不可用。"
    exit 6
  fi

  mkdir -p "$output_root"

  env \
    -u ALL_PROXY \
    -u all_proxy \
    -u HTTP_PROXY \
    -u http_proxy \
    -u HTTPS_PROXY \
    -u https_proxy \
    PYTHONUNBUFFERED=1 \
    QWEN_API_KEY=local-vllm \
    "$PYTHON_BIN" scripts/iclr_validation_runner.py \
    --phase phase5_paper_matrix \
    --output-root "$output_root" \
    --families "$FAMILIES" \
    --scenarios "$SCENARIOS" \
    --repeats 1 \
    --provider qwen \
    --model Qwen3.6-35B-A3B-FP8 \
    --model-family Qwen \
    --endpoint "$ENDPOINT" \
    --detail true \
    "${history_args[@]}" \
    --agent-max-iterations 300 \
    --cell-timeout-s 1800 \
    --cell-timeout-grace-s 60 \
    --wait-for-user-input-timeout 5 \
    --max-concurrent 1 \
    --cost-cap-dollars 9999 \
    2>&1 | tee "$output_root/runner.nohup.log"

  runner_rc=${PIPESTATUS[0]}
  summary_line=$(grep 'SUMMARY:' "$output_root/runner.nohup.log" | tail -n 1 || true)

  echo
  echo "Finished history_window=${history_label}"
  echo "Runner return code: $runner_rc"
  echo "Summary: ${summary_line:-SUMMARY not found}"
  echo "Finished at: $(date '+%Y-%m-%d %H:%M:%S')"
done

echo
echo "============================================================"
echo "All configured history-window runs finished."
echo "Finished: $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================================"