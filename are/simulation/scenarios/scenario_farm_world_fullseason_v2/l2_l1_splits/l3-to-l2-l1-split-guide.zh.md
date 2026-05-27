# L3 Full-Season Scenario 切分 L2/L1 指南

本文档用于指导同事或其他模型：如何从一个已经验证过的 L3 full-season soybean scenario 中，切出独立可运行的 L2/L1 scenario。

目标不是写 spec、event list 或 markdown 说明，而是在 repo 里新增原生 Python scenario，并且每个 scenario 都能独立跑 base oracle。

## 核心口径

一次任务通常只处理一个指定 L3。不要擅自扩大到多个 L3。

对这个指定 L3，不先按数量硬切，但也不能因为“保守”而少切。先从新生成的 L3 trace 里列出所有真实关键农事窗口，只切不重复、有独立决策价值、能独立验证的窗口。最终数量由候选窗口筛选结果决定：真实窗口更多且不重复就多切，真实窗口不足才少切。不要为了数量硬造重复场景，也不要因为已经有几个就停。

一个指定 L3 的正常产出目标是 `3-6 个 L2 + 3-6 个 L1`。这不是让你凭空造场景，而是一个审计下限信号：如果最后少于 `3 个 L2`、少于 `3 个 L1`，或总数少于 `6`，不能直接交付代码说“完成”。必须先输出 `low-count blocker report`，证明 fresh trace 中确实没有更多非重复 task atom，并等待 reviewer 接受。没有 blocker report 的低数量结果视为不合格。

禁止在候选窗口全表完成前说“准备写 N 个 scenario”或“导出 N 个 checkpoint 后写 N 个 scenario”。checkpoint 数量不是 scenario 数量，候选状态数量也不是任务数量。

L2/L1 必须来自 L3 的真实关键窗口。不能凭空造状态，不能只从 CSV 手工拼几列状态。优先从 L3 真实 trace 时刻导出 full FARM checkpoint JSON，然后在 L2/L1 初始化时 restore。

## 先审计 task atom，不先定数量

写代码前必须先把 fresh L3 trace 拆成 `task atom`，再决定要实现几个 L2/L1。`task atom` 指一个独立农事任务单元：它有清楚的当前问题、证据链、动作类型、目标范围、参数和 primary metric。

不要按 checkpoint 数量决定 scenario 数量：

- 一个 checkpoint 可以产生 0 个 scenario：例如终态、纯记录点、无动作、无独立 metric。
- 一个 checkpoint 可以产生 1 个 scenario：例如只有一个清楚的 action-ready 单动作。
- 一个 checkpoint 可以产生多个 scenario：例如同一次例行检查后同时存在 nutrient stress 和 weed pressure，它们目标区、诊断原因、动作、参数和 metric 都不同。

因此，“导出 6 个 checkpoint，所以写 6 个 scenario”是错误推理。正确流程是：

1. 重新生成指定 L3 trace。
2. 列出 fresh trace 里所有可能切分的农事窗口。
3. 把每个窗口拆成一个或多个 task atom。
4. 对每个 task atom 做 accept/reject 判断。
5. accepted task atom 有几个，就实现几个 scenario。

同一个 checkpoint 下，如果存在多个不同 task atom，可以拆多个 scenario。可以拆开的依据包括：诊断原因不同、目标区不同、action 类型不同、action 参数不同、资源/预算约束不同、primary metric 不同、一个是 L2 闭环而另一个是 L1 action-ready 子任务、一个需要找窗口而另一个可以立即执行。

不能为了凑数量拆重复任务。如果同 checkpoint、同 prompt、同 oracle、同目标区、同 action、同参数、同 metric 只是换文件名或 scenario id，必须 reject。

如果最后少于 6 个 scenario，这不是普通交付摘要，而是异常情况。必须先写 `low-count blocker report`，并在报告里说明：fresh trace 一共审计了多少候选窗口、accepted 多少 task atom、rejected 哪些窗口及原因、有没有同 checkpoint 多个 task atom 被合并、scenario 数量是否刚好等于 checkpoint 数量、为什么无法达到最低 `3 L2 + 3 L1`。如果正好是 6 个 scenario，它只是最低合格线，必须提供完整 accepted/rejected 审计；不能用一句“窗口不多”带过。如果数量刚好等于 checkpoint 数量，必须重新审计一次，因为这通常说明把 checkpoint 当成了 scenario。

低数量报告不能只写“rejected”。每个被拒绝的真实管理窗口都要分别回答两件事：

- 为什么不能切 L2：没有问题起点、没有等待/诊断/复查闭环、没有独立 metric，还是会和已有 L2 完全重复。
- 为什么不能切 L1：没有 action-ready checkpoint、没有独立工具动作、动作已被另一个 L1 完全覆盖，还是缺少可验证 metric。

