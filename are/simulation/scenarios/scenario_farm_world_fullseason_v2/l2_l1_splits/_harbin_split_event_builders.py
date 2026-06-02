from __future__ import annotations

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
from are.simulation.scenarios.scenario_farm_world_fullseason_v2.harbin_l3_scenario_helpers import (
    harvest_range,
    plant_range,
    spray_blocks,
)
from are.simulation.types import EventRegisterer

from ._harbin_split_common import prep_and_plant_whole_field


def build_preplant_l2_events(
    scenario,
    *,
    briefing_text: str,
    report_text: str,
    prefix: str,
    seed_type: str = "HEINONG84",
    seed_spacing_cm: float = 7.9,
) -> list[object]:
    aui = scenario.get_typed_app(AgentUserInterface)
    weather = scenario.get_typed_app(WeatherApp)
    sensor = scenario.get_typed_app(SensorApp)
    farm_world = scenario.get_typed_app(FarmWorldApp)
    tractor = scenario.get_typed_app(TractorApp)

    with EventRegisterer.capture_mode():
        briefing = aui.send_message_to_agent(content=briefing_text).with_id(
            "briefing"
        ).depends_on(None, delay_seconds=5)
        o_weather = weather.get_current_weather().oracle().with_id(
            f"{prefix}_weather_before_prep"
        ).depends_on(briefing, delay_seconds=1)
        o_forecast = weather.get_forecast(days=3).oracle().with_id(
            f"{prefix}_forecast_before_prep"
        ).depends_on(o_weather, delay_seconds=1)
        o_soil = sensor.read_soil_sensors().oracle().with_id(
            f"{prefix}_soil_before_prep"
        ).depends_on(o_forecast, delay_seconds=1)
        o_inventory = farm_world.get_inventory().oracle().with_id(
            f"{prefix}_inventory_before_prep"
        ).depends_on(o_soil, delay_seconds=1)
        o_tractor = tractor.get_status().oracle().with_id(
            f"{prefix}_tractor_before_prep"
        ).depends_on(o_inventory, delay_seconds=1)
        o_planted = prep_and_plant_whole_field(
            tractor,
            o_tractor,
            prefix=prefix,
            seed_type=seed_type,
            seed_spacing_cm=seed_spacing_cm,
        )
        o_commit = farm_world.commit_daily_physics().oracle().with_id(
            f"{prefix}_commit_planting_physics"
        ).depends_on(o_planted, delay_seconds=2)
        o_recheck = farm_world.get_farm_overview().oracle().with_id(
            f"{prefix}_recheck_planted_overview"
        ).depends_on(o_commit, delay_seconds=1)
        o_report = aui.send_message_to_user(content=report_text).oracle().with_id(
            "o_report"
        ).depends_on(o_recheck, delay_seconds=2)

    return [
        briefing,
        o_weather,
        o_forecast,
        o_soil,
        o_inventory,
        o_tractor,
        o_planted,
        o_commit,
        o_recheck,
        o_report,
    ]


