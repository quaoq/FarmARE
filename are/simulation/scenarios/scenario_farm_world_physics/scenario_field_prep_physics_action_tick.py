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
from are.simulation.scenarios.oracle_matching import OracleStepSpec, oracle_validate
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


_BASE_FERTILIZER_LOAD_KG = 200.0


@register_scenario("scenario_farm_world_field_prep_physics_action_tick")
class ScenarioFarmWorldFieldPrepPhysicsActionTick(Scenario):
    """
    Physics-aware action/tick variant of the baseline scenario. The oracle sequence stays close to the original; action tools apply direct physical effects, and elapsed-time effects are handled by explicit time/tick mechanisms.

    Pre-planting field preparation.

    A realistic field-prep shift: the agent must first check weather and soil
    conditions to confirm the field is workable, then complete three preparation
    steps in the correct agronomic order:
      1. level          — attach grader, rotary till and level the soil surface
      2. base_fertilize — load fertilizer from warehouse, apply across field
      3. form_ridges    — form the 64 ridge rows (1.1 m width)

    The oracle sequence includes the ground-condition checks a real farmer
    would perform before driving onto the field.
    """

    # 2026-04-25 08:00 CST (UTC+8)
    start_time: float | None = (
        datetime(2026, 4, 25, 8, 0, 0, tzinfo=timezone.utc).timestamp() - 8 * 3600
    )
    duration: float | None = 25000
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True  # Set to True for detailed instructions
    expects_agent_harvest: bool = False  # pre-planting field prep, no crop yet

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

        weather.set_weather(
            date="2026-04-25",
            temp_c=15.0,
            humidity_pct=55.0,
            wind_speed_ms=2.0,
            rainfall_mm=0.0,
            solar_radiation=420.0,
            forecast=[
                {
                    "date": "2026-04-26",
                    "temp_c": 16.5,
                    "humidity_pct": 50.0,
                    "wind_speed_ms": 3.0,
                    "rainfall_mm": 0.0,
                    "solar_radiation": 450.0,
                },
                {
                    "date": "2026-04-27",
                    "temp_c": 14.0,
                    "humidity_pct": 70.0,
                    "wind_speed_ms": 5.0,
                    "rainfall_mm": 8.0,
                    "solar_radiation": 200.0,
                },
            ],
            avg_soil_vwc=0.22,
        )
        farm_world.set_season_phase("prep")
        for i in range(64):
            r = farm_world.get_ridge(i)
            r.soil_vwc = 0.22 + ((i % 4) - 1.5) * 0.002
            r.soil_temp_c = 10.0 + (i % 3) * 0.3

        self._sync_sensors(farm_world, sensor)

    def _sync_sensors(self, farm_world: FarmWorldApp, sensor: SensorApp) -> None:
        """Push ground-truth ridge data into sensor caches."""
        for s in sensor.get_state()["soil_sensors"]:
            sid, rs, re = s["sensor_id"], s["ridge_start"], s["ridge_end"]
            ridges = [farm_world.get_ridge(r) for r in range(rs, re + 1)]
            avg_vwc = sum(r.soil_vwc for r in ridges) / len(ridges)
            avg_temp = sum(r.soil_temp_c for r in ridges) / len(ridges)
            sensor.update_soil_sensor(sid, avg_vwc, avg_temp)

    def _configure_physics_layers(self) -> None:
        """Attach physics-engine state to the original field-prep setup.

        Physics intent:
            Keep the original task and oracle order unchanged: check weather,
            forecast, soil, tractor, inventory, then level, base-fertilize, and
            form ridges.

        Implementation choice:
            No new oracle step is added for this direct action. The existing tractor tools should be
            updated internally so that:
              - level() updates surface/workability state;
              - load_fertilizer()/apply_base_fertilizer() updates the
                management-effect nutrient index;
              - form_ridges() updates the farm geometry/ridge status.

        Action/tick boundary:
                This method initializes the hidden physics state. It is not an oracle
                step. The actual scenario actions below must update direct effects,
                while elapsed-time changes come from waits/ticks.

            Pitfall:
            If these tools only mutate string flags such as completed_prep_ops,
            the physics engines will not see any management effect.
        """
        farm_world = self.get_typed_app(FarmWorldApp)
        try:
            farm_world.configure_physics_profile(
                profile_name="physics_field_prep_workable_seedbed",
                location="Harbin/Heilongjiang",
                scenario_type="field_prep",
            )
        except AttributeError:
            # Expected until FarmWorldApp exposes profile binding.
            pass

        for i in range(64):
            r = farm_world.get_ridge(i)
            r.physics_top_vwc = getattr(r, "soil_vwc", 0.22)
            r.physics_top_temp_c = getattr(r, "soil_temp_c", 10.0)
            r.physics_trafficability = "good" if r.physics_top_vwc < 0.35 else "blocked"
            r.management_nutrient_index = 0.75
            r.management_effect_tags = ["pre_planting_workable_seedbed"]

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        tractor = self.get_typed_app(TractorApp)
        farm_world = self.get_typed_app(FarmWorldApp)

        # --- Two briefing versions ---
        if self.detailed_briefing:
            briefing_text = (
                "农场进入种植前准备阶段，今天需要完成全部整地工作。\n"
                "请按以下步骤操作：\n"
                "1. 查看今天天气，确认无雨可以下地。\n"
                "2. 查看 3 天天气预报（后天有雨 → 今天必须做完）。\n"
                "3. 读取土壤传感器，确认土壤 VWC < 0.35（拖拉机可通行）。\n"
                "4. 检查拖拉机油量和挂接状态。\n"
                "5. 查看仓库库存，确认化肥和柴油充足。\n"
                "6. 平整地面：先挂接平地机，"
                "然后旋耕平整全田（平地机宽 3m，速度 3 km/h），"
                "完成后卸下平地机。\n"
                "7. 施基肥：从仓库装载 200 kg 化肥到施肥机，"
                "然后全田撒施（撒播机宽 6 m，速度 6 km/h）。\n"
                "8. 起垄：挂接开沟机，起垄（垄宽 1.1 m，4 垄/趟，速度 4 km/h），"
                "做完后卸下开沟机。\n"
                "9. 全部完成后向我汇报。"
            )
        else:
            briefing_text = (
                "要种地了，请开始种植前的准备处理。"
                "完成后告诉我。"
            )

        with EventRegisterer.capture_mode():
            # --- Briefing ---
            briefing = (
                aui.send_message_to_agent(content=briefing_text)
                .with_id("field_prep_briefing")
                .depends_on(None, delay_seconds=5)
            )

            # --- Ground checks ---
            oracle_check_weather = (
                weather.get_current_weather()
                .oracle()
                .with_id("oracle_check_weather")
                .depends_on(briefing, delay_seconds=2)
            )

            oracle_check_forecast = (
                weather.get_forecast(days=3)
                .oracle()
                .with_id("oracle_check_forecast")
                .depends_on(oracle_check_weather, delay_seconds=1)
            )

            oracle_read_soil = (
                sensor.read_soil_sensors()
                .oracle()
                .with_id("oracle_read_soil")
                .depends_on(oracle_check_forecast, delay_seconds=1)
            )

            oracle_check_tractor = (
                tractor.get_status()
                .oracle()
                .with_id("oracle_check_tractor")
                .depends_on(oracle_read_soil, delay_seconds=1)
            )

            oracle_check_inventory = (
                farm_world.get_inventory()
                .oracle()
                .with_id("oracle_check_inventory")
                .depends_on(oracle_check_tractor, delay_seconds=1)
            )

            # --- Step 1: Level ---
            oracle_attach = (
                tractor.attach_implement("grader")
                .oracle()
                .with_id("oracle_attach_grader")
                .depends_on(oracle_check_inventory, delay_seconds=2)
            )

            oracle_level = (
                tractor.level()
                .oracle()
                .with_id("oracle_level")
                .depends_on(oracle_attach, delay_seconds=2)
            )

            oracle_detach = (
                tractor.detach_implement()
                .oracle()
                .with_id("oracle_detach_implement")
                .depends_on(oracle_level, delay_seconds=1)
            )

            # --- Step 2: Base fertilize ---
            oracle_load_fertilizer = (
                tractor.load_fertilizer(_BASE_FERTILIZER_LOAD_KG)
                .oracle()
                .with_id("oracle_load_fertilizer")
                .depends_on(oracle_detach, delay_seconds=1)
            )

            oracle_fertilize = (
                tractor.base_fertilize()
                .oracle()
                .with_id("oracle_base_fertilize")
                .depends_on(oracle_load_fertilizer, delay_seconds=2)
            )

            # --- Step 3: Form ridges ---
            oracle_attach_furrower = (
                tractor.attach_implement("furrower")
                .oracle()
                .with_id("oracle_attach_furrower")
                .depends_on(oracle_fertilize, delay_seconds=2)
            )
            oracle_ridges = (
                tractor.form_ridges(1.1)
                .oracle()
                .with_id("oracle_form_ridges")
                .depends_on(oracle_attach_furrower, delay_seconds=2)
            )
            oracle_detach_furrower = (
                tractor.detach_implement()
                .oracle()
                .with_id("oracle_detach_furrower")
                .depends_on(oracle_ridges, delay_seconds=1)
            )

            # --- Report ---
            oracle_report = (
                aui.send_message_to_user(content="种植前准备完成，可以进入播种阶段。")
                .oracle()
                .with_id("oracle_report_completion")
                .depends_on(oracle_detach_furrower, delay_seconds=2)
            )

        self.events = [
            briefing,
            oracle_check_weather,
            oracle_check_forecast,
            oracle_read_soil,
            oracle_check_tractor,
            oracle_check_inventory,
            oracle_attach,
            oracle_level,
            oracle_detach,
            oracle_load_fertilizer,
            oracle_fertilize,
            oracle_attach_furrower,
            oracle_ridges,
            oracle_detach_furrower,
            oracle_report,
        ]

    def validate(self, env) -> ScenarioValidationResult:
        step_specs = [
            OracleStepSpec(function_name="get_current_weather", class_name="WeatherApp"),
            OracleStepSpec(function_name="get_forecast", class_name="WeatherApp"),
            OracleStepSpec(function_name="read_soil_sensors", class_name="SensorApp"),
            OracleStepSpec(function_name="get_status", class_name="TractorApp"),
            OracleStepSpec(function_name="get_inventory", class_name="FarmWorldApp"),
            OracleStepSpec(function_name="attach_implement", class_name="TractorApp", penalty_if_repeated=0.05),
            OracleStepSpec(function_name="level", class_name="TractorApp", penalty_if_repeated=0.1),
            OracleStepSpec(function_name="detach_implement", class_name="TractorApp", penalty_if_repeated=0.05),
            OracleStepSpec(function_name="load_fertilizer", class_name="TractorApp", penalty_if_repeated=0.05),
            OracleStepSpec(function_name="base_fertilize", class_name="TractorApp", penalty_if_repeated=0.1),
            OracleStepSpec(function_name="attach_implement", class_name="TractorApp", penalty_if_repeated=0.05),
            OracleStepSpec(function_name="form_ridges", class_name="TractorApp", penalty_if_repeated=0.1),
            OracleStepSpec(function_name="detach_implement", class_name="TractorApp", penalty_if_repeated=0.05),
            OracleStepSpec(function_name="send_message_to_user", class_name="AgentUserInterface", penalty_if_repeated=0.05),
        ]
        result = oracle_validate(
            scenario=self,
            env=env,
            step_specs=step_specs,
            success_threshold=0.8,
            harmless_extra_penalty=0.02,
        )
        result = append_workflow_evaluation(self, env, result)
        result = append_fos_evaluation(self, env, result, gates=self._gates())
        return result

    def _gates(self) -> list[GateSpec]:
        return [
            GateSpec(name="G1_check_status", intent="agent checks tractor status",
                window_days=(0.0, 1.0),
                eligible_tools=[("TractorApp", "get_status")]),
            GateSpec(name="G2_level", intent="level field",
                window_days=(0.0, 1.0),
                eligible_tools=[("TractorApp", "level")]),
            GateSpec(name="G3_form_ridges", intent="form ridges after leveling",
                window_days=(0.0, 1.0),
                eligible_tools=[("TractorApp", "form_ridges")],
                requires=after_observation("TractorApp", "level")),
        ]