如果一个窗口能切 L2 但不能切 L1，或能切 L1 但不能切 L2，也要写清楚。不能用“窗口少”“价值不大”“通用”来代替这两个判断。

## 最低产出和拒绝门槛

默认做法是“最大化真实非重复 task atom”，不是“挑几个最明显的”。对每个源 L3，必须从 fresh trace 逐项审计这些窗口；只要源 L3 里真实存在，就必须进入候选表：

- 播前/整地/基肥/起垄窗口
- 播种或改播密度/品种/日期窗口
- 出苗/补种窗口
- 水分或灌溉/水肥窗口
- 营养/补肥/fertigation 窗口
- 杂草窗口
- 病害窗口
- 虫害窗口
- 资源/预算/容量/机器约束窗口
- R8 或 harvest_allowed 开始后的收获找窗口
- harvest-ready 单动作窗口
- 卸粮/烘干/入库/容量窗口

每个真实管理窗口至少审两种切法：

- `L2`: 从问题出现或窗口未满足处开始，包含 observe -> diagnose/recheck -> wait if needed -> act -> recheck。
- `L1`: 从 action-ready 或单决策状态开始，做最小复核后执行单动作。

如果某个窗口只实现 L2 不实现 L1，或只实现 L1 不实现 L2，必须在候选表里写明原因。原因必须是 trace 证据或工具限制，例如没有 action-ready checkpoint、没有独立 metric、动作不是独立工具、或会与已有同 L3 split 完全重复。不能只写“通用”“重复度高”“价值不大”。

拒绝一个候选 task atom 的门槛很高。下面这些理由本身不够：

- “这是通用播种/通用收获。”
- “已有别的 L3 有类似任务。”
- “这个窗口比较简单。”
- “已经有 4/6 个 scenario 了。”
- “这个 checkpoint 已经用于另一个 scenario。”

如果要以“重复”为由 reject，必须写出被比较的现有 scenario id，并逐项比较 checkpoint/date/stage、visible setup、目标区、动作、参数、窗口选择、资源约束和 primary metric。只要其中有真实差异，就不能简单 reject；应该保留为独立 task atom，或说明为什么这个差异不影响任务。

## 不重复的判断对象

“不重复”要同时看两层：

1. 同一个 L3 内部不重复。新增 L2/L1 之间不能只是换名字。L1 可以是某个 L2 的 action-ready 子窗口，但必须更短、更具体，checkpoint 或 oracle 粒度不同，不能复制 L2 的完整 observe-diagnose-wait-act-recheck 闭环。
2. 跨 L3 全局不重复。不同 L3 可以都有同类任务，例如杀菌、播种、收获；但必须有真实不同的初始 checkpoint、约束、诊断原因、目标区、操作参数、窗口选择或 primary metric。不能只是换 L3 名字、scenario id 或文案。

| 比较对象 | 可以接受 | 不接受 |
|---|---|---|
| 同 L3 的 L2 vs L2 | 不同窗口、不同决策、不同约束或不同目标区 | 同 checkpoint、同 oracle、同参数，只改名字 |
| 同 L3 的 L1 vs L1 | 不同单动作或不同 action-ready checkpoint | 同一动作拆成多个名字 |
| 同 L3 的 L1 vs L2 | L1 是 L2 的更短 action-ready 子任务 | L1 复制 L2 的完整闭环 |
| 跨 L3 同类任务 | 初始状态、约束、目标区、参数、窗口或 metric 至少有真实差异 | prompt、状态、oracle、参数、metric 本质一样，只改 L3 名 |

开工前先建候选窗口表，不要直接写 scenario：

| 字段 | 说明 |
|---|---|
| checkpoint/trace label | 候选窗口从 L3 哪个真实时刻开始 |
| date/DAP/stage | 初始日期、生育天数和阶段 |
| task type | 播种、水肥、草、病、虫、收获、入库、资源等 |
| task atom | 这个窗口下的独立任务单元；同 checkpoint 可以有多个 task atom |
| 当前问题或决策 | agent 需要解决什么，不写 oracle 答案 |
| L3 后续 oracle 动作 | 源 L3 后面真实做了什么 |
| target ridges / 参数 | 目标范围、用量、机器/资源参数 |
| split level | 更适合 L2 还是 L1 |
| same-checkpoint split | 如果同 checkpoint 拆多个 scenario，说明它们为什么不是重复 |
| 不重复原因 | 和同 L3 已选 split、跨 L3 已有同类 split 的真实差异 |
| primary metric | 做对/做错在哪里体现 |

只有“不重复原因”和 “primary metric” 能写清楚的候选窗口，才进入实现。候选窗口表必须覆盖 fresh trace 里所有可能有独立农事决策的窗口；不能只列已经准备导 checkpoint 的少数几个。