def build_planting_l1_events(
    scenario,
    *,
    briefing_text: str,
    report_text: str,
    prefix: str,
    seed_type: str = "HEINONG84",
    seed_spacing_cm: float = 7.9,
) -> list[object]:
    aui = scenario.get_typed_app(AgentUserInterface)
    weather = scenario.get_typed_app(WeatherApp)
    sensor = scenario.get_typed_app(SensorApp)
    farm_world = scenario.get_typed_app(FarmWorldApp)
    tractor = scenario.get_typed_app(TractorApp)

    with EventRegisterer.capture_mode():
        briefing = aui.send_message_to_agent(content=briefing_text).with_id(
            "briefing"
        ).depends_on(None, delay_seconds=5)
        o_weather = weather.get_current_weather().oracle().with_id(
            f"{prefix}_weather_check"
        ).depends_on(briefing, delay_seconds=1)
        o_forecast = weather.get_forecast(days=3).oracle().with_id(
            f"{prefix}_forecast_check"
        ).depends_on(o_weather, delay_seconds=1)
        o_soil = sensor.read_soil_sensors().oracle().with_id(
            f"{prefix}_soil_check"
        ).depends_on(o_forecast, delay_seconds=1)
        o_tractor = tractor.get_status().oracle().with_id(
            f"{prefix}_tractor_before_planting"
        ).depends_on(o_soil, delay_seconds=1)
        o_planted = plant_range(
            tractor,
            o_tractor,
            start_ridge=0,
            end_ridge=63,
            seed_type=seed_type,
            spacing_cm=seed_spacing_cm,
            id_prefix=prefix,
        )
        o_commit = farm_world.commit_daily_physics().oracle().with_id(
            f"{prefix}_commit_planting_physics"
        ).depends_on(o_planted, delay_seconds=2)
        o_recheck = farm_world.get_farm_overview().oracle().with_id(
            f"{prefix}_recheck_planted_overview"
        ).depends_on(o_commit, delay_seconds=1)
        o_report = aui.send_message_to_user(content=report_text).oracle().with_id(
            "o_report"
        ).depends_on(o_recheck, delay_seconds=2)

    return [
        briefing,
        o_weather,
        o_forecast,
        o_soil,
        o_tractor,
        o_planted,
        o_commit,
        o_recheck,
        o_report,
    ]


def build_irrigation_l2_events(
    scenario,
    *,
    briefing_text: str,
    report_text: str,
    start_ridge: int,
    end_ridge: int,
    reference_start: int,
    reference_end: int,
    hours: float,
    wait_days: int,
) -> list[object]:
    aui = scenario.get_typed_app(AgentUserInterface)
    weather = scenario.get_typed_app(WeatherApp)
    sensor = scenario.get_typed_app(SensorApp)
    farm_world = scenario.get_typed_app(FarmWorldApp)
    mavic = scenario.get_typed_app(DroneApp, "Mavic3M")
    matrice = scenario.get_typed_app(DroneApp, "Matrice300")
    robot = scenario.get_typed_app(RobotApp, "Robot0")
    field_ops = scenario.get_typed_app(FieldOpsApp)
    system = scenario.get_typed_app(SystemApp)

    with EventRegisterer.capture_mode():
        briefing = aui.send_message_to_agent(content=briefing_text).with_id(
            "briefing"
        ).depends_on(None, delay_seconds=5)
        o_weather = weather.get_current_weather().oracle().with_id(
            "o_check_r5_weather"
        ).depends_on(briefing, delay_seconds=1)
        o_forecast = weather.get_forecast(days=3).oracle().with_id(
            "o_check_r5_forecast"
        ).depends_on(o_weather, delay_seconds=1)
        o_soil = sensor.read_soil_sensors().oracle().with_id(
            "o_check_r5_soil_water"
        ).depends_on(o_forecast, delay_seconds=1)
        o_canopy = sensor.read_canopy_sensors().oracle().with_id(
            "o_check_r5_canopy_temperature"
        ).depends_on(o_soil, delay_seconds=1)
        o_overview = farm_world.get_farm_overview().oracle().with_id(
            "o_check_r5_field_overview"
        ).depends_on(o_canopy, delay_seconds=1)
        o_target = farm_world.get_ridge_range_state(start_ridge, end_ridge).oracle().with_id(
            "o_read_priority_patch_state"
        ).depends_on(o_overview, delay_seconds=1)
        o_reference = farm_world.get_ridge_range_state(reference_start, reference_end).oracle().with_id(
            "o_read_reference_patch_state"
        ).depends_on(o_target, delay_seconds=1)
        o_ndvi = mavic.fly_survey(start_ridge, end_ridge).oracle().with_id(
            "o_target_ndvi_survey"
        ).depends_on(o_reference, delay_seconds=2)
        o_thermal = matrice.fly_survey(start_ridge, end_ridge).oracle().with_id(
            "o_target_thermal_survey"
        ).depends_on(o_ndvi, delay_seconds=2)
        o_ground = robot.inspect_crop_health(start_ridge, end_ridge).oracle().with_id(
            "o_ground_confirm_water_stress"
        ).depends_on(o_thermal, delay_seconds=2)
        o_wait = system.advance_time(days=wait_days).oracle().with_id(
            "o_wait_for_irrigation_window"
        ).depends_on(o_ground, delay_seconds=1)
        o_weather_ready = weather.get_current_weather().oracle().with_id(
            "o_recheck_irrigation_weather"
        ).depends_on(o_wait, delay_seconds=1)
        o_state_ready = farm_world.get_ridge_range_state(start_ridge, end_ridge).oracle().with_id(
            "o_recheck_priority_patch_before_irrigation"
        ).depends_on(o_weather_ready, delay_seconds=1)
        o_inventory = farm_world.get_inventory().oracle().with_id(
            "o_check_water_budget_before_irrigation"
        ).depends_on(o_state_ready, delay_seconds=1)
        o_irrigate = field_ops.irrigate(start_ridge, end_ridge, hours).oracle().with_id(
            f"o_irrigate_{start_ridge}_{end_ridge}"
        ).depends_on(o_inventory, delay_seconds=2)
        o_response = system.advance_time(hours=6).oracle().with_id(
            "o_wait_irrigation_response"
        ).depends_on(o_irrigate, delay_seconds=1)
        o_recheck = farm_world.get_ridge_range_state(start_ridge, end_ridge).oracle().with_id(
            "o_recheck_priority_patch_after_irrigation"
        ).depends_on(o_response, delay_seconds=1)
        o_report = aui.send_message_to_user(content=report_text).oracle().with_id(
            "o_report"
        ).depends_on(o_recheck, delay_seconds=2)

    return [
        briefing,
        o_weather,
        o_forecast,
        o_soil,
        o_canopy,
        o_overview,
        o_target,
        o_reference,
        o_ndvi,
        o_thermal,
        o_ground,
        o_wait,
        o_weather_ready,
        o_state_ready,
        o_inventory,
        o_irrigate,
        o_response,
        o_recheck,
        o_report,
    ]


