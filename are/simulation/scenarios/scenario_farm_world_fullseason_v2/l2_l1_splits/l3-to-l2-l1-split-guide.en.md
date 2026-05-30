# Guide: Splitting L3 Full-Season Scenarios Into L2/L1 Scenarios

This document explains how to split a validated L3 full-season soybean scenario into standalone L2/L1 scenarios.

The goal is not to write a spec, an event list, or a markdown-only task. Each new L2/L1 must be a native Python scenario in this repository, registered normally, and runnable independently with the base oracle.

## Scope

Each split task normally handles one explicitly specified L3 scenario. Do not expand the task to additional L3 scenarios unless asked.

For the specified L3, do not split by a fixed count first, but also do not under-split out of excessive caution. Start from the freshly generated L3 trace, list all real key farm-management windows, and only split windows that are non-duplicate, have independent decision value, and can be validated independently. The final count is determined by the candidate-window screening result: split more when the L3 has more real non-duplicate windows, and split fewer only when the trace truly has fewer usable windows. Do not invent duplicate scenarios just to hit a number, and do not stop only because some scenarios already exist.

The normal target for one specified L3 is `3-6 L2 + 3-6 L1`. This is not permission to invent scenarios; it is an audit lower-bound signal. If the final result has fewer than `3 L2`, fewer than `3 L1`, or fewer than `6` scenarios total, do not deliver code as "done." First produce a `low-count blocker report` proving from the fresh trace that no more non-duplicate task atoms exist, and wait for reviewer acceptance. A low-count result without that report is invalid.

Do not say "I will write N scenarios" or "I exported N checkpoints, so I will write N scenarios" before the full candidate-window table is complete. The number of checkpoints is not the number of scenarios. The number of candidate states is not the number of tasks.

L2/L1 scenarios must come from real key windows in the L3 trace. Do not invent state. Do not manually assemble a state from a few CSV columns. Prefer exporting full FARM checkpoint JSON from real L3 trace moments and restoring that checkpoint in each standalone L2/L1 scenario.

## Audit Task Atoms Before Deciding Count

Before writing code, split the fresh L3 trace into `task atoms`, then decide how many L2/L1 scenarios to implement. A `task atom` is one independent farm-management task unit with a clear current problem, evidence chain, action type, target scope, parameters, and primary metric.

Do not derive scenario count from checkpoint count:

- One checkpoint can produce 0 scenarios: for example a terminal state, pure record point, no-action state, or state with no independent metric.
- One checkpoint can produce 1 scenario: for example one clear action-ready single action.
- One checkpoint can produce multiple scenarios: for example one routine check may reveal both nutrient stress and weed pressure, with different target areas, diagnostic causes, actions, parameters, and metrics.

Therefore, "I exported 6 checkpoints, so I will write 6 scenarios" is wrong reasoning. The correct flow is:

1. Regenerate the specified L3 trace.
2. List every possible farm-management split window in the fresh trace.
3. Split each window into one or more task atoms.
4. Accept or reject each task atom.
5. Implement one scenario for each accepted task atom.

If multiple different task atoms exist under the same checkpoint, split them into multiple scenarios. Valid reasons include different diagnostic cause, target area, action type, action parameters, resource/budget constraint, primary metric, L2 closed loop vs L1 action-ready subtask, or window-seeking vs immediate execution.

Do not split duplicate tasks just to increase count. If the checkpoint, prompt, oracle, target area, action, parameters, and metric are the same and only the file name or scenario id changes, reject the candidate.

If the final implementation contains fewer than 6 scenarios, this is not a normal delivery summary; it is an exception. First write a `low-count blocker report` explaining how many candidate windows were audited, how many task atoms were accepted, which windows were rejected and why, whether multiple task atoms under the same checkpoint were accidentally merged, whether scenario count equals checkpoint count, and why the minimum `3 L2 + 3 L1` cannot be reached. If the final implementation has exactly 6 scenarios, it is only the minimum passing count and must include a complete accepted/rejected audit; do not hand-wave it as "few windows." If the scenario count equals checkpoint count, audit again because that usually means checkpoints were mistaken for scenarios.

A low-count report must not only list rejected candidates. For every rejected real management window, answer both questions:

- Why it cannot become an L2: no problem-start state, no wait/diagnose/recheck loop, no independent metric, or complete duplication with an existing L2.
- Why it cannot become an L1: no action-ready checkpoint, no standalone tool action, complete duplication with another L1, or no verifiable metric.

If a window can produce an L2 but not an L1, or an L1 but not an L2, state that explicitly. Do not replace these two checks with "few windows", "low value", or "generic".

## Minimum Output And Rejection Bar

The default behavior is to maximize real non-duplicate task atoms, not to pick only the most obvious few. For every source L3, audit these windows from the fresh trace whenever they exist:

- pre-plant / field prep / base fertilization / ridge formation
- planting or adjusted planting density / cultivar / date
- emergence / replanting
- water / irrigation / fertigation
- nutrient / fertilization / fertigation
- weeds
- disease
- insects
- resource / budget / capacity / machinery constraint
- harvest-window start after R8 or `harvest_allowed`
- harvest-ready single-action window
- unload / drying / storage / capacity

For every real management window, audit both split directions:

- `L2`: starts from the problem or an unmet window condition and includes observe -> diagnose/recheck -> wait if needed -> act -> recheck.
- `L1`: starts from an action-ready or single-decision state and performs minimal rechecks before the single action.

If a window only produces an L2 but no L1, or only an L1 but no L2, write the reason in the candidate table. The reason must come from trace evidence or a tool limitation, such as no action-ready checkpoint, no independent metric, no standalone tool action, or complete duplication with an already selected same-L3 split. Do not write only "generic", "duplicate", or "low value."

The bar for rejecting a candidate task atom is high. These reasons are not sufficient by themselves:

- "This is generic planting / generic harvest."
- "Another L3 already has a similar task."
- "This window is simple."
- "There are already 4/6 scenarios."
- "This checkpoint is already used by another scenario."

If rejecting a task as duplicate, name the existing scenario id being compared and compare checkpoint/date/stage, visible setup, target area, action, parameters, window choice, resource constraints, and primary metric. If any of those are truly different, do not reject it casually; keep it as an independent task atom or explain why the difference does not affect the task.

## What Non-Duplicate Means

Non-duplicate must be checked at two levels:

1. Within the same L3. New L2/L1 scenarios from the same source L3 must not be rename-only copies. An L1 may be an action-ready sub-window of an L2, but it must be shorter, more specific, and different in checkpoint or oracle granularity. It must not copy the full L2 observe-diagnose-wait-act-recheck loop.
2. Across L3s globally. Different L3 scenarios may contain the same task type, such as fungicide, planting, or harvest. That is acceptable only when the initial checkpoint, constraint, diagnostic cause, target area, operation parameters, window choice, or primary metric is truly different. Do not only change the L3 name, scenario id, or wording.

| Comparison | Acceptable | Not acceptable |
|---|---|---|
| Same-L3 L2 vs L2 | Different window, decision, constraint, or target area | Same checkpoint, oracle, and parameters with a new name |
| Same-L3 L1 vs L1 | Different single action or different action-ready checkpoint | Splitting the same action into multiple names |
| Same-L3 L1 vs L2 | L1 is a shorter action-ready subtask of the L2 | L1 copies the full L2 loop |
| Cross-L3 same task type | Initial state, constraint, target area, parameters, window, or metric has a real difference | Prompt, state, oracle, parameters, and metric are essentially the same with only a different L3 name |

Before writing scenarios, build a candidate-window table:

| Field | Meaning |
|---|---|
| checkpoint/trace label | The real L3 moment where this candidate starts |
| date/DAP/stage | Initial date, days after planting, and growth stage |
| task type | Planting, water/fertilizer, weeds, disease, insects, harvest, storage, resource, etc. |
| task atom | The independent task unit under this window; one checkpoint may contain multiple task atoms |
| current problem or decision | What the agent must solve, without giving the oracle answer |
| source L3 follow-up oracle action | What the source L3 actually did afterward |
| target ridges / parameters | Target range, rate, machinery, or resource parameters |
| split level | Whether this is better as L2 or L1 |
| same-checkpoint split | If multiple scenarios come from the same checkpoint, explain why they are not duplicates |
| non-duplicate reason | Real difference from same-L3 selected splits and cross-L3 existing same-type splits |
| primary metric | Where correct vs incorrect behavior will show up |

Only implement candidate windows whose non-duplicate reason and primary metric can be stated clearly. The candidate-window table must cover all fresh-trace windows that may contain an independent farm-management decision; do not list only the few labels already chosen for checkpoint export.

The candidate table must include both accepted and rejected candidates. Each rejected candidate must include:

- trace label/date/stage
- source L3 follow-up action or no-action evidence
- rejection reason
- if the reason is duplication: compared scenario id plus field-by-field comparison
- if the reason is no metric: explain why yield/stress/resource/recovered/storage cannot distinguish correct from incorrect behavior

A low-count delivery without rejected-candidate detail does not pass review. In particular, if it has only 4 scenarios or exactly the minimum 6 scenarios, it must prove that no real task atoms were missed.

## What Counts As A Valid Split

A valid split must satisfy:

- It is an independent Python scenario file using the repository's native scenario class/init/workflow/oracle style.
- It does not require running the source L3 at runtime.
- Its initial state comes from a freshly regenerated checkpoint for the source L3.
- Its agent-facing prompt does not leak hidden answers, future affected ridges, final diagnosis, or oracle actions.
- Its oracle decision is basically aligned with the source L3: timing window, target range, parameters, resource constraints, and action order.
- An L2 is a short closed loop: observe -> diagnose -> act -> recheck.
- An L1 is a single action or single decision window, such as planting, replanting, fertilization, fertigation, irrigation, weed control, fungicide, insecticide, harvest, unload, drying, storage, capacity, or budget handling.
- The outcome should distinguish correct from incorrect behavior. For in-season tasks, use biological yield, stress, budget, resource state, or quality proxy. For harvest/postharvest tasks, use recovered yield, storage status, grain moisture, and dry/store sequence.

## Always Regenerate The L3 Trace

Existing traces may be stale. Before splitting, rerun the specified L3 oracle trace and choose checkpoints from that fresh output.

Typical outputs:

- `*-field-summary.csv`
- `*-ridge-states.csv`
- `*-oracle-trace.json`
- full FARM checkpoint JSON

A checkpoint is a complete world snapshot from the moment when the L3 oracle reaches a real trace point. It is not a CSV row and not a plain event id. It should include FarmWorld, Weather, physics engines, soil, phenology, canopy, biotic, yield, WeatherGenerator RNG/cache, current sim time, and related state.

`--checkpoint-label X` means: when the trace app executes the `trace_X` event, write the complete FARM state at that moment to `X.json`. The label is only the place where state is captured. It does not mean every label should become a scenario.

This section is engineering guidance for developers and models. It is not a `briefing_text` template. Do not copy implementation terms such as `checkpoint`, `trace label`, `source L3`, or `action-ready checkpoint` into agent-facing prompts.

Concrete generation steps:

1. Find the trace runner for the specified L3.

```bash
ls scripts/fullseason/run_*trace.py | grep <l3_short_name>
```

If the runner name is unclear, search by scenario id or file-name fragment:

```bash
rg -n "<scenario_id_or_short_name>" scripts/fullseason are/simulation/scenarios/scenario_farm_world_fullseason_v2
```

2. First run the trace without checkpoint export. This gives the real event trace and candidate trace labels.

```bash
uv run python scripts/fullseason/run_<l3_short_name>_trace.py \
  --field-csv docs/ai/<l3_short_name>-field-summary.csv \
  --ridge-csv docs/ai/<l3_short_name>-ridge-states.csv \
  --trace-json docs/ai/<l3_short_name>-oracle-trace.json
```

3. List trace labels that can be used as checkpoint capture points from `*-oracle-trace.json`.

```bash
uv run python - <<'PY'
import json
from pathlib import Path

trace_path = Path("docs/ai/<l3_short_name>-oracle-trace.json")
payload = json.loads(trace_path.read_text(encoding="utf-8"))
for event in payload.get("completed_events", []):
    event_id = str(event.get("event_id", ""))
    if event_id.startswith("trace_"):
        print(event_id.removeprefix("trace_"))
PY
```

Important: `--checkpoint-label` takes the label without the `trace_` prefix. Use `before_o_mid_fungicide_0_18_39_action`, not `trace_before_o_mid_fungicide_0_18_39_action`. Do not export every label or turn every label into a scenario. Export checkpoints only for moments that pass the candidate-window screening.

4. After choosing labels, rerun the trace and export full checkpoint JSON.

```bash
uv run python scripts/fullseason/run_<l3_short_name>_trace.py \
  --field-csv docs/ai/<l3_short_name>-field-summary.csv \
  --ridge-csv docs/ai/<l3_short_name>-ridge-states.csv \
  --trace-json docs/ai/<l3_short_name>-oracle-trace.json \
  --checkpoint-state-dir are/simulation/scenarios/scenario_farm_world_fullseason_v2/l2_l1_splits/<l3_short_name>/checkpoints \
  --checkpoint-label <label_for_l2_or_l1_start> \
  --checkpoint-label <another_label_if_needed>
```

Example:

```bash
uv run python scripts/fullseason/run_hb_insect_after_fungicide_budget_conflict_trace.py \
  --field-csv docs/ai/hb-insect-after-fungicide-budget-conflict-field-summary.csv \
  --ridge-csv docs/ai/hb-insect-after-fungicide-budget-conflict-ridge-states.csv \
  --trace-json docs/ai/hb-insect-after-fungicide-budget-conflict-oracle-trace.json \
  --checkpoint-state-dir are/simulation/scenarios/scenario_farm_world_fullseason_v2/l2_l1_splits/hb_insect_after_fungicide_budget_conflict/checkpoints \
  --checkpoint-label after_mid_routine_check \
  --checkpoint-label before_o_mid_fungicide_0_18_39_action \
  --checkpoint-label after_r5_routine_check \
  --checkpoint-label o_wait_harvest_day_022 \
  --checkpoint-label o_wait_harvest_day_037
```

