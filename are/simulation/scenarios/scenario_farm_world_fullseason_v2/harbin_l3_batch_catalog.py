from __future__ import annotations


from are.simulation.physics.soil_engine import SoilHydraulicModifier
from are.simulation.scenarios.scenario_farm_world_fullseason_v2.harbin_l3_batch_scenario import (
    PlantingZone,
    ScenarioAction,
    ScenarioSpec,
)
from are.simulation.scenarios.scenario_farm_world_fullseason_v2.harbin_l3_detailed_briefings import (
    get_detailed_briefing,
)
from are.simulation.scenarios.scenario_farm_world_fullseason_v2.harbin_l3_scenario_helpers import (
    HEINONG84_SPACING_CM,
    PriorFieldHistoryPreset,
)

LOW_DENSITY_SPACING_CM = 11.8
HN60_HIGH_DENSITY_SPACING_CM = 6.8
HEIHE50_SPACING_CM = 8.2
HEIHE43_DENSITY_OPT_SPACING_CM = 4.05
HEIHE43_RECOMMENDED_SPACING_CM = 8.1

NORMAL_BLACK_SOIL = SoilHydraulicModifier(
    root_depth_m=1.45,
    field_capacity_vwc=0.37,
    top_drainage_rate=0.22,
    root_drainage_rate=0.03,
    rainfall_capture_efficiency=0.97,
    irrigation_efficiency=0.92,
    max_infiltration_mm_day=85.0,
)

FAST_DRAIN = SoilHydraulicModifier(
    field_capacity_vwc=0.31,
    root_drainage_rate=0.26,
    rainfall_capture_efficiency=0.82,
    irrigation_efficiency=0.86,
    max_infiltration_mm_day=85.0,
)
LOW_HOLDING_DRY_PATCH = SoilHydraulicModifier(
    root_depth_m=0.80,
    field_capacity_vwc=0.29,
    root_drainage_rate=0.30,
    rainfall_capture_efficiency=0.66,
    irrigation_efficiency=0.80,
    max_infiltration_mm_day=72.0,
)
MODERATE_LOW_HOLDING_DRY_PATCH = SoilHydraulicModifier(
    root_depth_m=0.75,
    field_capacity_vwc=0.27,
    root_drainage_rate=0.36,
    rainfall_capture_efficiency=0.55,
    irrigation_efficiency=0.82,
    max_infiltration_mm_day=76.0,
)
COMPACTED = SoilHydraulicModifier(
    root_depth_m=0.55,
    field_capacity_vwc=0.36,
    top_drainage_rate=0.16,
    root_drainage_rate=0.10,
    rainfall_capture_efficiency=0.80,
    irrigation_efficiency=0.78,
    max_infiltration_mm_day=36.0,
)
HEADLAND_COMPACTED = SoilHydraulicModifier(
    root_depth_m=0.75,
    field_capacity_vwc=0.34,
    top_drainage_rate=0.18,
    root_drainage_rate=0.12,
    rainfall_capture_efficiency=0.84,
    irrigation_efficiency=0.82,
    max_infiltration_mm_day=48.0,
)
POOR_DRAINAGE = SoilHydraulicModifier(
    field_capacity_vwc=0.38,
    top_drainage_rate=0.10,
    root_drainage_rate=0.04,
    rainfall_capture_efficiency=0.96,
    irrigation_efficiency=0.90,
    max_infiltration_mm_day=30.0,
)

SPECS: dict[str, ScenarioSpec] = {}


def add(spec: ScenarioSpec) -> ScenarioSpec:
    SPECS[spec.slug] = spec
    return spec


add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_coldspring_planting_window_heihe50",
        class_name="ScenarioFullSeasonHBColdspringPlantingWindowHeihe50",
        slug="hb_coldspring_planting_window_heihe50",
        profile_name="harbin_l3_heihe50_coldspring_seed_1513",
        cultivar="黑河50早熟/冷春播种窗口",
        primary_seed="HEIHE50",
        seed_stocks={"HEIHE50": 1000000},
        start_date="2026-05-17",
        description="哈尔滨冷春年份，全田黑河50，播种需等待seedbed达标但不能过度推迟。",
        briefing_text="任务：管理哈尔滨冷春年份的黑河50大豆全季生产。请先确认天气、预报、土壤温度/水分和拖拉机状态，再决定播种窗口；后续完成出苗、长势、R5/R6、成熟收获、干燥和入库。不要预设未来天气答案或异常原因。",
        planting_zones=(
            PlantingZone("whole_field", 0, 63, "HEIHE50", HEIHE50_SPACING_CM, 0),
        ),
        waits={"emergence": 14, "r1": 22, "mid": 18, "r5": 22, "harvest": 47},
        zones=(("whole_field_0_63", 0, 63),),
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_coldspring_planting_window_heihe50", ""
        )
        or None,
    )
)

add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_low_nutrient_carryover_flowering_nutrition",
        class_name="ScenarioFullSeasonHBLowNutrientCarryoverFloweringNutrition",
        slug="hb_low_nutrient_carryover_flowering_nutrition",
        profile_name="harbin_l3_low_nutrient_flowering_seed_1516",
        cultivar="黑农84标准密度/低养分带出",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        description="前季养分带出较多，出苗正常，R1/R3前后长势偏弱，需要按需营养管理。",
        briefing_text="任务：管理一块前季养分带出较多的黑农84标准密度大豆田。请通过全田检查、叶色/NDVI、土壤和地面复查判断是否需要花期营养补充，不能在没有工具返回支持时直接补肥。",
        prior_histories=(("low_nutrient_carryover", 0, 63),),
        actions=(
            ScenarioAction(
                "r1",
                "fertigation",
                0,
                63,
                amount=0.34,
            ),
        ),
        zones=(("whole_field_0_63", 0, 63),),
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_low_nutrient_carryover_flowering_nutrition", ""
        )
        or None,
    )
)

add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_high_weed_seedbank_mechanical_only_baseline",
        class_name="ScenarioFullSeasonHBHighWeedSeedbankMechanicalOnlyBaseline",
        slug="hb_high_weed_seedbank_early_control",
        profile_name="harbin_l3_high_weed_seedbank_seed_1518",
        cultivar="黑农84标准密度/高杂草种子库机械控草基准",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        management_regime={
            "regime": "low_chemical",
            "active_ingredient_cap_kg": 0.0,
            "max_machine_passes": 7,
            "max_mechanical_weed_ridges": 64,
        },
        description="高杂草种子库历史导致全田早期草害压力高，作为全田早期机械控草 baseline。",
        briefing_text=(
            "任务：管理有高杂草种子库历史的黑农84标准密度大豆田。"
            "全田 seed_spacing_cm=7.9。NDVI不能单独代表作物正常，"
            "本场景不允许使用化学除草剂，请结合地面杂草检查决定是否早期全田机械控草，"
            "并复查作物恢复。"
        ),
        prior_histories=(("high_weed_seed_bank", 0, 63),),
        actions=(
            ScenarioAction(
                "emergence",
                "mechanical_weed",
                0,
                63,
                target_wait_days=4,
            ),
        ),
        zones=(("whole_field_weed_seedbank", 0, 63),),
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 44},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_high_weed_seedbank_mechanical_only_baseline", ""
        )
        or None,
    )
)

add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_wetjune_short_spray_window",
        class_name="ScenarioFullSeasonHBWetjuneShortSprayWindow",
        slug="hb_wetjune_short_spray_window",
        profile_name="harbin_l3_wetjune_short_spray_window_seed_1521",
        cultivar="黑农84标准密度/湿六月短喷药窗口",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        description="6月偏湿后病害风险升高，但可喷药窗口很短，重点是时机。",
        briefing_text="任务：管理湿六月后存在病害风险且喷药窗口很短的黑农84田。必须先诊断病害与作业窗口，再在天气和土壤允许时有针对性喷药。",
        actions=(
            ScenarioAction(
                "mid",
                "fungicide",
                18,
                45,
                liters_per_ridge=4.2,
            ),
        ),
        zones=(("disease_risk_18_45", 18, 45), ("reference_0_17", 0, 17)),
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_wetjune_short_spray_window", ""
        )
        or None,
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 55},
    )
)

add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_r5_leaf_feeder_defoliation",
        class_name="ScenarioFullSeasonHBR5LeafFeederDefoliation",
        slug="hb_r5_leaf_feeder_defoliation",
        profile_name="harbin_l3_r5_leaf_feeder_seed_1523",
        cultivar="黑农84标准密度/R5食叶害虫",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        description="R5附近食叶性害虫造成叶面积损伤，影响灌浆。",
        briefing_text="任务：管理R5附近可能出现食叶性害虫的大豆田。请根据阶段、冠层/NDVI和地面虫害检查判断是否达到处理阈值，只对确认区域处理。",
        actions=(
            ScenarioAction(
                "r5",
                "insecticide",
                16,
                39,
                liters_per_ridge=3.8,
            ),
        ),
        zones=(("defoliation_risk_16_39", 16, 39), ("reference_40_63", 40, 63)),
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_r5_leaf_feeder_defoliation", ""
        )
        or None,
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 42},
    )
)

add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_limited_spray_budget_season",
        class_name="ScenarioFullSeasonHBLimitedSprayBudgetSeason",
        slug="hb_limited_spray_budget_season",
        profile_name="harbin_l3_limited_spray_budget_seed_1524",
        cultivar="黑农84标准密度/有限喷药预算",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        management_regime={
            "max_insecticide_applications": 3,
            "active_ingredient_cap_kg": 0.22,
        },
        description="早期轻虫害和后期较重虫害并存，喷药次数有限，需要保留预算。",
        briefing_text="任务：在季节喷药次数有限的黑农84田中管理虫害。早期轻信号应监测和复查，只有后期达到阈值并有地面确认时才使用有限喷药预算。",
        actions=(
            ScenarioAction(
                "r5",
                "insecticide",
                18,
                45,
                liters_per_ridge=3.6,
            ),
        ),
        zones=(("later_insect_18_45", 18, 45), ("reference_0_17", 0, 17)),
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_limited_spray_budget_season", ""
        )
        or None,
        postharvest_drying_zones=('whole_field',),
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 40},
    )
)

add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_dryr5r6_hn84_water_limit",
        class_name="ScenarioFullSeasonHBDryR5R6HN84WaterLimit",
        slug="hb_dryr5r6_hn84_water_limit",
        profile_name="harbin_l3_hn84_dryr5r6_waterlimit_seed_1526",
        cultivar="黑农84标准密度/R5R6水量限制",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        initial_vwc=0.27,
        management_regime={"irrigation_quota_mm_total": 1.8},
        hydraulic_modifiers=((22, 43, FAST_DRAIN),),
        description="黑农84标准密度在R5/R6遇到干旱，灌溉水量有限。",
        briefing_text="任务：管理R5/R6干旱且灌溉水量有限的黑农84田。请根据root-zone水分、热胁迫、阶段和预报选择需要保护的区域，不能全田平均灌水。",
        actions=(
            ScenarioAction(
                "r5",
                "irrigation",
                22,
                43,
                hours=0.9,
            ),
        ),
        zones=(
            ("priority_22_43", 22, 43),
            ("reference_west_0_10", 0, 10),
            ("reference_east_54_63", 54, 63),
        ),
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 35},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_dryr5r6_hn84_water_limit", ""
        )
        or None,
    )
)

add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_hn60_high_dryr5r6_water_demand",
        class_name="ScenarioFullSeasonHBHN60HighDryR5R6WaterDemand",
        slug="hb_hn60_high_dryr5r6_water_demand",
        profile_name="harbin_l3_hn60_high_dryr5r6_seed_1528",
        cultivar="黑农60高密度/R5R6高需水",
        primary_seed="HEINONG60",
        seed_stocks={"HEINONG60": 1000000},
        density_target_plants_m2=29.0,
        hydraulic_modifiers=((20, 43, MODERATE_LOW_HOLDING_DRY_PATCH),),
        planting_zones=(
            PlantingZone(
                "whole_field_high_density",
                0,
                63,
                "HEINONG60",
                HN60_HIGH_DENSITY_SPACING_CM,
                0,
            ),
        ),
        initial_vwc=0.23,
        management_regime={"irrigation_quota_mm_total": 12.0},
        description="黑农60高密度群体在R5/R6干旱下需水更高。",
        briefing_text="任务：管理黑农60高密度大豆田的R5/R6干旱风险。请先用土壤、冠层和热信号确认水分胁迫，再决定是否补水。",
        actions=(
            ScenarioAction(
                "r5",
                "irrigation",
                0,
                63,
                hours=0.75,
            ),
        ),
        zones=(("whole_field_high_density", 0, 63),),
        postharvest_drying_zones=('whole_field',),
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 80},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_hn60_high_dryr5r6_water_demand", ""
        )
        or None,
    )
)

add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_two_dry_patches_one_irrigation",
        class_name="ScenarioFullSeasonHBTwoDryPatchesOneIrrigation",
        slug="hb_two_dry_patches_one_irrigation",
        profile_name="harbin_l3_two_dry_patches_seed_1529",
        cultivar="黑农84标准密度/两块缺水一块水量",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        initial_vwc=0.22,
        management_regime={"irrigation_quota_mm_total": 1.2},
        hydraulic_modifiers=(
            (8, 19, MODERATE_LOW_HOLDING_DRY_PATCH),
            (42, 53, MODERATE_LOW_HOLDING_DRY_PATCH),
        ),
        description="两个局部dry patches同时缺水，但水量只够优先灌一个区域。",
        briefing_text="任务：管理两个局部缺水斑块但灌溉水量不足的黑农84田。请比较阶段、水分胁迫和产量敏感性，优先处理风险更高区域。",
        actions=(
            ScenarioAction(
                "r5",
                "irrigation",
                42,
                53,
                hours=1.25,
                target_wait_days=2,
            ),
        ),
        zones=(
            ("dry_patch_west_8_19", 8, 19),
            ("priority_patch_east_42_53", 42, 53),
            ("reference_24_35", 24, 35),
        ),
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_two_dry_patches_one_irrigation", ""
        )
        or None,
        postharvest_drying_zones=('whole_field',),
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 67},
    )
)