def build_irrigation_l1_events(
    scenario,
    *,
    briefing_text: str,
    report_text: str,
    start_ridge: int,
    end_ridge: int,
    hours: float,
) -> list[object]:
    aui = scenario.get_typed_app(AgentUserInterface)
    weather = scenario.get_typed_app(WeatherApp)
    sensor = scenario.get_typed_app(SensorApp)
    farm_world = scenario.get_typed_app(FarmWorldApp)
    field_ops = scenario.get_typed_app(FieldOpsApp)
    system = scenario.get_typed_app(SystemApp)

    with EventRegisterer.capture_mode():
        briefing = aui.send_message_to_agent(content=briefing_text).with_id(
            "briefing"
        ).depends_on(None, delay_seconds=5)
        o_weather = weather.get_current_weather().oracle().with_id(
            "o_recheck_irrigation_weather"
        ).depends_on(briefing, delay_seconds=1)
        o_soil = sensor.read_soil_sensors().oracle().with_id(
            "o_recheck_irrigation_soil"
        ).depends_on(o_weather, delay_seconds=1)
        o_state = farm_world.get_ridge_range_state(start_ridge, end_ridge).oracle().with_id(
            "o_recheck_irrigation_target"
        ).depends_on(o_soil, delay_seconds=1)
        o_inventory = farm_world.get_inventory().oracle().with_id(
            "o_recheck_water_budget"
        ).depends_on(o_state, delay_seconds=1)
        o_irrigate = field_ops.irrigate(start_ridge, end_ridge, hours).oracle().with_id(
            f"o_irrigate_{start_ridge}_{end_ridge}"
        ).depends_on(o_inventory, delay_seconds=2)
        o_response = system.advance_time(hours=6).oracle().with_id(
            "o_wait_irrigation_response"
        ).depends_on(o_irrigate, delay_seconds=1)
        o_recheck = farm_world.get_ridge_range_state(start_ridge, end_ridge).oracle().with_id(
            "o_recheck_after_irrigation"
        ).depends_on(o_response, delay_seconds=1)
        o_report = aui.send_message_to_user(content=report_text).oracle().with_id(
            "o_report"
        ).depends_on(o_recheck, delay_seconds=2)

    return [briefing, o_weather, o_soil, o_state, o_inventory, o_irrigate, o_response, o_recheck, o_report]