5. Check that checkpoint files were created and quickly inspect date/DAP/stage/key state.

```bash
ls are/simulation/scenarios/scenario_farm_world_fullseason_v2/l2_l1_splits/<l3_short_name>/checkpoints
```

```bash
uv run python - <<'PY'
import json
from pathlib import Path

for path in sorted(Path("are/simulation/scenarios/scenario_farm_world_fullseason_v2/l2_l1_splits/<l3_short_name>/checkpoints").glob("*.json")):
    payload = json.loads(path.read_text(encoding="utf-8"))
    farm = payload.get("farm_world_state", {})
    physics = payload.get("physics_state", {})
    print(path.name, payload.get("checkpoint_label"), payload.get("sim_time"), physics.get("profile_name"))
PY
```

If the existing trace runner cannot export checkpoints, add a trace-only hook. The hook must satisfy:

- Normal L3 oracle behavior remains unchanged.
- The default hook is no-op.
- Checkpoint export happens only in the trace wrapper or trace app.
- Labels should identify the state precisely, for example `after_mid_routine_check`, `before_o_mid_fungicide_0_18_39_action`, or `o_wait_harvest_day_037`.

If `uv run python scripts/fullseason/run_<l3_short_name>_trace.py --help` does not show `--checkpoint-state-dir` and `--checkpoint-label`, the runner is not wired for checkpoint export yet. Usually, copy the pattern from an existing runner: add those two argparse options and pass them into `scripts.fullseason.harbin_l3_trace_utils.run_trace(...)`. If you need a new action-ready label and no existing trace label captures that moment, add a default no-op hook in the L3 batch flow, such as `before_{prefix}_action`, and let the trace wrapper capture it. The hook must not change normal L3 oracle behavior.

Good checkpoint capture moments:

- Problem confirmed, action not yet taken: good for L2.
- Before waiting for an action window: good for window-seeking L2.
- Action-ready, before loading chemical/seed or starting work: good for L1.
- R8 start: good for harvest-window L2.
- Harvest-ready: good for harvest/store L1.
- After harvest but before unload/dry/store: good for postharvest L1/L2.

Poor checkpoint capture moments:

- Pure intermediate wait day with no new decision.
- Consecutive days with the same state and only a date difference.
- Action already completed and no follow-up decision remains.
- A check-only moment with no action or decision.

Before implementing each split, answer:

- What is this split's independent decision?
- How is it different from already selected splits from the same L3?
- How is it truly different from existing cross-L3 splits of the same task type?
- Which metric shows correct vs incorrect behavior?

If these cannot be answered, do not split it.

## Recommended Directory Layout

Place new files under:

```text
are/simulation/scenarios/scenario_farm_world_fullseason_v2/l2_l1_splits/<l3_short_name>/
```

Typical layout:

```text
<l3_short_name>/
  __init__.py
  _<l3_short_name>_split_common.py
  checkpoints/
    <checkpoint_label>.json
  scenario_l2_<task_name>.py
  scenario_l1_<task_name>.py
```

Shared checkpoint restore helpers, target block helpers, and validation helpers should live in `_..._split_common.py`.

Registration/discovery requirements:

- Both `l2_l1_splits/` and each `<l3_short_name>/` directory need an `__init__.py`.
- Each scenario filename should contain `scenario`; otherwise repository scenario discovery may not import it.
- Every scenario file must use `@register_scenario(SCENARIO_ID)` with a unique scenario id.
- After adding scenarios, run `build_oracle_baselines.py --scenarios <new_ids>` to verify the registry can find those ids. `py_compile` alone is not enough.

## Checkpoint Rules

Prefer full checkpoint restore. A useful full checkpoint should include:

- `FarmWorldApp` state
- `WeatherApp` state
- physics engine state
- soil hydraulic modifiers
- phenology state
- canopy/biomass state
- biotic pressure state
- management effect state
- yield recovery state
- action log
- WeatherGenerator RNG state/cache
- current sim time / last physics sim time

Do not read CSV files at scenario initialization time. CSV outputs are review evidence, not a runtime state source for standalone scenarios.

If full checkpoint restore is temporarily unavailable, hard-code the necessary source-L3 state into the scenario script. Do not dynamically read trace CSVs during scenario initialization.

## Choosing L2/L1 Windows

### L2

Choose a short closed-loop window:

```text
observe -> diagnose -> act -> recheck
```

Good L2 windows include:

- Planting: enter planting season -> recheck soil temperature/VWC/rain/cold risk/trafficability/seed and machinery resources -> wait if needed -> plant when the window is suitable -> recheck planted area, seed/fuel use, and emergence risk.
- Disease: sensor/overview anomaly -> drone localization -> robot/ground confirmation -> fungicide -> pressure/budget recheck.
- Insects: scouting or leaf damage -> ground pest count -> insecticide/manual spray -> pressure/budget recheck.
- Water: soil moisture/water stress -> target confirmation -> irrigation/fertigation -> VWC/stress/budget recheck.
- Weeds: weed pressure/cover -> localization and ground confirmation -> herbicide/mechanical control -> recheck.
- Harvest: R8 start -> repeated weather/trafficability/grain moisture/storage checks -> agent decides harvest day -> harvest/unload/dry-or-store.
- Resource conflict: budget/capacity/machine constraint -> priority decision -> action -> resource recheck.

Do not let an L2 replay the whole season. Short waits and rechecks are fine; season-scale replay is not.

Some L2 scenarios must explicitly represent window seeking. These L2s should not start from an already action-ready state and should not tell the agent a fixed action date in the prompt. They should start from a checkpoint where the problem or task is known, but current weather, soil, maturity, or resource conditions are not yet suitable. The workflow should check forecast/current weather/soil/resource/state, wait, recheck, detect the usable window, and only then act.

A window-seeking L2 must not be only a fixed-date `advance_time(days=N)`. It must leave state evidence before and after the wait so the reviewer can see why action was not suitable earlier and why it is suitable later. At minimum include:

- pre-wait weather/forecast/soil/resource/state evidence;
- post-wait weather/soil/resource/state recheck;
- before/after comparison for the core window variable, such as soil temperature/VWC/rain risk for planting, wind/rain/leaf condition/trafficability for spraying, or grain moisture/weather/capacity for harvest;
- then the action.

If an L2 only says `wait N days -> action` without before/after evidence, it is not a valid window-seeking L2.

Typical window-seeking L2s:

- Planting window: plant only when soil temperature, VWC, future cold/rain risk, field trafficability, seed, fuel, and planter readiness support it.
- Spray window: spray only when wind, rain, leaf/canopy condition, soil trafficability, and chemical budget support it.
- Harvest window: after R8, decide harvest day from weather, trafficability, grain moisture, and capacity.
- Irrigation/fertigation window: water stress is known, but water volume, power/pump availability, weather, and field conditions still matter.
- Mechanical weed-control window: weed pressure is known, but soil and machinery must allow field work.

Do not make every L2 action-ready. If the L3 contains planting, spraying, irrigation, mechanical field work, or harvest waiting, preserve at least one window-seeking L2 whenever possible. A harvest L2 should especially start at R8 or `harvest_allowed` and let the agent decide the harvest day from rechecks, rather than starting from the final harvest-ready state.

### L1

Choose a single action or single decision window. Avoid L1 tasks that are only "check/inspect" unless the repository already has diagnostic L1 patterns.

An L1 may restore from an action-ready checkpoint internally, but the agent-facing briefing must not say "start from a checkpoint." Describe the visible farm state instead, such as "ground confirmation is complete; perform the pre-spray recheck now." The L1 still needs minimal pre-action rechecks:

- current weather
- soil/trafficability
- target state
- inventory/resource/machine state
- then the single action

For example, an L1 fungicide scenario can restore internally from a state after robot confirmation and before sprayer loading. In that case it does not need to repeat the drone/robot diagnosis chain, but the briefing should say "ground checking is complete; perform the pre-spray recheck now" and the workflow should recheck weather, target state, fungicide budget, and sprayer status before spraying.

An L1 planting scenario may represent a single planting execution window. If the task is primarily about waiting for the right planting day, it is usually an L2. When L1 planting starts from an action-ready checkpoint, it should still recheck soil temperature, VWC, forecast, trafficability, seed/fuel, and planter status before planting.

An L1 may come from the same broad window as an L2, but the granularity must differ. Examples:

- L2 planting window: start at planting season, recheck weather/soil, wait if needed, plant, and recheck.
- L1 planting execution: start after field prep/base fertilization/ridge formation is complete or nearly ready; do minimal rechecks, then plant.
- L2 replanting loop: detect poor emergence -> localize -> ground-confirm -> wait for window -> replant -> recheck.
- L1 replanting execution: prior confirmation is complete; recheck seedbed/inventory/equipment, then replant.
- L2 harvest window: start at R8/harvest_allowed, repeatedly check moisture/weather/trafficability/capacity, then decide harvest day.
- L1 harvest execution: start from harvest-ready state, recheck, then harvest -> unload -> dry/store.

## Prompt Rules

Each scenario must provide:

- a `detail=True` prompt
- a `detail=False` prompt

The detailed prompt is an agent-facing task brief, not the oracle answer.

Agent briefing may only use information visible in farm operations. It must not expose scenario implementation details. Fields such as `source_checkpoint_label`, `source_checkpoint_date`, `source_dap`, and `source_growth_stage` may remain in code metadata and validation reports, but they must not appear in `briefing_text`.