候选表不能只写 accepted 项，也必须写 rejected 项。rejected 项至少包含：

- trace label/date/stage
- L3 真实后续动作或无动作证据
- reject 原因
- 如果原因是重复：被比较的 scenario id 和逐项对比
- 如果原因是无 metric：说明 yield/stress/resource/recovered/storage 哪些都不能区分

没有 rejected 明细的低数量交付不能通过 review；尤其是只切 4 个，或刚好切到最低 6 个时，必须证明没有遗漏真实 task atom。

## 合格定义

一个合格的 L2/L1 split 应满足：

- 是独立 Python scenario 文件，使用 repo 原生 scenario class/init/build workflow/oracle flow 风格。
- 不依赖源 L3 运行；单独注册、单独 baseline 可跑。
- 初始状态来自源 L3 当次重新生成的真实 checkpoint。
- agent-facing prompt 不泄露隐藏答案、未来目标区、最终诊断或 oracle 操作结果。
- oracle 操作与源 L3 的关键决策基本一致，包括日期窗口、目标范围、参数、资源约束和操作顺序。
- L2 是短闭环：observe -> diagnose -> act -> recheck。
- L1 是单动作或单决策窗口：播种、补种、补肥/水肥、灌溉、控草、杀菌、杀虫、收获、卸粮、烘干、入库、容量/预算操作等。
- 结果能体现做对和做错的差别。对生育期任务，至少看 biological yield、stress、budget、resource、质量 proxy 之一；对 harvest/postharvest，重点看 recovered yield、storage status、grain moisture、dry/store sequence。

## 必须重新生成 L3 trace

trace 可能是旧的。每次切分前必须重新跑指定 L3 的 oracle trace，并基于这次输出选择 checkpoint。

通常需要生成：

- `*-field-summary.csv`
- `*-ridge-states.csv`
- `*-oracle-trace.json`
- full FARM checkpoint JSON

checkpoint 是 L3 oracle 跑到某个真实时刻时的完整世界快照，不是 CSV 行，也不是普通 event id。它应该包含 FarmWorld、Weather、physics engines、soil、phenology、canopy、biotic、yield、WeatherGenerator RNG/cache、当前 sim time 等状态。

`--checkpoint-label X` 的意思是：trace app 执行到 `trace_X` 这个 trace event 时，把当时完整 FARM 状态导出为 `X.json`。label 只是“抓状态的位置”，不代表每个 label 都要切一个 scenario。

本节是给开发者和模型实现者看的工程说明，不是 `briefing_text` 模板。不要把 `checkpoint`、`trace label`、`source L3`、`action-ready checkpoint` 这些实现词复制进 agent-facing prompt。

具体生成步骤：

1. 找到指定 L3 对应的 trace runner。

```bash
ls scripts/fullseason/run_*trace.py | grep <l3_short_name>
```

如果不确定 runner 名字，先用 L3 scenario id 或文件名片段搜：

```bash
rg -n "<scenario_id_or_short_name>" scripts/fullseason are/simulation/scenarios/scenario_farm_world_fullseason_v2
```

2. 先跑一次 trace，不导 checkpoint，用来查看真实事件和可用 trace labels。

```bash
uv run python scripts/fullseason/run_<l3_short_name>_trace.py \
  --field-csv docs/ai/<l3_short_name>-field-summary.csv \
  --ridge-csv docs/ai/<l3_short_name>-ridge-states.csv \
  --trace-json docs/ai/<l3_short_name>-oracle-trace.json
```

3. 从 `*-oracle-trace.json` 列出可抓 checkpoint 的 trace labels。

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

注意：`--checkpoint-label` 使用的是去掉 `trace_` 前缀后的 label，例如 `before_o_mid_fungicide_0_18_39_action`，不是 `trace_before_o_mid_fungicide_0_18_39_action`。不要把所有 label 都导出或都切成 scenario；只给通过候选窗口筛选的时刻导 checkpoint。

4. 选好 labels 后，重新跑 trace 并导出 full checkpoint JSON。

```bash
uv run python scripts/fullseason/run_<l3_short_name>_trace.py \
  --field-csv docs/ai/<l3_short_name>-field-summary.csv \
  --ridge-csv docs/ai/<l3_short_name>-ridge-states.csv \
  --trace-json docs/ai/<l3_short_name>-oracle-trace.json \
  --checkpoint-state-dir are/simulation/scenarios/scenario_farm_world_fullseason_v2/l2_l1_splits/<l3_short_name>/checkpoints \
  --checkpoint-label <label_for_l2_or_l1_start> \
  --checkpoint-label <another_label_if_needed>
```

例子：

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

5. 检查 checkpoint 文件真的生成，并快速确认日期、DAP、stage 和关键状态。

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

