from __future__ import annotations

from typing import Any

from are.simulation.apps.agent_user_interface import AgentUserInterface
from are.simulation.apps.farm_world import DroneApp, FarmWorldApp, RobotApp, SensorApp, TractorApp, WeatherApp
from are.simulation.apps.system import SystemApp
from are.simulation.scenarios.scenario import Scenario
from are.simulation.scenarios.scenario_farm_world_fullseason_v2.harbin_l3_scenario_helpers import (
    HEINONG84_SPACING_CM,
    RIDGE_WIDTH_M,
    advance_days,
    collect_event_graph,
    configure_common_field,
    harbin_start_time,
    harvest_range,
    install_common_farm_apps,
    plant_range,
    spray_blocks,
)
from are.simulation.scenarios.utils.registry import register_scenario
from are.simulation.types import EventRegisterer


SCENARIO_ID = "scenario_full_season_heinong84_low_chemical_wet_disease"
PROFILE_NAME = "harbin_heinong84_low_chemical_wet_disease_seed_818"
SEED_TYPE = "HEINONG84"
AFFECTED_START = 22
AFFECTED_END = 43
SENSOR_FLAGGED_START = 22
SENSOR_FLAGGED_END = AFFECTED_END


@register_scenario(SCENARIO_ID)
class ScenarioFullSeasonHeinong84LowChemicalWetDisease(Scenario):
    """Harbin Heinong84 low-chemical-input disease-threshold scenario."""

    start_time: float | None = harbin_start_time()
    duration: float | None = 170 * 24 * 3600
    queue_based_loop: bool = True
    time_increment_in_seconds: int = 60
    detailed_briefing: bool = True
    nb_turns: int = 1

    def init_and_populate_apps(self, *args: Any, **kwargs: Any) -> None:
        install_common_farm_apps(self, thermal=True)
        configure_common_field(
            self,
            profile_name=PROFILE_NAME,
            cultivar="黑农84标准密度/低化学投入湿六月病害阈值管理",
            seed_stocks={SEED_TYPE: 1000000},
            pesticide_liters=120.0,
            tractor_fuel_l=190.0,
            initial_vwc=0.30,
        )

    def _after_named_step(self, prev: Any, label: str) -> Any:
        return prev

    def build_events_flow(self) -> None:
        aui = self.get_typed_app(AgentUserInterface)
        weather = self.get_typed_app(WeatherApp)
        sensor = self.get_typed_app(SensorApp)
        farm_world = self.get_typed_app(FarmWorldApp)
        mavic = self.get_typed_app(DroneApp, "Mavic3M")
        matrice = self.get_typed_app(DroneApp, "Matrice4T")
        robot = self.get_typed_app(RobotApp, "Robot0")
        tractor = self.get_typed_app(TractorApp)
        system = self.get_typed_app(SystemApp)

        briefing_text = (
            "你负责完成一个哈尔滨低化学投入黑农84大豆full-season任务。按真实农事流程完成"
            "播前检查、整地、基肥、播种、出苗检查、生长期巡查和收获。病害管理必须先检查"
            "天气、土壤、NDVI/热红外和地面作物健康；低化学投入条件下，轻微风险只记录并复查。"
            "只有检查返回显示病害达到明确处理阈值、天气可喷且田间可通行时，才对异常垄段"
            "靶向fungicide，不能全田统一喷。"
        )

        with EventRegisterer.capture_mode():
            briefing = aui.send_message_to_agent(content=briefing_text).with_id("briefing").depends_on(None, delay_seconds=5)

            o_weather_0 = weather.get_current_weather().oracle().with_id("o_weather_before_prep").depends_on(briefing, delay_seconds=2)
            o_forecast_0 = weather.get_forecast(days=5).oracle().with_id("o_forecast_before_prep").depends_on(o_weather_0, delay_seconds=1)
            o_soil_0 = sensor.read_soil_sensors().oracle().with_id("o_soil_before_prep").depends_on(o_forecast_0, delay_seconds=1)
            o_inventory_0 = farm_world.get_inventory().oracle().with_id("o_inventory_before_prep").depends_on(o_soil_0, delay_seconds=1)
            o_tractor_0 = tractor.get_status().oracle().with_id("o_tractor_before_prep").depends_on(o_inventory_0, delay_seconds=1)

            o_attach_grader = tractor.attach_implement("grader").oracle().with_id("o_attach_grader").depends_on(o_tractor_0, delay_seconds=1)
            o_level = tractor.level().oracle().with_id("o_level_field").depends_on(o_attach_grader, delay_seconds=2)
            o_detach_grader = tractor.detach_implement().oracle().with_id("o_detach_grader_after_leveling").depends_on(o_level, delay_seconds=1)
            o_load_base = tractor.load_fertilizer(250.0).oracle().with_id("o_load_base_fertilizer").depends_on(o_detach_grader, delay_seconds=1)
            o_base = tractor.base_fertilize().oracle().with_id("o_apply_base_fertilizer").depends_on(o_load_base, delay_seconds=2)
            o_attach_furrower = tractor.attach_implement("furrower").oracle().with_id("o_attach_furrower_before_ridging").depends_on(o_base, delay_seconds=1)
            o_ridge = tractor.form_ridges(RIDGE_WIDTH_M).oracle().with_id("o_form_1p1m_ridges").depends_on(o_attach_furrower, delay_seconds=2)
            o_detach_furrower = tractor.detach_implement().oracle().with_id("o_detach_furrower_after_ridging").depends_on(o_ridge, delay_seconds=1)

            o_plant_weather = weather.get_current_weather().oracle().with_id("o_plant_weather_check").depends_on(o_detach_furrower, delay_seconds=1)
            o_plant_forecast = weather.get_forecast(days=3).oracle().with_id("o_plant_forecast_check").depends_on(o_plant_weather, delay_seconds=1)
            o_plant_soil = sensor.read_soil_sensors().oracle().with_id("o_plant_soil_check").depends_on(o_plant_forecast, delay_seconds=1)
            o_plant_tractor = tractor.get_status().oracle().with_id("o_tractor_before_planting").depends_on(o_plant_soil, delay_seconds=1)
            o_plant = plant_range(
                tractor,
                o_plant_tractor,
                start_ridge=0,
                end_ridge=63,
                seed_type=SEED_TYPE,
                spacing_cm=HEINONG84_SPACING_CM,
                id_prefix="o_whole_field",
            )
            o_commit_plant = farm_world.commit_daily_physics().oracle().with_id("o_commit_planting").depends_on(o_plant, delay_seconds=1)
            o_after_plant = self._after_named_step(o_commit_plant, "after_whole_field_planting")

            o_wait_emergence = advance_days(self, o_after_plant, 15, "o_wait_emergence")
            o_emergence_overview = farm_world.get_farm_overview().oracle().with_id("o_emergence_farm_overview").depends_on(o_wait_emergence, delay_seconds=1)
            o_emergence_soil = sensor.read_soil_sensors().oracle().with_id("o_emergence_soil_sensor_check").depends_on(o_emergence_overview, delay_seconds=1)
            o_emergence_canopy = sensor.read_canopy_sensors().oracle().with_id("o_emergence_canopy_sensor_check").depends_on(o_emergence_soil, delay_seconds=1)
            o_emergence_ndvi = mavic.fly_survey(0, 63).oracle().with_id("o_emergence_whole_field_ndvi_survey").depends_on(o_emergence_canopy, delay_seconds=2)
            o_charge_mavic_after_emergence = mavic.charge().oracle().with_id("o_charge_mavic_after_emergence_survey").depends_on(o_emergence_ndvi, delay_seconds=1)
            o_wait_mavic_charge_after_emergence = system.advance_time(hours=1).oracle().with_id("o_wait_mavic_charge_after_emergence_survey").depends_on(o_charge_mavic_after_emergence, delay_seconds=1)

            o_wait_risk = advance_days(self, o_wait_mavic_charge_after_emergence, 22, "o_wait_wet_june_risk")
            o_risk_weather = weather.get_current_weather().oracle().with_id("o_wet_june_risk_weather_check").depends_on(o_wait_risk, delay_seconds=1)
            o_risk_soil = sensor.read_soil_sensors().oracle().with_id("o_wet_june_risk_soil_check").depends_on(o_risk_weather, delay_seconds=1)
            o_risk_canopy = sensor.read_canopy_sensors().oracle().with_id("o_wet_june_risk_canopy_sensor_check").depends_on(o_risk_soil, delay_seconds=1)
            o_no_fungicide_commit = farm_world.commit_daily_physics().oracle().with_id("o_commit_no_fungicide_below_threshold").depends_on(o_risk_canopy, delay_seconds=1)
            o_after_no_fungicide = self._after_named_step(o_no_fungicide_commit, "after_below_threshold_disease_no_fungicide")

            o_wait_threshold = advance_days(self, o_after_no_fungicide, 15, "o_wait_disease_threshold")
            o_threshold_weather = weather.get_current_weather().oracle().with_id("o_disease_threshold_weather_check").depends_on(o_wait_threshold, delay_seconds=1)
            o_threshold_forecast = weather.get_forecast(days=3).oracle().with_id("o_disease_threshold_forecast_check").depends_on(o_threshold_weather, delay_seconds=1)
            o_threshold_soil = sensor.read_soil_sensors().oracle().with_id("o_disease_threshold_soil_check").depends_on(o_threshold_forecast, delay_seconds=1)
            o_threshold_canopy = sensor.read_canopy_sensors().oracle().with_id("o_disease_threshold_canopy_sensor_check").depends_on(o_threshold_soil, delay_seconds=1)
            o_threshold_ndvi_sensor_band = mavic.fly_survey(SENSOR_FLAGGED_START, SENSOR_FLAGGED_END).oracle().with_id("o_disease_threshold_sensor_flagged_ndvi_detail").depends_on(o_threshold_canopy, delay_seconds=2)
            o_threshold_thermal_sensor_band = matrice.fly_survey(SENSOR_FLAGGED_START, SENSOR_FLAGGED_END).oracle().with_id("o_disease_threshold_sensor_flagged_thermal").depends_on(o_threshold_ndvi_sensor_band, delay_seconds=2)
            o_charge_robot_before_threshold = robot.charge().oracle().with_id("o_charge_robot_before_threshold_ground_check").depends_on(o_threshold_thermal_sensor_band, delay_seconds=1)
            o_wait_robot_before_threshold = system.advance_time(hours=1).oracle().with_id("o_wait_robot_charge_before_threshold_ground_check").depends_on(o_charge_robot_before_threshold, delay_seconds=1)
            o_robot_status_threshold = robot.check_status().oracle().with_id("o_robot_status_before_threshold_ground_check").depends_on(o_wait_robot_before_threshold, delay_seconds=1)
            o_threshold_ground = robot.inspect_crop_health(SENSOR_FLAGGED_START, min(SENSOR_FLAGGED_START + 7, SENSOR_FLAGGED_END)).oracle().with_id("o_disease_threshold_sensor_flagged_ground_confirmed").depends_on(o_robot_status_threshold, delay_seconds=2)
            o_load_fungicide = tractor.load_fungicide(90.0).oracle().with_id("o_load_limited_fungicide").depends_on(o_threshold_ground, delay_seconds=1)
            o_spray = spray_blocks(
                tractor,
                o_load_fungicide,
                start_ridge=SENSOR_FLAGGED_START,
                end_ridge=SENSOR_FLAGGED_END,
                liters_per_ridge=3.0,
                fungicide=True,
                id_prefix="o_targeted_fungicide",
            )
            o_commit_spray = farm_world.commit_daily_physics().oracle().with_id("o_commit_targeted_fungicide").depends_on(o_spray, delay_seconds=1)
            o_after_spray = self._after_named_step(o_commit_spray, "after_targeted_fungicide")
            o_charge_before_recheck = robot.charge().oracle().with_id("o_charge_robot_before_disease_recheck").depends_on(o_after_spray, delay_seconds=1)
            o_wait_robot_charge = system.advance_time(hours=1).oracle().with_id("o_wait_robot_charge_before_disease_recheck").depends_on(o_charge_before_recheck, delay_seconds=1)
            o_recheck = robot.inspect_crop_health(SENSOR_FLAGGED_START, min(SENSOR_FLAGGED_START + 7, SENSOR_FLAGGED_END)).oracle().with_id("o_recheck_after_targeted_fungicide").depends_on(o_wait_robot_charge, delay_seconds=2)
            o_charge = robot.charge().oracle().with_id("o_charge_robot_after_disease_recheck").depends_on(o_recheck, delay_seconds=1)

            o_wait_harvest = advance_days(self, o_charge, 74, "o_wait_harvest_window")
            o_harvest_weather = weather.get_current_weather().oracle().with_id("o_harvest_weather_check").depends_on(o_wait_harvest, delay_seconds=1)
            o_harvest_forecast = weather.get_forecast(days=3).oracle().with_id("o_harvest_forecast_check").depends_on(o_harvest_weather, delay_seconds=1)
            o_harvest_soil = sensor.read_soil_sensors().oracle().with_id("o_harvest_soil_check").depends_on(o_harvest_forecast, delay_seconds=1)
            o_harvest_overview = farm_world.get_farm_overview().oracle().with_id("o_harvest_farm_overview").depends_on(o_harvest_soil, delay_seconds=1)
            o_attach = tractor.attach_implement("harvester").oracle().with_id("o_attach_harvester").depends_on(o_harvest_overview, delay_seconds=1)
            o_harvest = harvest_range(tractor, farm_world, o_attach, start_ridge=0, end_ridge=63, id_prefix="o_whole_field")
            o_after_harvest = self._after_named_step(o_harvest, "after_whole_field_harvest_store")
            o_report = aui.send_message_to_user(content="已完成低化学投入湿六月病害阈值场景：早期未喷，阈值后只对sensor异常带22-43垄靶向fungicide，并完成收获入库。").oracle().with_id("o_report").depends_on(o_after_harvest, delay_seconds=2)

            self.events = collect_event_graph(briefing)