`briefing_text` / agent prompts must not contain internal phrases such as:

- `checkpoint`
- `source checkpoint`
- `source L3 checkpoint`
- `action-ready checkpoint`
- `trace label`
- `oracle label`
- "this L1 continues from ... checkpoint"
- "this L2 starts from ... checkpoint"

Rewrite implementation state as farm-operation state:

- Do not write: "This L1 starts from the source L3 action-ready checkpoint on 2026-07-10."
- Write: "As of 2026-07-10, ground disease confirmation and the waiting period are complete; perform the pre-spray recheck now."
- Do not write: "Start from the harvest-ready checkpoint."
- Write: "As of 2026-09-09, the field is in harvest pre-check status; verify weather, trafficability, grain moisture, and storage capacity."

It may reveal visible setup:

- date/stage/DAP
- field size
- cultivar or seed plan
- known management history
- visible resource constraints
- already completed visible checks

It must not reveal:

- hidden future affected ridges
- final diagnosis
- future oracle target
- future action date
- "treat ridges X-Y" unless that range is part of the planting plan, known cultivar zoning, or completed management history visible to the agent

Do not use undefined aliases such as `Zone A`, `Zone B`, `Zone C`, `target zone`, or `treatment zone`. If a briefing uses a zone name, the same briefing or the source L3 visible setup must define:

- zone name
- cultivar or management meaning
- ridge range

Only planting plans, known cultivar zones, and known management zones may directly reveal ridge ranges. If the target area comes from diagnosis of an anomaly, do not directly provide the ridges in the prompt; require sensor/drone/robot/state evidence to identify it.

Examples:

```python
# Bad: checkpoint is an implementation term, and Zone A is undefined.
briefing_text = (
    "This L1 continues from the source L3 pre-harvest checkpoint for HEIHE50 Zone A on 2026-08-23."
)
```

```python
# Good: if three cultivar zones are visible setup, define them in the briefing.
briefing_text = (
    "The field has three known cultivar management zones: "
    "the early HEIHE50 zone is ridges 0-20, the HEINONG84 zone is ridges 21-42, "
    "and the HEINONG58 zone is ridges 43-63. "
    "As of 2026-08-23, the early HEIHE50 zone is in harvest pre-check status. "
    "Recheck weather, soil trafficability, R8/harvest_allowed, grain moisture, trailer capacity, and storage capacity; "
    "when conditions are suitable, handle that visible zone in harvest -> unload -> dry/store order."
)
```

```python
# Good: if the target area should not be given directly, make the agent identify it from evidence.
briefing_text = (
    "As of 2026-08-23, the field is entering staged harvest review, and different cultivars or areas may mature at different rates. "
    "First inspect whole-field and zone state to identify which areas have reached R8/harvest_allowed and satisfy grain moisture, "
    "weather, soil trafficability, and storage constraints; harvest only the mature areas supported by evidence, then unload and dry/store correctly."
)
```

For water, fertilizer, pesticide, and weed-control tasks, require an evidence chain:

- sensor/overview detects anomaly
- drone localizes anomaly
- robot/ground check confirms cause
- targeted action follows

## Oracle Rules

The oracle should be basically consistent with the source L3 decision.

Align on:

- action date or short action window
- target ridge range
- batch/block range
- application rate or operation parameters
- manual vs mechanical operation
- weather-window wait
- harvest/dry/store sequence
- resource budget effects

If a split diverges by 1-3 days after restoring a checkpoint, explain it with physics/weather evidence. The divergence must not invalidate the core decision. For example, if the source L3 can harvest, the L2 must not drift to an impossible moisture state.

Do not use external `adjust_waits`, `adjust_spec`, trace review constants, or patch tables to control oracle behavior. The scenario's oracle logic must be directly defined in its `build_events_flow`, `build_oracle_flow`, or `ScenarioSpec`.

Every major action must be preceded by observation/state evidence.

## Event Order And Helper Expansion

`self.events` is not an arbitrary list of variables. Reviewers may check the oracle workflow by reading `self.events` order, so that order must match the real dependency and execution order.

Basic rules:

- Every event must appear in `self.events` after the event it directly `depends_on(...)`.
- If an event is defined earlier in code but depends on a later wait/recheck step, put it after that wait/recheck in `self.events`.
- `self.events` should read as the oracle workflow: briefing -> observe -> diagnose/recheck -> wait -> recheck -> act -> recheck/report.
- Do not rely on the scheduler to fix a messy `self.events` list. The baseline may still pass, but the scenario should not pass review.

When using helpers, distinguish two cases:

1. The helper returns a list of events, for example `apply_replant_block(...) -> ([load, replant], replant)`. In this case, expand that list in `self.events` with `*replant_events`, and place it where it belongs in dependency order.
2. The helper returns only a terminal event while creating a longer internal chain. For example, `harvest_range(...)` returns the final `store_grain` event, but internally creates repeated `harvest -> unload` blocks, followed by `dry_grain` and `store_grain`.

The second case is not automatically wrong, but it must satisfy all of these:

- the helper's internal order has been inspected and is correct;
- the terminal event is placed in the correct top-level workflow position in `self.events`;
- the audit/validation notes state the real expanded helper order and expanded event count;
- event-count checks must not assume `len(self.events)` equals the number of `completed_events`.

For example, a harvest L2 may have only a dozen top-level `self.events`, while the oracle run has 51 completed events, because `harvest_range(0, 63, dry_after_harvest=True)` expands to 16 `harvest/unload` pairs plus `dry_grain`, `store_grain`, and surrounding observation/recheck events. That count difference is acceptable by itself. The real check is that the expanded order is still:

```text
pre-harvest evidence -> wait/recheck window -> harvest block -> unload -> ... -> dry_grain if needed -> store_grain -> storage recheck
```

If a reviewer or script checks order from `self.events`, treat this terminal helper event as an audited opaque group. If the helper is not an existing repository helper with a known, inspected order, do not hide the expanded events; return a list from the helper and expand it in `self.events`.

## Harvest/Postharvest Rules

A harvest L2 should start at R8 or at the beginning of `harvest_allowed`. Do not tell the agent a fixed harvest date in the prompt; the agent should decide from weather, soil trafficability, grain moisture, and capacity.

The oracle may wait and recheck according to the source L3's real harvest window, but it must satisfy:

- R8 or `harvest_allowed`
- suitable harvest weather
- suitable soil trafficability
- known grain moisture
- normal scenarios should naturally dry to `<=13.5%` and store directly
- late-rain or quality-risk scenarios may harvest at `13.5%-18%`, then dry and store
- `<=13.5%` does not require drying
- do not over-wait for `8%-10%` moisture
- order must be harvest -> unload -> dry/store
- do not continue unload/dry/store after a failed harvest

For harvest/postharvest, biological yield may match the do-nothing baseline. That is acceptable. The primary metric should be recovered yield, warehouse grain, storage moisture, dry/store status, and event order.

## Scenario File Requirements

Each L1/L2 is an independent Python scenario file.

Common fields:

```python
SCENARIO_ID = "scenario_l2_<...>"

@register_scenario(SCENARIO_ID)
class Scenario...(Scenario):
    start_time: float | None = checkpoint_sim_time(CHECKPOINT_...)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True

    source_l3_scenario_id = SOURCE_L3_SCENARIO_ID
    source_checkpoint_label = CHECKPOINT_...
    source_checkpoint_date = "2026-07-10"
    source_dap = 67
    source_growth_stage = "R1_BEGINNING_BLOOM"
```

`start_time` must align with the restored checkpoint's `sim_time`. For full-checkpoint splits, prefer:

```python
start_time: float | None = checkpoint_sim_time(CHECKPOINT_...)
```

Do not force it to a neat wall-clock hour. If the checkpoint's real `sim_time` is `2026-09-06 00:24:58 UTC` but the scenario uses `cst_timestamp(2026, 9, 6, 8)`, harvest/recovered yield can drift.

Use this form only when you have verified that the checkpoint `sim_time` exactly matches the wall-clock start, or when the split intentionally does not restore from a full checkpoint:

```python
start_time: float | None = cst_timestamp(2026, 8, 2, 8)
```

Do not write a raw float.

`duration` is optional and usually unnecessary.

Initialization should restore a checkpoint:

```python
def init_and_populate_apps(self, *args, **kwargs) -> None:
    populate_..._apps(self)
    restore_..._checkpoint(self, CHECKPOINT_...)
```

## Validation Requirements

Run at least these checks for each new batch of scenarios.

### 1. Static Check

```bash
uv run python -m py_compile <new_l1_l2_files>
```

### 2. Independent Oracle Baseline

```bash
uv run python scripts/build_oracle_baselines.py \
  --scenarios <new_scenario_ids> \
  --output-dir oracle_baselines_l2_l1_check_<name> \
  --force \
  --continue-on-error \
  --include-donothing
```

Required result:

- `ok = number of new scenarios`
- `fail = 0`
- no failed events
- no tool error returns
- final yield or primary metric is non-empty
- harvest/postharvest must not continue unload/dry/store after failed harvest

`ok=all scenarios` is only the minimum gate; it does not prove the split is valid. Inspect actual `return_value` in oracle completed events:

- every major action has `status == "ok"`;
- no `error`;
- no semantically important `warning`;
- action dates, target ranges, and parameters align with the source L3;
- expanded helper event order matches the expected workflow.