如果现有 trace runner 不支持 checkpoint 导出，可以加 trace-only hook，但要保证：

- 正常 L3 oracle 行为不变。
- hook 默认 no-op。
- checkpoint 导出只在 trace wrapper 或 trace app 中发生。
- hook label 要能表达状态位置，例如 `after_mid_routine_check`、`before_o_mid_fungicide_0_18_39_action`、`o_wait_harvest_day_037`。

如果 `uv run python scripts/fullseason/run_<l3_short_name>_trace.py --help` 里没有 `--checkpoint-state-dir` 和 `--checkpoint-label`，说明 runner 还没接 checkpoint 导出。通常做法是参考已有 runner，给 argparse 增加这两个参数，并把它们传给 `scripts.fullseason.harbin_l3_trace_utils.run_trace(...)`。如果需要新的 action-ready label，而现有 trace labels 没有对应位置，可以在 L3 batch flow 中加默认 no-op hook，例如 `before_{prefix}_action`，再由 trace wrapper 捕获；不要让这个 hook 改变正常 L3 oracle 行为。

适合导 checkpoint 的时刻：

- 问题刚确认、动作还没做：适合 L2。
- 等待窗口前：适合 window-seeking L2。
- action-ready、装药/装种/作业前：适合 L1。
- R8 开始：适合 harvest window L2。
- harvest-ready：适合 harvest/store L1。
- harvest 后、卸粮/烘干/入库前：适合 postharvest L1/L2。

不适合导 checkpoint 的时刻：

- 纯中间等待日，没有新决策。
- 同一状态连续几天只差日期。
- 动作已经完成且没有后续决策。
- 只能做“检查一下”的无动作窗口。

每个 split 开始实现前，必须回答：

- 这个 split 的独立决策是什么？
- 它和同 L3 已选 split 的差异是什么？
- 它和已有跨 L3 同类 split 的真实差异是什么？
- 做对/做错会在哪个 metric 上体现？

答不上来就不切。

## 推荐目录结构

新增文件建议放在：

```text
are/simulation/scenarios/scenario_farm_world_fullseason_v2/l2_l1_splits/<l3_short_name>/
```

常见结构：

```text
<l3_short_name>/
  __init__.py
  _<l3_short_name>_split_common.py
  checkpoints/
    <checkpoint_label>.json
  scenario_l2_<task_name>.py
  scenario_l1_<task_name>.py
```

如果多个 split 共用 checkpoint restore、目标区 block helper、validation helper，放到 `_..._split_common.py`。

注册发现要求：

- `l2_l1_splits/` 和每个 `<l3_short_name>/` 目录都要有 `__init__.py`。
- 每个 scenario 文件名要包含 `scenario`，否则 repo 的 scenario discovery 可能不会 import。
- 每个文件都必须用 `@register_scenario(SCENARIO_ID)` 注册唯一 scenario id。
- 新增后要用 `build_oracle_baselines.py --scenarios <new_ids>` 验证 registry 能找到这些 id；只通过 `py_compile` 不够。

## checkpoint 原则

优先 full checkpoint restore，至少覆盖：

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

不要运行时读 CSV 初始化 scenario。CSV 可以作为 review 证据，但不是 standalone scenario 的状态来源。

如果暂时没有 full checkpoint，只能把 L3 当天关键状态硬编码到脚本中；不能在 scenario 初始化时动态读 trace CSV。

## L2/L1 选择原则

### L2

选择一个短闭环窗口：

```text
observe -> diagnose -> act -> recheck
```

适合 L2 的窗口：

- 播种：进入可播期 -> 复查 soil temperature/VWC/降雨/低温风险/通行性/种子和机器资源 -> 必要时等待 -> 窗口满足后播种 -> 复查播种面积、种子/燃油和后续出苗风险。
- 病害：传感器/概览异常 -> 无人机定位 -> robot/ground check -> fungicide -> 复查压力/预算。
- 虫害：例行检查/叶片损伤 -> ground check 虫口确认 -> insecticide/manual spray -> 复查压力/预算。
- 水分：soil moisture/water stress -> 确认目标区 -> irrigation/fertigation -> 复查 VWC/stress/budget。
- 杂草：weed pressure/cover -> 定位和地面确认 -> herbicide/mechanical weed control -> 复查。
- 收获：R8 开始 -> 连续复查天气/土壤/籽粒水分/仓储 -> agent 自己决定哪天收 -> harvest/unload/dry-or-store。
- 资源冲突：预算/容量/机器约束 -> 选择优先级 -> 执行动作 -> 复查资源结果。

L2 不要覆盖过长未来窗口。允许短期等待和 recheck，但不要把完整季节重演一遍。