def build_fungicide_l2_events(
    scenario,
    *,
    briefing_text: str,
    report_text: str,
    start_ridge: int,
    end_ridge: int,
    reference_start: int,
    reference_end: int,
    liters_per_ridge: float,
    wait_days: int,
) -> list[object]:
    aui = scenario.get_typed_app(AgentUserInterface)
    weather = scenario.get_typed_app(WeatherApp)
    sensor = scenario.get_typed_app(SensorApp)
    farm_world = scenario.get_typed_app(FarmWorldApp)
    mavic = scenario.get_typed_app(DroneApp, "Mavic3M")
    robot = scenario.get_typed_app(RobotApp, "Robot0")
    tractor = scenario.get_typed_app(TractorApp)
    system = scenario.get_typed_app(SystemApp)

    total_liters = round((end_ridge - start_ridge + 1) * liters_per_ridge + 1.1, 1)
    with EventRegisterer.capture_mode():
        briefing = aui.send_message_to_agent(content=briefing_text).with_id(
            "briefing"
        ).depends_on(None, delay_seconds=5)
        o_weather = weather.get_current_weather().oracle().with_id(
            "o_check_disease_weather"
        ).depends_on(briefing, delay_seconds=1)
        o_forecast = weather.get_forecast(days=3).oracle().with_id(
            "o_check_disease_forecast"
        ).depends_on(o_weather, delay_seconds=1)
        o_soil = sensor.read_soil_sensors().oracle().with_id(
            "o_check_spray_trafficability"
        ).depends_on(o_forecast, delay_seconds=1)
        o_canopy = sensor.read_canopy_sensors().oracle().with_id(
            "o_check_disease_canopy"
        ).depends_on(o_soil, delay_seconds=1)
        o_overview = farm_world.get_farm_overview().oracle().with_id(
            "o_check_disease_field_overview"
        ).depends_on(o_canopy, delay_seconds=1)
        o_target = farm_world.get_ridge_range_state(start_ridge, end_ridge).oracle().with_id(
            "o_read_disease_target_state"
        ).depends_on(o_overview, delay_seconds=1)
        o_reference = farm_world.get_ridge_range_state(reference_start, reference_end).oracle().with_id(
            "o_read_reference_state"
        ).depends_on(o_target, delay_seconds=1)
        o_survey = mavic.fly_survey(start_ridge, end_ridge).oracle().with_id(
            "o_target_ndvi_survey"
        ).depends_on(o_reference, delay_seconds=2)
        o_ground = robot.inspect_crop_health(start_ridge, end_ridge).oracle().with_id(
            "o_ground_confirm_disease"
        ).depends_on(o_survey, delay_seconds=2)
        o_wait = system.advance_time(days=wait_days).oracle().with_id(
            "o_wait_for_spray_window"
        ).depends_on(o_ground, delay_seconds=1)
        o_weather_ready = weather.get_current_weather().oracle().with_id(
            "o_recheck_spray_weather"
        ).depends_on(o_wait, delay_seconds=1)
        o_soil_ready = sensor.read_soil_sensors().oracle().with_id(
            "o_recheck_spray_soil"
        ).depends_on(o_weather_ready, delay_seconds=1)
        o_state_ready = farm_world.get_ridge_range_state(start_ridge, end_ridge).oracle().with_id(
            "o_recheck_disease_target_before_spray"
        ).depends_on(o_soil_ready, delay_seconds=1)
        o_tractor = tractor.get_status().oracle().with_id(
            "o_check_sprayer_status"
        ).depends_on(o_state_ready, delay_seconds=1)
        o_load = tractor.load_fungicide(total_liters).oracle().with_id(
            "o_load_fungicide"
        ).depends_on(o_tractor, delay_seconds=1)
        o_spray = spray_blocks(
            tractor,
            o_load,
            start_ridge=start_ridge,
            end_ridge=end_ridge,
            liters_per_ridge=liters_per_ridge,
            fungicide=True,
            id_prefix="o_apply_targeted_fungicide",
        )
        o_recheck = farm_world.get_ridge_range_state(start_ridge, end_ridge).oracle().with_id(
            "o_recheck_disease_after_spray"
        ).depends_on(o_spray, delay_seconds=2)
        o_report = aui.send_message_to_user(content=report_text).oracle().with_id(
            "o_report"
        ).depends_on(o_recheck, delay_seconds=2)

    return [
        briefing,
        o_weather,
        o_forecast,
        o_soil,
        o_canopy,
        o_overview,
        o_target,
        o_reference,
        o_survey,
        o_ground,
        o_wait,
        o_weather_ready,
        o_soil_ready,
        o_state_ready,
        o_tractor,
        o_load,
        o_spray,
        o_recheck,
        o_report,
    ]