add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_staggered_dryr5r6_stage_mismatch",
        class_name="ScenarioFullSeasonHBStaggeredDryR5R6StageMismatch",
        slug="hb_staggered_dryr5r6_stage_mismatch",
        profile_name="harbin_l3_staggered_dryr5r6_seed_1532",
        cultivar="黑农84错期播种/R5R6阶段错配干旱",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        planting_zones=(
            PlantingZone("early_0_31", 0, 31, "HEINONG84", HEINONG84_SPACING_CM, 0),
            PlantingZone("late_32_63", 32, 63, "HEINONG84", HEINONG84_SPACING_CM, 7),
        ),
        initial_vwc=0.30,
        management_regime={"irrigation_quota_mm_total": 6.0},
        description="错期播种后遇到干旱，同一干旱在不同区对应不同生育阶段。",
        briefing_text="任务：管理错期播种黑农84田在R5/R6干旱下的分区水分决策。请按区比较生育阶段和root-zone水分，不要把全田当作统一作物状态。",
        actions=(
            ScenarioAction(
                "r5",
                "irrigation",
                0,
                31,
                hours=0.85,
            ),
        ),
        zones=(("early_0_31", 0, 31), ("late_32_63", 32, 63)),
        harvest_zones=(
            ("early_0_31", 0, 31),
            ("late_32_63", 32, 63),
        ),
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_staggered_dryr5r6_stage_mismatch", ""
        )
        or None,
        enforce_planting_windows=True,
        postharvest_drying_zones=('late_32_63',),
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 32},
    )
)

add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_staggered_wetjune_canopy_disease",
        class_name="ScenarioFullSeasonHBStaggeredWetjuneCanopyDisease",
        slug="hb_staggered_wetjune_canopy_disease",
        profile_name="harbin_l3_staggered_wetjune_disease_seed_1533",
        cultivar="黑农84错期播种/湿六月冠层病害",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        planting_zones=(
            PlantingZone("early_0_31", 0, 31, "HEINONG84", HEINONG84_SPACING_CM, 0),
            PlantingZone("late_32_63", 32, 63, "HEINONG84", HEINONG84_SPACING_CM, 6),
        ),
        description="错期播种叠加湿六月，早播区冠层更密、病害风险更高。",
        briefing_text="任务：管理错期播种黑农84田的湿六月病害风险。请按区检查冠层闭合、NDVI、热信号和地面病害，只处理确认区域。",
        actions=(
            ScenarioAction(
                "mid",
                "fungicide",
                0,
                31,
            ),
        ),
        zones=(("early_closed_canopy_0_31", 0, 31), ("late_open_canopy_32_63", 32, 63)),
        harvest_zones=(
            ("early_0_31", 0, 31),
            ("late_32_63", 32, 63),
        ),
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_staggered_wetjune_canopy_disease", ""
        )
        or None,
        enforce_planting_windows=True,
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 36},
    )
)

add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_hn60_high_late_grain_moisture",
        class_name="ScenarioFullSeasonHBHN60HighLateGrainMoisture",
        slug="hb_hn60_high_late_grain_moisture",
        profile_name="harbin_l3_hn60_late_grain_moisture_seed_1535",
        cultivar="黑农60高密度/后期籽粒水分风险",
        primary_seed="HEINONG60",
        seed_stocks={"HEINONG60": 1000000},
        density_target_plants_m2=29.0,
        planting_zones=(
            PlantingZone(
                "whole_field_high_density",
                0,
                63,
                "HEINONG60",
                HN60_HIGH_DENSITY_SPACING_CM,
                0,
            ),
        ),
        postharvest_market={"name": "late_moisture", "max_storage_moisture_pct": 13.5},
        description="黑农60高密度成熟后籽粒降水慢，晚雨前需判断先收后烘。",
        briefing_text="任务：管理高密度黑农60的成熟和籽粒水分风险。收获前必须检查成熟、籽粒水分、未来降雨和可作业性，并按批次干燥入库。",
        zones=(("whole_field_high_density", 0, 63),),
        harvest_zones=(
            ("west_0_31", 0, 31),
            ("east_32_63", 32, 63),
        ),
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 75},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_hn60_high_late_grain_moisture", ""
        )
        or None,
    )
)

add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_harvester_days_limit_laterain",
        class_name="ScenarioFullSeasonHBHarvesterDaysLimitLateRain",
        slug="hb_harvester_days_limit_laterain",
        profile_name="harbin_l3_harvester_days_laterain_seed_1536",
        cultivar="黑农84标准密度/晚雨前收获能力限制",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        management_regime={"max_machine_passes": 40},
        description="晚雨前多个区域接近成熟，但收获能力有限，需要排序。",
        briefing_text="任务：管理晚雨风险前的黑农84收获排序。请根据各区成熟度、籽粒水分、天气和收获机状态分批收获，不能硬等全田统一窗口。",
        zones=(("west_0_31", 0, 31), ("east_32_63", 32, 63)),
        harvest_zones=(
            ("west_0_31", 0, 31),
            ("east_32_63", 32, 63),
        ),
        postharvest_drying_zones=('west_0_31', 'east_32_63'),
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 35},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_harvester_days_limit_laterain", ""
        )
        or None,
    )
)

add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_adversarial_multi_event_light",
        class_name="ScenarioFullSeasonHBAdversarialMultiEventLight",
        slug="hb_adversarial_multi_event_light",
        profile_name="harbin_l3_adversarial_multi_light_seed_1542",
        cultivar="黑农84标准密度/轻量综合事件",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        start_date="2026-05-07",
        initial_vwc=0.207,
        description="轻度冷春、轻病害、轻干旱和小晚雨顺序出现，考验全季优先级。",
        briefing_text="任务：管理一个轻量综合压力的黑农84全季场景。每个窗口都要先检查再行动，干预应适度、固定、可解释，不能把轻度压力当作极端灾害。",
        planting_zones=(
            PlantingZone("whole_field", 0, 63, "HEINONG84", HEINONG84_SPACING_CM, 0),
        ),
        actions=(
            ScenarioAction(
                "mid",
                "fungicide",
                24,
                43,
                liters_per_ridge=3.2,
            ),
            ScenarioAction(
                "r5",
                "irrigation",
                20,
                43,
                hours=0.65,
            ),
        ),
        zones=(("middle_risk_20_43", 20, 43), ("reference_0_19", 0, 19)),
        harvest_zones=(
            ("west_0_31", 0, 31),
            ("east_32_63", 32, 63),
        ),
        postharvest_drying_zones=('west_0_31', 'east_32_63'),
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 38},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_adversarial_multi_event_light", ""
        )
        or None,
    )
)

add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_wetcold_high_residue_establishment",
        class_name="ScenarioFullSeasonHBWetcoldHighResidueEstablishment",
        slug="hb_wetcold_high_residue_establishment",
        profile_name="harbin_l3_wetcold_high_residue_seed_1514",
        cultivar="黑农84标准密度/湿冷高残茬建苗",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        start_date="2026-05-08",
        initial_vwc=0.207,
        description="湿冷春加高残茬导致seedbed升温慢、出苗不齐。",
        briefing_text="任务：管理湿冷春高残茬黑农84田。请根据土温、水分、预报和出苗检查决定播种/复查/少量补种，不能把问题直接归因于肥力。",
        prior_histories=(("high_residue_cool_seedbed", 0, 63),),
        custom_histories=(
            (
                PriorFieldHistoryPreset(
                    name="wet_cold_residue_slow_establishment",
                    soil_temp_delta_c=-1.0,
                    stand_fraction_delta=-0.12,
                    nutrient_index_delta=-0.04,
                ),
                0,
                15,
            ),
        ),
        planting_zones=(
            PlantingZone(
                "whole_field_residue", 0, 63, "HEINONG84", HEINONG84_SPACING_CM, 0
            ),
        ),
        actions=(
            ScenarioAction(
                "emergence",
                "replant",
                0,
                7,
                target_wait_days=8,
            ),
        ),
        zones=(("slow_emergence_0_15", 0, 15), ("reference_32_47", 32, 47)),
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_wetcold_high_residue_establishment", ""
        )
        or None,
        postharvest_drying_zones=('whole_field',),
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 50},
    )
)

add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_compacted_headland_stand_recovery",
        class_name="ScenarioFullSeasonHBCompactedHeadlandStandRecovery",
        slug="hb_compacted_headland_stand_recovery",
        profile_name="harbin_l3_compacted_headland_seed_1515",
        cultivar="黑农84标准密度/地头压实出苗恢复",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        custom_histories=(
            (
                PriorFieldHistoryPreset(
                    name="headland_compaction", stand_fraction_delta=-0.16
                ),
                0,
                11,
            ),
        ),
        hydraulic_modifiers=((0, 11, HEADLAND_COMPACTED),),
        initial_vwc=0.28,
        description="地头压实造成局部出苗慢、根系弱，需要排除缺肥和病害。",
        briefing_text="任务：管理有地头压实风险的黑农84田。请通过土壤、冠层、无人机和地面检查区分压实/湿土、缺肥和病害，并做局部恢复管理。",
        actions=(
            ScenarioAction(
                "emergence",
                "replant",
                0,
                7,
            ),
            ScenarioAction(
                "r1",
                "fertigation",
                0,
                11,
                amount=0.18,
                water_mm=1.0,
            ),
        ),
        zones=(("compacted_headland_0_11", 0, 11), ("reference_20_31", 20, 31)),
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 42},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_compacted_headland_stand_recovery", ""
        )
        or None,
        harvest_zone_waits={"compacted_late_0_7": 14},
        harvest_zones=(
            ("ready_8_63", 8, 63),
            ("compacted_late_0_7", 0, 7),
        ),
    )
)

add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_storage_capacity_limit_batching",
        class_name="ScenarioFullSeasonHBStorageCapacityLimitBatching",
        slug="hb_storage_capacity_limit_batching",
        profile_name="harbin_l3_storage_capacity_seed_1538",
        cultivar="黑农84标准密度/储藏容量分批",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        postharvest_market={"name": "limited_storage", "storage_capacity_kg": 11000.0},
        description="储藏容量有限，收获、干燥和入库需要按批次闭环。",
        briefing_text="任务：管理储藏容量有限的黑农84收获季。请根据库存、粮食水分和天气按批次收获、干燥和入库，不要把收后处理拖到最后。",
        zones=(("west_0_31", 0, 31), ("east_32_63", 32, 63)),
        harvest_zones=(
            ("west_0_31", 0, 31),
            ("east_32_63", 32, 63),
        ),
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_storage_capacity_limit_batching", ""
        )
        or None,
        postharvest_drying_zones=('west_0_31', 'east_32_63'),
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 38},
    )
)

add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_low_carbon_min_machinery_passes",
        class_name="ScenarioFullSeasonHBLowCarbonMinMachineryPasses",
        slug="hb_low_carbon_min_machinery_passes",
        profile_name="harbin_l3_low_carbon_min_pass_seed_1540",
        cultivar="黑农84标准密度/低碳少机械进地",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        management_regime={"regime": "low_carbon", "max_machine_passes": 38},
        description="低碳/少机械进地目标要求合并操作，但不能错过关键窗口。",
        briefing_text="任务：按低碳少机械进地目标管理黑农84田。请用检查结果决定是否合并操作，减少不必要进地，但不能牺牲关键营养、病虫草和收获窗口。",
        actions=(
            ScenarioAction(
                "r1",
                "fertigation",
                0,
                63,
                amount=0.22,
                water_mm=1.0,
            ),
        ),
        zones=(("whole_field_0_63", 0, 63),),
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_low_carbon_min_machinery_passes", ""
        )
        or None,
        postharvest_drying_zones=('whole_field',),
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 41},
    )
)

add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_organic_patch_weed_mechanical_capacity",
        class_name="ScenarioFullSeasonHBOrganicPatchWeedMechanicalCapacity",
        slug="hb_organic_weed_pressure_allowed_inputs",
        profile_name="harbin_l3_organic_weed_seed_1541",
        cultivar="黑农84标准密度/有机斑块草害机械资源限制",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        management_regime={
            "regime": "organic",
            "max_machine_passes": 2,
            "max_mechanical_weed_ridges": 16,
        },
        description="有机田早期草害呈斑块分布，机械资源只够处理最高压力16垄。",
        briefing_text=(
            "任务：管理有机黑农84标准密度大豆田的早期斑块草害。"
            "全田 seed_spacing_cm=7.9。常规除草剂不允许使用，机械除草资源只够处理16条垄。"
            "请通过传感器、无人机和地面杂草检查区分高/中/低草害斑块，"
            "只对最高压力且最需要保护的斑块机械除草，并复查作物恢复。"
        ),
        actions=(
            ScenarioAction(
                "emergence",
                "mechanical_weed",
                0,
                15,
                target_wait_days=6,
            ),
        ),
        zones=(
            ("high_weed_priority_0_15", 0, 15),
            ("medium_weed_monitor_16_31", 16, 31),
            ("low_weed_reference_32_63", 32, 63),
        ),
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 39},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_organic_patch_weed_mechanical_capacity", ""
        )
        or None,
    )
)

add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_coldspring_lateplanting_laterain_hn50",
        class_name="ScenarioFullSeasonHBColdspringLateplantingLaterainHN50",
        slug="hb_coldspring_lateplanting_laterain_hn50",
        profile_name="harbin_l3_coldspring_lateplanting_laterain_heihe50_seed_1602",
        cultivar="黑河50早熟/冷春晚播晚雨风险",
        primary_seed="HEIHE50",
        seed_stocks={"HEIHE50": 1000000},
        start_date="2026-05-23",
        planting_zones=(
            PlantingZone(
                "whole_field_heihe50", 0, 63, "HEIHE50", HEIHE50_SPACING_CM, 0
            ),
        ),
        description="冷春推迟播种，黑河50降低成熟风险，但晚雨前仍需按成熟和籽粒水分决策。",
        briefing_text="任务：管理冷春推迟播种后的早熟黑河50大豆田。请根据土壤和天气决定播种窗口，后期根据成熟度、籽粒水分和预报安排收获与烘干，不能预设未来降雨答案。",
        zones=(("whole_field_heihe50", 0, 63),),
        harvest_zones=(
            ("west_0_31", 0, 31),
            ("east_32_63", 32, 63),
        ),
        waits={"emergence": 15, "r1": 22, "mid": 18, "r5": 22, "harvest": 57},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_coldspring_lateplanting_laterain_hn50", ""
        )
        or None,
    )
)

add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_wetjune_highdensity_poordrainage_disease",
        class_name="ScenarioFullSeasonHBWetjuneHighdensityPoordrainageDisease",
        slug="hb_wetjune_highdensity_poordrainage_disease",
        profile_name="harbin_l3_wetjune_highdensity_poordrainage_disease_seed_1603",
        cultivar="黑农60高密度/湿六月排水差病害",
        primary_seed="HEINONG60",
        seed_stocks={"HEINONG60": 1000000},
        density_target_plants_m2=29.0,
        planting_zones=(
            PlantingZone(
                "whole_field_hn60_high",
                0,
                63,
                "HEINONG60",
                HN60_HIGH_DENSITY_SPACING_CM,
                0,
            ),
        ),
        hydraulic_modifiers=((40, 55, POOR_DRAINAGE),),
        description="高密度黑农60叠加局部排水差，湿六月后病害风险和可作业性同时成为约束。",
        briefing_text="任务：管理高密度黑农60田在湿六月后的病害和进地风险。请先用土壤、冠层、无人机和地面检查定位异常，再判断天气/土壤是否允许 targeted fungicide。",
        actions=(
            ScenarioAction(
                "mid",
                "fungicide",
                40,
                55,
                liters_per_ridge=4.1,
            ),
        ),
        zones=(("poor_drainage_high_density_40_55", 40, 55), ("reference_0_15", 0, 15)),
        waits={"emergence": 15, "r1": 24, "mid": 20, "r5": 24, "harvest": 51},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_wetjune_highdensity_poordrainage_disease", ""
        )
        or None,
    )
)