一部分 L2 必须体现“找窗口”。这类 L2 不应该从 action-ready 状态直接执行，也不应该在 prompt 里告诉固定执行日期。它应该从“问题/任务已明确，但当前天气、土壤、成熟度或资源条件未满足”的 checkpoint 开始，通过 forecast/current weather/soil/resource/state recheck 发现窗口，等待，再复查，最后执行动作。

找窗口 L2 不能只是固定日期 `advance_time(days=N)`。它必须在等待前后留下状态证据，让 reviewer 看出为什么之前不能做、为什么后来可以做。至少要有：

- 等待前的 weather/forecast/soil/resource/state 证据；
- 等待后的 weather/soil/resource/state recheck；
- 对核心窗口变量的前后对比，例如播种的 soil temperature/VWC/降雨风险，喷药的风雨/叶面/通行性，收获的 grain moisture/天气/容量；
- 再执行 action。

如果 L2 只写了 `wait N days -> action`，没有前后证据，它不是合格的 window-seeking L2。

典型 window-seeking L2：

- 播种找窗口：土温、VWC、未来冷雨、田间通行性、种子/燃油/播种机都支持时才播。
- 喷药找窗口：风、雨、叶面条件、土壤通行性和药剂预算支持时才喷。
- 收获找窗口：R8 后根据天气、通行性、grain moisture 和容量决定哪天收。
- 灌溉/水肥找窗口：水分压力明确，但还要看水量、电力/泵、天气和作业条件。
- 机械控草找窗口：weed pressure 明确，但还要看土壤和机器是否能下地。

不要把所有 L2 都做成 action-ready。一个 L3 里如果有播种、喷药、灌溉、机械作业或收获等待，至少应优先保留一个“找窗口”L2。harvest L2 尤其应该从 R8 或 harvest_allowed 开始，让 agent 根据复查结果决定哪天收，而不是从最终 harvest-ready 状态直接执行。

### L1

选择单动作或单决策窗口。L1 不建议只是“检查/判断”，除非 repo 现有 L1 任务本来就是诊断型。

L1 可以在实现上从 action-ready checkpoint restore，但 agent-facing briefing 不能写“从 checkpoint 开始”。给 agent 的说法应是业务状态，例如“已完成地面确认，当前进入喷药前复核”。L1 仍必须做最小复核：

- 当前天气
- 土壤/通行性
- 目标区状态
- 资源/库存/机器状态
- 然后执行单一动作

例如，L1 杀菌如果从“机器人确认后、装药前”的内部 checkpoint restore，可以不重复无人机/机器人诊断链，但 briefing 应写成“地面检查已完成，当前需做喷药前复核”，并重新检查天气、目标区、药剂预算和喷雾设备。

L1 播种可以是单次播种执行窗口，但如果任务重点是“等合适播种日”，通常更适合做 L2。L1 播种从 action-ready checkpoint 开始时，也要复核 soil temperature、VWC、forecast、通行性、种子/燃油/播种机状态后再播。

L1 可以和 L2 来自同一个大窗口，但必须粒度不同。例如：

- L2 播种找窗口：从可播期开始，复查天气/土壤，必要时等待，再播种并复查。
- L1 播种执行：从整地、基肥、起垄已完成且接近可播的状态开始，最小复核后播种。
- L2 补种闭环：发现出苗异常 -> 定位 -> 地面确认 -> 等窗口 -> 补种 -> 复查。
- L1 补种执行：前序确认已完成，复核种床/库存/设备后补种。
- L2 收获找窗口：R8/harvest_allowed 开始，连续复查水分/天气/通行性/容量后决定收获日。
- L1 收获执行：从 harvest-ready 状态开始，复核后 harvest -> unload -> dry/store。

## prompt 规则

每个 scenario 都要有：

- `detail=True` prompt
- `detail=False` prompt

detail prompt 是完整的 agent-facing task，不是 oracle 答案。

Agent briefing 只能写农场业务中可见的信息，不能写 scenario 实现信息。`source_checkpoint_label`、`source_checkpoint_date`、`source_dap`、`source_growth_stage` 这些字段可以留在代码 metadata 和验证报告里，但不要进入 `briefing_text`。

`briefing_text` / agent prompt 不要出现这些内部说法：

- `checkpoint`
- `source checkpoint`
- `source L3 checkpoint`
- `action-ready checkpoint`
- `trace label`
- `oracle label`
- “本L1承接……checkpoint”
- “本L2从……checkpoint开始”

把内部实现状态改写成业务状态：

- 不写：“本L1承接源L3在2026-07-10的 action-ready checkpoint。”
- 改写：“截至2026-07-10，地面病害确认和等待窗口已完成，当前进入喷药前复核。”
- 不写：“从 harvest-ready checkpoint 开始。”
- 改写：“截至2026-09-09，田块进入收获前复核状态，需要确认天气、通行性、籽粒水分和仓储容量。”

