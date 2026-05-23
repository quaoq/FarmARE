# FarmARE Full-Season 脚本说明

本文档说明 `scripts/fullseason/` 目录的用途：它不是 scenario 定义目录，而是 L3 full-season soybean 场景的 trace、review、audit 和 human-inventory 导出层。

## 1. 目录角色

`scripts/fullseason/` 主要有五类脚本：

| 脚本类型 | 代表文件 | 作用 | 是否修改场景 |
|---|---|---|---|
| 单场景 trace runner | `run_hb_base_hn84_std_normal_trace.py`、`run_hb_*.py` | 运行一个 L3 scenario 的 expert oracle，并导出 field/ridge/oracle trace | 否 |
| trace 工具库 | `harbin_l3_trace_utils.py` | 给 production scenario 动态插入 trace app，采集 daily state 和 event evidence | 否 |
| review/audit | `review_fullseason_l3_scenarios.py`、`audit_fullseason_l3_value.py` | 读取已生成 trace，检查事件、CSV、target、yield/value 等 | 否 |
| inventory/export | `export_v2_scenario_inventory.py`、`generate_final_human_inventory_v2.py` | 把 trace、scenario metadata、review 结果整理成人审表和 Markdown/CSV | 否 |
| 实验脚本 | `run_biotic_pressure_mixed_ridge_experiment.py` | 用于局部物理/病虫草压力实验，不是正式 L3 scenario runner | 否 |

场景本体在：

```text
are/simulation/scenarios/scenario_farm_world_fullseason_v2/
```

`scripts/fullseason/` 只负责“运行场景并导出证据”，不应该在这里定义新的 production scenario。

## 2. 常用命令

运行单个 trace：

```bash
uv run python scripts/fullseason/run_hb_base_hn84_std_normal_trace.py
```

每个 trace runner 通常支持覆盖输出路径：

```bash
uv run python scripts/fullseason/run_hb_base_hn84_std_normal_trace.py \
  --field-csv /tmp/field-summary.csv \
  --ridge-csv /tmp/ridge-states.csv \
  --trace-json /tmp/oracle-trace.json
```

批量运行所有 trace runner 时，只跑 `run_*_trace.py`，不要把实验脚本也混进去：

```bash
for script in scripts/fullseason/run_*_trace.py; do
  uv run python "$script"
done
```

运行 review：

```bash
uv run python scripts/fullseason/review_fullseason_l3_scenarios.py
```

导出 V2 inventory：

```bash
uv run python scripts/fullseason/export_v2_scenario_inventory.py
```

导出 final human inventory v2：

```bash
uv run python scripts/fullseason/generate_final_human_inventory_v2.py
```

运行 value audit：

```bash
uv run python scripts/fullseason/audit_fullseason_l3_value.py
```

## 3. Trace runner 生成什么

大多数 `run_*_trace.py` 会生成三个核心文件：

| 输出 | 默认位置 | 内容 |
|---|---|---|
| field summary CSV | `docs/ai/<slug>-field-summary.csv` | 按 trace checkpoint 汇总全田/分区状态，例如 stage counts、平均 GDD、平均 NDVI、压力最大值等 |
| ridge states CSV | `docs/ai/<slug>-ridge-states.csv` | 每个 checkpoint、每条 ridge 的 stage、GDD、VWC、water/nutrient/biotic stress、LAI、NDVI、biomass、grain moisture、recovered yield |
| oracle trace JSON | `docs/ai/<slug>-oracle-trace.json` | completed events、action justifications、diagnostic warnings、tool return evidence |

这些文件是后续 review、inventory、one-row table 和 final documentation 的证据来源。

## 4. 脚本和场景的映射关系

映射链路是：

```text
scenario wrapper 文件
  -> SCENARIO_ID / SPEC
  -> run_<slug>_trace.py
  -> docs/ai/*field-summary.csv, *ridge-states.csv, *oracle-trace.json
  -> review_fullseason_l3_scenarios.SCENARIOS
  -> inventory / review / final docs
```

### 4.1 Scenario wrapper

场景文件位于：

```text
are/simulation/scenarios/scenario_farm_world_fullseason_v2/scenario_full_season_*.py
```

批量 catalog 场景通常是轻量 wrapper：

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

真正的 scenario metadata 在：

```text
are/simulation/scenarios/scenario_farm_world_fullseason_v2/harbin_l3_batch_catalog.py
```

其中 `ScenarioSpec` 记录：

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

少数早期/legacy 场景不是 catalog-driven，配置和 oracle event 直接写在 scenario wrapper 内。

### 4.2 Trace runner

每个常规 trace runner 会导入一个 scenario class：

```python
from are.simulation.scenarios.scenario_farm_world_fullseason_v2.scenario_full_season_hb_xxx import (
    SCENARIO_ID,
    SPEC,
    ScenarioFullSeasonHBXxx,
)
from scripts.fullseason.harbin_l3_trace_utils import run_trace
```