add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_wetjune_soyhistory_poordrainage_disease",
        class_name="ScenarioFullSeasonHBWetjuneSoyhistoryPoordrainageDisease",
        slug="hb_wetjune_soyhistory_poordrainage_disease",
        profile_name="harbin_l3_wetjune_soyhistory_poordrainage_disease_seed_1604",
        cultivar="黑农84标准密度/前茬病史排水差",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        prior_histories=(("soybean_after_soybean", 0, 63),),
        hydraulic_modifiers=((22, 43, POOR_DRAINAGE),),
        description="前茬大豆/病史提高病害基线，局部排水差让湿六月后的病害和喷药窗口更紧。",
        briefing_text="任务：管理有前茬病史且局部排水偏差的黑农84田。6月湿后要加强巡查，但任何喷药都必须由诊断和可作业窗口共同支持。",
        actions=(
            ScenarioAction(
                "mid",
                "fungicide",
                22,
                43,
                target_wait_days=2,
            ),
            ScenarioAction(
                "r5",
                "fungicide",
                22,
                43,
                liters_per_ridge=2.8,
            ),
        ),
        zones=(("history_poor_drainage_22_43", 22, 43), ("reference_0_15", 0, 15)),
        waits={"emergence": 15, "r1": 24, "mid": 20, "r5": 24, "harvest": 41},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_wetjune_soyhistory_poordrainage_disease", ""
        )
        or None,
    )
)

add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_wetjune_weed_disease_diagnosis",
        class_name="ScenarioFullSeasonHBWetjuneWeedDiseaseDiagnosis",
        slug="hb_wetjune_weed_disease_diagnosis",
        profile_name="harbin_l3_wetjune_weed_disease_diagnosis_seed_1605",
        cultivar="黑农84标准密度/杂草病害鉴别",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        description="局部NDVI异常可能来自杂草绿色覆盖，也可能来自病害，需要分阶段诊断。",
        briefing_text="任务：管理湿六月下的黑农84田。NDVI异常不能直接等同病害，请先区分杂草覆盖、作物长势和病害症状，再选择对应的 targeted action。",
        actions=(
            ScenarioAction(
                "emergence",
                "mechanical_weed",
                6,
                21,
                target_wait_days=13,
            ),
            ScenarioAction(
                "mid",
                "fungicide",
                34,
                49,
                liters_per_ridge=3.8,
                target_wait_days=4,
            ),
        ),
        zones=(
            ("early_weed_6_21", 6, 21),
            ("later_disease_34_49", 34, 49),
            ("reference_54_63", 54, 63),
        ),
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_wetjune_weed_disease_diagnosis", ""
        )
        or None,
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 27},
    )
)

add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_highdensity_wetjune_limited_fungicide",
        class_name="ScenarioFullSeasonHBHighdensityWetjuneLimitedFungicide",
        slug="hb_highdensity_wetjune_limited_fungicide",
        profile_name="harbin_l3_highdensity_wetjune_limited_fungicide_seed_1606",
        cultivar="黑农60高密度/湿六月杀菌剂次数限制",
        primary_seed="HEINONG60",
        seed_stocks={"HEINONG60": 1000000},
        density_target_plants_m2=29.0,
        planting_zones=(
            PlantingZone(
                "whole_field_hn60_high",
                0,
                63,
                "HEINONG60",
                HN60_HIGH_DENSITY_SPACING_CM,
                0,
            ),
        ),
        management_regime={"active_ingredient_cap_kg": 0.30},
        description="高密度湿六月场景中杀菌剂只能用一次，早期亚阈值信号应复查而非立即消耗机会。",
        briefing_text="任务：管理杀菌剂使用次数有限的高密度黑农60田。早期轻微风险要监测；只有工具返回显示病害达到明确阈值、且天气和土壤窗口允许时，才使用有限处理机会。",
        actions=(
            ScenarioAction(
                "mid",
                "fungicide",
                36,
                59,
                liters_per_ridge=3.6,
                target_wait_days=9,
            ),
        ),
        zones=(("threshold_disease_36_59", 36, 59), ("reference_0_23", 0, 23)),
        waits={"emergence": 15, "r1": 24, "mid": 13, "r5": 24, "harvest": 61},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_highdensity_wetjune_limited_fungicide", ""
        )
        or None,
    )
)

add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_wetjune_shortwindow_trafficability",
        class_name="ScenarioFullSeasonHBWetjuneShortwindowTrafficability",
        slug="hb_wetjune_shortwindow_trafficability",
        profile_name="harbin_l3_wetjune_shortwindow_trafficability_seed_1607",
        cultivar="黑农84标准密度/短喷药窗口和可通行性",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        hydraulic_modifiers=((22, 45, POOR_DRAINAGE),),
        description="湿六月病害后只有短暂可喷窗口，土壤过湿时不能强行作业。",
        briefing_text="任务：管理湿六月后短喷药窗口的大豆田。请同时检查病害、天气、风速、土壤水分和进地条件；只有窗口合适时才对确认区域处理。",
        actions=(
            ScenarioAction(
                "mid",
                "fungicide",
                22,
                45,
                target_wait_days=4,
            ),
        ),
        zones=(("short_window_22_45", 22, 45), ("reference_0_15", 0, 15)),
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_wetjune_shortwindow_trafficability", ""
        )
        or None,
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 53},
    )
)

add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_hn84_hn58_dry_patch_waterlimit",
        class_name="ScenarioFullSeasonHBHN84HN58DryPatchWaterlimit",
        slug="hb_hn84_hn58_dry_patch_waterlimit",
        profile_name="harbin_l3_hn84_hn58_dry_patch_waterlimit_seed_1610",
        cultivar="黑农84/黑农58分区局部干旱水量限制",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 600000, "HEINONG58": 600000},
        planting_zones=(
            PlantingZone("a_hn84_0_31", 0, 31, "HEINONG84", HEINONG84_SPACING_CM, 0),
            PlantingZone("b_hn58_32_63", 32, 63, "HEINONG58", HEINONG84_SPACING_CM, 0),
        ),
        hydraulic_modifiers=((20, 31, FAST_DRAIN), (44, 55, FAST_DRAIN)),
        initial_vwc=0.25,
        management_regime={"irrigation_quota_mm_total": 1.2},
        description="标准品种和抗逆品种同田，局部fast-draining patch在水量有限时需要排序。",
        briefing_text="任务：管理黑农84和黑农58分区田的局部干旱。请结合品种、root-zone水分、热胁迫和生育期决定灌溉优先级，不能全田平均灌水。",
        actions=(
            ScenarioAction(
                "r5",
                "irrigation",
                22,
                31,
                hours=0.8,
            ),
        ),
        zones=(
            ("hn84_fast_patch_22_31", 22, 31),
            ("hn58_fast_patch_44_55", 44, 55),
            ("reference_0_11", 0, 11),
        ),
        harvest_zones=(("whole_field", 0, 63),),
        postharvest_drying_zones=('whole_field',),
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 34, "harvest": 41},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_hn84_hn58_dry_patch_waterlimit", ""
        )
        or None,
    )
)

add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_hn60_high_fastdrain_dryr5r6",
        class_name="ScenarioFullSeasonHBHN60HighFastdrainDryR5R6",
        slug="hb_hn60_high_fastdrain_dryr5r6",
        profile_name="harbin_l3_hn60_high_fastdrain_dryr5r6_seed_1611",
        cultivar="黑农60高密度/fast-draining R5R6干旱",
        primary_seed="HEINONG60",
        seed_stocks={"HEINONG60": 1000000},
        density_target_plants_m2=29.0,
        planting_zones=(
            PlantingZone(
                "whole_field_hn60_high",
                0,
                63,
                "HEINONG60",
                HN60_HIGH_DENSITY_SPACING_CM,
                0,
            ),
        ),
        hydraulic_modifiers=((24, 39, FAST_DRAIN),),
        initial_vwc=0.25,
        management_regime={"irrigation_quota_mm_total": 1.2},
        description="高密度黑农60需水高，局部fast-draining区域在R5/R6更早进入水分胁迫。",
        briefing_text="任务：管理黑农60高密度田的R5/R6局部缺水风险。请通过土壤、热信号和冠层状态定位真正缺水区域，只做 targeted irrigation。",
        actions=(
            ScenarioAction(
                "r5",
                "irrigation",
                24,
                39,
                hours=0.9,
            ),
        ),
        zones=(("fastdrain_high_density_24_39", 24, 39), ("reference_0_15", 0, 15)),
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 37},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_hn60_high_fastdrain_dryr5r6", ""
        )
        or None,
    )
)

add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_dryr5r6_insect_threshold_waterstress",
        class_name="ScenarioFullSeasonHBDryR5R6InsectThresholdWaterstress",
        slug="hb_dryr5r6_insect_threshold_waterstress",
        profile_name="harbin_l3_dryr5r6_insect_threshold_waterstress_seed_1613",
        cultivar="黑农84标准密度/R5R6缺水虫害鉴别",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        initial_vwc=0.24,
        hydraulic_modifiers=((10, 27, FAST_DRAIN),),
        management_regime={"irrigation_quota_mm_total": 6.0},
        description="R5/R6缺水和虫害同时可能造成叶片/NDVI异常，需要双重诊断。",
        briefing_text="任务：管理R5/R6干旱和虫害阈值可能混淆的黑农84田。请用soil/thermal判断缺水，用地面虫害检查判断虫口，不能直接灌或直接喷。",
        actions=(
            ScenarioAction(
                "r5",
                "insecticide",
                30,
                47,
                liters_per_ridge=3.5,
            ),
            ScenarioAction(
                "r5",
                "irrigation",
                10,
                27,
                hours=0.75,
            ),
        ),
        zones=(
            ("dry_patch_10_27", 10, 27),
            ("insect_risk_30_47", 30, 47),
            ("reference_52_63", 52, 63),
        ),
        postharvest_drying_zones=('whole_field',),
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 24},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_dryr5r6_insect_threshold_waterstress", ""
        )
        or None,
    )
)

add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_heatdry_aphid_limitedspray_waterlimit",
        class_name="ScenarioFullSeasonHBHeatdryAphidLimitedsprayWaterlimit",
        slug="hb_heatdry_aphid_limitedspray_waterlimit",
        profile_name="harbin_l3_heatdry_aphid_limitedspray_waterlimit_seed_1614",
        cultivar="黑农84标准密度/热干蚜虫喷药水量限制",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        initial_vwc=0.25,
        management_regime={"irrigation_quota_mm_total": 1.8},
        description="热干中期同时提高水分压力和蚜虫风险，需判断有限水和喷药机会的优先级。",
        briefing_text="任务：管理热干天气下的蚜虫和水分限制。请先区分水分胁迫与虫害阈值，再决定是否补水、是否保留或使用有限喷药机会。",
        actions=(
            ScenarioAction(
                "r5",
                "irrigation",
                0,
                31,
                hours=0.65,
            ),
            ScenarioAction(
                "r5",
                "insecticide",
                12,
                35,
                liters_per_ridge=3.4,
            ),
        ),
        zones=(
            ("water_priority_0_31", 0, 31),
            ("aphid_threshold_12_35", 12, 35),
            ("reference_44_63", 44, 63),
        ),
        postharvest_drying_zones=('whole_field',),
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 34},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_heatdry_aphid_limitedspray_waterlimit", ""
        )
        or None,
    )
)

add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_lowdensity_weed_dry_competition",
        class_name="ScenarioFullSeasonHBLowdensityWeedDryCompetition",
        slug="hb_lowdensity_weed_dry_competition",
        profile_name="harbin_l3_lowdensity_weed_dry_competition_seed_1615",
        cultivar="黑农84低密度/杂草和轻旱竞争",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        density_target_plants_m2=16.5,
        planting_zones=(
            PlantingZone(
                "whole_field_low_density", 0, 63, "HEINONG84", LOW_DENSITY_SPACING_CM, 0
            ),
        ),
        initial_vwc=0.25,
        management_regime={"irrigation_quota_mm_total": 4.0},
        description="低密度冠层闭合慢，杂草与R5/R6轻旱共同竞争水分和光。",
        briefing_text="任务：管理低密度黑农84田的杂草和轻旱竞争。NDVI偏高可能来自杂草，请结合地面检查、土壤水分和冠层状态决定控草和补水。",
        actions=(
            ScenarioAction(
                "emergence",
                "mechanical_weed",
                8,
                55,
                target_wait_days=12,
            ),
            ScenarioAction(
                "r5",
                "irrigation",
                16,
                47,
                hours=0.55,
                target_wait_days=6,
            ),
        ),
        zones=(
            ("weed_only_8_15", 8, 15),
            ("water_competition_16_47", 16, 47),
            ("weed_only_48_55", 48, 55),
        ),
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_lowdensity_weed_dry_competition", ""
        )
        or None,
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 24},
    )
)

add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_hn84_hn58_weed_mechanical_allocation",
        class_name="ScenarioFullSeasonHBHN84HN58WeedMechanicalAllocation",
        slug="hb_highweedseedbank_lowchemical",
        profile_name="harbin_l3_highweedseedbank_lowchemical_seed_1616",
        cultivar="黑农84/黑农58分区高草害机械资源限制",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000, "HEINONG58": 1000000},
        planting_zones=(
            PlantingZone(
                "zone_b_hn84_high_pressure_0_31",
                0,
                31,
                "HEINONG84",
                HEINONG84_SPACING_CM,
                0,
            ),
            PlantingZone(
                "zone_a_hn58_weed_competitive_32_63",
                32,
                63,
                "HEINONG58",
                HEINONG84_SPACING_CM,
                0,
            ),
        ),
        prior_histories=(("high_weed_seed_bank", 0, 63),),
        management_regime={
            "regime": "low_chemical",
            "active_ingredient_cap_kg": 0.0,
            "max_machine_passes": 4,
            "max_mechanical_weed_ridges": 32,
        },
        description="高草害下黑农84与较耐草竞争黑农58分区，机械资源只够处理一个32垄分区。",
        briefing_text=(
            "任务：按低化学投入目标管理高草害大豆田。"
            "B区0-31垄为黑农84，seed_spacing_cm=7.9；"
            "A区32-63垄为较耐草竞争的黑农58，seed_spacing_cm=7.9。"
            "本季不允许使用化学除草剂，机械除草资源只够处理一个32垄分区。"
            "请先用传感器、无人机和地面检查确认草害与作物状态，"
            "再选择可避免产量损失更大的分区机械除草，不能全田机械除草。"
        ),
        actions=(
            ScenarioAction(
                "emergence",
                "mechanical_weed",
                0,
                31,
                target_wait_days=9,
            ),
        ),
        zones=(
            ("zone_b_hn84_high_pressure_0_31", 0, 31),
            ("zone_a_hn58_weed_competitive_32_63", 32, 63),
        ),
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_hn84_hn58_weed_mechanical_allocation", ""
        )
        or None,
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 63},
    )
)

