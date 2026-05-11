from __future__ import annotations

from datetime import datetime, timezone

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import (
    DroneApp,
    FarmWorldApp,
    FieldOpsApp,
    RobotApp,
    SensorApp,
    TractorApp,
    WeatherApp,
)
from are.simulation.apps.system import SystemApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.fos.evaluation import append_fos_evaluation
from are.simulation.scenarios.fos.gates import GateSpec
from are.simulation.scenarios.fos.predicates import after_observation
from are.simulation.scenarios.workflow_validation import append_workflow_evaluation
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

# ---------------------------------------------------------------------------
# Farm-ARE action/tick/observation boundary
# ---------------------------------------------------------------------------
# This scenario follows the boundary we want in the implementation:
#
# 1. Direct farm actions update the relevant physical state immediately enough
#    for the operation to be meaningful. Examples:
#       plant_seeds      -> planting action, seed depth, stand/phenology init
#       irrigate         -> water delivery/action record/soil-water input
#       apply_fertilizer -> nutrient management effect
#       spray_pesticide  -> treatment action + residual treatment state
#       harvest          -> recovered-yield pass/inventory action
#
# 2. Elapsed time updates are not hidden in arbitrary tools. They should happen
#    through SystemApp time advancement, scheduled notification/wait events, or
#    the simulator clock/tick that ARE already uses between events.
#
# 3. Observations read physics state through the observation layer. Drone,
#    sensor, and robot tools should not expose hidden ground truth directly.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Physics version note
# ---------------------------------------------------------------------------
# This file is derived from the corresponding uploaded baseline scenario and
# intentionally preserves the original task scope, prompt style, and oracle
# event-flow shape.
#
# The main change is semantic and architectural: existing operation tools
# own their direct physical effects, while explicit clock/tick utilities own
# elapsed-time evolution. This keeps the scenario close to the baseline while
# making the physics contract clear.
#
# Boundary used here:
#   action tools  -> direct operational effects and action-history records
#   time/tick     -> weather, soil drying/wetting, GDD, growth, biotic evolution
#   observations -> noisy/sparse/latent-state readings exposed to the agent
# ---------------------------------------------------------------------------


# 64 ridges, 4 per pass, 16 successful passes total
# seeds_per_ridge ≈ 268m * 2 rows * 100 / 5cm = 10720
# 64 ridges * 10720 = 686080 seeds total
# hopper max = 300000, so planting follows a 7+7+4 call pattern:
# batches 1 and 2 each end with one failed pass due to seed depletion.
_SEEDS_PER_LOAD = 300000
_SEED_TYPE = "STANDARD"
_DEPTH_CM = 4.0
_SPACING_CM = 5.0


