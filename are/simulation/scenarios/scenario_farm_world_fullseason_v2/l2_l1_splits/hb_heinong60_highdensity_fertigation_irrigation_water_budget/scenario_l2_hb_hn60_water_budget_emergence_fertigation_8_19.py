from __future__ import annotations

from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult

from .._batch_generated_split_common import (
    checkpoint_date,
    checkpoint_sim_time,
    get_spec,
    restore_batch_checkpoint,
    validate_native_workflow,
    build_management_l2_flow,
)

SOURCE_SLUG = 'hb_heinong60_highdensity_fertigation_irrigation_water_budget'
SPEC_SLUG = 'hb_heinong60_highdensity_fertigation_irrigation_water_budget'
CHECKPOINT_LABEL = 'after_emergence_routine_check'
SPEC = get_spec(SPEC_SLUG)
SCENARIO_ID = 'scenario_l2_hb_hn60_water_budget_emergence_fertigation_8_19'


@register_scenario(SCENARIO_ID)
class ScenarioL2HbHn60WaterBudgetEmergenceFertigation819(Scenario):
    start_time: float | None = checkpoint_sim_time(SOURCE_SLUG, CHECKPOINT_LABEL)
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = False

    source_l3_scenario_id = SPEC.scenario_id
    source_checkpoint_label = CHECKPOINT_LABEL
    source_checkpoint_date = checkpoint_date(SOURCE_SLUG, CHECKPOINT_LABEL)

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        restore_batch_checkpoint(self, SPEC, SOURCE_SLUG, CHECKPOINT_LABEL)
        self.start_time = checkpoint_sim_time(SOURCE_SLUG, CHECKPOINT_LABEL)

    def build_events_flow(self) -> None:
        if self.detailed_briefing:
            briefing_text = (
                '准备诊断黑农60高密度田出苗期水肥恢复窗口。重点关注8-19垄，'
                '目标是确认弱势是否来自水肥不足，而不是单纯冷害或晚出苗。\n'
                '请按以下步骤操作：\n'
                '1. 查看全田概览，确认当前处于出苗期管理窗口。\n'
                '2. 查看天气和3天预报，判断水肥作业窗口。\n'
                '3. 等待目标窗口后重新复查天气、土壤和冠层。\n'
                '4. 读取目标区状态，并用无人机巡查该区域。\n'
                '5. 用地面机器人检查目标区，确认长势/营养弱势。\n'
                '6. 查看肥料、水量和设备余量；注意水预算有限。\n'
                '7. 证据支持时只对目标区执行小水量水肥恢复：amount=0.24，water_mm=2.0。\n'
                '8. 完成后复查目标区和资源消耗。\n'
                '9. 向我汇报证据链、供水参数、肥料参数和水预算风险。'
            )
        else:
            briefing_text = '请诊断黑农60高密度田8-19垄出苗期水肥弱势；证据支持时执行小水量水肥恢复并复查。'
        build_management_l2_flow(self, SPEC, SPEC.actions[0], briefing_text, 'o_emergence_fertigation_8_19')

    def validate(self, env) -> ScenarioValidationResult:
        return validate_native_workflow(self, env, 'hb_heinong60_highdensity_fertigation_irrigation_water_budget emergence_fertigation_8_19 L2')