add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_organic_residue_weed_establishment",
        class_name="ScenarioFullSeasonHBOrganicResidueWeedEstablishment",
        slug="hb_organic_residue_weed_establishment",
        profile_name="harbin_l3_organic_residue_weed_establishment_seed_1617",
        cultivar="黑农84有机/残茬湿冷建苗草害",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        start_date="2026-05-11",
        prior_histories=(
            ("high_residue_cool_seedbed", 0, 63),
            ("high_weed_seed_bank", 0, 63),
        ),
        custom_histories=(
            (
                PriorFieldHistoryPreset(
                    name="residue_crusted_gap_stand",
                    stand_fraction_delta=-0.40,
                    soil_temp_delta_c=-0.8,
                ),
                0,
                7,
            ),
        ),
        management_regime={"regime": "organic", "max_machine_passes": 46},
        description="有机管理下高残茬湿冷春影响出苗，同时早期杂草压力高。",
        briefing_text="任务：按有机/允许投入约束管理高残茬湿冷春大豆田。请检查seedbed、出苗和杂草，使用允许的机械/监测路径，不能使用常规化学除草剂。",
        planting_zones=(
            PlantingZone(
                "whole_field_residue", 0, 63, "HEINONG84", HEINONG84_SPACING_CM, 0
            ),
        ),
        actions=(
            ScenarioAction(
                "emergence",
                "mechanical_weed",
                0,
                63,
                target_wait_days=4,
            ),
            ScenarioAction(
                "emergence",
                "replant",
                0,
                7,
                target_wait_days=12,
            ),
        ),
        zones=(("slow_emergence_0_15", 0, 15), ("weed_pressure_0_63", 0, 63)),
        postharvest_drying_zones=('whole_field',),
        waits={"emergence": 16, "r1": 24, "mid": 18, "r5": 24, "harvest": 19},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_organic_residue_weed_establishment", ""
        )
        or None,
    )
)

add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_lowcarbon_batch_operations_wetdisease",
        class_name="ScenarioFullSeasonHBLowcarbonBatchOperationsWetdisease",
        slug="hb_lowcarbon_batch_operations_wetdisease",
        profile_name="harbin_l3_lowcarbon_batch_wetdisease_seed_1618",
        cultivar="黑农84低碳少进地/湿六月病害",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        management_regime={"regime": "low_carbon", "max_machine_passes": 42},
        description="低碳少机械进地目标要求合并操作，但湿六月病害不能拖过关键窗口。",
        briefing_text="任务：按低碳少机械进地目标管理湿六月病害风险。请减少不必要进地，但病害达到阈值且窗口合适时仍要 targeted 处理。",
        actions=(
            ScenarioAction(
                "mid",
                "fungicide",
                24,
                47,
                liters_per_ridge=3.6,
                target_wait_days=8,
            ),
        ),
        zones=(("disease_batch_24_47", 24, 47), ("reference_0_15", 0, 15)),
        waits={"emergence": 15, "r1": 24, "mid": 16, "r5": 24, "harvest": 55},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_lowcarbon_batch_operations_wetdisease", ""
        )
        or None,
    )
)

add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_dryer_capacity_batch_harvest_storage",
        class_name="ScenarioFullSeasonHBDryerCapacityBatchHarvestStorage",
        slug="hb_dryer_capacity_batch_harvest_storage",
        profile_name="harbin_l3_dryer_storage_double_capacity_seed_1620",
        cultivar="黑农84标准密度/烘干能力约束分批收储",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        postharvest_market={
            "name": "dryer_storage_limited",
            "drying_capacity_kg_per_day": 5200.0,
            "storage_capacity_kg": 12000.0,
            "max_storage_moisture_pct": 13.5,
        },
        description="收获期烘干能力限制分批收获、烘干和入库；储藏容量需要检查但不是主要约束。",
        briefing_text="任务：管理烘干能力限制下的黑农84收获季。每批收获前检查成熟、水分、天气和烘干/储藏容量，收后及时卸粮、烘干和入库。",
        zones=(("west_0_31", 0, 31), ("east_32_63", 32, 63)),
        harvest_zones=(
            ("west_0_31", 0, 31),
            ("east_32_63", 32, 63),
        ),
        harvest_zone_waits={"east_32_63": 1},
        postharvest_drying_zones=('west_0_31', 'east_32_63'),
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 41},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_dryer_capacity_batch_harvest_storage", ""
        )
        or None,
    )
)

add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_laterain_shattering_drying_tradeoff",
        class_name="ScenarioFullSeasonHBLaterainShatteringDryingTradeoff",
        slug="hb_laterain_shattering_drying_tradeoff",
        profile_name="harbin_l3_laterain_shattering_drying_tradeoff_seed_1621",
        cultivar="黑农84标准密度/晚雨裂荚烘干权衡",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        postharvest_market={
            "name": "shattering_drying_tradeoff",
            "quality_discount_damage": 0.07,
            "quality_discount_wet": 0.02,
        },
        description="晚雨前等待自然降水分可能增加裂荚/掉粒，提前收获则增加烘干成本。",
        briefing_text="任务：管理晚雨、籽粒水分、裂荚风险和烘干成本之间的权衡。请根据工具返回选择收获窗口，不能简单等到全田最低水分。",
        zones=(("whole_field_0_63", 0, 63),),
        harvest_zones=(
            ("west_0_31", 0, 31),
            ("east_32_63", 32, 63),
        ),
        postharvest_drying_zones=('west_0_31', 'east_32_63'),
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 36},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_laterain_shattering_drying_tradeoff", ""
        )
        or None,
    )
)

add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_cool_august_lategrain_laterain",
        class_name="ScenarioFullSeasonHBCoolAugustLategrainLaterain",
        slug="hb_cool_august_lategrain_laterain",
        profile_name="harbin_l3_cool_august_lategrain_laterain_seed_1622",
        cultivar="黑农84标准密度/凉8月灌浆慢晚雨",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        description="8月偏凉导致成熟和籽粒降水慢，后期又有晚雨风险。",
        briefing_text="任务：管理凉8月导致成熟偏慢且收获期有降雨风险的黑农84田。请按成熟、籽粒水分和天气窗口决定是否等待或分批收获烘干。",
        zones=(("whole_field_0_63", 0, 63),),
        harvest_zones=(
            ("west_0_31", 0, 31),
            ("east_32_63", 32, 63),
        ),
        waits={"emergence": 15, "r1": 25, "mid": 19, "r5": 25, "harvest": 70},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_cool_august_lategrain_laterain", ""
        )
        or None,
    )
)

add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_hn50_hn84_hn58_mixed_stress",
        class_name="ScenarioFullSeasonHBHN50HN84HN58MixedStress",
        slug="hb_hn50_hn84_hn58_mixed_stress",
        profile_name="harbin_l3_hn50_hn84_hn58_mixed_stress_seed_1624",
        cultivar="黑河50/黑农84/黑农58三品种综合压力",
        primary_seed="HEIHE50",
        seed_stocks={"HEIHE50": 800000, "HEINONG84": 800000, "HEINONG58": 800000},
        initial_vwc=0.23,
        planting_zones=(
            PlantingZone("early_heihe50_0_20", 0, 20, "HEIHE50", HEIHE50_SPACING_CM, 2),
            PlantingZone(
                "standard_hn84_21_42", 21, 42, "HEINONG84", HEINONG84_SPACING_CM, 5
            ),
            PlantingZone(
                "stress_hn58_43_63", 43, 63, "HEINONG58", HEINONG84_SPACING_CM, 2
            ),
        ),
        management_regime={"irrigation_quota_mm_total": 6.0},
        description="三品种分区错期播种，冷春、干旱和收获窗口对不同品种与播期的影响不同。",
        briefing_text=(
            "任务：管理早熟、标准和抗逆品种分区错期播种大豆田。"
            "按计划分三批播种：先播0-20垄HEIHE50，约5天后播21-42垄HEINONG84，"
            "再约2天后播43-63垄HEINONG58；不要把三个区同日播完。"
            "后续请按区检查出苗、生育期、水分和成熟窗口，不能把全田视为同一品种或同一播期状态。"
        ),
        actions=(
            ScenarioAction(
                "r5",
                "irrigation",
                21,
                42,
                hours=0.7,
            ),
        ),
        zones=(("heihe50_0_20", 0, 20), ("hn84_21_42", 21, 42), ("hn58_43_63", 43, 63)),
        harvest_zones=(
            ("heihe50_0_20", 0, 20),
            ("hn84_21_42", 21, 42),
            ("hn58_43_63", 43, 63),
        ),
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_hn50_hn84_hn58_mixed_stress", ""
        )
        or None,
        enforce_planting_windows=True,
        harvest_zone_waits={"hn84_21_42": 16},
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 23},
    )
)

add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_density_gradient_wetdry",
        class_name="ScenarioFullSeasonHBDensityGradientWetdry",
        slug="hb_density_gradient_wetdry",
        profile_name="harbin_l3_density_gradient_wetdry_seed_1625",
        cultivar="黑农84/黑农60密度梯度湿转干",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 700000, "HEINONG60": 500000},
        planting_zones=(
            PlantingZone(
                "low_density_0_20", 0, 20, "HEINONG84", LOW_DENSITY_SPACING_CM, 0
            ),
            PlantingZone(
                "standard_21_42", 21, 42, "HEINONG84", HEINONG84_SPACING_CM, 0
            ),
            PlantingZone(
                "high_density_43_63",
                43,
                63,
                "HEINONG60",
                HN60_HIGH_DENSITY_SPACING_CM,
                0,
            ),
        ),
        management_regime={"irrigation_quota_mm_total": 5.0},
        description="低/标/高密度三区在6月湿和后期转干中面临不同草害、病害和水分压力。",
        briefing_text="任务：管理密度梯度田的湿转干季节。请按区判断低密度草害、高密度病害和后期水分需求，不能全田统一处理。",
        actions=(
            ScenarioAction(
                "emergence",
                "mechanical_weed",
                0,
                20,
                target_wait_days=9,
            ),
            ScenarioAction(
                "mid",
                "fungicide",
                43,
                63,
                liters_per_ridge=3.8,
                target_wait_days=15,
            ),
        ),
        zones=(
            ("low_density_0_20", 0, 20),
            ("standard_21_42", 21, 42),
            ("high_density_43_63", 43, 63),
        ),
        harvest_zones=(
            ("low_density_0_20", 0, 20),
            ("standard_21_42", 21, 42),
            ("high_density_43_63", 43, 63),
        ),
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_density_gradient_wetdry", ""
        )
        or None,
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 45},
    )
)

add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_fertilizer_quota_edge_lowfertility",
        class_name="ScenarioFullSeasonHBFertilizerQuotaEdgeLowfertility",
        slug="hb_fertilizer_quota_edge_lowfertility",
        profile_name="harbin_l3_fertilizer_quota_edge_lowfertility_seed_1626",
        cultivar="黑农84标准密度/边缘低肥力肥料配额",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        custom_histories=(
            (
                PriorFieldHistoryPreset(
                    name="severe_edge_lowfertility",
                    nutrient_index_delta=-0.26,
                    stand_fraction_delta=-0.18,
                ),
                0,
                7,
            ),
            (
                PriorFieldHistoryPreset(
                    name="mild_edge_lowfertility",
                    nutrient_index_delta=-0.18,
                    stand_fraction_delta=-0.08,
                ),
                8,
                15,
            ),
        ),
        management_regime={"fertilizer_quota_kg": 520.0},
        description="局部弱苗/长势偏弱区域可能需要补肥或少量补种，但肥料配额有限，需要优先级。",
        briefing_text="任务：管理肥料配额有限条件下的黑农84田。请先通过全田出苗、叶色、NDVI、土壤和地面检查定位弱苗或长势偏弱区域，排除水分、病虫和机械原因后，再按严重程度排序做局部恢复管理。",
        actions=(
            ScenarioAction(
                "emergence",
                "fertigation",
                0,
                7,
                amount=0.32,
                water_mm=1.2,
            ),
            ScenarioAction(
                "emergence",
                "replant",
                0,
                3,
            ),
            ScenarioAction(
                "r1",
                "fertigation",
                8,
                15,
                amount=0.16,
                water_mm=1.0,
                target_wait_days=4,
            ),
        ),
        zones=(
            ("severe_edge_0_7", 0, 7),
            ("mild_edge_8_15", 8, 15),
            ("healthy_20_63", 20, 63),
        ),
        postharvest_drying_zones=('whole_field',),
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 28},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_fertilizer_quota_edge_lowfertility", ""
        )
        or None,
    )
)

add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_insect_after_fungicide_budget_conflict",
        class_name="ScenarioFullSeasonHBInsectAfterFungicideBudgetConflict",
        slug="hb_insect_after_fungicide_budget_conflict",
        profile_name="harbin_l3_insect_after_fungicide_budget_conflict_seed_1627",
        cultivar="黑农84标准密度/病害后虫害喷药预算冲突",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        management_regime={"active_ingredient_cap_kg": 0.35},
        description="中期病害和后期虫害先后出现，总喷药预算有限；后期虫害窗口土壤偏湿，需在确认阈值后用人工背负式点喷保护目标区。",
        briefing_text="任务：管理先病害后虫害且总喷药预算有限的黑农84田。请每次都确认阈值、预算和窗口，不能见到轻信号就消耗全部预算。",
        actions=(
            ScenarioAction(
                "mid",
                "fungicide",
                18,
                39,
                liters_per_ridge=3.4,
                target_wait_days=6,
            ),
            ScenarioAction(
                "r5",
                "insecticide",
                24,
                47,
                liters_per_ridge=3.2,
                target_wait_days=0,
                manual=True,
            ),
        ),
        zones=(
            ("disease_18_39", 18, 39),
            ("insect_24_47", 24, 47),
            ("reference_0_15", 0, 15),
        ),
        waits={"emergence": 15, "r1": 24, "mid": 20, "r5": 24, "harvest": 37},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_insect_after_fungicide_budget_conflict", ""
        )
        or None,
    )
)