@register_scenario("scenario_farm_world_planting_physics_action_tick")
class ScenarioFarmWorldPlantingPhysicsActionTick(Scenario):
    """
    Physics-aware action/tick variant of the baseline scenario. The oracle sequence stays close to the original; action tools apply direct physical effects, and elapsed-time effects are handled by explicit time/tick mechanisms.

    Planting 64 ridges of soybean after field prep is complete.

    A realistic planting shift: the agent must check conditions, load seeds,
    plant in 4-ridge batches, reload seeds when the hopper runs low, monitor
    fuel, and handle the full 64-ridge field. Field prep (level, base_fertilize,
    form_ridges) is already done before this scenario starts.
    """

    # 2026-04-28 07:00 CST (UTC+8) — 3 days after field prep
    start_time: float | None = (
        datetime(2026, 4, 28, 7, 0, 0, tzinfo=timezone.utc).timestamp() - 8 * 3600
    )
    duration: float | None = 36000
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = False  # planting only, harvest is months later

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        aui = AgentUserInterface()
        farm_world = FarmWorldApp()
        weather = WeatherApp()
        sensor = SensorApp(farm_world_app=farm_world)
        mavic = DroneApp(
            farm_world_app=farm_world,
            weather_app=weather,
            name="Mavic3M",
            description="DJI Mavic 3 Multispectral — multispectral imaging drone for NDVI vegetation index mapping",
            speed_ms=5.0,
            effective_ridges_per_pass=7,
            battery_pct_per_ridge=1.0,
        )
        matrice = DroneApp(
            farm_world_app=farm_world,
            weather_app=weather,
            name="Matrice4T",
            description="DJI Matrice 4T — thermal imaging drone for canopy temperature and stress detection",
            speed_ms=4.0,
            effective_ridges_per_pass=5,
            battery_pct_per_ridge=1.5,
        )
        robot_0 = RobotApp(
            farm_world_app=farm_world,
            weather_app=weather,
            name="Robot0",
            description="Zhiyuan D1 Max #1 — ground-level pest/disease inspection robot",
        )
        robot_1 = RobotApp(
            farm_world_app=farm_world,
            weather_app=weather,
            name="Robot1",
            description="Zhiyuan D1 Max #2 — ground-level pest/disease inspection robot",
        )
        tractor = TractorApp(farm_world_app=farm_world, weather_app=weather)
        field_ops = FieldOpsApp(farm_world_app=farm_world, weather_app=weather)
        system = SystemApp()

        self.apps = [
            aui,
            farm_world,
            weather,
            sensor,
            mavic,
            matrice,
            robot_0,
            robot_1,
            tractor,
            field_ops,
            system,
        ]
        self._configure_initial_state()
        self._configure_physics_layers()

    def _configure_initial_state(self) -> None:
        farm_world = self.get_typed_app(FarmWorldApp)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        tractor = self.get_typed_app(TractorApp)

        # Field prep already done
        tractor._completed_prep_ops = ["level", "base_fertilize", "form_ridges"]

        # Planting-ready weather: warm, dry, low wind
        weather.set_weather(
            date="2026-04-28",
            temp_c=18.0,
            humidity_pct=50.0,
            wind_speed_ms=2.5,
            rainfall_mm=0.0,
            solar_radiation=480.0,
            forecast=[
                {
                    "date": "2026-04-29",
                    "temp_c": 19.0,
                    "humidity_pct": 45.0,
                    "wind_speed_ms": 2.0,
                    "rainfall_mm": 0.0,
                    "solar_radiation": 500.0,
                },
                {
                    "date": "2026-04-30",
                    "temp_c": 16.0,
                    "humidity_pct": 65.0,
                    "wind_speed_ms": 4.0,
                    "rainfall_mm": 5.0,
                    "solar_radiation": 250.0,
                },
                {
                    "date": "2026-05-01",
                    "temp_c": 14.0,
                    "humidity_pct": 70.0,
                    "wind_speed_ms": 6.0,
                    "rainfall_mm": 12.0,
                    "solar_radiation": 180.0,
                },
            ],
            avg_soil_vwc=0.24,
        )

        farm_world.set_season_phase("planting")

        # Soil: VWC 0.22-0.26 (within 0.20-0.30 planting window), temp > 10°C
        for i in range(64):
            r = farm_world.get_ridge(i)
            r.soil_vwc = 0.22 + (i % 5) * 0.01
            r.soil_temp_c = 12.0 + (i % 4) * 0.5

        # Tractor starts with 80L fuel (not full — farmer must check)
        tractor._fuel_tank_l = 80.0

        self._sync_sensors(farm_world, sensor)

    def _sync_sensors(self, farm_world: FarmWorldApp, sensor: SensorApp) -> None:
        for s in sensor.get_state()["soil_sensors"]:
            sid, rs, re = s["sensor_id"], s["ridge_start"], s["ridge_end"]
            ridges = [farm_world.get_ridge(r) for r in range(rs, re + 1)]
            avg_vwc = sum(r.soil_vwc for r in ridges) / len(ridges)
            avg_temp = sum(r.soil_temp_c for r in ridges) / len(ridges)
            sensor.update_soil_sensor(sid, avg_vwc, avg_temp)

    def _configure_physics_layers(self) -> None:
        """Attach soil/phenology/management physics to the original planting setup.

        Physics intent:
            Preserve the original planting scenario: field prep is already done,
            the agent checks weather and soil, then plants 64 ridges with hopper
            reloads. The action-effect change is that successful plant_seeds() should
            initialize management-effect and phenology state instead of only
            setting planted=True.

        Implementation choice:
            No new oracle step is added for this direct action. The existing plant_seeds() tool should
            internally create:
              - planting_quality from seed depth, spacing, soil readiness, and
                row/ridge alignment;
              - stand_fraction for the canopy/biomass model;
              - phenology state PLANTED_PRE_EMERGENCE with seed type STANDARD;
              - management action record for later sequence-level evaluation.
        """
        farm_world = self.get_typed_app(FarmWorldApp)
        try:
            farm_world.configure_physics_profile(
                profile_name="physics_planting_ready_seedbed",
                location="Harbin/Heilongjiang",
                scenario_type="planting",
                seed_type=_SEED_TYPE,
            )
        except AttributeError:
            pass

        for i in range(64):
            r = farm_world.get_ridge(i)
            r.physics_top_vwc = getattr(r, "soil_vwc", 0.24)
            r.physics_top_temp_c = getattr(r, "soil_temp_c", 12.0)
            r.physics_planting_ready = (
                0.20 <= r.physics_top_vwc <= 0.30
                and r.physics_top_temp_c >= 10.0
            )
            r.phenology_stage = "NOT_PLANTED"
            r.accumulated_gdd = 0.0
            r.effective_development_gdd = 0.0
            r.management_stand_fraction = 0.0

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        tractor = self.get_typed_app(TractorApp)
        farm_world = self.get_typed_app(FarmWorldApp)

        # --- Two briefing versions ---
        if self.detailed_briefing:
            briefing_text = (
                "整地已完成（平整、施基肥、起垄），今天开始播种大豆。\n"
                "请按以下步骤操作：\n"
                "1. 查看今天天气，确认无雨；"
                "2. 读取土壤传感器，确认VWC在0.20-0.30之间、"
                "土壤温度>10°C（适合播种）。\n"
                "3. 检查拖拉机油量（当前80L，不满）和料斗状态。\n"
                "4. 查看仓库种子库存。\n"
                "5. 装载第一批种子（料斗最大30万株）。\n"
                "6. 逐批播种，每次4条垄（如0-3, 4-7, ...）。"
                "播深4cm，株距5cm。每条垄约消耗10720株种子。\n"
                "7. 第一批种子能成功播完前24垄，尝试播24-27时会因种子不足失败，"
                "需要补装第二批后重试。\n"
                "8. 第二批能继续成功播到47垄，尝试播48-51时会再次因种子不足失败，"
                "需要补装第三批后完成剩余48-63垄。\n"
                "9. 全部64垄播完后向我汇报。"
            )
        else:
            briefing_text = (
                "整地已完成，今天开始播种。"
                "务必今天种完全部64垄。完成后告诉我。"
            )

        with EventRegisterer.capture_mode():
            # --- Briefing ---
            briefing = (
                aui.send_message_to_agent(content=briefing_text)
                .with_id("planting_briefing")
                .depends_on(None, delay_seconds=5)
            )

            # --- Pre-planting checks ---
            o_weather = (
                weather.get_current_weather()
                .oracle()
                .with_id("o_check_weather")
                .depends_on(briefing, delay_seconds=2)
            )
            o_soil = (
                sensor.read_soil_sensors()
                .oracle()
                .with_id("o_read_soil")
                .depends_on(o_weather, delay_seconds=1)
            )
            o_tractor = (
                tractor.get_status()
                .oracle()
                .with_id("o_check_tractor")
                .depends_on(o_soil, delay_seconds=1)
            )
            o_inventory = (
                farm_world.get_inventory()
                .oracle()
                .with_id("o_check_inventory")
                .depends_on(o_tractor, delay_seconds=1)
            )

            # --- Load seeds (1st batch) ---
            o_load1 = (
                tractor.load_seeds(_SEED_TYPE, _SEEDS_PER_LOAD)
                .oracle()
                .with_id("o_load_seeds_1")
                .depends_on(o_inventory, delay_seconds=2)
            )

            # --- Batch 1: six successful passes, then one failed pass at 24-27. ---
            prev = o_load1
            batch1_events = []
            for i in range(7):
                start = i * 4
                end = start + 3
                o_plant = (
                    tractor.plant_seeds(start, end, _DEPTH_CM, _SPACING_CM)
                    .oracle()
                    .with_id(f"o_plant_b1_{start}_{end}")
                    .depends_on(prev, delay_seconds=2)
                )
                batch1_events.append(o_plant)
                prev = o_plant

            # Hopper empty → refill (2nd load).
            o_load2 = (
                tractor.load_seeds(_SEED_TYPE, _SEEDS_PER_LOAD)
                .oracle()
                .with_id("o_load_seeds_2")
                .depends_on(prev, delay_seconds=2)
            )

            # --- Batch 2: retry 24-27, continue to 44-47, then fail at 48-51. ---
            prev = o_load2
            batch2_events = []
            for i in range(7):
                start = 24 + i * 4
                end = start + 3
                o_plant = (
                    tractor.plant_seeds(start, end, _DEPTH_CM, _SPACING_CM)
                    .oracle()
                    .with_id(f"o_plant_b2_{start}_{end}")
                    .depends_on(prev, delay_seconds=2)
                )
                batch2_events.append(o_plant)
                prev = o_plant

            # Hopper empty again → 3rd load.
            o_load3 = (
                tractor.load_seeds(_SEED_TYPE, _SEEDS_PER_LOAD)
                .oracle()
                .with_id("o_load_seeds_3")
                .depends_on(prev, delay_seconds=2)
            )

            # --- Batch 3: retry 48-51, then finish 52-63. ---
            prev = o_load3
            batch3_events = []
            for i in range(4):
                start = 48 + i * 4
                end = start + 3
                o_plant = (
                    tractor.plant_seeds(start, end, _DEPTH_CM, _SPACING_CM)
                    .oracle()
                    .with_id(f"o_plant_b3_{start}_{end}")
                    .depends_on(prev, delay_seconds=2)
                )
                batch3_events.append(o_plant)
                prev = o_plant

            # --- Final report ---
            o_report = (
                aui.send_message_to_user(content="64垄大豆播种全部完成。")
                .oracle()
                .with_id("o_report")
                .depends_on(prev, delay_seconds=2)
            )

        self.events = [
            briefing,
            o_weather,
            o_soil,
            o_tractor,
            o_inventory,
            o_load1,
            *batch1_events,   # 7 calls: 6 succeed, last fails (24-27)
            o_load2,
            *batch2_events,   # 7 calls: retry 24-27, then fail at 48-51
            o_load3,
            *batch3_events,   # 4 successful passes: 48-63
            o_report,
        ]

    def _gates(self) -> list[GateSpec]:
        return [
            GateSpec(name="G1_observe_conditions", intent="agent reads weather + soil before planting",
                window_days=(0.0, 1.0),
                eligible_tools=[("WeatherApp", "get_current_weather"), ("SensorApp", "read_soil_sensors")]),
            GateSpec(name="G2_load_seeds", intent="load seeds before planting",
                window_days=(0.0, 1.0),
                eligible_tools=[("TractorApp", "load_seeds")]),
            GateSpec(name="G3_plant", intent="plant seeds in valid window",
                window_days=(0.0, 1.0),
                eligible_tools=[("TractorApp", "plant_seeds")],
                requires=after_observation("TractorApp", "load_seeds")),
        ]

    def validate(self, env) -> ScenarioValidationResult:
        result = ScenarioValidationResult(
            success=True,
            rationale="round-1+2 physics action-tick",
        )
        result = append_workflow_evaluation(self, env, result)
        result = append_fos_evaluation(self, env, result, gates=self._gates())
        return result