可以告诉 agent 的 visible setup：

- 日期/阶段/DAP
- 田块大小
- 品种/播种计划
- 已知管理历史
- 可见资源约束
- 已经完成的 visible 检查

不要泄露：

- hidden future affected ridges
- 最终诊断答案
- 未来 oracle 目标
- 未来处理日期
- “应该处理 ridges X-Y” 这种答案，除非是播种计划、已知品种分区或已完成且对 agent 可见的管理历史。

不要写 `A区`、`B区`、`C区`、`目标区`、`处理区` 这类未定义代号。如果 briefing 使用分区名，必须在同一个 briefing 或源 L3 visible setup 中明确定义：

- 分区名
- 品种或管理含义
- ridge 范围

只有播种计划、已知品种分区、已知管理分区可以直接给 ridge 范围。如果目标区来自异常诊断，不能直接在 prompt 里告诉 ridges，要通过 sensor/drone/robot/state evidence 找出来。

示例：

```python
# Bad: checkpoint 是内部实现词，A区也没有定义。
briefing_text = (
    "本L1承接源L3在2026-08-23的HEIHE50 A区收获前 checkpoint。"
)
```

```python
# Good: 如果三品种分区是 visible setup，就在 briefing 中定义清楚。
briefing_text = (
    "已知本田块按品种分为三个可见管理分区："
    "HEIHE50 早熟分区为 ridges 0-20，HEINONG84 分区为 ridges 21-42，"
    "HEINONG58 分区为 ridges 43-63。"
    "截至2026-08-23，HEIHE50 早熟分区进入收获前复核状态。"
    "请复核该分区天气、土壤通行性、R8/harvest_allowed、籽粒水分、拖车和仓储容量；"
    "条件满足时按 harvest -> unload -> dry/store 顺序处理该可见分区。"
)
```

```python
# Good: 如果目标区不应直接给，就让 agent 通过状态证据识别。
briefing_text = (
    "截至2026-08-23，田块进入分区收获复核阶段，不同品种或区域成熟进度可能不同。"
    "请先查看全田和分区状态，确认哪些区域达到R8/harvest_allowed并满足籽粒水分、"
    "天气、土壤通行性和仓储条件；只对证据支持的成熟可收区域执行 harvest -> unload -> dry/store。"
)
```

对水肥药草，prompt 要要求证据链：

- sensor/overview 发现异常
- drone 定位异常区
- robot/ground check 确认原因
- targeted action

## oracle 规则

oracle 要和源 L3 的决策基本一致。

对齐维度包括：

- 操作日期或短窗口
- 目标 ridge 范围
- 分批范围
- 用量参数
- 是否人工/机械
- 是否等待天气窗口
- harvest/dry/store 顺序
- 资源预算变化

如果 split 和源 L3 有 1-3 天小偏差，必须能从 restored checkpoint 后的 physics/weather 状态解释。不能偏到核心决策失效，例如源 L3 可收，L2 因水分漂移到完全不能收。

不要写 `adjust_waits`、`adjust_spec`、外部补丁表或 trace review 常量来改 oracle。场景自己的 oracle 逻辑必须直接定义在 scenario 的 `build_events_flow` / `build_oracle_flow` / `ScenarioSpec` 中。

每个主要 action 前必须有 observation/state evidence。

## events 顺序和 helper 展开

`self.events` 不是随便列变量的清单。后续 review 会按 `self.events` 的顺序检查 oracle workflow，所以它必须和真实依赖/执行顺序一致。

基本规则：

- 每个事件在 `self.events` 中的位置必须晚于它直接 `depends_on(...)` 的事件。
- 如果代码里先定义了某个事件，但它依赖一个后面才会执行的 wait/recheck，也要把它放在 wait/recheck 后面。
- `self.events` 的顺序应该读起来就是 agent/oracle 的工作流顺序：briefing -> observe -> diagnose/recheck -> wait -> recheck -> act -> recheck/report。
- 不要因为调度器会按 dependency 运行，就把 `self.events` 写成乱序。baseline 可能仍然通过，但这种 scenario 不能通过 review。

如果使用 helper，需要分清两种情况：

1. helper 返回事件列表，例如 `apply_replant_block(...) -> ([load, replant], replant)`。这种情况下要在 `self.events` 里用 `*replant_events` 展开，且展开位置必须符合依赖顺序。
2. helper 返回一个 terminal event，但内部创建了一串事件，例如 `harvest_range(...)` 返回最后的 `store_grain` event，内部会展开为多组 `harvest -> unload`，然后 `dry_grain` 和 `store_grain`。这种情况下 `self.events` 里可能只看到一个 `o_harvest_done`，但实际 `completed_events` 会多很多。

第二种情况不是天然错误，但必须满足：