add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_disease_then_drought_recovery_tradeoff",
        class_name="ScenarioFullSeasonHBDiseaseThenDroughtRecoveryTradeoff",
        slug="hb_disease_then_drought_recovery_tradeoff",
        profile_name="harbin_l3_disease_then_drought_recovery_seed_1628",
        cultivar="黑农84标准密度/病害后干旱恢复权衡",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        initial_vwc=0.25,
        management_regime={"irrigation_quota_mm_total": 6.0},
        hydraulic_modifiers=((20, 43, FAST_DRAIN),),
        description="湿六月病害处理后作物恢复期又遇R5/R6干旱，需要避免误判病害复发。",
        briefing_text="任务：管理先病害后干旱的大豆田。请在病害处理后复查恢复，后续若长势慢要用土壤/热信号区分缺水和病害复发。",
        actions=(
            ScenarioAction(
                "mid",
                "fungicide",
                20,
                43,
                liters_per_ridge=3.8,
            ),
            ScenarioAction(
                "r5",
                "irrigation",
                20,
                43,
                hours=0.75,
            ),
        ),
        zones=(("recovery_zone_20_43", 20, 43), ("reference_0_15", 0, 15)),
        waits={"emergence": 15, "r1": 24, "mid": 19, "r5": 24, "harvest": 40},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_disease_then_drought_recovery_tradeoff", ""
        )
        or None,
    )
)

add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_weed_then_disease_canopy_confusion",
        class_name="ScenarioFullSeasonHBWeedThenDiseaseCanopyConfusion",
        slug="hb_weed_then_disease_canopy_confusion",
        profile_name="harbin_l3_weed_then_disease_canopy_confusion_seed_1629",
        cultivar="黑农84标准密度/早草后病冠层混淆",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        description="早期杂草可能让NDVI不低，后期病害真正损伤作物冠层。",
        briefing_text="任务：管理早期草害和后期湿病害相继出现的大豆田。请区分field greenness和crop health，不能把高NDVI直接当作作物健康。",
        actions=(
            ScenarioAction(
                "emergence",
                "mechanical_weed",
                4,
                23,
                target_wait_days=11,
            ),
            ScenarioAction(
                "mid",
                "fungicide",
                32,
                51,
                liters_per_ridge=3.8,
                target_wait_days=9,
            ),
        ),
        zones=(
            ("early_weed_4_23", 4, 23),
            ("later_disease_32_51", 32, 51),
            ("reference_54_63", 54, 63),
        ),
        waits={"emergence": 15, "r1": 24, "mid": 20, "r5": 24, "harvest": 65},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_weed_then_disease_canopy_confusion", ""
        )
        or None,
    )
)

add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_lowinput_waterlimited_weed_pressure",
        class_name="ScenarioFullSeasonHBLowinputWaterlimitedWeedPressure",
        slug="hb_lowinput_waterlimited_weed_pressure",
        profile_name="harbin_l3_lowinput_waterlimited_weed_pressure_seed_1630",
        cultivar="黑农84低投入/水量限制草害",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        initial_vwc=0.24,
        prior_histories=(("high_weed_seed_bank", 0, 63),),
        hydraulic_modifiers=((16, 47, LOW_HOLDING_DRY_PATCH),),
        management_regime={
            "regime": "low_input",
            "irrigation_quota_mm_total": 4.0,
            "active_ingredient_cap_kg": 0.08,
        },
        description="低投入和水量限制下，杂草压力会加重大豆水分竞争。",
        briefing_text="任务：按低投入和水量限制管理有杂草压力的大豆田。请在允许投入和有限水量下判断控草与补水优先级，不能依赖全田高投入处理。",
        actions=(
            ScenarioAction(
                "emergence",
                "mechanical_weed",
                0,
                63,
                target_wait_days=10,
            ),
            ScenarioAction(
                "r5",
                "irrigation",
                16,
                47,
                hours=1.2,
            ),
        ),
        zones=(
            ("weed_pressure_0_15", 0, 15),
            ("water_priority_16_47", 16, 47),
            ("weed_pressure_48_63", 48, 63),
        ),
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 29},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_lowinput_waterlimited_weed_pressure", ""
        )
        or None,
    )
)


add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_planter_skip_rows_stand_gap",
        class_name="ScenarioFullSeasonHBPlanterSkipRowsStandGap",
        slug="hb_planter_skip_rows_stand_gap",
        profile_name="harbin_l3_planter_skip_rows_stand_gap_seed_1704",
        cultivar="黑农84标准密度/播种机漏播补种",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        description="播种机局部漏播导致条带缺苗，出苗检查定位后局部补种。",
        briefing_text="任务：管理黑农84播种后出苗检查和补种窗口。请通过全田出苗检查、无人机和地面检查定位缺苗条带，排除肥水病虫原因后只对确认区域补种。",
        custom_histories=(
            (
                PriorFieldHistoryPreset(
                    name="skip_rows_planting_gap", stand_fraction_delta=-0.48
                ),
                12,
                15,
            ),
            (
                PriorFieldHistoryPreset(
                    name="skip_rows_planting_gap", stand_fraction_delta=-0.42
                ),
                28,
                31,
            ),
        ),
        actions=(
            ScenarioAction(
                "emergence",
                "replant",
                12,
                15,
            ),
            ScenarioAction(
                "emergence",
                "replant",
                28,
                31,
            ),
        ),
        zones=(
            ("skip_rows_12_15", 12, 15),
            ("skip_rows_28_31", 28, 31),
            ("reference_44_55", 44, 55),
        ),
        harvest_zones=(
            ("ready_0_11", 0, 11),
            ("ready_16_63", 16, 63),
            ("replanted_gap_12_15", 12, 15),
        ),
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 37},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_planter_skip_rows_stand_gap", ""
        )
        or None,
        harvest_zone_waits={"replanted_gap_12_15": 7},
        postharvest_drying_zones=('ready_0_11', 'ready_16_63', 'replanted_gap_12_15'),
    )
)


add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_replant_after_crusting_short_season",
        class_name="ScenarioFullSeasonHBReplantAfterCrustingShortSeason",
        slug="hb_replant_after_crusting_short_season",
        profile_name="harbin_l3_replant_after_crusting_short_season_seed_1706",
        cultivar="黑农84标准密度/板结后补种季节压缩",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        description="播后强雨形成板结，局部补种会推迟成熟，需要权衡补种收益和成熟风险。",
        briefing_text="任务：管理播后板结导致的局部出苗风险。请先确认降雨、土壤表层状态和出苗情况，再决定是否局部补种；后期按成熟和籽粒水分分批收获。",
        custom_histories=(
            (
                PriorFieldHistoryPreset(
                    name="crusting_slow_emergence",
                    stand_fraction_delta=-0.30,
                    soil_temp_delta_c=-0.8,
                ),
                0,
                11,
            ),
        ),
        actions=(
            ScenarioAction(
                "emergence",
                "replant",
                0,
                7,
            ),
        ),
        zones=(("crusted_replant_0_11", 0, 11), ("reference_20_31", 20, 31)),
        harvest_zones=(
            ("reference_ready_12_63", 12, 63),
            ("replanted_0_11", 0, 11),
        ),
        waits={"emergence": 16, "r1": 24, "mid": 18, "r5": 24, "harvest": 43},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_replant_after_crusting_short_season", ""
        )
        or None,
    )
)


add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_fertilizer_misapplication_strip_recovery",
        class_name="ScenarioFullSeasonHBFertilizerMisapplicationStripRecovery",
        slug="hb_fertilizer_misapplication_strip_recovery",
        profile_name="harbin_l3_fertilizer_misapplication_strip_recovery_seed_1711",
        cultivar="黑农84标准密度/施肥条带不均恢复",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        description="播种/施肥条带肥量不足，形成条带状弱苗，需要 targeted 补肥。",
        briefing_text="任务：管理出现局部叶色和长势异常的大豆田。请通过叶色、NDVI、土壤和地面复查识别空间模式，排除病虫水分后做 targeted 补肥。",
        custom_histories=(
            (
                PriorFieldHistoryPreset(
                    name="fertilizer_underapplied_strip",
                    nutrient_index_delta=-0.24,
                    stand_fraction_delta=-0.04,
                ),
                24,
                39,
            ),
        ),
        actions=(
            ScenarioAction(
                "r1",
                "fertigation",
                24,
                39,
                amount=0.3,
                water_mm=1.2,
            ),
        ),
        zones=(("underfertilized_strip_24_39", 24, 39), ("reference_0_15", 0, 15)),
        postharvest_drying_zones=('whole_field',),
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 18},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_fertilizer_misapplication_strip_recovery", ""
        )
        or None,
    )
)


add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_nutrient_vs_disease_leafcolor_diagnosis",
        class_name="ScenarioFullSeasonHBNutrientVsDiseaseLeafcolorDiagnosis",
        slug="hb_nutrient_vs_disease_leafcolor_diagnosis",
        profile_name="harbin_l3_nutrient_vs_disease_leafcolor_diagnosis_seed_1713",
        cultivar="黑农84标准密度/叶色营养病害鉴别",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        description="叶色浅和NDVI下降可能来自营养不足或湿六月早期病害，必须ground check鉴别。",
        briefing_text="任务：管理叶色异常的大豆田。请结合水分、病虫、叶色、NDVI和地面检查区分营养不足与病害，不能直接补肥或直接杀菌。",
        custom_histories=(
            (
                PriorFieldHistoryPreset(
                    name="leafcolor_nutrient_patch", nutrient_index_delta=-0.20
                ),
                8,
                23,
            ),
        ),
        actions=(
            ScenarioAction(
                "r1",
                "fertigation",
                8,
                23,
                amount=0.24,
                water_mm=1.0,
            ),
            ScenarioAction(
                "mid",
                "fungicide",
                44,
                53,
                liters_per_ridge=3.6,
            ),
        ),
        zones=(
            ("nutrient_leafcolor_8_23", 8, 23),
            ("disease_leafcolor_44_53", 44, 53),
            ("reference_24_35", 24, 35),
        ),
        waits={"emergence": 15, "r1": 24, "mid": 21, "r5": 24, "harvest": 24},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_nutrient_vs_disease_leafcolor_diagnosis", ""
        )
        or None,
    )
)


add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_weed_green_ndvi_masked_drought",
        class_name="ScenarioFullSeasonHBWeedGreenNdviMaskedDrought",
        slug="hb_weed_green_ndvi_masked_drought",
        profile_name="harbin_l3_weed_green_ndvi_masked_drought_seed_1714",
        cultivar="黑农84标准密度/杂草NDVI掩盖缺水",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        description="杂草贡献绿色NDVI，但作物和root-zone显示R5/R6水分竞争。",
        briefing_text="任务：管理R5/R6水分和杂草竞争。NDVI偏高不代表作物不缺水，请结合土壤水分、热信号和地面杂草/作物状态决定控草与补水。",
        initial_vwc=0.25,
        management_regime={"irrigation_quota_mm_total": 5.0},
        actions=(
            ScenarioAction(
                "emergence",
                "mechanical_weed",
                0,
                31,
            ),
            ScenarioAction(
                "r5",
                "irrigation",
                16,
                47,
                hours=0.65,
            ),
        ),
        zones=(
            ("weed_green_0_31", 0, 31),
            ("crop_water_stress_16_47", 16, 47),
            ("reference_48_63", 48, 63),
        ),
        waits={"emergence": 24, "r1": 24, "mid": 18, "r5": 24, "harvest": 21},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_weed_green_ndvi_masked_drought", ""
        )
        or None,
    )
)


add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_herbicide_window_missed_rain_delay",
        class_name="ScenarioFullSeasonHBHerbicideWindowMissedRainDelay",
        slug="hb_herbicide_window_missed_rain_delay",
        profile_name="harbin_l3_herbicide_window_missed_rain_delay_seed_1717",
        cultivar="黑农84标准密度/除草窗口降雨延误",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        description="计划除草窗口被连续降雨打断，杂草继续生长，需要等可作业窗口或替代处理。",
        briefing_text="任务：管理早期除草窗口被降雨延误的大豆田。请检查天气、土壤可作业性和杂草压力，窗口合适后再 targeted 控草。",
        actions=(
            ScenarioAction(
                "emergence",
                "herbicide",
                12,
                45,
                liters_per_ridge=2.0,
            ),
        ),
        zones=(("rain_delayed_weed_12_45", 12, 45), ("reference_0_11", 0, 11)),
        waits={"emergence": 30, "r1": 24, "mid": 18, "r5": 24, "harvest": 26},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_herbicide_window_missed_rain_delay", ""
        )
        or None,
    )
)


add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_mechanical_weed_trafficability_window",
        class_name="ScenarioFullSeasonHBMechanicalWeedTrafficabilityWindow",
        slug="hb_mechanical_weed_control_soil_wetness",
        profile_name="harbin_l3_mechanical_weed_control_soil_wetness_seed_1718",
        cultivar="黑农84有机/雨后机械除草可作业窗口",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        description="雨后早期草害需要处理，但湿土条件下必须等待机械除草可作业窗口。",
        briefing_text=(
            "任务：按有机约束管理雨后早期草害。全田 seed_spacing_cm=7.9。"
            "出苗后若发现 weed pressure 或 NDVI/冠层异常，必须先检查 weather、soil sensors "
            "和 trafficability；如果 top VWC 过高，不能强行进地，也不能改用 herbicide，"
            "需要等待并复查土壤可作业性。trafficable 后再用 robot 全田确认草害/作物状态，"
            "然后全田 mechanical_weed_control，并复查恢复。"
        ),
        management_regime={
            "regime": "organic",
            "max_machine_passes": 7,
            "max_mechanical_weed_ridges": 64,
        },
        actions=(
            ScenarioAction(
                "emergence",
                "mechanical_weed",
                0,
                63,
                reason="wet_soil_trafficability_delay",
            ),
        ),
        zones=(("whole_field_wet_soil_weed", 0, 63),),
        postharvest_drying_zones=('whole_field',),
        waits={"emergence": 24, "r1": 24, "mid": 18, "r5": 24, "harvest": 25},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_mechanical_weed_trafficability_window", ""
        )
        or None,
    )
)