def build_fungicide_l1_events(
    scenario,
    *,
    briefing_text: str,
    report_text: str,
    start_ridge: int,
    end_ridge: int,
    liters_per_ridge: float,
) -> list[object]:
    aui = scenario.get_typed_app(AgentUserInterface)
    weather = scenario.get_typed_app(WeatherApp)
    sensor = scenario.get_typed_app(SensorApp)
    farm_world = scenario.get_typed_app(FarmWorldApp)
    tractor = scenario.get_typed_app(TractorApp)

    total_liters = round((end_ridge - start_ridge + 1) * liters_per_ridge + 1.1, 1)
    with EventRegisterer.capture_mode():
        briefing = aui.send_message_to_agent(content=briefing_text).with_id(
            "briefing"
        ).depends_on(None, delay_seconds=5)
        o_weather = weather.get_current_weather().oracle().with_id(
            "o_recheck_spray_weather"
        ).depends_on(briefing, delay_seconds=1)
        o_soil = sensor.read_soil_sensors().oracle().with_id(
            "o_recheck_spray_soil"
        ).depends_on(o_weather, delay_seconds=1)
        o_state = farm_world.get_ridge_range_state(start_ridge, end_ridge).oracle().with_id(
            "o_recheck_disease_target"
        ).depends_on(o_soil, delay_seconds=1)
        o_tractor = tractor.get_status().oracle().with_id(
            "o_check_sprayer_status"
        ).depends_on(o_state, delay_seconds=1)
        o_load = tractor.load_fungicide(total_liters).oracle().with_id(
            "o_load_fungicide"
        ).depends_on(o_tractor, delay_seconds=1)
        o_spray = spray_blocks(
            tractor,
            o_load,
            start_ridge=start_ridge,
            end_ridge=end_ridge,
            liters_per_ridge=liters_per_ridge,
            fungicide=True,
            id_prefix="o_apply_targeted_fungicide",
        )
        o_recheck = farm_world.get_ridge_range_state(start_ridge, end_ridge).oracle().with_id(
            "o_recheck_disease_after_spray"
        ).depends_on(o_spray, delay_seconds=2)
        o_report = aui.send_message_to_user(content=report_text).oracle().with_id(
            "o_report"
        ).depends_on(o_recheck, delay_seconds=2)

    return [briefing, o_weather, o_soil, o_state, o_tractor, o_load, o_spray, o_recheck, o_report]


