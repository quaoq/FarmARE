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
from are.simulation.scenarios.fos import GateSpec, append_fos_evaluation
from are.simulation.scenarios.fos.predicates import (
    after_any_of,
    after_observation,
    and_,
    arg_equals,
    max_arg,
    min_arg,
    or_,
    targets_ridges_overlap,
)
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.workflow_validation import append_workflow_evaluation
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.scenarios.validation_result import ScenarioValidationResult
from are.simulation.types import EventRegisterer

# NOTE:
# These are physics-aware scenario scaffolds. They intentionally reference
# several tools that may not exist yet in the current FarmWorld apps.
# Assumed tools are marked inline. The goal is to define oracle structure
# and scenario logic first, then update the apps/tools to support them.

_DISEASE_START = 34
_DISEASE_END = 46

@register_scenario("scenario_physics_disease_after_rain_fungicide")
class ScenarioPhysicsDiseaseAfterRainFungicide(Scenario):
    """
    L2 episode: disease risk after wet period.

    Objective:
        After repeated rainfall and wet topsoil, the oracle distinguishes fungal
        disease risk from drought/pest stress, waits for a sprayable window, and
        applies fungicide before the disease pressure causes sustained biomass loss.
    """

    start_time: float | None = (
        datetime(2026, 7, 12, 8, 0, 0, tzinfo=timezone.utc).timestamp() - 8 * 3600
    )
    duration: float | None = 2 * 24 * 3600
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    expects_agent_harvest: bool = False  # mid-season disease decision episode

    def init_and_populate_apps(self, *args, **kwargs) -> None:
        aui = AgentUserInterface()
        farm_world = FarmWorldApp()
        weather = WeatherApp()
        sensor = SensorApp(farm_world_app=farm_world)
        mavic = DroneApp(
            farm_world_app=farm_world,
            weather_app=weather,
            name="Mavic3M",
            description="DJI Mavic 3 Multispectral — multispectral NDVI mapping drone",
            speed_ms=5.0,
            effective_ridges_per_pass=7,
            battery_pct_per_ridge=1.0,
        )
        matrice = DroneApp(
            farm_world_app=farm_world,
            weather_app=weather,
            name="Matrice4T",
            description="DJI Matrice 4T — thermal imaging drone",
            speed_ms=4.0,
            effective_ridges_per_pass=5,
            battery_pct_per_ridge=1.5,
        )
        robot_0 = RobotApp(
            farm_world_app=farm_world,
            weather_app=weather,
            name="Robot0",
            description="Zhiyuan D1 Max #1 — ground-level inspection robot",
        )
        tractor = TractorApp(farm_world_app=farm_world, weather_app=weather)
        field_ops = FieldOpsApp(farm_world_app=farm_world, weather_app=weather)
        system = SystemApp()

        self.apps = [aui, farm_world, weather, sensor, mavic, matrice, robot_0, tractor, field_ops, system]
        self._configure_initial_state()
        farm_world.attach_system_app(system)
        self._configure_physics_layers()
    def _configure_initial_state(self) -> None:
        farm_world = self.get_typed_app(FarmWorldApp)
        weather = self.get_typed_app(WeatherApp)
        mavic = self.get_typed_app(DroneApp, "Mavic3M")
        matrice = self.get_typed_app(DroneApp, "Matrice4T")
        tractor = self.get_typed_app(TractorApp)

        weather.set_weather(
            date="2026-07-12",
            temp_c=22.0,
            humidity_pct=88.0,
            wind_speed_ms=5.5,
            rainfall_mm=3.0,
            solar_radiation=260.0,
            forecast=[
                {"date": "2026-07-13", "temp_c": 23.0, "humidity_pct": 70.0, "wind_speed_ms": 3.0, "rainfall_mm": 0.0, "solar_radiation": 480.0},
                {"date": "2026-07-14", "temp_c": 24.0, "humidity_pct": 65.0, "wind_speed_ms": 2.5, "rainfall_mm": 0.0, "solar_radiation": 500.0},
            ],
            avg_soil_vwc=0.31,
        )
        farm_world.set_season_phase("growing")
        mavic._battery_pct = 90.0
        matrice._battery_pct = 90.0
        tractor._completed_prep_ops = ["level", "base_fertilize", "form_ridges"]
        tractor._fuel_tank_l = 50.0

        for i in range(64):
            r = farm_world.get_ridge(i)
            r.planted = True
            r.seed_type = "STANDARD"
            r.days_since_planted = 72
            r.growth_stage = "R1"
            r.soil_vwc = 0.29 + (i % 3) * 0.015
            r.pest_pressure = 0.03
            if _DISEASE_START <= i <= _DISEASE_END:
                # Engine reads disease_pressure_base; legacy r.disease_pressure
                # is shadow-synced from biotic state. Set the base so the bridge
                # in _seed_physics_from_ridges seeds the biotic engine.
                r.disease_pressure_base = 0.38
                r.disease_pressure = 0.38
                r.ndvi = 0.58
                r.ndvi_proxy = 0.58
                r.canopy_temp_c = 27.5
            else:
                r.disease_pressure_base = 0.03
                r.disease_pressure = 0.03
                r.ndvi = 0.75
                r.ndvi_proxy = 0.75
                r.canopy_temp_c = 25.0

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        mavic = self.get_typed_app(DroneApp, "Mavic3M")
        matrice = self.get_typed_app(DroneApp, "Matrice4T")
        robot = self.get_typed_app(RobotApp, "Robot0")
        tractor = self.get_typed_app(TractorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        system = self.get_typed_app(SystemApp)

        if self.detailed_briefing:
            briefing_text = (
                "连续降雨后，R1期局部出现低NDVI和湿热异常。请判断是否为病害风险，不要把它当成虫害直接喷杀虫剂。\n"
                "请按以下步骤操作：\n"
                "1. 查看当前天气，判断现在是否仍有雨、风大或不适合喷施。\n"
                "2. 查看未来3天预报，寻找可喷施窗口。\n"
                "3. 读取土壤传感器，确认雨后土壤/冠层环境偏湿。\n"
                "4. 检查Mavic3M并飞行病害疑似区周边，确认低NDVI区域。\n"
                "5. 检查Matrice4T并飞行同一区域，确认湿热/热异常与病害风险一致。\n"
                "6. 检查Robot0并做地面作物健康检查，确认病害症状。\n"
                "7. 如果当前窗口不适合喷施，等待约24小时后重新查看天气。\n"
                "8. 到达可喷窗口后，检查拖拉机和杀菌剂库存，装载杀菌剂。\n"
                "9. 对34-46垄病害区分段施用杀菌剂，不要喷错为杀虫剂；完成后提交物理状态并汇报。"
            )
        else:
            briefing_text = (
                "连续降雨后R1期局部出现低NDVI和湿热异常。请判断是否为病害风险，"
                "不要在仍有雨/风大的窗口喷药；等到可喷窗口后再处理。"
            )
        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id("briefing").depends_on(None, delay_seconds=5)
            o_weather = weather.get_current_weather().oracle().with_id("o_weather_not_sprayable").depends_on(briefing, delay_seconds=2)
            o_forecast = weather.get_forecast(days=3).oracle().with_id("o_find_spray_window").depends_on(o_weather, delay_seconds=1)
            o_soil = sensor.read_soil_sensors().oracle().with_id("o_soil_wet").depends_on(o_forecast, delay_seconds=1)
            o_mavic = mavic.check_status().oracle().with_id("o_check_mavic").depends_on(o_soil, delay_seconds=1)
            o_ndvi = mavic.fly_survey(_DISEASE_START - 2, _DISEASE_END + 2).oracle().with_id("o_ndvi_disease_zone").depends_on(o_mavic, delay_seconds=2)
            o_thermal_status = matrice.check_status().oracle().with_id("o_check_thermal").depends_on(o_ndvi, delay_seconds=1)
            o_thermal = matrice.fly_survey(_DISEASE_START - 2, _DISEASE_END + 2).oracle().with_id("o_thermal_disease_zone").depends_on(o_thermal_status, delay_seconds=2)
            o_robot_status = robot.check_status().oracle().with_id("o_check_robot").depends_on(o_thermal, delay_seconds=1)
            o_ground = robot.inspect_crop_health(_DISEASE_START + 4, _DISEASE_START + 6).oracle().with_id("o_ground_confirm_disease").depends_on(o_robot_status, delay_seconds=2)

            o_wait = system.advance_time(hours=24).oracle().with_id("o_wait_until_sprayable_window").depends_on(o_ground, delay_seconds=1)
            o_weather2 = weather.get_current_weather().oracle().with_id("o_recheck_weather_sprayable").depends_on(o_wait, delay_seconds=1)
            o_tractor = tractor.get_status().oracle().with_id("o_check_tractor").depends_on(o_weather2, delay_seconds=1)
            o_inventory = farm_world.get_inventory().oracle().with_id("o_check_fungicide_inventory").depends_on(o_tractor, delay_seconds=1)

            # ASSUMED TOOL: load and apply fungicide; use separate from insecticide.
            o_load = tractor.load_fungicide(120.0).oracle().with_id("o_load_fungicide").depends_on(o_inventory, delay_seconds=2)
            # apply_fungicide max_width = 10. Disease block 34-46 is 13 ridges. Split.
            o_apply_a = tractor.apply_fungicide(_DISEASE_START, _DISEASE_START + 9, liters_per_ridge=5.0).oracle().with_id("o_apply_fungicide_a").depends_on(o_load, delay_seconds=2)
            o_apply_b = tractor.apply_fungicide(_DISEASE_START + 10, _DISEASE_END, liters_per_ridge=5.0).oracle().with_id("o_apply_fungicide_b").depends_on(o_apply_a, delay_seconds=2)
            o_commit = farm_world.commit_daily_physics().oracle().with_id("o_commit_fungicide_effect").depends_on(o_apply_b, delay_seconds=1)
            o_report = aui.send_message_to_user(content="已确认湿后病害风险，并在可喷窗口完成杀菌剂处理。").oracle().with_id("o_report").depends_on(o_commit, delay_seconds=2)

        self.events = [briefing, o_weather, o_forecast, o_soil, o_mavic, o_ndvi, o_thermal_status, o_thermal, o_robot_status, o_ground, o_wait, o_weather2, o_tractor, o_inventory, o_load, o_apply_a, o_apply_b, o_commit, o_report]

    def _configure_physics_layers(self) -> None:
        """Activate physics for this round-3 episode."""
        farm_world = self.get_typed_app(FarmWorldApp)
        farm_world.configure_physics_profile(
            profile_name="physics_disease_after_rain",
            location="Harbin/Heilongjiang",
            scenario_type="disease_after_rain_fungicide",
        )
        # Post-rain disease pressure on a localized block.
        physics = farm_world.physics
        for i in range(64):
            soil = physics.soil.states[i]
            ridge = farm_world._ridges[i]
            soil.top_vwc = float(ridge.soil_vwc)
            soil.root_vwc = float(ridge.soil_vwc)
            biotic = physics.biotic.states[i]
            biotic.disease_pressure = max(
                biotic.disease_pressure,
                float(getattr(ridge, "disease_pressure_base", 0.0)),
            )

    def _gates(self) -> list[GateSpec]:
        """FOS Decision-component gates for this episode."""
        return [
            GateSpec(
                name="G1_post_rain_weather_check",
                intent="confirm spray window after rain has passed",
                window_days=(0.0, 0.5),
                eligible_tools=[("WeatherApp", "get_current_weather")],
            ),
            GateSpec(
                name="G2_observe_field_state",
                intent="agent reads soil/canopy sensors to assess disease block",
                window_days=(0.0, 1.0),
                eligible_tools=[
                    ("SensorApp", "read_soil_sensors"),
                    ("SensorApp", "read_canopy_sensors"),
                ],
            ),
            GateSpec(
                name="G3_robot_disease_confirm",
                intent="robot confirms disease via ground inspection",
                window_days=(0.0, 1.5),
                eligible_tools=[("Robot0", "inspect_crop_health")],
            ),
            GateSpec(
                name="G4_load_fungicide",
                intent="load fungicide (not insecticide) before spray",
                window_days=(0.0, 2.0),
                eligible_tools=[("TractorApp", "load_fungicide")],
            ),
            GateSpec(
                name="G5_apply_fungicide_in_window",
                intent="apply fungicide on diseased block in dry sprayable window",
                window_days=(0.0, 2.0),
                eligible_tools=[("TractorApp", "apply_fungicide")],
                requires=after_observation("TractorApp", "load_fungicide"),
            ),
        ]

    def validate(self, env) -> ScenarioValidationResult:
        result = ScenarioValidationResult(success=True, rationale="round-3 episode")
        result = append_workflow_evaluation(self, env, result)
        result = append_fos_evaluation(self, env, result, gates=self._gates())
        return result