- helper 本身的内部顺序已确认正确；
- `self.events` 里这个 terminal event 放在它所属 workflow 的正确位置；
- audit/validation 里要明确写出 helper 展开的真实顺序和 expanded event count；
- 检查事件数量时不能把 `len(self.events)` 和 `completed_events` 数量当成必须相等。

例如，某个 harvest L2 的 `self.events` 顶层可能只有十几个事件，但实际 oracle completed events 可能是 51 个，因为 `harvest_range(0, 63, dry_after_harvest=True)` 会展开成 16 组 `harvest/unload`，再加 `dry_grain`、`store_grain` 和前后的 observation/recheck。这个数量差异本身可以接受；真正要检查的是展开后的顺序必须仍然是：

```text
pre-harvest evidence -> wait/recheck window -> harvest block -> unload -> ... -> dry_grain if needed -> store_grain -> storage recheck
```

如果 reviewer 或脚本按 `self.events` 做顺序检查，遇到这种 terminal helper event 时，必须把它当成一个已审计的 opaque group；如果 helper 不是 repo 里已有且已知顺序的 helper，就不要隐藏展开事件，应该显式返回列表并在 `self.events` 里展开。

## harvest/postharvest 规则

harvest L2 的开始时间应该在 R8 开始或 harvest_allowed 开始。不要在 prompt 里直接告诉 agent 固定哪天收；应该让 agent 根据天气、土壤、grain moisture 和容量自己决定。

oracle 可以按照源 L3 的真实收获窗口等待和复查，但必须满足：

- R8 或 `harvest_allowed`
- harvest weather 合适
- soil trafficability 合适
- grain moisture 已知
- 正常场景自然干到 `<=13.5%` 后直接入库
- 晚雨/质量风险场景可在 `13.5%-18%` 收后烘干入库
- `<=13.5%` 不需要烘干
- 不要为了 `8%-10%` 过度等待
- 顺序必须是 harvest -> unload -> dry/store
- harvest failed 后不能继续 unload/dry/store

对 harvest/postharvest，biological yield 可能与 do-nothing 相同，这是正常的；primary metric 应该看 recovered yield、warehouse grain、storage moisture、dry/store status 和事件顺序。

## scenario 文件要求

每个 L1/L2 是独立 Python scenario 文件。

常见字段：

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

`start_time` 必须和 restored checkpoint 的 `sim_time` 对齐。full checkpoint split 优先使用：

```python
start_time: float | None = checkpoint_sim_time(CHECKPOINT_...)
```

不要为了好看强行写成整点。如果 checkpoint 的真实 `sim_time` 是 `2026-09-06 00:24:58 UTC`，但 scenario 写成 `cst_timestamp(2026, 9, 6, 8)`，就可能让 harvest/recovered yield 漂移。

只有在你确认 checkpoint `sim_time` 和目标整点完全一致，或这个 split 明确不依赖 full checkpoint restore 时，才使用：

```python
start_time: float | None = cst_timestamp(2026, 8, 2, 8)
```

不要写裸 float。

`duration` 可以不加。

初始化应 restore checkpoint：

```python
def init_and_populate_apps(self, *args, **kwargs) -> None:
    populate_..._apps(self)
    restore_..._checkpoint(self, CHECKPOINT_...)
```

## 验证要求

每批新增 scenario 至少跑：

### 1. 静态检查

```bash
uv run python -m py_compile <new_l1_l2_files>
```

### 2. 独立 oracle baseline

```bash
uv run python scripts/build_oracle_baselines.py \
  --scenarios <new_scenario_ids> \
  --output-dir oracle_baselines_l2_l1_check_<name> \
  --force \
  --continue-on-error \
  --include-donothing
```

要求：

- `ok = 新增 scenario 数量`
- `fail = 0`
- no failed events
- no tool error returns
- final yield 或 primary metric 非空
- harvest/postharvest 不能 harvest failed 后继续 unload/dry/store

`ok=全部数量` 只是最低门槛，不等于合格。必须抽查 oracle completed events 的真实 `return_value`：

- 所有主要 action 的 `status` 是 `ok`；
- 没有 `error`；
- 没有会影响语义的 `warning`；
- action 日期、范围、参数和源 L3 对齐；
- helper 展开后的事件顺序和预期一致。

尤其 harvest/postharvest 不能只看 baseline JSON 的 `ridges_harvested`。必须检查 `harvest`、`unload_grain`、`dry_grain`、`store_grain` 的 return_value，确认是否有 warning、`moved_kg` 是否非空、`storage_grain_moisture_pct` 是否满足规则、`stored_without_drying` 是否符合 grain moisture 语义。

### 3. 生育期任务要推到成熟期看 yield