add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_leaffeeder_vs_disease_spots_diagnosis",
        class_name="ScenarioFullSeasonHBLeaffeederVsDiseaseSpotsDiagnosis",
        slug="hb_leaffeeder_vs_disease_spots_diagnosis",
        profile_name="harbin_l3_leaffeeder_vs_disease_spots_diagnosis_seed_1721",
        cultivar="黑农84标准密度/虫食病斑鉴别",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        description="叶片异常可能来自食叶虫害或病斑，R3/R4需地面诊断决定用药类型。",
        briefing_text="任务：管理R3/R4叶片异常的大豆田。请用无人机、热信号和地面检查区分虫食与病斑，再选择 pesticide 或 fungicide，不能凭视觉异常直接喷错药。",
        actions=(
            ScenarioAction(
                "mid",
                "insecticide",
                33,
                43,
                liters_per_ridge=3.5,
                reason="routine_whole_field_scouting",
            ),
        ),
        zones=(
            ("leaf_feeder_33_43", 33, 43),
            ("disease_spot_reference_44_55", 44, 55),
            ("healthy_reference_0_15", 0, 15),
        ),
        postharvest_drying_zones=('whole_field',),
        waits={"emergence": 15, "r1": 24, "mid": 27, "r5": 22, "harvest": 31},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_leaffeeder_vs_disease_spots_diagnosis", ""
        )
        or None,
    )
)


add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_wetjune_disease_recheck_after_fungicide",
        class_name="ScenarioFullSeasonHBWetjuneDiseaseRecheckAfterFungicide",
        slug="hb_wetjune_disease_recheck_after_fungicide",
        profile_name="harbin_l3_wetjune_disease_recheck_after_fungicide_seed_1724",
        cultivar="黑农84标准密度/湿六月杀菌后复查",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        description="湿六月病害风险处理后仍需复查，持续湿度可能导致病害再发展。",
        briefing_text="任务：管理湿六月病害风险和后续复查。请先用传感器、无人机和地面检查确认病害及处理窗口；任何处理后都要在后续湿度窗口复查病害是否再发展，避免过度或不足处理。",
        actions=(
            ScenarioAction(
                "mid",
                "fungicide",
                20,
                43,
                liters_per_ridge=3.6,
            ),
            ScenarioAction(
                "r5",
                "fungicide",
                20,
                43,
                liters_per_ridge=2.8,
            ),
        ),
        zones=(("disease_recheck_20_43", 20, 43), ("reference_0_15", 0, 15)),
        postharvest_drying_zones=('whole_field',),
        waits={"emergence": 15, "r1": 24, "mid": 23, "r5": 16, "harvest": 35},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_wetjune_disease_recheck_after_fungicide", ""
        )
        or None,
    )
)


add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_drought_recovery_false_disease_signal",
        class_name="ScenarioFullSeasonHBDroughtRecoveryFalseDiseaseSignal",
        slug="hb_drought_recovery_false_disease_signal",
        profile_name="harbin_l3_drought_recovery_false_disease_signal_seed_1725",
        cultivar="黑农84标准密度/旱后恢复慢误判病害",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        description="干旱恢复后局部仍弱，叶色异常可能被误判为病害。",
        briefing_text="任务：管理旱后恢复慢的大豆田。请用土壤水分、热信号、历史天气和病害地面症状区分恢复慢与病害，不要把弱长势直接当作病害复发。",
        initial_vwc=0.24,
        hydraulic_modifiers=((18, 39, FAST_DRAIN),),
        management_regime={"irrigation_quota_mm_total": 5.0},
        actions=(
            ScenarioAction(
                "r5",
                "irrigation",
                18,
                39,
                hours=0.7,
            ),
        ),
        zones=(("slow_recovery_18_39", 18, 39), ("reference_44_63", 44, 63)),
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 18, "harvest": 48},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_drought_recovery_false_disease_signal", ""
        )
        or None,
    )
)


add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_r5_heat_stress_without_soil_drought",
        class_name="ScenarioFullSeasonHBR5HeatStressWithoutSoilDrought",
        slug="hb_r5_heat_stress_without_soil_drought",
        profile_name="harbin_l3_r5_heat_stress_without_soil_drought_seed_1726",
        cultivar="黑农84标准密度/R5热胁迫非土壤干旱",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        description="高温导致冠层热胁迫，但root-zone水分尚可，不能盲目灌溉。",
        briefing_text="任务：管理R5热胁迫但土壤水分尚可的大豆田。请比较canopy thermal、root-zone VWC和预报，避免把高温热信号误判成缺水而盲目灌溉。",
        zones=(("heat_stress_whole_field", 0, 63),),
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 26, "harvest": 43},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_r5_heat_stress_without_soil_drought", ""
        )
        or None,
    )
)


add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_cloudy_wet_low_radiation_biomass",
        class_name="ScenarioFullSeasonHBCloudyWetLowRadiationBiomass",
        slug="hb_cloudy_wet_low_radiation_biomass",
        profile_name="harbin_l3_cloudy_wet_low_radiation_biomass_seed_1728",
        cultivar="黑农84标准密度/连阴雨低辐射生物量慢",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        description="连阴雨低辐射导致biomass积累慢，NDVI不一定明显下降，不应误判缺肥。",
        briefing_text="任务：管理连阴雨低辐射季节的大豆田。请结合天气辐射、NDVI、叶色和土壤养分判断长势慢原因，不要把低辐射造成的生物量慢直接当缺肥。",
        zones=(("whole_field_low_radiation", 0, 63),),
        waits={"emergence": 15, "r1": 25, "mid": 20, "r5": 25, "harvest": 44},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_cloudy_wet_low_radiation_biomass", ""
        )
        or None,
    )
)


add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_split_irrigation_schedule_power_limit",
        class_name="ScenarioFullSeasonHBSplitIrrigationSchedulePowerLimit",
        slug="hb_split_irrigation_schedule_power_limit",
        profile_name="harbin_l3_split_irrigation_schedule_power_limit_seed_1731",
        cultivar="黑农84标准密度/电力限制分时灌溉",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        description="灌溉设备/电力限制导致只能分批灌溉，不能同时覆盖全部缺水区。",
        briefing_text="任务：管理电力/设备限制下的R5/R6灌溉。请根据soil moisture、thermal stress和阶段安排分批灌溉，不能把所有缺水垄段一次性全灌。",
        initial_vwc=0.22,
        management_regime={"irrigation_quota_mm_total": 8.0},
        hydraulic_modifiers=((8, 23, FAST_DRAIN), (40, 55, FAST_DRAIN)),
        actions=(
            ScenarioAction(
                "r5",
                "irrigation",
                8,
                23,
                hours=0.65,
            ),
            ScenarioAction(
                "r5",
                "irrigation",
                40,
                55,
                hours=0.55,
            ),
        ),
        zones=(
            ("first_irrigation_8_23", 8, 23),
            ("second_irrigation_40_55", 40, 55),
            ("reference_24_35", 24, 35),
        ),
        waits={"emergence": 15, "r1": 24, "mid": 23, "r5": 24, "harvest": 24},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_split_irrigation_schedule_power_limit", ""
        )
        or None,
    )
)


add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_fuel_limit_irrigation_harvest_ops",
        class_name="ScenarioFullSeasonHBFuelLimitIrrigationHarvestOps",
        slug="hb_fuel_limit_irrigation_harvest_ops",
        profile_name="harbin_l3_fuel_limit_irrigation_harvest_ops_seed_1733",
        cultivar="黑农84标准密度/燃油限制作业排序",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        description="燃油有限，不能完成所有灌溉、喷药和收获准备，需要把资源用于收益最高窗口。",
        briefing_text="任务：在燃油有限条件下管理灌溉、喷药和收获。请检查库存和设备状态，根据压力和天气窗口排序关键作业，不能平均分配资源。",
        tractor_fuel_l=260.0,
        initial_vwc=0.25,
        management_regime={"irrigation_quota_mm_total": 5.0, "max_machine_passes": 36},
        actions=(
            ScenarioAction(
                "r5",
                "irrigation",
                20,
                43,
                hours=0.65,
            ),
        ),
        zones=(("fuel_priority_water_20_43", 20, 43), ("reference_0_15", 0, 15)),
        harvest_zones=(
            ("priority_harvest_0_31", 0, 31),
            ("later_harvest_32_63", 32, 63),
        ),
        postharvest_drying_zones=('priority_harvest_0_31', 'later_harvest_32_63'),
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 48},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_fuel_limit_irrigation_harvest_ops", ""
        )
        or None,
    )
)


add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_market_discount_high_moisture_delivery",
        class_name="ScenarioFullSeasonHBMarketDiscountHighMoistureDelivery",
        slug="hb_market_discount_high_moisture_delivery",
        profile_name="harbin_l3_market_discount_high_moisture_delivery_seed_1739",
        cultivar="黑农84标准密度/高水分折扣烘干权衡",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        description="高水分交售折扣、烘干成本和降雨风险共同决定收获处理。",
        briefing_text="任务：管理高水分交售折扣与烘干成本。请在成熟、籽粒水分、降雨风险和烘干能力之间权衡，收获后按批次干燥或入库。",
        postharvest_market={
            "name": "high_moisture_discount",
            "quality_discount_wet": 0.05,
            "max_storage_moisture_pct": 13.5,
        },
        zones=(("whole_field_market_moisture", 0, 63),),
        harvest_zones=(
            ("west_0_31", 0, 31),
            ("east_32_63", 32, 63),
        ),
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 50},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_market_discount_high_moisture_delivery", ""
        )
        or None,
    )
)


add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_split_quality_batches_late_disease",
        class_name="ScenarioFullSeasonHBSplitQualityBatchesLateDisease",
        slug="hb_split_quality_batches_late_disease",
        profile_name="harbin_l3_split_quality_batches_late_disease_seed_1740",
        cultivar="黑农84标准密度/晚期病害质量分批",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        description="部分ridges晚期病害导致质量和水分不同，需要分批收获储藏，避免混批。",
        briefing_text="任务：管理晚期病害造成的分批质量差异。请按区检查病害、籽粒水分和收获窗口，分批收获、干燥、入库，避免把问题批次和正常批次混在一起。",
        postharvest_market={
            "name": "split_quality_late_disease",
            "quality_discount_damage": 0.08,
            "max_storage_moisture_pct": 13.5,
        },
        actions=(
            ScenarioAction(
                "r5",
                "fungicide",
                32,
                55,
                liters_per_ridge=3.2,
            ),
        ),
        zones=(("late_disease_quality_32_55", 32, 55), ("clean_reference_0_23", 0, 23)),
        harvest_zones=(
            ("clean_batch_0_31", 0, 31),
            ("quality_risk_batch_32_63", 32, 63),
        ),
        postharvest_drying_zones=('clean_batch_0_31', 'quality_risk_batch_32_63'),
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 48},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_split_quality_batches_late_disease", ""
        )
        or None,
    )
)


add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_rhizobia_nodulation_failure_nutrition",
        class_name="ScenarioFullSeasonHBRhizobiaNodulationFailureNutrition",
        slug="hb_rhizobia_nodulation_failure_nutrition",
        profile_name="harbin_l3_rhizobia_nodulation_failure_nutrition_seed_1702",
        cultivar="黑农84标准密度/根瘤固氮不足营养管理",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        description="出苗正常，中期叶色偏浅且水分病虫正常，根瘤/固氮不足作为营养proxy处理。",
        briefing_text="任务：管理中期叶色偏浅但水分和病虫害正常的大豆田。请通过叶色、NDVI、土壤水分和地面复查判断是否为根瘤/固氮或氮素供应不足，再按需营养补充。",
        custom_histories=(
            (
                PriorFieldHistoryPreset(
                    name="nodulation_failure_proxy", nutrient_index_delta=-0.20
                ),
                18,
                45,
            ),
        ),
        actions=(
            ScenarioAction(
                "r1",
                "fertigation",
                18,
                45,
                amount=0.26,
                water_mm=1.0,
            ),
        ),
        zones=(("nodulation_nutrition_18_45", 18, 45), ("reference_0_15", 0, 15)),
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_rhizobia_nodulation_failure_nutrition", ""
        )
        or None,
        postharvest_drying_zones=('whole_field',),
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 29},
    )
)


add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_local_soil_constraint_nutrition_patch",
        class_name="ScenarioFullSeasonHBLocalSoilConstraintNutritionPatch",
        slug="hb_local_soil_constraint_nutrition_patch",
        profile_name="harbin_l3_local_soil_constraint_nutrition_patch_seed_1708",
        cultivar="黑农84标准密度/局部土壤约束营养恢复",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        description="局部 soil constraint 导致出苗、root-zone水分和养分吸收偏弱；当前engine以营养/水分/stand代理表达。",
        briefing_text="任务：管理局部土壤约束导致的出苗和营养吸收问题。请通过土壤、出苗、NDVI和地面检查排除病虫害，再判断是否需要 targeted recovery。",
        custom_histories=(
            (
                PriorFieldHistoryPreset(
                    name="local_soil_constraint_proxy",
                    nutrient_index_delta=-0.18,
                    stand_fraction_delta=-0.18,
                ),
                48,
                59,
            ),
        ),
        hydraulic_modifiers=((48, 59, COMPACTED),),
        actions=(
            ScenarioAction(
                "emergence",
                "fertigation",
                48,
                59,
                amount=0.18,
                water_mm=1.0,
            ),
        ),
        zones=(("soil_constraint_patch_48_59", 48, 59), ("reference_20_31", 20, 31)),
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_local_soil_constraint_nutrition_patch", ""
        )
        or None,
        postharvest_drying_zones=('whole_field',),
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 51},
    )
)


add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_micronutrient_deficiency_flowering_patch",
        class_name="ScenarioFullSeasonHBMicronutrientDeficiencyFloweringPatch",
        slug="hb_micronutrient_deficiency_flowering_patch",
        profile_name="harbin_l3_micronutrient_deficiency_flowering_patch_seed_1709",
        cultivar="黑农84标准密度/初花期微量元素不足",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        description="初花期局部叶色异常且水分病虫正常，按微肥/叶面营养proxy处理。",
        briefing_text="任务：管理初花期局部叶色异常。请确认水分和病虫害正常，再判断是否需要小剂量微肥/叶面营养，不能误喷药或灌溉。",
        custom_histories=(
            (
                PriorFieldHistoryPreset(
                    name="micronutrient_deficiency_proxy", nutrient_index_delta=-0.16
                ),
                28,
                43,
            ),
        ),
        actions=(
            ScenarioAction(
                "r1",
                "fertigation",
                28,
                43,
                amount=0.16,
                water_mm=0.8,
            ),
        ),
        zones=(("micronutrient_patch_28_43", 28, 43), ("reference_0_15", 0, 15)),
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_micronutrient_deficiency_flowering_patch", ""
        )
        or None,
        postharvest_drying_zones=('whole_field',),
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 30},
    )
)