For harvest/postharvest especially, do not only read `ridges_harvested` from the baseline JSON. Inspect `harvest`, `unload_grain`, `dry_grain`, and `store_grain` return values. Check warnings, non-empty `moved_kg`, valid `storage_grain_moisture_pct`, and whether `stored_without_drying` matches the grain-moisture rule.

### 3. In-Season Tasks Must Extrapolate To Maturity

For planting, post-planting, and pre-harvest tasks, run both oracle and do-nothing to maturity and compare biological yield. For planting tasks, also check planted ridges, emergence/R8 counts, and whether the do-nothing baseline is correctly unplanted or delayed.

Record:

- oracle biological yield
- do-nothing biological yield
- delta kg
- delta %
- `ridges_r8`
- `ridges_harvested`

### 4. Harvest/Postharvest Tasks Must Check Recovery/Storage

Record:

- recovered yield
- warehouse grain
- trailer/bin is empty after unload/store
- storage moisture
- dried or stored_without_drying
- `store_grain` `warning` / `error`
- `dry_grain` `target_moisture_pct` and `ridges_dried` when using the drying path
- `store_grain` `moved_kg`, `storage_grain_moisture_pct`, and `stored_without_drying`
- harvest/unload/dry/store order

### 5. Compare Against The Source L3

Check:

- initial checkpoint date/DAP/stage/main stress/grain moisture matches the source L3
- key L2/L1 action timing is aligned with the source L3
- target ridge range, batches, and parameters match the source L3
- any divergence is explained with trace/checkpoint evidence

## Review Checklist

Before submitting:

- The new split reaches the normal target: `3-6 L2 + 3-6 L1`; if not, a `low-count blocker report` exists and has reviewer acceptance.
- The candidate-window table covers all real management windows in the source L3, not only exported checkpoints.
- Every real management window was audited for both L2 and L1 directions; skipped directions have trace-backed explanations.
- Every rejected task atom has concrete evidence; do not write only "generic", "duplicate", or "low value."
- New scenario ids are registered and discoverable by `build_oracle_baselines.py`.
- New splits do not depend on source L3 runtime.
- Checkpoint JSON files are committed.
- `farm_checkpoint_state.py` or equivalent restore helper is committed.
- No `__pycache__`, temporary baseline outputs, or IDE files are included.
- Scenario initialization does not read CSV trace files.
- No external `adjust_*` patch controls oracle behavior.
- Prompts do not leak hidden answers.
- L1/L2 files are not duplicate-by-rename.
- L2 has a closed loop; L1 is a single action or single decision.
- Every action has prior evidence.
- Harvest rules satisfy moisture/weather/trafficability/capacity constraints.
- Oracle vs do-nothing metrics are recorded.

## Not Accepted

These are invalid:

- Delivering fewer than 6 scenarios for a specified L3 without a reviewer-accepted `low-count blocker report`.
- Delivering only the minimum 6 scenarios for a specified L3 without a complete accepted/rejected audit.
- Rejecting a real planting/harvest/management window as "generic" or "similar to existing tasks" without field-by-field comparison to an existing scenario.
- Creating exactly one scenario per checkpoint, making checkpoint count equal scenario count.
- JSON-only, markdown-only, or batch-runner-only outputs.
- Oracle event lists without registered scenarios.
- Building state from a few CSV columns.
- Runtime CSV reads during scenario initialization.
- Same checkpoint, same prompt, same oracle, same target ridges, same parameters, different name.
- Cross-L3 duplicates that only rename scenario ids.
- An L1 that waits for a long window before acting, effectively becoming L2.
- An L2 that replays the whole season, effectively becoming L3.
- Prompt leaks of hidden target ranges or final diagnosis.
- Fertilizer used to solve pest/disease/weed/water problems, or pesticide used to solve water/fertilizer problems.
- Drying grain that is already `<=13.5%`.
- Continuing unload/dry/store after harvest failure.
- Changing engines or shared behavior just to make a split pass, unless explicitly requested and fully validated for global impact.

## Delivery Summary Template

Use this structure when reporting completion:

```text
Source L3:
Candidate windows audited:
Accepted task atoms:
Rejected windows:
Same-checkpoint multi-task splits:
L2 count:
L1 count:
Number of new L2/L1 scenarios:
Meets 3-6 L2 + 3-6 L1:
If not, low-count blocker report:
New files:
Checkpoint labels/date/DAP/stage:
Why the final count is justified:

Scenario table:
- scenario_id
- level
- checkpoint
- task
- oracle action
- primary metric

Validation:
- py_compile command/result
- baseline command/result
- ok/fail
- no tool errors
- oracle vs do-nothing yield table
- harvest recovered/storage table if applicable

L3 alignment:
- action date
- ridge range
- parameters
- wait window
- resource constraints

Known risks/uncertainties:
- Only list real residual risk. Avoid generic filler.
```