然后调用：

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

当前目录中有 90 个 `run_*_trace.py`。其中 86 个使用共享 `harbin_l3_trace_utils.run_trace()`，另有 4 个 custom trace runner 自己写了更细的导出逻辑：

- `run_fastdraining_dry_patch_irrigation_trace.py`
- `run_heinong84_edge_low_fertility_trace.py`
- `run_heinong84_staggered_planting_trace.py`
- `run_wet_june_ab_zoned_disease_trace.py`

这 4 个仍然是 trace/export 脚本，不是 production scenario 定义。

### 4.3 Review mapping

`review_fullseason_l3_scenarios.py` 中的 `SCENARIOS` 是 review 层的 artifact 映射表：

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

它告诉 review 脚本：

- 这个 scenario 的 trace CSV/JSON 在哪里；
- 哪些 zone 必须存在；
- 应该从哪个 ridge-state CSV 和 oracle trace JSON 读证据。

对 catalog-driven 场景，review 脚本还会根据 `BATCH_L3_SPECS` 自动扩展：

```python
DOCS_AI / f"{spec.slug.replace('_', '-')}-field-summary.csv"
DOCS_AI / f"{spec.slug.replace('_', '-')}-ridge-states.csv"
DOCS_AI / f"{spec.slug.replace('_', '-')}-oracle-trace.json"
```

所以 catalog slug 会直接决定默认 artifact 文件名。

## 5. 生成文件和下游用途

| 命令 | 读取 | 生成 | 用途 |
|---|---|---|---|
| `uv run python scripts/fullseason/run_<slug>_trace.py` | production scenario + physics engine | `docs/ai/*field-summary.csv`、`docs/ai/*ridge-states.csv`、`docs/ai/*oracle-trace.json` | 真实 oracle 执行证据 |
| `uv run python scripts/fullseason/review_fullseason_l3_scenarios.py` | `SCENARIOS` 中登记的 CSV/JSON | `docs/ai/fullseason-l3-review-summary.md` | 检查 trace 是否有失败事件、error return、target/action 问题、CSV 异常 |
| `uv run python scripts/fullseason/export_v2_scenario_inventory.py` | review specs + scenario source + trace artifacts | `docs/ai/fullseason-v2-scenario-inventory.csv`、`.md` | 早期人审 inventory |
| `uv run python scripts/fullseason/generate_final_human_inventory_v2.py` | final review artifacts + scenario metadata + previous inventory | `outputs/fullseason_review/final_human_scenario_inventory_v2/` | 人审表、compact workflow、missing/leakage report、metric summary |
| `uv run python scripts/fullseason/audit_fullseason_l3_value.py` | ridge CSV + trace JSON | `docs/ai/fullseason-l3-value-audit.csv/json/md` | 场景价值、yield before/after、管理窗口审计 |

## 6. 和最终中/英文 L3 文档的关系

最终文档：

```text
are/simulation/scenarios/scenario_farm_world_fullseason_v2/FarmARE_L3_scenario_CN.md
are/simulation/scenarios/scenario_farm_world_fullseason_v2/FarmARE_L3_scenario_EN.md
```

是最终 formal scenario system 的说明文档。它们不是 trace runner 输出，也不是 `scripts/fullseason/` 当前固定脚本自动生成的文件。

这两份文档读取/参考的是下游 acceptance artifacts，例如：

```text
outputs/fullseason_review/final_acceptance_90_v2/
outputs/fullseason_review/final_acceptance_90_v2/final_one_row_inventory_cn_clean_v2/
```

以及 scenario code / trace runner 结构。

如果以后需要稳定复现这两份 CN/EN 文档，建议新增一个专门脚本，例如：

```text
scripts/fullseason/generate_l3_scenario_docs.py
```

该脚本应读取 final acceptance inventory、scenario catalog 和 script mapping，然后生成 `FarmARE_L3_scenario_CN.md` 与 `FarmARE_L3_scenario_EN.md`。

## 7. 维护注意事项

1. 不要把 `scripts/fullseason/` 当作 scenario source of truth。production scenario 在 `are/simulation/scenarios/scenario_farm_world_fullseason_v2/`。
2. 不要只根据是否存在 `run_*_trace.py` 判断是否属于正式评估池。正式池应看 final acceptance artifact 或 formal scenario id list。
3. 新增或改场景后，顺序应是：改 scenario/catalog/profile -> 跑对应 trace runner -> 跑 review -> 更新 inventory/final artifacts。
4. Trace CSV/JSON 不要手改。发现问题应修 scenario/oracle/app/engine 后重新跑 trace。
5. `ridge-states.csv` 是判断 daily state、stress 是否真实出现、处理是否有效的核心文件。
6. `oracle-trace.json` 是判断关键 action 是否有前置 tool return 支撑的核心文件。