如果是播种、播种后、收获前的环节，baseline 必须用 oracle 和 do-nothing 都推到成熟期，看 biological yield 对比。播种类任务还要检查 planted ridges、emergence/R8 数量和 do-nothing 是否合理为未播种或延迟播种基线。

需要记录：

- oracle biological yield
- do-nothing biological yield
- delta kg
- delta %
- `ridges_r8`
- `ridges_harvested`

### 4. harvest/postharvest 要看 recovered/storage

需要记录：

- recovered yield
- warehouse grain
- trailer/bin 是否清空
- storage moisture
- dried 或 stored_without_drying
- `store_grain` 的 `warning` / `error`
- `dry_grain` 的 `target_moisture_pct` 和 `ridges_dried`，如果走烘干路径
- `store_grain` 的 `moved_kg`、`storage_grain_moisture_pct`、`stored_without_drying`
- harvest/unload/dry/store 顺序

### 5. 和源 L3 对照

检查：

- 初始 checkpoint 日期/DAP/stage/主要 stress/grain moisture 与 L3 一致。
- L2/L1 关键 action 时间和源 L3 基本一致。
- 目标 ridge 范围、分批、参数和源 L3 一致。
- 如果有偏差，必须给出 trace/checkpoint 证据解释。

## review checklist

提交前逐项检查：

- 新增数量达到正常目标：`3-6 L2 + 3-6 L1`；如果没有达到，已有 `low-count blocker report` 并被 reviewer 接受。
- 候选窗口表覆盖了源 L3 的所有真实管理窗口，不只是已导出的 checkpoint。
- 每个真实管理窗口都审过 L2 和 L1 两种切法；没实现的一侧有 trace 证据解释。
- 每个 rejected task atom 都有具体证据；不能只写“通用”“重复”“价值不大”。
- 新 scenario id 已注册且能被 `build_oracle_baselines.py` 找到。
- 新 split 不依赖源 L3 runtime。
- checkpoint JSON 已提交。
- `farm_checkpoint_state.py` 或等价 restore helper 已提交。
- 没有 `__pycache__`、临时 baseline 输出、IDE 文件。
- 没有 runtime 读 CSV 初始化状态。
- 没有 `adjust_*` 外部补丁控制 oracle。
- prompt 没泄露答案。
- L1/L2 不是同名复制。
- L2 有闭环，L1 是单动作/单决策。
- oracle action 前有 evidence。
- harvest 规则符合 moisture/weather/trafficability/capacity。
- baseline 结果表已记录 oracle vs do-nothing。

## 不接受

以下情况不合格：

- 一个指定 L3 少于 6 个 scenario，却没有 reviewer 接受的 `low-count blocker report`。
- 一个指定 L3 只交最低 6 个 scenario，却没有完整 accepted/rejected 审计。
- 用“通用播种/通用收获/已有类似任务”直接拒绝真实窗口，没有逐项对比现有 scenario。
- 只把每个 checkpoint 切一个 scenario，导致 checkpoint 数量等于 scenario 数量。
- 只写 JSON、markdown、batch runner，没有原生 Python scenario。
- 只列 oracle event list，不注册 scenario。
- 从 CSV 拼几列状态当 checkpoint。
- 运行时读取 trace CSV 初始化 scenario。
- 同一个 checkpoint、同一个 prompt、同一个 oracle、同一目标区、同一参数，只改名字。
- 跨 L3 只改 scenario id 或名字，本质任务完全重复。
- L1 内部等待很久才执行动作，导致它变成 L2。
- L2 覆盖完整季节，导致它变成 L3。
- prompt 直接告诉 hidden 目标区或最终诊断答案。
- fertilizer 用来处理病虫草水，或 pesticide 用来处理水肥问题。
- harvest 水分 `<=13.5%` 还烘干。
- harvest failed 后继续 unload/dry/store。
- 修改 engine 或 shared behavior 来让 split 过，除非用户明确要求且已跑全量影响验证。

## 交付摘要模板

完成后给 reviewer 的摘要建议包含：

```text
指定 L3:
候选窗口审计数量:
accepted task atoms:
rejected windows:
same-checkpoint multi-task splits:
L2 数量:
L1 数量:
新增 L2/L1 数量:
是否达到 3-6 L2 + 3-6 L1:
如果未达到，low-count blocker report:
新增文件:
checkpoint labels/date/DAP/stage:
最终数量为什么合理:

scenario table:
- scenario_id
- level
- checkpoint
- task
- oracle action
- primary metric

验证:
- py_compile command/result
- baseline command/result
- ok/fail
- no tool errors
- oracle vs do-nothing yield table
- harvest recovered/storage table if applicable

L3 对齐:
- action 日期
- ridge 范围
- 参数
- 等待窗口
- 资源约束

已知风险/不确定:
- 只列真实剩余风险，不写泛泛而谈。
```