def build_whole_harvest_l2_events(
    scenario,
    *,
    briefing_text: str,
    report_text: str,
    wait_days: int,
    dry_after_harvest: bool = True,
) -> list[object]:
    aui = scenario.get_typed_app(AgentUserInterface)
    weather = scenario.get_typed_app(WeatherApp)
    sensor = scenario.get_typed_app(SensorApp)
    farm_world = scenario.get_typed_app(FarmWorldApp)
    tractor = scenario.get_typed_app(TractorApp)
    system = scenario.get_typed_app(SystemApp)

    with EventRegisterer.capture_mode():
        briefing = aui.send_message_to_agent(content=briefing_text).with_id(
            "briefing"
        ).depends_on(None, delay_seconds=5)
        o_weather = weather.get_current_weather().oracle().with_id(
            "o_check_r8_weather"
        ).depends_on(briefing, delay_seconds=1)
        o_forecast = weather.get_forecast(days=3).oracle().with_id(
            "o_check_r8_forecast"
        ).depends_on(o_weather, delay_seconds=1)
        o_soil = sensor.read_soil_sensors().oracle().with_id(
            "o_check_harvest_soil"
        ).depends_on(o_forecast, delay_seconds=1)
        o_state = farm_world.get_ridge_range_state(0, 63).oracle().with_id(
            "o_check_maturity_and_moisture"
        ).depends_on(o_soil, delay_seconds=1)
        o_inventory = farm_world.get_inventory().oracle().with_id(
            "o_check_harvest_storage_resources"
        ).depends_on(o_state, delay_seconds=1)
        o_wait = system.advance_time(days=wait_days).oracle().with_id(
            "o_wait_to_harvest_window"
        ).depends_on(o_inventory, delay_seconds=1)
        o_weather_ready = weather.get_current_weather().oracle().with_id(
            "o_recheck_harvest_weather"
        ).depends_on(o_wait, delay_seconds=1)
        o_soil_ready = sensor.read_soil_sensors().oracle().with_id(
            "o_recheck_harvest_soil"
        ).depends_on(o_weather_ready, delay_seconds=1)
        o_state_ready = farm_world.get_ridge_range_state(0, 63).oracle().with_id(
            "o_recheck_harvest_moisture"
        ).depends_on(o_soil_ready, delay_seconds=1)
        o_capacity = farm_world.get_inventory().oracle().with_id(
            "o_recheck_storage_capacity"
        ).depends_on(o_state_ready, delay_seconds=1)
        o_harvest = harvest_range(
            tractor,
            farm_world,
            o_capacity,
            start_ridge=0,
            end_ridge=63,
            id_prefix="o_whole_field",
            dry_after_harvest=dry_after_harvest,
        )
        o_recheck = farm_world.get_inventory().oracle().with_id(
            "o_recheck_stored_grain"
        ).depends_on(o_harvest, delay_seconds=2)
        o_report = aui.send_message_to_user(content=report_text).oracle().with_id(
            "o_report"
        ).depends_on(o_recheck, delay_seconds=2)

    return [
        briefing,
        o_weather,
        o_forecast,
        o_soil,
        o_state,
        o_inventory,
        o_wait,
        o_weather_ready,
        o_soil_ready,
        o_state_ready,
        o_capacity,
        o_harvest,
        o_recheck,
        o_report,
    ]


def build_harvest_l1_events(
    scenario,
    *,
    briefing_text: str,
    report_text: str,
    start_ridge: int,
    end_ridge: int,
    id_prefix: str,
    dry_after_harvest: bool = True,
) -> list[object]:
    aui = scenario.get_typed_app(AgentUserInterface)
    weather = scenario.get_typed_app(WeatherApp)
    sensor = scenario.get_typed_app(SensorApp)
    farm_world = scenario.get_typed_app(FarmWorldApp)
    tractor = scenario.get_typed_app(TractorApp)

    with EventRegisterer.capture_mode():
        briefing = aui.send_message_to_agent(content=briefing_text).with_id(
            "briefing"
        ).depends_on(None, delay_seconds=5)
        o_weather = weather.get_current_weather().oracle().with_id(
            f"{id_prefix}_harvest_weather"
        ).depends_on(briefing, delay_seconds=1)
        o_forecast = weather.get_forecast(days=3).oracle().with_id(
            f"{id_prefix}_harvest_forecast"
        ).depends_on(o_weather, delay_seconds=1)
        o_soil = sensor.read_soil_sensors().oracle().with_id(
            f"{id_prefix}_harvest_soil"
        ).depends_on(o_forecast, delay_seconds=1)
        o_state = farm_world.get_ridge_range_state(start_ridge, end_ridge).oracle().with_id(
            f"{id_prefix}_harvest_range_state"
        ).depends_on(o_soil, delay_seconds=1)
        o_capacity = farm_world.get_inventory().oracle().with_id(
            f"{id_prefix}_storage_capacity"
        ).depends_on(o_state, delay_seconds=1)
        o_harvest = harvest_range(
            tractor,
            farm_world,
            o_capacity,
            start_ridge=start_ridge,
            end_ridge=end_ridge,
            id_prefix=id_prefix,
            dry_after_harvest=dry_after_harvest,
        )
        o_recheck = farm_world.get_inventory().oracle().with_id(
            f"{id_prefix}_recheck_inventory"
        ).depends_on(o_harvest, delay_seconds=2)
        o_report = aui.send_message_to_user(content=report_text).oracle().with_id(
            "o_report"
        ).depends_on(o_recheck, delay_seconds=2)

    return [briefing, o_weather, o_forecast, o_soil, o_state, o_capacity, o_harvest, o_recheck, o_report]