add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_potassium_deficit_dry_podfill_interaction",
        class_name="ScenarioFullSeasonHBPotassiumDeficitDryPodfillInteraction",
        slug="hb_potassium_deficit_dry_podfill_interaction",
        profile_name="harbin_l3_potassium_deficit_dry_podfill_interaction_seed_1710",
        cultivar="黑农84标准密度/钾不足与鼓粒轻旱交互",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        description="局部钾素不足使R5/R6轻旱下更易减产，需要区分缺水与养分×水分交互。",
        briefing_text="任务：管理钾素不足与R5/R6轻旱交互风险。请在花期检查营养，在R5/R6检查root-zone水分和热信号，区分单纯缺水和养分水分交互。",
        initial_vwc=0.22,
        hydraulic_modifiers=((20, 39, FAST_DRAIN),),
        custom_histories=(
            (
                PriorFieldHistoryPreset(
                    name="potassium_deficit_proxy", nutrient_index_delta=-0.17
                ),
                20,
                39,
            ),
        ),
        management_regime={"irrigation_quota_mm_total": 5.0},
        actions=(
            ScenarioAction(
                "r1",
                "fertigation",
                20,
                39,
                amount=0.2,
                water_mm=0.8,
            ),
            ScenarioAction(
                "r5",
                "irrigation",
                20,
                39,
                hours=0.65,
            ),
        ),
        zones=(("k_deficit_dry_20_39", 20, 39), ("reference_44_63", 44, 63)),
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 27},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_potassium_deficit_dry_podfill_interaction", ""
        )
        or None,
    )
)


add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_overfertilized_dense_canopy_disease_risk",
        class_name="ScenarioFullSeasonHBOverfertilizedDenseCanopyDiseaseRisk",
        slug="hb_overfertilized_dense_canopy_disease_risk",
        profile_name="harbin_l3_overfertilized_dense_canopy_disease_risk_seed_1712",
        cultivar="黑农60高密度/过旺密冠层病害风险",
        description="局部过旺/密冠层提高冠层湿度和病害风险；当前engine表达为dense canopy disease risk。",
        briefing_text="任务：管理黑农60高密度大豆田的局部过旺密冠层病害风险。请通过冠层、无人机和地面病害检查判断是否需要 targeted fungicide，不要继续补肥。",
        primary_seed="HEINONG60",
        seed_stocks={"HEINONG60": 1000000},
        density_target_plants_m2=29.0,
        planting_zones=(
            PlantingZone(
                "whole_field_hn60_high",
                0,
                63,
                "HEINONG60",
                HN60_HIGH_DENSITY_SPACING_CM,
                0,
            ),
        ),
        custom_histories=(),
        actions=(
            ScenarioAction(
                "mid",
                "fungicide",
                22,
                43,
                liters_per_ridge=3.4,
                reason="routine_whole_field_scouting",
                target_wait_days=4,
            ),
        ),
        zones=(("dense_canopy_disease_risk_22_43", 22, 43), ("reference_0_15", 0, 15)),
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 18},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_overfertilized_dense_canopy_disease_risk", ""
        )
        or None,
    )
)


add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_laterain_insect_risk",
        class_name="ScenarioFullSeasonHBLaterainInsectRisk",
        slug="hb_laterain_insect_risk",
        profile_name="harbin_l3_laterain_insect_risk_seed_1723",
        cultivar="黑农84标准密度/晚雨后虫害风险",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        description="晚雨后局部湿冠层环境提高虫害风险；当前engine表达为late-rain insect pressure。",
        briefing_text="任务：管理晚雨后的局部虫害风险。请先检查虫害迹象和虫口阈值，再决定是否 targeted pesticide。",
        postharvest_market={
            "name": "late_rain_insect_quality_risk",
            "quality_discount_damage": 0.05,
        },
        actions=(
            ScenarioAction(
                "r5",
                "insecticide",
                36,
                55,
                liters_per_ridge=3.5,
            ),
        ),
        zones=(("laterain_insect_risk_36_55", 36, 55), ("reference_0_23", 0, 23)),
        harvest_zones=(
            ("reference_0_31", 0, 31),
            ("insect_risk_32_63", 32, 63),
        ),
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 40, "harvest": 54},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_laterain_insect_risk", ""
        )
        or None,
    )
)


add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_coolwet_flowering_disease_risk",
        class_name="ScenarioFullSeasonHBCoolwetFloweringDiseaseRisk",
        slug="hb_coolwet_flowering_disease_risk",
        profile_name="harbin_l3_coolwet_flowering_disease_risk_seed_1727",
        cultivar="黑农84标准密度/花期凉湿病害风险",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        description="花期凉湿提高湿冠层病害风险；核心是观察、病害确认、targeted fungicide和复查。",
        briefing_text="任务：管理花期凉湿后的湿冠层病害风险。请结合天气、冠层和病害地面症状判断；未达到病害阈值时只复查，达到阈值后才做 targeted fungicide。",
        actions=(
            ScenarioAction(
                "mid",
                "fungicide",
                22,
                32,
                liters_per_ridge=3.0,
            ),
        ),
        zones=(("coolwet_flowering_22_32", 22, 32), ("reference_0_15", 0, 15)),
        waits={"emergence": 15, "r1": 24, "mid": 24, "r5": 25, "harvest": 42},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_coolwet_flowering_disease_risk", ""
        )
        or None,
    )
)


add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_grain_moisture_sensor_failure_harvest",
        class_name="ScenarioFullSeasonHBGrainMoistureSensorFailureHarvest",
        slug="hb_grain_moisture_sensor_failure_harvest",
        profile_name="harbin_l3_grain_moisture_sensor_failure_harvest_seed_1736",
        cultivar="黑农84标准密度/籽粒水分传感器异常收获决策",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        description="收获窗口中关键水分读数不可靠，需要用替代观测和分区状态确认。",
        briefing_text="任务：管理收获窗口中籽粒水分读数不可靠的场景。请结合天气、田间成熟、分区状态和替代检查确认收获窗口，不要只依赖单一水分读数。",
        postharvest_market={
            "name": "moisture_sensor_crosscheck",
            "quality_discount_wet": 0.03,
        },
        zones=(("whole_field_moisture_crosscheck", 0, 63),),
        harvest_zones=(
            ("west_crosscheck_0_31", 0, 31),
            ("east_crosscheck_32_63", 32, 63),
        ),
        postharvest_drying_zones=('west_crosscheck_0_31', 'east_crosscheck_32_63'),
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 42},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_grain_moisture_sensor_failure_harvest", ""
        )
        or None,
    )
)


add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_storage_aeration_failure_after_harvest",
        class_name="ScenarioFullSeasonHBStorageAerationFailureAfterHarvest",
        slug="hb_storage_aeration_failure_after_harvest",
        profile_name="harbin_l3_storage_aeration_failure_after_harvest_seed_1737",
        cultivar="黑农84标准密度/储藏通风故障水分边缘风险",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        description="收获后储藏通风能力不足，水分边缘批次需要先干燥或延迟入库。",
        briefing_text="任务：管理收获后储藏通风能力不足的粮食处理。请检查库存、水分和储藏能力，优先干燥或分批入库低风险批次。",
        postharvest_market={
            "name": "aeration_failure_proxy",
            "storage_capacity_kg": 9500.0,
            "max_storage_moisture_pct": 13.0,
        },
        zones=(("whole_field_aeration_risk", 0, 63),),
        harvest_zones=(
            ("lower_moisture_0_31", 0, 31),
            ("edge_moisture_32_63", 32, 63),
        ),
        postharvest_drying_zones=('lower_moisture_0_31', 'edge_moisture_32_63'),
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 34},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_storage_aeration_failure_after_harvest", ""
        )
        or None,
    )
)


add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_dryer_breakdown_between_batches",
        class_name="ScenarioFullSeasonHBDryerBreakdownBetweenBatches",
        slug="hb_dryer_breakdown_between_batches",
        profile_name="harbin_l3_dryer_breakdown_between_batches_seed_1738",
        cultivar="黑农84标准密度/烘干机批次间故障",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000},
        description="第一批处理后烘干能力下降，后续批次不能立即同量烘干，需要调整收获/等待/储藏。",
        briefing_text="任务：管理分批收获中烘干能力突然受限的场景。请根据批次水分、天气和可用烘干/储藏能力调整后续收获和处理。",
        postharvest_market={
            "name": "dryer_breakdown_proxy",
            "drying_capacity_kg_per_day": 5200.0,
            "max_storage_moisture_pct": 13.5,
        },
        zones=(("west_batch_0_31", 0, 31), ("east_batch_32_63", 32, 63)),
        harvest_zones=(
            ("west_batch_0_31", 0, 31),
            ("east_batch_32_63", 32, 63),
        ),
        postharvest_drying_zones=('west_batch_0_31', 'east_batch_32_63'),
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 33},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_dryer_breakdown_between_batches", ""
        )
        or None,
    )
)


add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_heinong58_drought_insect_diagnosis_waterlimit",
        class_name="ScenarioFullSeasonHBHeinong58DroughtInsectDiagnosisWaterlimit",
        slug="hb_heinong58_drought_insect_diagnosis_waterlimit",
        profile_name="harbin_l3_hn58_drought_insect_waterlimit_seed_1801",
        cultivar="黑农58抗逆/水分虫害诊断",
        primary_seed="HEINONG58",
        seed_stocks={"HEINONG58": 1000000},
        initial_vwc=0.28,
        management_regime={"irrigation_quota_mm_total": 0.8},
        hydraulic_modifiers=((8, 23, MODERATE_LOW_HOLDING_DRY_PATCH),),
        description=(
            "黑农58抗逆品种在R5/R6附近遇到局部水分下降，同时另一区域出现虫害压力；"
            "重点是区分水分胁迫与虫害，并在有限水量下只处理有证据的区域。"
        ),
        briefing_text=(
            "任务：管理哈尔滨黑农58大豆全季生产，季节中后期可能出现水分或虫害相关异常。"
            "请根据天气、土壤水分、冠层、NDVI和地面虫害检查判断原因；水量有限，"
            "不要在没有root-zone水分胁迫证据时全田灌溉，也不要在虫害未达阈值时喷药。"
        ),
        actions=(
            ScenarioAction(
                "r5",
                "irrigation",
                8,
                23,
                hours=0.55,
            ),
            ScenarioAction(
                "r5",
                "insecticide",
                40,
                55,
                liters_per_ridge=3.5,
                target_wait_days=4,
            ),
        ),
        zones=(
            ("dry_priority_8_23", 8, 23),
            ("insect_risk_40_55", 40, 55),
            ("reference_24_35", 24, 35),
        ),
        harvest_zones=(
            ("early_west_0_7", 0, 7),
            ("early_rest_24_63", 24, 63),
            ("dry_priority_8_23", 8, 23),
        ),
        harvest_zone_waits={"dry_priority_8_23": 20},
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 30},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_heinong58_drought_insect_diagnosis_waterlimit", ""
        )
        or None,
    )
)


add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_heinong60_highdensity_wetjune_weed_disease",
        class_name="ScenarioFullSeasonHBHeinong60HighdensityWetjuneWeedDisease",
        slug="hb_heinong60_highdensity_wetjune_weed_disease",
        profile_name="harbin_l3_hn60_highdensity_wetjune_weed_disease_seed_1802",
        cultivar="黑农60高密度/湿六月草病诊断",
        primary_seed="HEINONG60",
        seed_stocks={"HEINONG60": 1000000},
        density_target_plants_m2=27.0,
        planting_zones=(
            PlantingZone(
                "whole_field_high_density",
                0,
                63,
                "HEINONG60",
                HN60_HIGH_DENSITY_SPACING_CM,
                0,
            ),
        ),
        description=(
            "黑农60高密度冠层在湿六月后出现两个不同异常区：一个以杂草竞争为主，"
            "另一个以病害压力为主；oracle必须区分草害与病害，不能全田统一处理。"
        ),
        briefing_text=(
            "任务：管理哈尔滨黑农60高密度大豆田。湿润天气后请通过传感器、无人机和地面检查"
            "区分杂草覆盖、病害迹象、土壤水分和作物长势；只有证据支持时才对相应区域控草或杀菌。"
        ),
        actions=(
            ScenarioAction(
                "emergence",
                "mechanical_weed",
                4,
                23,
                target_wait_days=12,
            ),
            ScenarioAction(
                "mid",
                "fungicide",
                40,
                55,
                liters_per_ridge=3.8,
                target_wait_days=4,
            ),
        ),
        zones=(
            ("weed_dominant_4_23", 4, 23),
            ("disease_dominant_40_55", 40, 55),
            ("reference_24_35", 24, 35),
        ),
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 39},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_heinong60_highdensity_wetjune_weed_disease", ""
        )
        or None,
    )
)


add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_heike71_lateplanting_laterain_highmoisture_quality",
        class_name="ScenarioFullSeasonHBHeike71LateplantingLaterainHighmoistureQuality",
        slug="hb_heike71_lateplanting_laterain_highmoisture_quality",
        profile_name="harbin_l3_heike71_lateplant_laterain_quality_seed_1803",
        cultivar="黑科71晚播/晚雨高水分品质",
        primary_seed="HEIKE71",
        seed_stocks={"HEIKE71": 1000000},
        start_date="2026-05-15",
        postharvest_market={
            "name": "heike71_lateplant_high_moisture_quality",
            "quality_discount_wet": 0.045,
            "max_storage_moisture_pct": 13.0,
        },
        description=(
            "黑科71早熟品种用于晚播压缩季节，后期存在降雨和高籽粒水分风险；"
            "核心是遵守R8/harvest gate、天气窗口、烘干和安全入库。"
        ),
        briefing_text=(
            "任务：管理晚播黑科71大豆全季生产。请持续检查GDD、生育期、天气预报和籽粒水分；"
            "只有作物达到可收获状态且天气允许时才能收获，水分高于安全入库目标时必须先烘干再入库。"
        ),
        planting_zones=(
            PlantingZone("whole_field_late_heike71", 0, 63, "HEIKE71", 8.4, 0),
        ),
        zones=(("whole_field_late_heike71", 0, 63),),
        harvest_zones=(("whole_field_late_heike71", 0, 63),),
        postharvest_drying_zones=('whole_field_late_heike71',),
        waits={"emergence": 14, "r1": 22, "mid": 18, "r5": 22, "harvest": 25},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_heike71_lateplanting_laterain_highmoisture_quality",
            "",
        )
        or None,
    )
)


