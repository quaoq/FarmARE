# FarmARE Full-Season Scripts

This document explains the `scripts/fullseason/` directory. The directory is not the scenario-definition layer; it is the trace, review, audit, and human-inventory export layer for FarmARE L3 full-season soybean scenarios.

## 1. Directory Role

`scripts/fullseason/` contains five main groups of scripts:

| Script type | Representative files | Purpose | Modifies scenarios |
|---|---|---|---|
| Single-scenario trace runners | `run_hb_base_hn84_std_normal_trace.py`, `run_hb_*.py` | Run one L3 scenario in expert-oracle mode and export field/ridge/oracle evidence | No |
| Trace utility library | `harbin_l3_trace_utils.py` | Dynamically inserts a trace app into a production scenario and captures daily state plus event evidence | No |
| Review/audit scripts | `review_fullseason_l3_scenarios.py`, `audit_fullseason_l3_value.py` | Read generated traces and check events, CSVs, targets, yield/value evidence | No |
| Inventory/export scripts | `export_v2_scenario_inventory.py`, `generate_final_human_inventory_v2.py` | Organize trace, scenario metadata, and review results into CSV/Markdown files for human review | No |
| Experiment scripts | `run_biotic_pressure_mixed_ridge_experiment.py` | Local physics or biotic-pressure experiment; not a formal L3 scenario runner | No |

Production scenario definitions live in:

```text
are/simulation/scenarios/scenario_farm_world_fullseason_v2/
```

`scripts/fullseason/` should be treated as the evidence-generation and documentation-export layer, not as the scenario source of truth.

## 2. Common Commands

Run one trace:

```bash
uv run python scripts/fullseason/run_hb_base_hn84_std_normal_trace.py
```

Most trace runners allow output overrides:

```bash
uv run python scripts/fullseason/run_hb_base_hn84_std_normal_trace.py \
  --field-csv /tmp/field-summary.csv \
  --ridge-csv /tmp/ridge-states.csv \
  --trace-json /tmp/oracle-trace.json
```

Run all trace runners without running experiment scripts:

```bash
for script in scripts/fullseason/run_*_trace.py; do
  uv run python "$script"
done
```

Run the full review:

```bash
uv run python scripts/fullseason/review_fullseason_l3_scenarios.py
```

Export the V2 scenario inventory:

```bash
uv run python scripts/fullseason/export_v2_scenario_inventory.py
```

Export the final human inventory package:

```bash
uv run python scripts/fullseason/generate_final_human_inventory_v2.py
```

Run the value audit:

```bash
uv run python scripts/fullseason/audit_fullseason_l3_value.py
```

## 3. What Trace Runners Generate

Most `run_*_trace.py` scripts generate three core evidence files:

| Output | Default location | Contents |
|---|---|---|
| Field summary CSV | `docs/ai/<slug>-field-summary.csv` | Checkpoint-level whole-field and zone summaries, such as stage counts, average GDD, average NDVI, and max stress values |
| Ridge states CSV | `docs/ai/<slug>-ridge-states.csv` | Checkpoint-level per-ridge stage, GDD, VWC, water/nutrient/biotic stress, LAI, NDVI, biomass, grain moisture, and recovered yield |
| Oracle trace JSON | `docs/ai/<slug>-oracle-trace.json` | Completed events, action justifications, diagnostic warnings, and tool-return evidence |

These files are the evidence source for review scripts, inventories, one-row tables, and final documentation.

## 4. Mapping Between Scripts and Scenarios

The mapping chain is:

```text
scenario wrapper file
  -> SCENARIO_ID / SPEC
  -> run_<slug>_trace.py
  -> docs/ai/*field-summary.csv, *ridge-states.csv, *oracle-trace.json
  -> review_fullseason_l3_scenarios.SCENARIOS
  -> inventory / review / final docs
```

### 4.1 Scenario Wrapper

Scenario files are under:

```text
are/simulation/scenarios/scenario_farm_world_fullseason_v2/scenario_full_season_*.py
```

Most batch catalog scenarios use a thin wrapper:

```python
SPEC = SPECS["hb_xxx"]
SCENARIO_ID = SPEC.scenario_id
SCENARIO_DESCRIPTION = SPEC.description

class ScenarioFullSeasonHBXxx(Scenario):
    def init_and_populate_apps(...):
        init_batch_apps(self, SPEC)

    def build_oracle_flow(...):
        build_batch_events(self, SPEC)
```

The scenario metadata is stored in:

```text
are/simulation/scenarios/scenario_farm_world_fullseason_v2/harbin_l3_batch_catalog.py
```

Each `ScenarioSpec` records:

- `scenario_id`
- `slug`
- `profile_name`
- `description`
- `briefing_text`
- seed stocks
- planting zones
- prior histories
- hydraulic modifiers
- management regime
- postharvest market
- action windows
- harvest zones

Some older or custom scenarios are not catalog-driven; their constants and oracle event flow are written directly in the scenario wrapper.

### 4.2 Trace Runner

A normal trace runner imports one scenario class:

```python
from are.simulation.scenarios.scenario_farm_world_fullseason_v2.scenario_full_season_hb_xxx import (
    SCENARIO_ID,
    SPEC,
    ScenarioFullSeasonHBXxx,
)
from scripts.fullseason.harbin_l3_trace_utils import run_trace
```

It then calls:

```python
run_trace(
    scenario_cls=ScenarioFullSeasonHBXxx,
    scenario_id=SCENARIO_ID,
    trace_app_name=TRACE_APP_NAME,
    zones=list(SPEC.zones),
    field_csv=args.field_csv,
    ridge_csv=args.ridge_csv,
    trace_json=args.trace_json,
)
```

The current directory contains 90 `run_*_trace.py` scripts. Of those, 86 use the shared `harbin_l3_trace_utils.run_trace()` path. Four custom trace runners write more specialized exports:

- `run_fastdraining_dry_patch_irrigation_trace.py`
- `run_heinong84_edge_low_fertility_trace.py`
- `run_heinong84_staggered_planting_trace.py`
- `run_wet_june_ab_zoned_disease_trace.py`

These four are still trace/export scripts. They are not production scenario definitions.

### 4.3 Review Mapping

`review_fullseason_l3_scenarios.py` defines a review-layer artifact map named `SCENARIOS`:

```python
ScenarioSpec(
    slug,
    scenario_id,
    field_csv,
    ridge_csv,
    trace_json,
    required_zones,
)
```

This tells the review script:

- where each scenario's generated CSV/JSON artifacts are;
- which zones are expected in the ridge-state evidence;
- which oracle trace JSON should be used for action-support checks.

For catalog-driven scenarios, the review script also extends this list from `BATCH_L3_SPECS`:

```python
DOCS_AI / f"{spec.slug.replace('_', '-')}-field-summary.csv"
DOCS_AI / f"{spec.slug.replace('_', '-')}-ridge-states.csv"
DOCS_AI / f"{spec.slug.replace('_', '-')}-oracle-trace.json"
```

Therefore, the catalog `slug` directly determines the default artifact filenames.

## 5. Generated Files and Downstream Use

| Command | Reads | Generates | Purpose |
|---|---|---|---|
| `uv run python scripts/fullseason/run_<slug>_trace.py` | production scenario + physics engine | `docs/ai/*field-summary.csv`, `docs/ai/*ridge-states.csv`, `docs/ai/*oracle-trace.json` | Real oracle execution evidence |
| `uv run python scripts/fullseason/review_fullseason_l3_scenarios.py` | CSV/JSON listed in `SCENARIOS` | `docs/ai/fullseason-l3-review-summary.md` | Checks failed events, error returns, target/action issues, CSV anomalies |
| `uv run python scripts/fullseason/export_v2_scenario_inventory.py` | review specs + scenario source + trace artifacts | `docs/ai/fullseason-v2-scenario-inventory.csv`, `.md` | Early human-review inventory |
| `uv run python scripts/fullseason/generate_final_human_inventory_v2.py` | final review artifacts + scenario metadata + previous inventory | `outputs/fullseason_review/final_human_scenario_inventory_v2/` | Human inventory, compact workflows, missing/leakage report, metric summary |
| `uv run python scripts/fullseason/audit_fullseason_l3_value.py` | ridge CSV + trace JSON | `docs/ai/fullseason-l3-value-audit.csv/json/md` | Scenario value audit, yield before/after, management-window audit |

## 6. Relationship to the Final Chinese/English L3 Documents

The final documents are:

```text
are/simulation/scenarios/scenario_farm_world_fullseason_v2/FarmARE_L3_scenario_CN.md
are/simulation/scenarios/scenario_farm_world_fullseason_v2/FarmARE_L3_scenario_EN.md
```

They are final system documentation for the formal scenario pool. They are not trace-runner outputs, and they are not currently generated by a dedicated stable script under `scripts/fullseason/`.

They were produced from downstream acceptance artifacts, such as:

```text
outputs/fullseason_review/final_acceptance_90_v2/
outputs/fullseason_review/final_acceptance_90_v2/final_one_row_inventory_cn_clean_v2/
```

plus scenario code and trace-runner structure.

If these two Markdown files need to be regenerated in a reproducible way, add a dedicated script such as:

```text
scripts/fullseason/generate_l3_scenario_docs.py
```

That script should read the final acceptance inventory, scenario catalog, and script mapping, then write `FarmARE_L3_scenario_CN.md` and `FarmARE_L3_scenario_EN.md`.

## 7. Maintenance Notes

1. Do not treat `scripts/fullseason/` as the scenario source of truth. Production scenarios live under `are/simulation/scenarios/scenario_farm_world_fullseason_v2/`.
2. Do not decide formal evaluation membership only from the presence of `run_*_trace.py`. Formal membership should come from the final acceptance artifacts or formal scenario id list.
3. After adding or changing a scenario, use this order: edit scenario/catalog/profile -> run the corresponding trace runner -> run review -> update inventory/final artifacts.
4. Do not hand-edit trace CSV/JSON files. Fix the scenario, oracle, app, or engine, then regenerate trace evidence.
5. `ridge-states.csv` is the main evidence for daily state, stress visibility, and treatment effect.
6. `oracle-trace.json` is the main evidence for whether key actions have supporting prior tool returns.