def build_storage_two_batch_l2_events(
    scenario,
    *,
    briefing_text: str,
    report_text: str,
    wait_days: int,
) -> list[object]:
    aui = scenario.get_typed_app(AgentUserInterface)
    weather = scenario.get_typed_app(WeatherApp)
    sensor = scenario.get_typed_app(SensorApp)
    farm_world = scenario.get_typed_app(FarmWorldApp)
    tractor = scenario.get_typed_app(TractorApp)
    system = scenario.get_typed_app(SystemApp)

    with EventRegisterer.capture_mode():
        briefing = aui.send_message_to_agent(content=briefing_text).with_id(
            "briefing"
        ).depends_on(None, delay_seconds=5)
        o_weather = weather.get_current_weather().oracle().with_id(
            "o_check_storage_harvest_weather"
        ).depends_on(briefing, delay_seconds=1)
        o_forecast = weather.get_forecast(days=3).oracle().with_id(
            "o_check_storage_harvest_forecast"
        ).depends_on(o_weather, delay_seconds=1)
        o_state = farm_world.get_ridge_range_state(0, 63).oracle().with_id(
            "o_check_whole_field_moisture"
        ).depends_on(o_forecast, delay_seconds=1)
        o_inventory = farm_world.get_inventory().oracle().with_id(
            "o_check_capacity_before_wait"
        ).depends_on(o_state, delay_seconds=1)
        o_wait = system.advance_time(days=wait_days).oracle().with_id(
            "o_wait_to_batch_harvest_window"
        ).depends_on(o_inventory, delay_seconds=1)
        o_soil = sensor.read_soil_sensors().oracle().with_id(
            "o_recheck_batch_harvest_soil"
        ).depends_on(o_wait, delay_seconds=1)
        o_west_state = farm_world.get_ridge_range_state(0, 31).oracle().with_id(
            "o_recheck_west_batch_state"
        ).depends_on(o_soil, delay_seconds=1)
        o_capacity_west = farm_world.get_inventory().oracle().with_id(
            "o_recheck_capacity_before_west_batch"
        ).depends_on(o_west_state, delay_seconds=1)
        o_west = harvest_range(
            tractor,
            farm_world,
            o_capacity_west,
            start_ridge=0,
            end_ridge=31,
            id_prefix="o_west_0_31",
            dry_after_harvest=True,
        )
        o_capacity_after_west = farm_world.get_inventory().oracle().with_id(
            "o_recheck_capacity_after_west_batch"
        ).depends_on(o_west, delay_seconds=2)
        o_east_state = farm_world.get_ridge_range_state(32, 63).oracle().with_id(
            "o_recheck_east_batch_state"
        ).depends_on(o_capacity_after_west, delay_seconds=1)
        o_east = harvest_range(
            tractor,
            farm_world,
            o_east_state,
            start_ridge=32,
            end_ridge=63,
            id_prefix="o_east_32_63",
            dry_after_harvest=True,
        )
        o_recheck = farm_world.get_inventory().oracle().with_id(
            "o_recheck_final_storage"
        ).depends_on(o_east, delay_seconds=2)
        o_report = aui.send_message_to_user(content=report_text).oracle().with_id(
            "o_report"
        ).depends_on(o_recheck, delay_seconds=2)

    return [
        briefing,
        o_weather,
        o_forecast,
        o_state,
        o_inventory,
        o_wait,
        o_soil,
        o_west_state,
        o_capacity_west,
        o_west,
        o_capacity_after_west,
        o_east_state,
        o_east,
        o_recheck,
        o_report,
    ]