add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_heihe43_early_density_weed_nutrient_recovery",
        class_name="ScenarioFullSeasonHBHeihe43EarlyDensityWeedNutrientRecovery",
        slug="hb_heihe43_early_density_weed_nutrient_recovery",
        profile_name="harbin_l3_heihe43_early_density_weed_nutrient_seed_1901",
        cultivar="黑河43密度/早期养分草害恢复",
        primary_seed="HEIHE43",
        seed_stocks={"HEIHE43": 2000000},
        density_target_plants_m2=22.436,
        planting_zones=(
            PlantingZone(
                "whole_field_heihe43_recommended_density",
                0,
                63,
                "HEIHE43",
                HEIHE43_RECOMMENDED_SPACING_CM,
                0,
            ),
        ),
        custom_histories=(
            (
                PriorFieldHistoryPreset(
                    name="heihe43_local_nutrient_weakness",
                    nutrient_index_delta=-0.18,
                    stand_fraction_delta=-0.02,
                ),
                8,
                19,
            ),
        ),
        description=(
            "黑河43按品种推荐密度建苗，早期同时存在局部营养偏弱和局部杂草竞争。"
            "场景重点是先用出苗、stand、养分、草害和土壤水分证据区分原因，再分别做局部水肥和控草恢复。"
        ),
        briefing_text=(
            "任务：管理哈尔滨黑河43大豆全季生产，并按黑河43推荐密度播种。"
            "出苗后若发现局部长势或冠层异常，请先检查stand、密度、养分、杂草、NDVI和土壤水分，"
            "只对有证据支持的区域做补肥/水肥或控草，参考区继续监测。"
        ),
        actions=(
            ScenarioAction(
                "emergence",
                "fertigation",
                8,
                19,
                amount=0.24,
                water_mm=1.2,
                target_wait_days=4,
            ),
            ScenarioAction(
                "emergence",
                "herbicide",
                44,
                55,
                liters_per_ridge=2.4,
                target_wait_days=5,
            ),
        ),
        zones=(
            ("nutrient_weak_8_19", 8, 19),
            ("weed_competition_44_55", 44, 55),
            ("reference_24_35", 24, 35),
        ),
        postharvest_drying_zones=('whole_field',),
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 13},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_heihe43_early_density_weed_nutrient_recovery", ""
        )
        or None,
    )
)


add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_heinong58_resistant_biotic_water_budget_priority",
        class_name="ScenarioFullSeasonHBHeinong58ResistantBioticWaterBudgetPriority",
        slug="hb_heinong58_resistant_biotic_water_budget_priority",
        profile_name="harbin_l3_hn58_resistant_biotic_water_budget_seed_1902",
        cultivar="黑农84/黑农58抗性水量预算对比",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000, "HEINONG58": 1000000},
        management_regime={"irrigation_quota_mm_total": 1.4},
        hydraulic_modifiers=((0, 31, MODERATE_LOW_HOLDING_DRY_PATCH),),
        planting_zones=(
            PlantingZone(
                "zone_a_heinong84_standard", 0, 31, "HEINONG84", HEINONG84_SPACING_CM, 0
            ),
            PlantingZone("zone_b_heinong58_resistant", 32, 63, "HEINONG58", 8.4, 0),
        ),
        description=(
            "同一田块分区种植黑农84和抗逆黑农58，在湿润生物压力窗口和后期水量受限窗口下比较分区响应。"
            "场景重点是阈值化病虫害处理和水量预算优先级，而不是把两个品种按同一规则全田处理。"
        ),
        briefing_text=(
            "任务：管理黑农84与黑农58分区大豆田。请按分区检查病害、虫害、土壤水分、water_stress、"
            "生育期和可用水量；只有达到阈值的区域才喷药，只有水分胁迫、阶段和水量预算都支持时才局部灌溉。"
        ),
        actions=(
            ScenarioAction(
                "mid",
                "fungicide",
                0,
                31,
                liters_per_ridge=3.7,
                target_wait_days=5,
            ),
            ScenarioAction(
                "r5",
                "irrigation",
                0,
                31,
                hours=0.55,
                target_wait_days=4,
            ),
        ),
        zones=(
            ("zone_a_heinong84_standard_0_31", 0, 31),
            ("zone_b_heinong58_resistant_32_63", 32, 63),
        ),
        harvest_zones=(
            ("zone_b_heinong58_resistant_32_63", 32, 63),
            ("zone_a_heinong84_standard_0_31", 0, 31),
        ),
        harvest_zone_waits={"zone_a_heinong84_standard_0_31": 27},
        postharvest_drying_zones=('zone_b_heinong58_resistant_32_63', 'zone_a_heinong84_standard_0_31'),
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 21},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_heinong58_resistant_biotic_water_budget_priority",
            "",
        )
        or None,
    )
)


add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_three_cultivar_wet_disease_dry_harvest_sequence",
        class_name="ScenarioFullSeasonHBThreeCultivarWetDiseaseDryHarvestSequence",
        slug="hb_three_cultivar_wet_disease_dry_harvest_sequence",
        profile_name="harbin_l3_three_cultivar_wet_disease_dry_harvest_seed_1904",
        cultivar="黑河50/黑农84/黑农58三品种病水收获序列",
        primary_seed="HEINONG84",
        seed_stocks={"HEIHE50": 1000000, "HEINONG84": 1000000, "HEINONG58": 1000000},
        management_regime={"irrigation_quota_mm_total": 1.4},
        hydraulic_modifiers=((43, 63, MODERATE_LOW_HOLDING_DRY_PATCH),),
        planting_zones=(
            PlantingZone("zone_a_heihe50", 0, 20, "HEIHE50", HEIHE50_SPACING_CM, 0),
            PlantingZone(
                "zone_b_heinong84", 21, 42, "HEINONG84", HEINONG84_SPACING_CM, 0
            ),
            PlantingZone("zone_c_heinong58", 43, 63, "HEINONG58", 8.4, 0),
        ),
        description=(
            "同一田块分区种植黑河50、黑农84和黑农58；湿六月病害主要考验黑农84区，"
            "后期转干主要考验黑农58区的水分预算决策，最终还要按各区R8和籽粒水分分批收获。"
        ),
        briefing_text=(
            "任务：管理三品种分区大豆田。请按区追踪GDD、生育期、病害压力、土壤水分、water_stress、"
            "籽粒水分和天气窗口；喷药、灌溉和收获都必须根据对应分区的工具返回执行，不能因为一个区需要处理就全田统一操作。"
        ),
        actions=(
            ScenarioAction(
                "mid",
                "fungicide",
                21,
                42,
                liters_per_ridge=3.8,
                target_wait_days=5,
            ),
            ScenarioAction(
                "r5",
                "irrigation",
                43,
                63,
                hours=0.8,
                target_wait_days=4,
            ),
        ),
        zones=(
            ("zone_a_heihe50_0_20", 0, 20),
            ("zone_b_heinong84_21_42", 21, 42),
            ("zone_c_heinong58_43_63", 43, 63),
        ),
        harvest_zones=(
            ("zone_a_heihe50_0_20", 0, 20),
            ("zone_b_heinong84_21_42", 21, 42),
            ("zone_c_heinong58_43_63", 43, 63),
        ),
        harvest_zone_waits={"zone_b_heinong84_21_42": 14, "zone_c_heinong58_43_63": 12},
        postharvest_drying_zones=('zone_a_heihe50_0_20', 'zone_b_heinong84_21_42', 'zone_c_heinong58_43_63'),
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 16},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_three_cultivar_wet_disease_dry_harvest_sequence",
            "",
        )
        or None,
    )
)


add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_heinong60_highdensity_fertigation_irrigation_water_budget",
        class_name="ScenarioFullSeasonHBHeinong60HighdensityFertigationIrrigationWaterBudget",
        slug="hb_heinong60_highdensity_fertigation_irrigation_water_budget",
        profile_name="harbin_l3_hn60_highdensity_fertigation_irrigation_budget_seed_1905",
        cultivar="黑农60高密度/水肥与灌溉共享水预算",
        primary_seed="HEINONG60",
        seed_stocks={"HEINONG60": 1000000},
        density_target_plants_m2=27.0,
        initial_vwc=0.29,
        management_regime={"irrigation_quota_mm_total": 1.1},
        hydraulic_modifiers=((40, 55, LOW_HOLDING_DRY_PATCH),),
        planting_zones=(
            PlantingZone(
                "whole_field_high_density_hn60",
                0,
                63,
                "HEINONG60",
                HN60_HIGH_DENSITY_SPACING_CM,
                0,
            ),
        ),
        custom_histories=(
            (
                PriorFieldHistoryPreset(
                    name="hn60_local_nutrient_stress",
                    nutrient_index_delta=-0.18,
                    stand_fraction_delta=-0.02,
                ),
                8,
                19,
            ),
        ),
        description=(
            "黑农60高密度田中，早期局部营养弱区需要水肥恢复，后期另一区域进入水分胁迫；"
            "fertigation和irrigation消耗同一有限水预算，oracle必须把水用在有证据的区域。"
        ),
        briefing_text=(
            "任务：管理哈尔滨黑农60高密度大豆全季生产。请根据出苗、营养、土壤水分、"
            "冠层和NDVI判断是否需要水肥或灌溉；水量有限，水肥也会消耗同一水预算，"
            "不要在没有足够处理依据时全田水肥或全田灌溉。"
        ),
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_heinong60_highdensity_fertigation_irrigation_water_budget",
            "",
        )
        or None,
        actions=(
            ScenarioAction(
                "emergence",
                "fertigation",
                8,
                19,
                amount=0.24,
                water_mm=2.0,
                target_wait_days=4,
            ),
            ScenarioAction(
                "r5",
                "irrigation",
                40,
                55,
                hours=0.55,
            ),
        ),
        zones=(
            ("nutrient_priority_8_19", 8, 19),
            ("water_priority_40_55", 40, 55),
            ("reference_24_35", 24, 35),
        ),
        harvest_zones=(
            ("early_ready_0_39", 0, 39),
            ("early_ready_56_63", 56, 63),
            ("water_priority_40_55", 40, 55),
        ),
        harvest_zone_waits={"water_priority_40_55": 20},
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 50},
    )
)


add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_hn84_hn58_low_chemical_disease_insect_budget",
        class_name="ScenarioFullSeasonHBHn84Hn58LowChemicalDiseaseInsectBudget",
        slug="hb_hn84_hn58_low_chemical_disease_insect_budget",
        profile_name="harbin_l3_hn84_hn58_lowchemical_disease_insect_budget_seed_1906",
        cultivar="黑农84/黑农58低化学病虫预算",
        primary_seed="HEINONG84",
        seed_stocks={"HEINONG84": 1000000, "HEINONG58": 1000000},
        management_regime={
            "regime": "low_chemical",
            "max_fungicide_applications": 1,
            "max_insecticide_applications": 1,
            "active_ingredient_cap_kg": 0.24,
        },
        planting_zones=(
            PlantingZone(
                "zone_a_heinong84", 0, 31, "HEINONG84", HEINONG84_SPACING_CM, 0
            ),
            PlantingZone("zone_b_heinong58", 32, 63, "HEINONG58", 8.4, 0),
        ),
        description=(
            "低化学投入条件下，黑农84区和黑农58区面对湿六月病害和后期虫害的压力不同；"
            "oracle需要按品种抗性和证据强度保留有限喷药预算。"
        ),
        briefing_text=(
            "任务：管理哈尔滨黑农84/黑农58分区大豆田，目标是低化学投入下完成全季管理。"
            "请按分区检查病害、虫害、作物长势和喷药窗口；化学处理预算有限，"
            "只有已有足够处理依据且值得消耗预算时才targeted喷药。"
        ),
        actions=(
            ScenarioAction(
                "mid",
                "fungicide",
                0,
                31,
                liters_per_ridge=3.2,
                target_wait_days=4,
            ),
            ScenarioAction(
                "r5",
                "insecticide",
                8,
                23,
                liters_per_ridge=3.0,
            ),
        ),
        zones=(
            ("zone_a_heinong84_standard_0_7", 0, 7),
            ("zone_b_heinong58_resistant_32_63", 32, 63),
            ("later_insect_priority_8_23", 8, 23),
            ("zone_a_heinong84_reference_24_31", 24, 31),
        ),
        harvest_zones=(
            ("zone_b_heinong58_resistant_32_63", 32, 63),
            ("zone_a_heinong84_standard_0_31", 0, 31),
        ),
        harvest_zone_waits={"zone_a_heinong84_standard_0_31": 13},
        postharvest_drying_zones=('zone_b_heinong58_resistant_32_63',),
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 24},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_hn84_hn58_low_chemical_disease_insect_budget", ""
        )
        or None,
    )
)


add(
    ScenarioSpec(
        scenario_id="scenario_full_season_hb_heinong58_water_chemical_priority_under_dual_stress",
        class_name="ScenarioFullSeasonHBHeinong58WaterChemicalPriorityUnderDualStress",
        slug="hb_heinong58_water_chemical_priority_under_dual_stress",
        profile_name="harbin_l3_hn58_water_chemical_priority_dual_stress_seed_1907",
        cultivar="黑农58水量/化学预算双压力优先级",
        primary_seed="HEINONG58",
        seed_stocks={"HEINONG58": 1000000},
        initial_vwc=0.285,
        management_regime={
            "irrigation_quota_mm_total": 0.72,
            "max_fungicide_applications": 1,
            "active_ingredient_cap_kg": 0.07,
        },
        hydraulic_modifiers=((8, 23, LOW_HOLDING_DRY_PATCH),),
        description=(
            "全田黑农58在R5/R6附近同时出现局部中等水分胁迫和局部病害压力；"
            "由于水量和化学处理预算都有限，oracle需要判断哪个压力更值得立即消耗资源。"
        ),
        briefing_text=(
            "任务：管理哈尔滨黑农58大豆全季生产。黑农58有一定抗逆性，请通过土壤水分、"
            "water_stress、冠层、病害/虫害迹象和预算状态判断是否应继续观察或优先处理。"
            "水量和化学处理预算都有限，不能全田灌溉或全田喷药。"
        ),
        actions=(
            ScenarioAction(
                "r5",
                "irrigation",
                8,
                23,
                hours=0.55,
            ),
            ScenarioAction(
                "r5",
                "fungicide",
                40,
                55,
                liters_per_ridge=3.0,
                target_wait_days=4,
            ),
        ),
        zones=(
            ("moderate_water_stress_8_23", 8, 23),
            ("biotic_priority_40_55", 40, 55),
            ("reference_24_35", 24, 35),
        ),
        harvest_zones=(
            ("early_ready_0_7", 0, 7),
            ("early_ready_24_63", 24, 63),
            ("moderate_water_stress_8_23", 8, 23),
        ),
        harvest_zone_waits={"moderate_water_stress_8_23": 18},
        waits={"emergence": 15, "r1": 24, "mid": 18, "r5": 24, "harvest": 34},
        detailed_briefing_text=get_detailed_briefing(
            "scenario_full_season_hb_heinong58_water_chemical_priority_under_dual_stress",
            "",
        )
        or None,
    )
)


def get_spec(slug: str) -> ScenarioSpec:
    return SPECS[slug]
